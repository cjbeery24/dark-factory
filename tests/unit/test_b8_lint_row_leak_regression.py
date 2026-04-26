"""ROADMAP.md Phase 2 B8: gate-report-row leak regression test.

Pins the d2fecbf failure mode shut. The 2026-04 ``d2fecbf`` commit
promoted the scenario-lint FAIL row to a gating row, which converted a
Reviewer/Oracle-only artifact into an Implementer-readable artifact on
the next iteration's prelude. ``5130935`` reverted the gating change;
B2 (``ff5378f``) sanitized the row to a count-only summary so even if
a future change re-promotes the row to gating, the content carries no
test-shape information.

This test goes one level systematic: rather than enumerate specific
strings, it iterates over **every** built-in foot-gun regex in
``scripts/lib/scenario_lint.py`` plus every spec-driven
``forbidden_test_shapes`` regex from a fixture spec, and asserts that
the lint row content (the stdout of ``scenario_lint.py --detail-out``,
which is what the bash wrapper writes to ``$REPORT_FILE``) contains
zero matches against any of them.

Why systematic: the d2fecbf failure mode was an audience escalation,
not a particular leaked string. Future regressions could leak via:

- A new built-in foot-gun pattern whose rule name happens to be a
  human-readable phrase that itself trips the pattern (self-match).
- A new ``--detail-out`` mode that buffers the body to stdout under
  some flag combination.
- A new gate-report row writer that ignores ``--detail-out`` and pipes
  the legacy verbose stdout into the report.

A fixed-string test only catches the leaks the test author already
imagined. The systematic scan catches any pattern in the closed
set without requiring the test to be updated.

Pairs with the barrier-mask invariant in
``tests/unit/test_barriers.py`` (``_REPORTS in implementer_masked``).
The mask is the primary barrier; this test is the content-level
defense-in-depth that survives any change which would weaken the mask.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


def _load_module() -> Any:
    """Load scripts/lib/scenario_lint.py as an importable module.

    Mirrors the loader in ``test_scenario_lint.py``. B8 uses the same
    bare ``"scenario_lint"`` key intentionally — both tests load the
    same script, sharing the module object across the test session is
    correct.
    """
    spec_path = (
        Path(__file__).resolve().parent.parent.parent
        / "scripts" / "lib" / "scenario_lint.py"
    )
    spec = importlib.util.spec_from_file_location("scenario_lint", spec_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["scenario_lint"] = mod
    spec.loader.exec_module(mod)
    return mod


scenario_lint = _load_module()


# --------------------------------------------------------------------------
# Fixture: scenarios that hit every built-in rule, plus a spec with §4a
# forbidden_test_shapes that themselves contain readable English so a
# self-match leak (rule name echoed into stdout) would be detectable.
# --------------------------------------------------------------------------

_HITS_DB_MIGRATE = """\
import { test } from "vitest";
test("[AC-1] migration", async () => {
  // Hits db-migrate-in-scenario via _MIGRATION_CMD_SHELL_RE.
  execSync("cd apps && pnpm db:migrate");
});
"""

# Hits ``_MIGRATION_CMD_LIST_RE`` (Python subprocess + list-form args).
# Filename glob is ``test_*.py`` (see ``_SCENARIO_GLOBS`` in
# ``scenario_lint.py``) so the file is named ``test_migrate_list.py``
# downstream.
_HITS_DB_MIGRATE_LIST_PY = """\
import subprocess
def test_migration_list_form():
    # Hits db-migrate-in-scenario via _MIGRATION_CMD_LIST_RE.
    subprocess.run(["alembic", "upgrade", "head"], check=True)
"""

_HITS_PARAMETERIZED_SET_LOCAL = """\
import { test } from "vitest";
test("[AC-2] set local param", async () => {
  // Hits parameterized-set-local.
  await tx`SET LOCAL app.tenant_id = ${'t1'}`;
});
"""

_HITS_PEER_CLIENT_GUC_READ = """\
import { test } from "vitest";
import postgres from "postgres";
test("[AC-3] peer guc read", async () => {
  // Hits peer-client-guc-read.
  const peer = postgres(process.env.DATABASE_URL!);
  const r = await peer`SELECT current_setting('app.tenant_id', true)`;
});
"""

# A spec with a §4a ``forbidden_test_shapes`` entry whose pattern is a
# regex-shape string that COULD plausibly leak via a future regression
# (e.g., a ``--detail-out`` mode that echoes the rule's source pattern
# back to stdout, or a stringified rule name embedding the raw regex).
# ``execSync\(`` is realistic — it's the kind of pattern a spec author
# might write, AND it's a substring of the rule name a pre-B2 verbose
# stdout would carry (``spec-forbidden-shape:execSync\(``). If a future
# change re-enables verbose stdout under any flag combination, the
# self-match against the rule name fires this regex.
_SPEC_WITH_FORBIDDEN_SHAPE = """\
# Demo Spec

