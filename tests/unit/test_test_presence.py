"""Tests for the deterministic test-presence preflight.

Covers:

- Pure path-classification helpers (``_is_test_path``, ``_in_source_dirs``).
- ``check_test_presence`` against a real git repo, since the failure
  mode (source without tests in the implementer's diff) is only
  testable through real git state.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.models.config import PipelineConfig
from darkfactory.pipeline.test_presence import (
    _in_source_dirs,
    _is_test_path,
    check_test_presence,
    format_violations_feedback,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestIsTestPathNode(unittest.TestCase):
    """Node/TypeScript filename + directory heuristics."""

    def test_dot_test_filename(self) -> None:
        self.assertTrue(_is_test_path("src/foo.test.ts", "node"))

    def test_dot_spec_filename(self) -> None:
        self.assertTrue(_is_test_path("src/foo.spec.ts", "node"))

    def test_tests_directory_segment(self) -> None:
        self.assertTrue(_is_test_path("tests/integration/foo.ts", "node"))

    def test_dunder_tests_directory(self) -> None:
        self.assertTrue(_is_test_path("src/__tests__/foo.ts", "node"))

    def test_test_directory_segment(self) -> None:
        self.assertTrue(_is_test_path("packages/shared/test/a.ts", "node"))

    def test_plain_source_file(self) -> None:
        self.assertFalse(_is_test_path("src/foo.ts", "node"))

    def test_filename_alone_with_test_word(self) -> None:
        # "testutils.ts" should NOT be classified as a test — no
        # dot-separator.
        self.assertFalse(_is_test_path("src/testutils.ts", "node"))

    def test_segment_substring_does_not_match(self) -> None:
        # ``testament`` and ``contests`` are NOT test segments. The
        # earlier implementation used an ``in`` membership check that
        # would not have matched these (frozenset check), but we lock
        # the intent in: only exact directory-component matches of
        # ``test``, ``tests``, ``__tests__`` classify a path as test.
        self.assertFalse(_is_test_path("src/testament/engine.ts", "node"))
        self.assertFalse(_is_test_path("src/contests/foo.ts", "node"))

    def test_filename_named_tests_does_not_match(self) -> None:
        # A regular source file happening to be named ``tests.ts`` sits
        # in a real source directory — it is source, not a test.
        self.assertFalse(_is_test_path("src/helpers/tests.ts", "node"))


class TestIsTestPathPython(unittest.TestCase):
    """Python-specific heuristics."""

    def test_test_prefix(self) -> None:
        self.assertTrue(_is_test_path("tests/test_foo.py", "python"))

    def test_test_suffix(self) -> None:
        self.assertTrue(_is_test_path("foo_test.py", "python"))

    def test_tests_dir_only(self) -> None:
        # tests/ directory segment alone counts.
        self.assertTrue(_is_test_path("tests/helpers.py", "python"))

    def test_plain_python_source(self) -> None:
        self.assertFalse(_is_test_path("src/foo.py", "python"))

    def test_dot_test_not_valid_for_python(self) -> None:
        # The Node heuristic (.test.) must NOT fire for Python.
        self.assertFalse(_is_test_path("src/foo.test.py", "python"))


class TestInSourceDirs(unittest.TestCase):
    def test_prefix_match(self) -> None:
        self.assertTrue(_in_source_dirs("src/foo.ts", ["src"]))

    def test_trailing_slash_normalized(self) -> None:
        self.assertTrue(_in_source_dirs("src/foo.ts", ["src/"]))

    def test_exact_path(self) -> None:
        self.assertTrue(_in_source_dirs("src", ["src"]))

    def test_no_prefix_match(self) -> None:
        self.assertFalse(_in_source_dirs("lib/foo.ts", ["src"]))

    def test_not_subpath(self) -> None:
        # "src" prefix must not match "src2/foo.ts".
        self.assertFalse(_in_source_dirs("src2/foo.ts", ["src"]))

    def test_multiple_dirs(self) -> None:
        self.assertTrue(_in_source_dirs("apps/api/x.ts", ["apps/api", "packages/shared"]))
        self.assertTrue(_in_source_dirs("packages/shared/y.ts", ["apps/api", "packages/shared"]))
        self.assertFalse(_in_source_dirs("apps/web/z.ts", ["apps/api", "packages/shared"]))

    def test_empty_source_dirs(self) -> None:
        self.assertFalse(_in_source_dirs("src/x.ts", []))


class TestFormatViolationsFeedback(unittest.TestCase):
    def test_no_violations_empty_string(self) -> None:
        from darkfactory.pipeline.test_presence import TestPresenceResult
        self.assertEqual(format_violations_feedback(TestPresenceResult()), "")

    def test_lists_added_sources(self) -> None:
        from darkfactory.pipeline.test_presence import TestPresenceResult
        r = TestPresenceResult(violations=["src/foo.ts"])
        text = format_violations_feedback(r)
        self.assertIn("src/foo.ts", text)
        self.assertIn("without any accompanying", text)


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
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "baseline.txt")
    _git(repo, "commit", "-q", "-m", "baseline")


def _make_config(source_dirs: list[str], language: str = "node") -> PipelineConfig:
    return PipelineConfig(
        language=language,
        source_dirs=source_dirs,
    )


class TestCheckTestPresenceIntegration(unittest.TestCase):
    def test_added_source_without_tests_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            (repo / "src").mkdir()
            (repo / "src" / "foo.ts").write_text("export const x = 1;\n", encoding="utf-8")

            result = check_test_presence(_make_config(["src"]), repo)

            self.assertTrue(result.failed)
            self.assertEqual(result.violations, ["src/foo.ts"])

    def test_added_source_with_companion_test_passes(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            src_dir = repo / "src"
            src_dir.mkdir()
            (src_dir / "foo.ts").write_text("export const x = 1;\n", encoding="utf-8")
            (src_dir / "foo.test.ts").write_text(
                "import {x} from './foo';\nit('works', () => {});\n",
                encoding="utf-8",
            )

            result = check_test_presence(_make_config(["src"]), repo)

            self.assertFalse(result.failed)
            self.assertEqual(result.violations, [])

    def test_added_source_with_modified_test_passes(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            src_dir = repo / "src"
            src_dir.mkdir()
            # Commit the test file first (tracked).
            (src_dir / "bar.test.ts").write_text(
                "it('existing', () => {});\n", encoding="utf-8",
            )
            _git(repo, "add", "src/bar.test.ts")
            _git(repo, "commit", "-q", "-m", "seed test")
            # Now add source and modify the test.
            (src_dir / "bar.ts").write_text("export const y = 2;\n", encoding="utf-8")
            (src_dir / "bar.test.ts").write_text(
                "it('existing', () => {});\nit('new', () => {});\n",
                encoding="utf-8",
            )

            result = check_test_presence(_make_config(["src"]), repo)

            self.assertFalse(result.failed)

    def test_empty_added_test_file_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            src_dir = repo / "src"
            src_dir.mkdir()
            (src_dir / "foo.ts").write_text("export const x = 1;\n", encoding="utf-8")
            # Stub test file with no invocation tokens.
            (src_dir / "foo.test.ts").write_text(
                "// TODO: add tests\n", encoding="utf-8",
            )

            result = check_test_presence(_make_config(["src"]), repo)

            self.assertTrue(result.failed)
            self.assertEqual(result.violations, ["src/foo.ts"])
            # Also emits an EMPTY_TEST_FILES warning.
            self.assertTrue(
                any("EMPTY_TEST_FILES" in w for w in result.warnings),
                result.warnings,
            )

    def test_delete_only_passes(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            src_dir = repo / "src"
            src_dir.mkdir()
            (src_dir / "gone.ts").write_text("// delete me\n", encoding="utf-8")
            _git(repo, "add", "src/gone.ts")
            _git(repo, "commit", "-q", "-m", "seed")
            (src_dir / "gone.ts").unlink()

            result = check_test_presence(_make_config(["src"]), repo)

            self.assertFalse(result.failed)

    def test_added_test_file_only_passes(self) -> None:
        # An add that is *only* a test (no source add) has nothing to
        # guard.
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            (repo / "src").mkdir()
            (repo / "src" / "orphan.test.ts").write_text(
                "it('x', () => {});\n", encoding="utf-8",
            )

            result = check_test_presence(_make_config(["src"]), repo)

            self.assertFalse(result.failed)

    def test_source_outside_source_dirs_emits_warning(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            (repo / "lib").mkdir()
            (repo / "lib" / "stray.ts").write_text(
                "export const z = 3;\n", encoding="utf-8",
            )

            result = check_test_presence(_make_config(["src"]), repo)

            # No source in `src`, so no violation; warning tells the
            # architect `source_dirs` may be wrong.
            self.assertFalse(result.failed)
            self.assertTrue(
                any("SOURCE_DIRS_COVERAGE" in w for w in result.warnings),
                result.warnings,
            )

    def test_python_language_heuristics(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            (repo / "src").mkdir()
            (repo / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")
            (repo / "tests").mkdir()
            (repo / "tests" / "test_foo.py").write_text(
                "def test_x():\n    assert True\n", encoding="utf-8",
            )

            result = check_test_presence(_make_config(["src"], language="python"), repo)

            self.assertFalse(result.failed)

    def test_python_source_without_python_test_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            (repo / "src").mkdir()
            (repo / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")

            result = check_test_presence(_make_config(["src"], language="python"), repo)

            self.assertTrue(result.failed)

    def test_pre_existing_untracked_source_not_attributed(self) -> None:
        # Linus C1 (follow-up): a source file untracked at baseline
        # capture time is not implementer authorship. The
        # baseline-untracked filter must exclude it so the implementer
        # is not asked to write a companion test for code they didn't
        # author. The orchestrator captures ``git ls-files --others``
        # at run start and threads the set into the check; this test
        # mirrors that pattern.
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            src_dir = repo / "src"
            src_dir.mkdir()

            # Pre-existing untracked source file.
            (src_dir / "pre_existing.ts").write_text(
                "export const existed = true;\n", encoding="utf-8"
            )

            # Capture the baseline-untracked set at this point.
            ls = subprocess.run(
                ["git", "-C", str(repo), "ls-files", "-z", "--others",
                 "--exclude-standard"],
                check=True, text=True, capture_output=True,
            )
            baseline_untracked = frozenset(
                p for p in ls.stdout.split("\x00") if p
            )
            self.assertIn("src/pre_existing.ts", baseline_untracked)

            # Implementer does nothing — no new sources, no tests.
            # The preflight must NOT flag pre_existing.ts as an
            # untested add just because it is currently untracked.
            result = check_test_presence(
                _make_config(["src"]),
                repo,
                diff_base="HEAD",
                baseline_untracked=baseline_untracked,
            )
            self.assertFalse(result.failed, result.violations)

    def test_partial_empty_added_tests_still_passes(self) -> None:
        # One real added test + one empty stub test. At least one
        # invocation token exists => presence satisfied. Guards against
        # an over-strict "all added tests must be non-empty" rule.
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            src_dir = repo / "src"
            src_dir.mkdir()
            (src_dir / "foo.ts").write_text("export const x = 1;\n", encoding="utf-8")
            (src_dir / "foo.test.ts").write_text(
                "it('covers x', () => {});\n", encoding="utf-8"
            )
            (src_dir / "bar.test.ts").write_text(
                "// TODO: write tests\n", encoding="utf-8"
            )

            result = check_test_presence(_make_config(["src"]), repo)
            self.assertFalse(result.failed)

    def test_git_failure_emits_fail_open_event(self) -> None:
        # Run ``check_test_presence`` in a directory that is NOT a git
        # repo — every ``git diff`` / ``git ls-files`` invocation fails.
        # The check must return empty-violations (no fabrication) AND
        # emit PREFLIGHT_FAIL_OPEN events to the provided events file.
        import json

        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Deliberately NOT calling _init_repo — no .git/.
            (repo / "src").mkdir()
            (repo / "src" / "foo.ts").write_text(
                "export const x = 1;\n", encoding="utf-8"
            )
            events_file = repo / "events.jsonl"

            result = check_test_presence(
                _make_config(["src"]),
                repo,
                events_file=events_file,
                spec_name="my-spec",
                run_id="abc123",
                iteration=2,
            )

            # Fail-open: no fabricated violations.
            self.assertFalse(result.failed)
            # But observable: event(s) emitted.
            self.assertTrue(events_file.exists())
            lines = [
                line
                for line in events_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(lines, "expected at least one event emitted")
            parsed = [json.loads(line) for line in lines]
            for ev in parsed:
                self.assertEqual(ev["event"], "PREFLIGHT_FAIL_OPEN")
                self.assertEqual(ev["spec"], "my-spec")
                self.assertEqual(ev["run_id"], "abc123")
                self.assertEqual(ev["iter"], 2)
                self.assertEqual(ev["data"]["check"], "test_presence")

    def test_monorepo_multiple_source_dirs(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            shared = repo / "packages" / "shared" / "src"
            shared.mkdir(parents=True)
            (shared / "parse-amount.ts").write_text(
                "export function parse(x: string) { return 0; }\n",
                encoding="utf-8",
            )

            config = _make_config(
                ["apps/api/src", "apps/web/src", "packages/shared/src"],
            )
            result = check_test_presence(config, repo)

            self.assertTrue(result.failed)
            self.assertEqual(
                result.violations, ["packages/shared/src/parse-amount.ts"]
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
