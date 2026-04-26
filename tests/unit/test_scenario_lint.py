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


class TestParameterizedSetLocal(unittest.TestCase):
    """Rule: flag ``tx`SET LOCAL app.x = '${val}'`` ``.

    Postgres rejects a bind parameter at the value position of SET LOCAL
    with "could not determine data type of parameter $1". The correct
    primitive is ``tx`SELECT set_config('app.x', ${val}, true)`` ``.
    """

    def _lint(self, content: str) -> list:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "x.test.ts").write_text(content)
            return scenario_lint.lint_dir(Path(d))

    def test_flags_basic_form(self) -> None:
        code = (
            "await client.begin(async (tx) => {\n"
            "  await tx`SET LOCAL app.tenant_id = '${tenant}'`;\n"
            "});\n"
        )
        issues = self._lint(code)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule, "parameterized-set-local")
        self.assertEqual(issues[0].line, 2)

    def test_flags_nonstring_value(self) -> None:
        # No surrounding quotes — still a bind-parameter at value position.
        code = "await tx`SET LOCAL app.flag = ${x}`;\n"
        self.assertEqual(len(self._lint(code)), 1)

    def test_flags_multiple_occurrences(self) -> None:
        code = (
            "await tx`SET LOCAL app.a = '${a}'`;\n"
            "await tx`SET LOCAL app.b = '${b}'`;\n"
        )
        issues = self._lint(code)
        self.assertEqual(len(issues), 2)
        self.assertEqual([i.line for i in issues], [1, 2])

    def test_flags_multiline_template(self) -> None:
        # Tagged template body spans multiple lines.
        code = (
            "await tx`\n"
            "  SET LOCAL app.tenant_id =\n"
            "    '${tenant}'\n"
            "`;\n"
        )
        issues = self._lint(code)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule, "parameterized-set-local")

    def test_does_not_flag_set_config(self) -> None:
        # Correct primitive: set_config takes a bindable value argument.
        code = "await tx`SELECT set_config('app.tenant_id', ${tenant}, true)`;\n"
        self.assertEqual(self._lint(code), [])

    def test_does_not_flag_literal_set_local(self) -> None:
        # Constant value in SET LOCAL is legal SQL — Postgres accepts it.
        # The foot-gun is specifically ``${…}`` in the value position.
        code = "await tx`SET LOCAL app.tenant_id = 'constant-literal'`;\n"
        self.assertEqual(self._lint(code), [])

    def test_does_not_flag_plain_comment(self) -> None:
        code = "// Example in docs: tx`SET LOCAL app.x = '${val}'`\n"
        # The regex has no concept of comments, so this WILL match.
        # Document the behavior: we err on the side of flagging, since
        # "a scenario shouldn't have this in a comment either" is a
        # reasonable stance. If this becomes noisy, we can add a
        # per-line comment filter — for now, the positive case is
        # vanishingly rare.
        self.assertEqual(len(self._lint(code)), 1)


class TestPeerClientGucRead(unittest.TestCase):
    """Rule: flag peer-connection ``current_setting('app.*')`` reads.

    ``SET LOCAL`` is tx-scoped to the app's own tx; a second
    ``postgres(process.env.…)`` client will always see ``''`` for
    ``app.*`` GUCs regardless of impl correctness.
    """

    def _lint(self, content: str) -> list:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "x.test.ts").write_text(content)
            return scenario_lint.lint_dir(Path(d))

    def test_flags_adjacent(self) -> None:
        code = (
            'const peer = postgres(process.env.DATABASE_URL);\n'
            "const v = await peer`SELECT current_setting('app.tenant_id')`;\n"
        )
        issues = self._lint(code)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule, "peer-client-guc-read")

    def test_flags_separated_by_lines(self) -> None:
        code = (
            'const peer = postgres(process.env.DATABASE_URL);\n'
            'const unrelated = 1;\n'
            'const other = "string";\n'
            "const v = await peer`SELECT current_setting('app.tenant_id')`;\n"
        )
        self.assertEqual(len(self._lint(code)), 1)

    def test_flags_alt_env_var_name(self) -> None:
        code = (
            'const peer = postgres(process.env.POSTGRES_URL);\n'
            "const v = await peer`SELECT current_setting('app.foo')`;\n"
        )
        self.assertEqual(len(self._lint(code)), 1)

    def test_does_not_flag_non_app_guc(self) -> None:
        # TimeZone, server_version, etc. are legitimate session GUCs
        # readable on any connection.
        code = (
            'const peer = postgres(process.env.DATABASE_URL);\n'
            "const v = await peer`SELECT current_setting('TimeZone')`;\n"
        )
        self.assertEqual(self._lint(code), [])

    def test_does_not_flag_same_tx_read(self) -> None:
        # No peer-client — current_setting on the same tx is correct.
        code = (
            "await client.begin(async (tx) => {\n"
            "  await tx`SELECT set_config('app.tenant_id', ${t}, true)`;\n"
            "  const v = await tx`SELECT current_setting('app.tenant_id')`;\n"
            "});\n"
        )
        self.assertEqual(self._lint(code), [])

    def test_does_not_flag_peer_without_app_read(self) -> None:
        # Peer client doing unrelated work is fine.
        code = (
            'const peer = postgres(process.env.DATABASE_URL);\n'
            'await peer`SELECT 1`;\n'
        )
        self.assertEqual(self._lint(code), [])


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