## 1. Goal
Whatever.

## 4a. Negative Space

```yaml
forbidden_test_shapes:
  - pattern: "execSync\\\\("
    reason: "Subprocess invocation belongs in setup, not scenarios"
```
"""

_HITS_SPEC_FORBIDDEN = """\
import { test } from "vitest";
test("[AC-4] spec forbidden", async () => {
  // Hits the spec-forbidden-shape entry above (execSync\\().
  execSync("ls");
});
"""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _all_builtin_patterns() -> "list[tuple[str, Any]]":
    """Closed set of built-in foot-gun regexes shipped with
    scenario_lint.py. Adding a new built-in rule requires adding the
    pattern here AND wiring it into the scanner.

    Returns ``[(name, compiled_pattern), ...]`` so an assertion failure
    can name the offending pattern.
    """
    return [
        ("_MIGRATION_CMD_SHELL_RE", scenario_lint._MIGRATION_CMD_SHELL_RE),
        ("_MIGRATION_CMD_LIST_RE", scenario_lint._MIGRATION_CMD_LIST_RE),
        ("_PARAMETERIZED_SET_LOCAL_RE", scenario_lint._PARAMETERIZED_SET_LOCAL_RE),
        ("_PEER_CLIENT_GUC_READ_RE", scenario_lint._PEER_CLIENT_GUC_READ_RE),
    ]


def _capture_main(argv: "list[str]") -> "tuple[int, str]":
    """Run scenario_lint.main() capturing stdout."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = scenario_lint.main(argv)
    return exit_code, buf.getvalue()


