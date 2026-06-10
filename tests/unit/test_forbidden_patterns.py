"""Tests for the deterministic forbidden-pattern preflight.

Covers three surfaces:

1. ``_iter_added_lines`` — unified-diff parser (pure string handling).
2. ``_pattern_matches`` — ``re:`` prefix vs literal classification.
3. ``check_forbidden_patterns`` — end-to-end against a real git repo,
   since the actual failure mode we're defending against (spec-anchored
   MUST-NOT violations in implementer diffs) only manifests through
   git output.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.pipeline.forbidden_patterns import (
    ForbiddenPatternViolation,
    _iter_added_lines,
    _pattern_matches,
    _strip_line_comment,
    check_forbidden_patterns,
    format_violations_feedback,
)


class TestStripLineComment(unittest.TestCase):
    """Sprint 10: strip the line-comment tail before matching so a comment that
    MENTIONS a forbidden construct is not treated as a USE of it. The check is a
    preflight SHIP-GATE — a false-positive (comment) blocks correct code; a
    false-negative (over-stripping a string) lets the real bug through. So the
    rejections AND the must-still-fire cases are both pinned."""

    def test_real_t20_comment_is_stripped(self) -> None:
        # The exact line that aborted Sprint 2 T2.0: implementer AVOIDED round()
        # and explained it; the comment must not survive into the match text.
        line = "    # Manual rounding to avoid using round() function"
        self.assertEqual(_strip_line_comment(line, ".py"), "    ")
        self.assertFalse(_pattern_matches("round(", _strip_line_comment(line, ".py")))

    def test_apostrophe_in_string_before_comment_still_strips(self) -> None:
        # Neo's load-bearing case: an apostrophe ("it's") must NOT defeat the
        # strip (double-quote parity only). Single-quote parity re-broke this.
        line = 'label = "it\'s fine"  # round( here'
        self.assertEqual(_strip_line_comment(line, ".py"), 'label = "it\'s fine"  ')
        self.assertFalse(_pattern_matches("round(", _strip_line_comment(line, ".py")))

    def test_trailing_comment_stripped_code_kept(self) -> None:
        line = "cents = int(c + 0.5)  # avoid round()"
        self.assertEqual(_strip_line_comment(line, ".py"), "cents = int(c + 0.5)  ")

    def test_slash_comment_stripped_for_ts(self) -> None:
        self.assertEqual(_strip_line_comment("foo();  // round( here", ".ts"), "foo();  ")

    def test_real_use_still_fires(self) -> None:
        # No comment: a genuine round() call must still be caught.
        line = "    cents = round(value * 100)"
        self.assertEqual(_strip_line_comment(line, ".py"), line)
        self.assertTrue(_pattern_matches("round(", _strip_line_comment(line, ".py")))

    def test_hash_inside_double_quoted_string_is_not_a_comment(self) -> None:
        # A real use AFTER a string containing '#' must still fire — the '#' in
        # the string is not a comment marker (odd quote parity skips it).
        line = 'x = "a # b" + round(y)  # c'
        self.assertEqual(_strip_line_comment(line, ".py"), 'x = "a # b" + round(y)  ')
        self.assertTrue(_pattern_matches("round(", _strip_line_comment(line, ".py")))

    def test_slash_inside_double_quoted_string_not_stripped(self) -> None:
        # A URL inside a string ("http://…") must not be mistaken for a comment.
        line = 'const u = "http://example.com"'
        self.assertEqual(_strip_line_comment(line, ".ts"), line)

    def test_unknown_extension_returns_line_unchanged(self) -> None:
        # No known comment syntax => no stripping (pre-Sprint-10 behavior, safe).
        line = "x = round(y) # not a python file"
        self.assertEqual(_strip_line_comment(line, ".xyz"), line)
        self.assertEqual(_strip_line_comment(line, ""), line)

    def test_escaped_quote_does_not_hide_real_use(self) -> None:
        # Codex's BLOCKING case: an escaped \" before an in-string // must NOT
        # flip the parity and strip a real forbidden use after it. The unescaped
        # double-quote count keeps the in-string // recognised as in-string, so
        # the line is returned intact and the real Math.round( still fires.
        line = 'const s = "a \\" // b"; Math.round(x);'
        self.assertEqual(_strip_line_comment(line, ".ts"), line)
        self.assertTrue(_pattern_matches("Math.round(", _strip_line_comment(line, ".ts")))

    def test_escaped_backslash_then_closed_string_strips_trailing_comment(self) -> None:
        # \\ is an escaped backslash (not a shielded quote); the string closes,
        # so the trailing comment IS a comment and strips, while the real use
        # before it survives.
        line = r'x = "a\\" + round(y)  # note'
        self.assertEqual(_strip_line_comment(line, ".py"), r'x = "a\\" + round(y)  ')
        self.assertTrue(_pattern_matches("round(", _strip_line_comment(line, ".py")))

    def test_known_residual_single_quoted_string_is_deferred(self) -> None:
        # KNOWN RESIDUAL (deferred per Neo/Sprint 10): a #/// inside a
        # SINGLE-quoted string is not recognised (single-quote parity collides
        # with apostrophes), so a real use after it on one line is currently
        # MISSED. Pinned in the safe direction so the future tokenizer sprint
        # flips this from "does not fire" to "fires". Softened in production by
        # the downstream blind-scenario / QA-holdout gate. If this assertion
        # starts FAILING, single-quote handling was added — update the residual.
        line = "s = 'a # b'; r = round(x)"
        stripped = _strip_line_comment(line, ".py")
        self.assertFalse(_pattern_matches("round(", stripped))  # residual: missed


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestPatternMatches(unittest.TestCase):
    """``re:``-prefix vs literal classification."""

    def test_literal_substring_matches(self) -> None:
        self.assertTrue(_pattern_matches("Math.round(", "const x = Math.round(y);"))

    def test_literal_substring_no_match(self) -> None:
        self.assertFalse(_pattern_matches("Math.round(", "const x = y + 1;"))

    def test_pure_literal_without_prefix_treats_as_literal(self) -> None:
        # ``console`` has no ``re:`` prefix — literal substring, not regex.
        self.assertTrue(_pattern_matches("console", "console.log(1)"))
        self.assertFalse(_pattern_matches("console", "clog"))

    def test_literal_with_dots_is_literal_match(self) -> None:
        # Without ``re:`` prefix, ``.`` is just a literal dot. This was
        # the exact footgun that the auto-detect heuristic had: bare
        # ``.toFixed`` used to be treated as regex, matching "XtoFixed"
        # too. With the explicit prefix, users who want a literal dot
        # just type a dot.
        self.assertTrue(_pattern_matches(".toFixed", "x.toFixed(2)"))
        self.assertFalse(_pattern_matches(".toFixed", "XtoFixed"))

    def test_regex_with_prefix_uses_regex_search(self) -> None:
        self.assertTrue(
            _pattern_matches(r"re:\.toFixed\(\d+\)", "x.toFixed(2);")
        )

    def test_regex_prefix_respects_anchors(self) -> None:
        self.assertTrue(_pattern_matches("re:^import", "import fs from 'fs';"))
        self.assertFalse(_pattern_matches("re:^import", "  import fs"))

    def test_malformed_regex_falls_back_to_literal_full_pattern(self) -> None:
        # Unbalanced ``(`` inside the regex body. Fallback matches the
        # FULL pattern (including the ``re:`` prefix) as a literal, so
        # the noise is unambiguous rather than silently passing.
        self.assertFalse(_pattern_matches("re:(broken", "a(broken thing"))
        self.assertTrue(
            _pattern_matches("re:(broken", "emit re:(broken literal")
        )


class TestIterAddedLines(unittest.TestCase):
    """Unified-diff parser shape and counting."""

    def test_emits_added_line_with_file_and_number(self) -> None:
        diff = (
            "diff --git a/src/foo.ts b/src/foo.ts\n"
            "--- a/src/foo.ts\n"
            "+++ b/src/foo.ts\n"
            "@@ -0,0 +1,3 @@\n"
            "+line one\n"
            "+line two\n"
            "+line three\n"
        )
        rows = list(_iter_added_lines(diff))
        self.assertEqual(
            rows,
            [
                ("src/foo.ts", 1, "line one"),
                ("src/foo.ts", 2, "line two"),
                ("src/foo.ts", 3, "line three"),
            ],
        )

    def test_ignores_removed_lines(self) -> None:
        diff = (
            "+++ b/src/a.ts\n"
            "@@ -5,3 +5,2 @@\n"
            " context\n"
            "-gone\n"
            "+kept\n"
        )
        rows = list(_iter_added_lines(diff))
        # context line advances counter (5 -> 6), +kept is at 6.
        self.assertEqual(rows, [("src/a.ts", 6, "kept")])

    def test_handles_new_file_block(self) -> None:
        diff = (
            "+++ b/src/new.ts\n"
            "@@ -0,0 +1 @@\n"
            "+only line\n"
        )
        rows = list(_iter_added_lines(diff))
        self.assertEqual(rows, [("src/new.ts", 1, "only line")])

    def test_deleted_file_ignored(self) -> None:
        # ``+++ /dev/null`` — deleted file, no added lines attributable.
        diff = (
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-line one\n"
            "-line two\n"
        )
        self.assertEqual(list(_iter_added_lines(diff)), [])

    def test_multiple_files_tracked_separately(self) -> None:
        diff = (
            "+++ b/a.ts\n"
            "@@ -0,0 +1 @@\n"
            "+alpha\n"
            "+++ b/b.ts\n"
            "@@ -0,0 +1 @@\n"
            "+beta\n"
        )
        rows = list(_iter_added_lines(diff))
        self.assertEqual(
            rows,
            [("a.ts", 1, "alpha"), ("b.ts", 1, "beta")],
        )


class TestFormatViolationsFeedback(unittest.TestCase):
    """Implementer-facing feedback rendering."""

    def test_empty_list_returns_empty_string(self) -> None:
        self.assertEqual(format_violations_feedback([]), "")

    def test_groups_and_cites_anchor_file_line(self) -> None:
        violations = [
            ForbiddenPatternViolation(
                anchor_id="FR-3",
                pattern="Math.round(",
                file="packages/shared/src/parse-amount.ts",
                line_number=70,
                matched_text="const cents = Math.round(numValue * 100);",
            ),
        ]
        text = format_violations_feedback(violations)
        self.assertIn("FR-3 forbids `Math.round(`", text)
        self.assertIn("packages/shared/src/parse-amount.ts:70", text)
        self.assertIn("const cents = Math.round(numValue * 100);", text)
        self.assertIn("MUST-NOT", text)


# ---------------------------------------------------------------------------
# Integration against a real git repo
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def _init_repo(repo: Path) -> None:
    """Create an empty git repo with a single committed baseline."""
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    baseline = repo / "baseline.txt"
    baseline.write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "baseline.txt")
    _git(repo, "commit", "-q", "-m", "baseline")


class TestCheckForbiddenPatternsIntegration(unittest.TestCase):
    """End-to-end: real git repo, real diff, real check invocation."""

    def test_empty_manifest_is_noop(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            (repo / "src").mkdir()
            (repo / "src" / "new.ts").write_text(
                "const cents = Math.round(x * 100);\n",
                encoding="utf-8",
            )
            self.assertEqual(check_forbidden_patterns({}, repo), [])

    def test_literal_match_reports_violation(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            src_dir = repo / "src"
            src_dir.mkdir()
            (src_dir / "parse.ts").write_text(
                "export function parse(x: number) {\n"
                "  const cents = Math.round(x * 100);\n"
                "  return cents;\n"
                "}\n",
                encoding="utf-8",
            )
            violations = check_forbidden_patterns(
                {"FR-3": ["Math.round("]}, repo
            )
            self.assertEqual(len(violations), 1)
            v = violations[0]
            self.assertEqual(v.anchor_id, "FR-3")
            self.assertEqual(v.pattern, "Math.round(")
            self.assertEqual(v.file, "src/parse.ts")
            self.assertEqual(v.line_number, 2)
            self.assertIn("Math.round", v.matched_text)

    def test_regex_pattern_with_prefix(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            src = repo / "m.ts"
            src.write_text("const s = n.toFixed(2);\n", encoding="utf-8")
            violations = check_forbidden_patterns(
                {"FR-3": [r"re:\.toFixed\(\d+\)"]}, repo
            )
            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0].pattern, r"re:\.toFixed\(\d+\)")

    def test_removed_line_not_flagged(self) -> None:
        # Precommit the offending line, then remove it in the working
        # tree. ``check_forbidden_patterns`` must not flag removals.
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            src = repo / "src.ts"
            src.write_text("Math.round(1);\nkeep me\n", encoding="utf-8")
            _git(repo, "add", "src.ts")
            _git(repo, "commit", "-q", "-m", "seed offending code")
            # Now remove the offending line.
            src.write_text("keep me\n", encoding="utf-8")
            self.assertEqual(
                check_forbidden_patterns({"FR-3": ["Math.round("]}, repo),
                [],
            )

    def test_unknown_anchor_id_still_runs_check(self) -> None:
        # The anchor is not in the known_anchors set, but the check
        # must still enforce the pattern — cross-referencing is a soft
        # warning, not a gate.
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            (repo / "src.ts").write_text("console.log(1);\n", encoding="utf-8")
            violations = check_forbidden_patterns(
                {"FR-99": ["console.log("]},
                repo,
                known_anchors={"FR-1"},  # FR-99 not declared
            )
            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0].anchor_id, "FR-99")

    def test_multiple_violations_across_files(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            (repo / "a.ts").write_text("Math.round(1);\n", encoding="utf-8")
            (repo / "b.ts").write_text("Math.round(2);\nconsole.log('x');\n", encoding="utf-8")
            violations = check_forbidden_patterns(
                {
                    "FR-3": ["Math.round("],
                    "FR-5": ["console.log("],
                },
                repo,
            )
            # 2 Math.round + 1 console.log
            self.assertEqual(len(violations), 3)
            anchors = sorted(v.anchor_id for v in violations)
            self.assertEqual(anchors, ["FR-3", "FR-3", "FR-5"])

    def test_pre_existing_untracked_file_not_attributed(self) -> None:
        # Linus C1 (follow-up): a pre-existing untracked file (present
        # at baseline capture time) must not be attributed to the
        # implementer. The orchestrator captures the untracked set at
        # run start via ``git ls-files --others --exclude-standard``;
        # the check subtracts that set from the currently-untracked
        # set before scanning.
        #
        # ``git stash create -u`` does not reliably encode untracked
        # files into the tree on all git versions (tested on 2.50.1
        # where parent3 may be missing entirely), so we cannot rely on
        # a tree-walk of the stash sha — the untracked list must be
        # captured separately. This test mirrors that real strategy.
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)

            # Pre-existing untracked file with a forbidden pattern.
            (repo / "pre_existing.ts").write_text(
                "Math.round(1);  // here before the pipeline started\n",
                encoding="utf-8",
            )

            # Capture baseline: (a) the currently-untracked set and
            # (b) a stash sha for tracked changes (empty here since
            # the tracked tree is clean — falls back to HEAD).
            ls = subprocess.run(
                ["git", "-C", str(repo), "ls-files", "-z", "--others",
                 "--exclude-standard"],
                check=True, text=True, capture_output=True,
            )
            baseline_untracked = frozenset(
                p for p in ls.stdout.split("\x00") if p
            )
            self.assertIn("pre_existing.ts", baseline_untracked)

            # Implementer now writes a DIFFERENT untracked file with
            # the same forbidden pattern.
            (repo / "new.ts").write_text(
                "Math.round(2);\n", encoding="utf-8"
            )

            violations = check_forbidden_patterns(
                {"FR-3": ["Math.round("]},
                repo,
                diff_base="HEAD",
                baseline_untracked=baseline_untracked,
            )
            # Only the implementer-authored file should be flagged.
            self.assertEqual(
                len(violations), 1,
                f"expected only new.ts flagged, got: {[v.file for v in violations]}",
            )
            self.assertEqual(violations[0].file, "new.ts")

    def test_diff_base_excludes_pre_existing_dirty_changes(self) -> None:
        # The reason we capture ``preflight_base_ref`` via
        # ``git stash create`` is to attribute only what the
        # implementer actually wrote. If the host tree was already
        # dirty (a half-edited file from the operator), diffing against
        # HEAD would blame the implementer for those lines.
        #
        # Scenario: ``dirty.ts`` is already modified when the preflight
        # baseline is captured. The implementer then writes
        # ``new.ts``. The baseline-relative diff must show new.ts but
        # NOT attribute the pre-existing dirty.ts line to the
        # implementer.
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)

            # Committed starting state.
            (repo / "dirty.ts").write_text("// ok\n", encoding="utf-8")
            _git(repo, "add", "dirty.ts")
            _git(repo, "commit", "-q", "-m", "seed dirty file")

            # Operator's pre-existing uncommitted edit with a Math.round
            # call that has nothing to do with the implementer.
            (repo / "dirty.ts").write_text(
                "// ok\nMath.round(1);  // pre-existing\n",
                encoding="utf-8",
            )

            # Capture baseline NOW — everything up to this point is
            # "not implementer's fault". The tracked dirty change lives
            # in the stash sha; there are no untracked-at-baseline
            # files here.
            result = subprocess.run(
                ["git", "-C", str(repo), "stash", "create"],
                check=True,
                text=True,
                capture_output=True,
            )
            base_ref = result.stdout.strip()
            self.assertTrue(
                base_ref,
                "dirty tracked tree should produce a non-empty stash sha",
            )

            # Implementer runs and writes a brand-new offending file.
            (repo / "new.ts").write_text(
                "Math.round(2);\n", encoding="utf-8"
            )

            # Diffing against HEAD would flag BOTH lines (pre-existing
            # dirty + implementer new). Diffing against base_ref
            # correctly flags only the implementer's new file.
            against_head = check_forbidden_patterns(
                {"FR-3": ["Math.round("]}, repo, diff_base="HEAD"
            )
            against_base = check_forbidden_patterns(
                {"FR-3": ["Math.round("]}, repo, diff_base=base_ref
            )
            self.assertEqual(len(against_head), 2)
            self.assertEqual(len(against_base), 1)
            self.assertEqual(against_base[0].file, "new.ts")

    def test_architect_manifest_excluded_from_scan(self) -> None:
        # Regression for run d717e915: the architect declares
        # ``forbidden_patterns`` in ``.dark-factory/architecture/<spec>-
        # manifest.yaml``. That file is born untracked each run, so the
        # scanner used to attribute its own declaration lines to the
        # implementer and self-match with 100% reliability. Tool-
        # generated paths must be filtered from the scan corpus.
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)

            # Architect writes a manifest containing the forbidden
            # pattern as a YAML list item (mimicking the real manifest).
            arch_dir = repo / ".dark-factory" / "architecture"
            arch_dir.mkdir(parents=True)
            (arch_dir / "demo-manifest.yaml").write_text(
                "forbidden_patterns:\n"
                "  FR-3:\n"
                "  - Math.round(\n",
                encoding="utf-8",
            )

            # Implementer writes real code in src/ that also contains
            # the forbidden pattern.
            (repo / "src").mkdir()
            (repo / "src" / "calc.ts").write_text(
                "const cents = Math.round(x * 100);\n",
                encoding="utf-8",
            )

            violations = check_forbidden_patterns(
                {"FR-3": ["Math.round("]}, repo,
            )
            # Only the implementer's src file should be flagged, never
            # the architect manifest.
            self.assertEqual(
                len(violations), 1,
                f"expected only src file flagged, got: "
                f"{[v.file for v in violations]}",
            )
            self.assertEqual(violations[0].file, "src/calc.ts")

    def test_excluded_common_tool_dirs_not_attributed(self) -> None:
        # Defense-in-depth: node_modules, .git mirrors, build output,
        # etc. live in the working tree but are not implementer
        # authorship. Scanning them produces noise; forbid it.
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)

            for excluded in (
                "node_modules/pkg/index.js",
                "dist/bundle.js",
                "build/out.js",
                ".pytest_cache/v/cache.txt",
                "__pycache__/mod.cpython.pyc",
                ".venv/lib/x.py",
            ):
                target = repo / excluded
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("console.log('x');\n", encoding="utf-8")

            # And one legit source file — should be the only violation.
            (repo / "src.ts").write_text(
                "console.log('real');\n", encoding="utf-8",
            )

            violations = check_forbidden_patterns(
                {"FR-9": ["console.log("]}, repo,
            )
            self.assertEqual(
                [v.file for v in violations], ["src.ts"],
            )

    def test_iter_added_lines_skips_excluded_path_block(self) -> None:
        # Parser-level defense: if a tracked diff contains an excluded
        # path (e.g. target project committed a .dark-factory/ artifact),
        # that file's added lines must not surface.
        diff = (
            "+++ b/.dark-factory/architecture/demo-manifest.yaml\n"
            "@@ -0,0 +1 @@\n"
            "+- Math.round(\n"
            "+++ b/src/real.ts\n"
            "@@ -0,0 +1 @@\n"
            "+console.log('hi');\n"
        )
        rows = list(_iter_added_lines(diff))
        self.assertEqual(rows, [("src/real.ts", 1, "console.log('hi');")])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