class TestForbiddenTestShapes(unittest.TestCase):
    """Phase 5 Stage 2d: §4a forbidden_test_shapes loaded from spec
    markdown and applied as additional lint rules."""

    def _spec_with_shape(
        self, pattern: str, reason: str = "test reason",
    ) -> str:
        return f"""\
# Test Spec

## 1. Problem Statement

stuff

## 4a. Negative Space

```yaml
forbidden_test_shapes:
  - pattern: '{pattern}'
    reason: "{reason}"
    applies_to: [scenarios]
```

## 5. Interface Contract

stuff
"""

    def test_load_forbidden_shapes_from_spec(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            spec_path = Path(d) / "spec.md"
            spec_path.write_text(self._spec_with_shape(r"forbidden_token"))
            shapes = scenario_lint.load_forbidden_test_shapes(spec_path)
            self.assertEqual(len(shapes), 1)
            self.assertEqual(shapes[0].pattern, "forbidden_token")
            self.assertEqual(shapes[0].reason, "test reason")

    def test_load_returns_empty_when_no_section_4a(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            spec_path = Path(d) / "spec.md"
            spec_path.write_text("# Spec\n\n## 1. Problem\n\nstuff\n")
            shapes = scenario_lint.load_forbidden_test_shapes(spec_path)
            self.assertEqual(shapes, [])

    def test_load_returns_empty_when_spec_missing(self) -> None:
        shapes = scenario_lint.load_forbidden_test_shapes(
            Path("/nonexistent/spec.md")
        )
        self.assertEqual(shapes, [])

    def test_invalid_regex_skipped_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            spec_path = Path(d) / "spec.md"
            spec_path.write_text(self._spec_with_shape(r"[unclosed"))
            shapes = scenario_lint.load_forbidden_test_shapes(spec_path)
            # Pattern compile failure → entry skipped, no crash.
            self.assertEqual(shapes, [])

    def test_lint_dir_applies_spec_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            spec_path = Path(d) / "spec.md"
            spec_path.write_text(self._spec_with_shape(r"banned_helper"))
            scenarios = Path(d) / "scenarios"
            scenarios.mkdir()
            (scenarios / "a.test.ts").write_text(
                "it('uses banned thing', () => {\n"
                "  banned_helper(1);\n"
                "});\n"
            )
            shapes = scenario_lint.load_forbidden_test_shapes(spec_path)
            issues = scenario_lint.lint_dir(
                scenarios, forbidden_test_shapes=shapes,
            )
            self.assertEqual(len(issues), 1)
            self.assertTrue(issues[0].rule.startswith("spec-forbidden-shape:"))
            self.assertIn("banned_helper", issues[0].rule)
            self.assertIn("test reason", issues[0].fix_hint)

    def test_lint_dir_no_match_no_issues(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            spec_path = Path(d) / "spec.md"
            spec_path.write_text(self._spec_with_shape(r"banned_helper"))
            scenarios = Path(d) / "scenarios"
            scenarios.mkdir()
            (scenarios / "a.test.ts").write_text(
                "it('clean', () => {});\n"
            )
            shapes = scenario_lint.load_forbidden_test_shapes(spec_path)
            issues = scenario_lint.lint_dir(
                scenarios, forbidden_test_shapes=shapes,
            )
            self.assertEqual(issues, [])

    def test_main_with_spec_path_arg_exits_one_on_match(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            spec_path = Path(d) / "spec.md"
            spec_path.write_text(self._spec_with_shape(r"badthing"))
            scenarios = Path(d) / "scenarios"
            scenarios.mkdir()
            (scenarios / "a.test.ts").write_text("badthing();\n")
            exit_code = scenario_lint.main([
                str(scenarios), "--spec-path", str(spec_path),
            ])
            self.assertEqual(exit_code, 1)

    def test_main_without_spec_path_runs_builtins_only(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            scenarios = Path(d) / "scenarios"
            scenarios.mkdir()
            # File contains 'banned_helper' but no --spec-path → no
            # spec rules → builtins find nothing → exit 0.
            (scenarios / "a.test.ts").write_text("banned_helper();\n")
            self.assertEqual(scenario_lint.main([str(scenarios)]), 0)


class TestDetailOutSanitization(unittest.TestCase):
    """ROADMAP.md Phase 2 B2: --detail-out splits the lint output so
    the gate-report-bound caller (the bash wrapper) gets only an
    integer count on stdout, while the verbose body — rule names,
    file:line anchors, source excerpts — lands in the audit dir.

    Together with the audit-dir mask in barriers.py:62
    (``_AUDIT in implementer_masked``), this keeps test-shape
    information off the validator-bridge surface even when scenario
    lint fires (defense-in-depth for ROADMAP.md Phase 2 B1).
    """

    def _capture_main(self, argv: list[str]) -> tuple[int, str]:
        """Run main() capturing stdout. Returns (exit, stdout)."""
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = scenario_lint.main(argv)
        return exit_code, buf.getvalue()

    def test_detail_out_emits_count_only_on_stdout_with_issues(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            scenarios = Path(d) / "scenarios"
            scenarios.mkdir()
            # Two issues — both will hit db-migrate-in-scenario.
            (scenarios / "a.test.ts").write_text(
                'execSync("cd apps && pnpm db:migrate");\n'
            )
            (scenarios / "b.test.ts").write_text(
                'execSync("npx drizzle-kit push");\n'
            )
            detail = Path(d) / "audit" / "scenario-lint-x-iter1.txt"
            exit_code, stdout = self._capture_main([
                str(scenarios), "--detail-out", str(detail),
            ])
            self.assertEqual(exit_code, 1)
            self.assertEqual(stdout.strip(), "2")
            # Verbose body landed in the audit file, not stdout.
            body = detail.read_text()
            self.assertIn("db-migrate-in-scenario", body)
            # Stdout must NOT contain rule names, file paths, or excerpts.
            self.assertNotIn("db-migrate-in-scenario", stdout)
            self.assertNotIn("a.test.ts", stdout)
            self.assertNotIn("execSync", stdout)

    def test_detail_out_emits_zero_on_clean(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            scenarios = Path(d) / "scenarios"
            scenarios.mkdir()
            (scenarios / "ok.test.ts").write_text("it('x', () => {});\n")
            detail = Path(d) / "audit" / "scenario-lint-x-iter1.txt"
            exit_code, stdout = self._capture_main([
                str(scenarios), "--detail-out", str(detail),
            ])
            self.assertEqual(exit_code, 0)
            self.assertEqual(stdout.strip(), "0")
            # Detail file still written so the audit trail is uniform
            # across pass and fail iterations.
            self.assertTrue(detail.exists())
            self.assertIn("no issues", detail.read_text())

    def test_detail_out_creates_parent_dir(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            scenarios = Path(d) / "scenarios"
            scenarios.mkdir()
            (scenarios / "a.test.ts").write_text(
                'execSync("cd apps && pnpm db:migrate");\n'
            )
            # Parent dirs do not exist; --detail-out must create them.
            detail = Path(d) / "deep" / "nested" / "audit" / "lint.txt"
            exit_code, _ = self._capture_main([
                str(scenarios), "--detail-out", str(detail),
            ])
            self.assertEqual(exit_code, 1)
            self.assertTrue(detail.exists())

    def test_no_detail_out_preserves_legacy_stdout(self) -> None:
        """Back-compat: callers that don't pass --detail-out still get
        the verbose markdown report on stdout.
        """
        with tempfile.TemporaryDirectory() as d:
            scenarios = Path(d) / "scenarios"
            scenarios.mkdir()
            (scenarios / "a.test.ts").write_text(
                'execSync("cd apps && pnpm db:migrate");\n'
            )
            exit_code, stdout = self._capture_main([str(scenarios)])
            self.assertEqual(exit_code, 1)
            self.assertIn("db-migrate-in-scenario", stdout)

    def test_detail_out_with_json_emits_count_to_stdout(self) -> None:
        """--json --detail-out: stdout gets the count; payload goes to
        the audit file. Same sanitization contract as the markdown form.
        """
        with tempfile.TemporaryDirectory() as d:
            scenarios = Path(d) / "scenarios"
            scenarios.mkdir()
            (scenarios / "a.test.ts").write_text(
                'execSync("cd apps && pnpm db:migrate");\n'
            )
            detail = Path(d) / "audit" / "lint.json"
            exit_code, stdout = self._capture_main([
                str(scenarios), "--json", "--detail-out", str(detail),
            ])
            self.assertEqual(exit_code, 1)
            self.assertEqual(stdout.strip(), "1")
            import json
            payload = json.loads(detail.read_text())
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["rule"], "db-migrate-in-scenario")


if __name__ == "__main__":
    unittest.main()