def _assert_no_pattern_matches(
    test_case: unittest.TestCase,
    text: str,
    *,
    spec_path: "Path | None" = None,
    label: str,
) -> None:
    """Run every built-in foot-gun regex and every spec-loaded §4a
    pattern against ``text``; fail with a precise breadcrumb on any
    match.
    """
    for name, pat in _all_builtin_patterns():
        matches = list(pat.finditer(text))
        test_case.assertEqual(
            matches, [],
            f"{label} contains a match against built-in foot-gun "
            f"pattern {name}: matched={[m.group(0) for m in matches]!r}\n"
            f"Full {label} content:\n{text!r}",
        )

    if spec_path is not None:
        shapes = scenario_lint.load_forbidden_test_shapes(spec_path)
        for shape in shapes:
            matches = list(shape.compiled.finditer(text))
            test_case.assertEqual(
                matches, [],
                f"{label} contains a match against spec §4a pattern "
                f"{shape.pattern!r}: matched={[m.group(0) for m in matches]!r}\n"
                f"Full {label} content:\n{text!r}",
            )


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class TestB8LintRowLeakRegression(unittest.TestCase):
    """Systematic scan of the gate-report-bound stdout against every
    known foot-gun regex (built-in + spec). The d2fecbf regression
    surface is the lint row content — not the rule names, file paths,
    or excerpts in isolation, but any of those that match a known
    foot-gun pattern at the byte level.
    """

    def test_count_only_stdout_clean_under_every_builtin_rule(self) -> None:
        """Scenarios that hit ALL three built-in rules. The
        ``--detail-out`` stdout (= ``$_LINT_OUT`` per
        ``scripts/quality-gate.sh:1368``) must contain zero matches
        against any built-in foot-gun pattern.
        """
        with tempfile.TemporaryDirectory() as d:
            scen = Path(d) / "scenarios"
            scen.mkdir()
            (scen / "a.test.ts").write_text(_HITS_DB_MIGRATE, encoding="utf-8")
            (scen / "b.test.ts").write_text(
                _HITS_PARAMETERIZED_SET_LOCAL, encoding="utf-8",
            )
            (scen / "c.test.ts").write_text(
                _HITS_PEER_CLIENT_GUC_READ, encoding="utf-8",
            )
            (scen / "test_migrate_list.py").write_text(
                _HITS_DB_MIGRATE_LIST_PY, encoding="utf-8",
            )
            detail = Path(d) / "audit" / "lint.txt"

            exit_code, stdout = _capture_main([
                str(scen), "--detail-out", str(detail),
            ])

            # Pre-conditions: lint fired, found ≥4 issues (one per file
            # — confirms all four built-in regexes fired, not just three).
            self.assertEqual(exit_code, 1)
            self.assertGreaterEqual(int(stdout.strip()), 4)
            # The verbose body landed in the audit dir (post-B2 contract).
            body = detail.read_text(encoding="utf-8")
            # Sanity: the verbose body DOES contain rule names. If this
            # assertion fails, the audit log itself is empty and the
            # leak-regression test below would trivially pass — guard
            # against that false-positive shape.
            self.assertIn("db-migrate-in-scenario", body)

            # The actual regression check: stdout (= row content) is
            # clean against every built-in foot-gun regex.
            _assert_no_pattern_matches(
                self, stdout,
                spec_path=None,
                label="lint count-only stdout (built-in scan)",
            )

    def test_count_only_stdout_clean_under_spec_forbidden_shapes(self) -> None:
        """Spec defines a §4a ``forbidden_test_shapes`` entry whose
        pattern value is a regex-shape phrase (``execSync\\(``). The
        scenario contains a match. The count-only stdout must contain
        zero matches against the spec pattern — pinning the *self-match*
        leak surface: if a future regression echoes the rule name
        (``spec-forbidden-shape:execSync\\(``) into stdout, that name
        embeds the raw pattern and the spec's own regex fires against
        the row content.
        """
        with tempfile.TemporaryDirectory() as d:
            project_dir = Path(d)
            spec_path = project_dir / "spec.md"
            spec_path.write_text(_SPEC_WITH_FORBIDDEN_SHAPE, encoding="utf-8")

            scen = project_dir / "scenarios"
            scen.mkdir()
            (scen / "a.test.ts").write_text(
                _HITS_SPEC_FORBIDDEN, encoding="utf-8",
            )
            detail = project_dir / "audit" / "lint.txt"

            exit_code, stdout = _capture_main([
                str(scen),
                "--spec-path", str(spec_path),
                "--detail-out", str(detail),
            ])

            self.assertEqual(exit_code, 1)
            self.assertGreaterEqual(int(stdout.strip()), 1)
            body = detail.read_text(encoding="utf-8")
            # Verify the audit body carries the spec-loaded rule name
            # (``spec-forbidden-shape:<pattern>``) — without this, the
            # leak-regression check below would be vacuous.
            self.assertIn("spec-forbidden-shape:execSync", body)

            _assert_no_pattern_matches(
                self, stdout,
                spec_path=spec_path,
                label="lint count-only stdout (spec §4a scan)",
            )

    def test_count_only_stdout_clean_on_clean_scenarios(self) -> None:
        """Negative control: when scenarios are clean, the lint stdout
        is the literal ``"0\\n"`` and trivially contains no matches.
        Pins that the assertion isn't accidentally vacuous on the
        non-firing path.
        """
        with tempfile.TemporaryDirectory() as d:
            scen = Path(d) / "scenarios"
            scen.mkdir()
            (scen / "ok.test.ts").write_text(
                "import { test } from \"vitest\";\n"
                "test(\"[AC-1] clean\", () => {});\n",
                encoding="utf-8",
            )
            detail = Path(d) / "audit" / "lint.txt"

            exit_code, stdout = _capture_main([
                str(scen), "--detail-out", str(detail),
            ])
            self.assertEqual(exit_code, 0)
            self.assertEqual(stdout.strip(), "0")
            _assert_no_pattern_matches(
                self, stdout,
                spec_path=None,
                label="clean lint stdout",
            )

    def test_audit_body_intentionally_carries_diagnostic_detail(self) -> None:
        """Symmetry check: the audit body (which goes to the
        Implementer-masked ``.dark-factory/audit/`` dir) DOES contain
        rule names + file paths + excerpts — that's the whole point of
        ``--detail-out``. If a future refactor accidentally swaps the
        sanitization (count-to-audit, body-to-stdout), THIS test fails
        loud at the same time the count_only_stdout tests catch the
        leak. Together, the two assertions pin the directionality of
        the sanitization, not just its presence.
        """
        with tempfile.TemporaryDirectory() as d:
            scen = Path(d) / "scenarios"
            scen.mkdir()
            (scen / "a.test.ts").write_text(
                _HITS_DB_MIGRATE, encoding="utf-8",
            )
            detail = Path(d) / "audit" / "lint.txt"

            _capture_main([
                str(scen), "--detail-out", str(detail),
            ])

            body = detail.read_text(encoding="utf-8")
            # Diagnostic content present in audit (masked from Implementer).
            self.assertIn("db-migrate-in-scenario", body)
            self.assertIn("a.test.ts", body)
            # And: the audit body MUST contain a foot-gun pattern match
            # (e.g., the verbatim ``pnpm db:migrate`` excerpt) — so the
            # mask-from-Implementer is the only thing keeping this
            # content out of iter-N+1's prelude. If a future change
            # promotes audit content to a non-masked surface, the
            # barrier mask invariant test in ``test_barriers.py`` is
            # the dual regression backstop.
            matches_anywhere = any(
                pat.search(body) is not None
                for _, pat in _all_builtin_patterns()
            )
            self.assertTrue(
                matches_anywhere,
                "audit body should contain at least one foot-gun "
                "pattern match (verbose diagnostic content) — if it "
                "doesn't, the lint row test above is trivially passing "
                "and the leak-regression coverage is illusory.",
            )


if __name__ == "__main__":
    unittest.main()
