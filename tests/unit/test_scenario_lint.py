"""Tests for scripts/lib/scenario_lint.py (Phase C).

The lint is a pre-gate shape check that prevents scenarios from
invoking schema-migration commands. It runs in the gate container,
which has no project mount, so the tests drive the module directly.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


def _load_module() -> Any:
    spec_path = (
        Path(__file__).resolve().parent.parent.parent
        / "scripts" / "lib" / "scenario_lint.py"
    )
    spec = importlib.util.spec_from_file_location("scenario_lint", spec_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass can resolve cls.__module__.
    sys.modules["scenario_lint"] = mod
    spec.loader.exec_module(mod)
    return mod


scenario_lint = _load_module()


class TestMigrationCommandDetection(unittest.TestCase):
    def _lint(self, content: str, filename: str = "x.test.ts") -> list:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / filename
            p.write_text(content)
            return scenario_lint.lint_dir(Path(d))

    def test_pnpm_db_migrate_flagged(self) -> None:
        code = (
            'import { execSync } from "child_process";\n'
            'beforeAll(() => { execSync("cd apps/api && pnpm db:migrate"); });\n'
        )
        issues = self._lint(code)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule, "db-migrate-in-scenario")

    def test_drizzle_kit_flagged(self) -> None:
        code = 'execSync("npx drizzle-kit push");\n'
        issues = self._lint(code)
        self.assertEqual(len(issues), 1)

    def test_alembic_upgrade_flagged_python(self) -> None:
        code = 'subprocess.run(["alembic", "upgrade", "head"])\n'
        issues = self._lint(code, filename="test_migrations.py")
        self.assertEqual(len(issues), 1)

    def test_plain_comment_not_flagged(self) -> None:
        code = (
            "// This test assumes migrations have run already.\n"
            "// (We used to call pnpm db:migrate here — don't.)\n"
            'it("works", () => { expect(true).toBe(true); });\n'
        )
        issues = self._lint(code)
        self.assertEqual(issues, [])

    def test_non_migration_execsync_not_flagged(self) -> None:
        code = 'execSync("pnpm build", { cwd: "apps/api" });\n'
        issues = self._lint(code)
        self.assertEqual(issues, [])

    def test_multiple_issues_collected(self) -> None:
        code = (
            'execSync("cd apps/api && pnpm db:migrate");\n'
            'execSync("npx drizzle-kit push");\n'
        )
        issues = self._lint(code)
        self.assertEqual(len(issues), 2)


class TestReportFormat(unittest.TestCase):
    def test_no_issues_report(self) -> None:
        report = scenario_lint.format_report([])
        self.assertIn("no issues", report)

    def test_issues_include_fix_hint(self) -> None:
        issues = [scenario_lint.LintIssue(
            file=Path("/x/y.test.ts"), line=5, rule="db-migrate-in-scenario",
            excerpt="execSync(\"pnpm db:migrate\")",
            fix_hint="DO X",
        )]
        report = scenario_lint.format_report(issues)
        self.assertIn("db-migrate-in-scenario", report)
        self.assertIn("DO X", report)
        self.assertIn("/x/y.test.ts:5", report)


class TestCLIExit(unittest.TestCase):
    def test_exit_zero_on_clean(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ok.test.ts").write_text("it('x', () => {});\n")
            self.assertEqual(scenario_lint.main([d]), 0)

    def test_exit_one_on_issues(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "bad.test.ts").write_text(
                'execSync("cd apps && pnpm db:migrate");\n'
            )
            self.assertEqual(scenario_lint.main([d]), 1)


if __name__ == "__main__":
    unittest.main()
