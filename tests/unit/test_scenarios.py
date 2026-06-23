"""Tests for scenarios/validator.py and scenarios/detection.py."""

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import TempDirTestCase
from darkfactory.scenarios.validator import (
    validate_scenarios,
    _check_typescript_compilation,
    _check_contract_helper_usage,
    _check_vi_stub_global,
    _generate_declare_module_stubs,
    _provision_iface_contract_types,
    _resolve_project_node_types,
    _discover_files,
    _check_assertions,
    _check_trivial,
    _check_empty_bodies,
)
from darkfactory.scenarios.detection import has_scenario_files


class TestDiscoverFiles(TempDirTestCase):
    def test_node_files(self) -> None:
        scenarios = self.tmp_dir / "scenarios"
        scenarios.mkdir()
        (scenarios / "auth.test.ts").write_text("test")
        (scenarios / "login.spec.js").write_text("test")
        (scenarios / "helper.ts").write_text("not a test")
        files = _discover_files(scenarios, "node")
        self.assertEqual(len(files), 2)

    def test_python_files(self) -> None:
        scenarios = self.tmp_dir / "scenarios"
        scenarios.mkdir()
        (scenarios / "test_auth.py").write_text("test")
        (scenarios / "helper.py").write_text("helper")
        files = _discover_files(scenarios, "python")
        self.assertEqual(len(files), 1)


class TestCheckAssertions(TempDirTestCase):
    def test_passes_with_assertions(self) -> None:
        f = self.tmp_dir / "test_auth.py"
        f.write_text("def test_login():\n    assert user.is_valid()\n    assert response.status == 200\n")
        result = _check_assertions([f], "python", 1)
        self.assertTrue(result.passed)

    def test_fails_without_assertions(self) -> None:
        f = self.tmp_dir / "test_auth.py"
        f.write_text("def test_login():\n    print('no assertions')\n")
        result = _check_assertions([f], "python", 1)
        self.assertFalse(result.passed)


class TestCheckTrivial(TempDirTestCase):
    def test_detects_trivial_node(self) -> None:
        f = self.tmp_dir / "auth.test.ts"
        f.write_text("test('trivial', () => {\n  expect(true)\n})")
        result = _check_trivial([f], "node")
        self.assertFalse(result.passed)

    def test_non_trivial_passes(self) -> None:
        f = self.tmp_dir / "auth.test.ts"
        f.write_text("test('real', () => {\n  expect(result).toBe(42)\n})")
        result = _check_trivial([f], "node")
        self.assertTrue(result.passed)


class TestCheckEmptyBodies(TempDirTestCase):
    def test_detects_empty_python(self) -> None:
        f = self.tmp_dir / "test_auth.py"
        f.write_text("def test_login(self):\n    pass\n")
        result = _check_empty_bodies([f], "python")
        self.assertFalse(result.passed)

    def test_non_empty_passes(self) -> None:
        f = self.tmp_dir / "test_auth.py"
        f.write_text("def test_login(self):\n    assert True\n")
        result = _check_empty_bodies([f], "python")
        self.assertTrue(result.passed)


class TestValidateScenarios(TempDirTestCase):
    def test_no_scenarios_dir(self) -> None:
        result = validate_scenarios("missing", "python", self.tmp_dir)
        self.assertFalse(result.passed)

    def test_full_validation(self) -> None:
        scenarios = self.tmp_dir / ".dark-factory" / "scenarios" / "spec"
        scenarios.mkdir(parents=True)
        for i in range(3):
            f = scenarios / f"test_case{i}.py"
            f.write_text(f"def test_{i}():\n    assert result == {i}\n")
        (self.tmp_dir / ".dark-factory" / "reports").mkdir(parents=True, exist_ok=True)
        result = validate_scenarios("spec", "python", self.tmp_dir)
        self.assertTrue(result.passed)
        self.assertEqual(result.file_count, 3)


class TestHasScenarioFilesMarkdown(TempDirTestCase):
    def test_journey_md_detected(self) -> None:
        scenarios = self.tmp_dir / "scenarios"
        scenarios.mkdir()
        (scenarios / "01-login.journey.md").write_text("# Scenario: x")
        self.assertTrue(has_scenario_files(scenarios))

    def test_empty_dir_returns_false(self) -> None:
        scenarios = self.tmp_dir / "scenarios"
        scenarios.mkdir()
        self.assertFalse(has_scenario_files(scenarios))

    def test_missing_dir_returns_false(self) -> None:
        self.assertFalse(has_scenario_files(self.tmp_dir / "does-not-exist"))

    def test_executable_scenarios_in_subdirs_detected(self) -> None:
        # Test-engineers sometimes organize executable scenarios under
        # acceptance/, edge-cases/, functional/, substrate/ subdirs. The
        # detector must walk subdirs (parity with bash has_scenario_files).
        scenarios = self.tmp_dir / "scenarios"
        (scenarios / "acceptance").mkdir(parents=True)
        (scenarios / "acceptance" / "01-contract.test.ts").write_text("test")
        self.assertTrue(has_scenario_files(scenarios))

    def test_python_test_files_in_subdirs_detected(self) -> None:
        scenarios = self.tmp_dir / "scenarios"
        (scenarios / "functional").mkdir(parents=True)
        (scenarios / "functional" / "test_auth.py").write_text("test")
        self.assertTrue(has_scenario_files(scenarios))

    def test_directory_named_test_does_not_count_as_scenario(self) -> None:
        # `**/test_*` recursive glob matches directories too; parity with
        # bash `find -type f` requires filtering to files only.
        scenarios = self.tmp_dir / "scenarios"
        (scenarios / "test_helpers").mkdir(parents=True)
        self.assertFalse(has_scenario_files(scenarios))


# TestValidateScenariosMarkdownOnly and TestValidateMarkdownOnlyDirect removed
# in Phase 4.6b.1a — Format A (*.scenarios.md) and the markdown-only validation
# path are retired.


class TestTypescriptCompilation(TempDirTestCase):
    """Tests for Check 9: tsc --noEmit pre-compilation."""

    def test_no_ts_files_skipped(self) -> None:
        """No .ts files → pass with 'no .ts files' detail."""
        js_file = self.tmp_dir / "test.js"
        js_file.write_text("test('x', () => {})")
        result = _check_typescript_compilation([js_file], self.tmp_dir)
        self.assertTrue(result.passed)
        self.assertIn("no .ts files", result.detail)

    def test_compilation_passes(self) -> None:
        """tsc exits 0 → check passes."""
        ts_file = self.tmp_dir / "01-contract.test.ts"
        ts_file.write_text("test('x', () => { expect(1).toBe(1) })")
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        with patch("darkfactory.scenarios.validator.subprocess.run", return_value=mock_result):
            result = _check_typescript_compilation([ts_file], self.tmp_dir)
        self.assertTrue(result.passed)
        self.assertIn("all files compile", result.detail)
        # Verify tsconfig.check.json was cleaned up.
        self.assertFalse((self.tmp_dir / "tsconfig.check.json").exists())

    def test_compilation_fails(self) -> None:
        """tsc exits non-zero → check fails with failing files."""
        ts_file = self.tmp_dir / "01-contract.test.ts"
        ts_file.write_text("test('x', () => { expect(1).toEndWith('y') })")
        tsc_output = (
            "01-contract.test.ts(1,36): error TS2339: "
            "Property 'toEndWith' does not exist on type 'Assertion'.\n"
        )
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=2, stdout=tsc_output, stderr="",
        )
        with patch("darkfactory.scenarios.validator.subprocess.run", return_value=mock_result):
            result = _check_typescript_compilation([ts_file], self.tmp_dir)
        self.assertFalse(result.passed)
        self.assertIn("toEndWith", result.detail)
        self.assertEqual(result.failing_files, ["01-contract.test.ts"])

    def test_tsc_not_available(self) -> None:
        """tsc not found → gracefully skip (pass)."""
        ts_file = self.tmp_dir / "01-contract.test.ts"
        ts_file.write_text("test('x', () => { expect(1).toBe(1) })")
        with patch("darkfactory.scenarios.validator.subprocess.run", side_effect=FileNotFoundError):
            result = _check_typescript_compilation([ts_file], self.tmp_dir)
        self.assertTrue(result.passed)
        self.assertIn("not available", result.detail)

    def test_tsc_timeout(self) -> None:
        """tsc times out → gracefully skip (pass)."""
        ts_file = self.tmp_dir / "01-contract.test.ts"
        ts_file.write_text("test('x', () => { expect(1).toBe(1) })")
        with patch("darkfactory.scenarios.validator.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd="tsc", timeout=60)):
            result = _check_typescript_compilation([ts_file], self.tmp_dir)
        self.assertTrue(result.passed)
        self.assertIn("timed out", result.detail)

    def test_tsconfig_uses_relative_paths(self) -> None:
        """Verify tsconfig.check.json uses relative paths in include."""
        ts_file = self.tmp_dir / "01-contract.test.ts"
        ts_file.write_text("test('x', () => {})")
        import json
        written_content = []

        def capture_run(*args, **kwargs):
            tsconfig_path = self.tmp_dir / "tsconfig.check.json"
            if tsconfig_path.exists():
                written_content.append(json.loads(tsconfig_path.read_text()))
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("darkfactory.scenarios.validator.subprocess.run", side_effect=capture_run):
            _check_typescript_compilation([ts_file], self.tmp_dir)
        self.assertEqual(len(written_content), 1)
        self.assertEqual(written_content[0]["include"], ["01-contract.test.ts"])

    def test_check9_integrated_in_validate_scenarios(self) -> None:
        """Check 9 runs for node language in validate_scenarios."""
        scenarios = self.tmp_dir / ".dark-factory" / "scenarios" / "spec"
        scenarios.mkdir(parents=True)
        (self.tmp_dir / ".dark-factory" / "reports").mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (scenarios / f"0{i+1}-test.test.ts").write_text(
                f"test('t{i}', () => {{ expect({i}).toBe({i}) }})"
            )
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        with patch("darkfactory.scenarios.validator.subprocess.run", return_value=mock_result):
            result = validate_scenarios("spec", "node", self.tmp_dir)
        check_names = [c.name for c in result.checks]
        self.assertIn("typescript_compilation", check_names)

    def test_check9_not_run_for_python(self) -> None:
        """Check 9 is skipped for non-node languages."""
        scenarios = self.tmp_dir / ".dark-factory" / "scenarios" / "spec"
        scenarios.mkdir(parents=True)
        (self.tmp_dir / ".dark-factory" / "reports").mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (scenarios / f"test_case{i}.py").write_text(
                f"def test_{i}():\n    assert result == {i}\n"
            )
        result = validate_scenarios("spec", "python", self.tmp_dir)
        check_names = [c.name for c in result.checks]
        self.assertNotIn("typescript_compilation", check_names)

    def test_tsc_partitions_code_vs_import_errors(self):
        """Phase 6: mixed tsc output with import AND code errors → FAIL on code errors."""
        ts_file = self.tmp_dir / "01-test.test.ts"
        ts_file.write_text("import { spawn } from 'child_process'; spawn('ls');")
        tsc_output = (
            "01-test.test.ts(1,1): error TS2304: Cannot find name 'spawn'\n"
            "01-test.test.ts(1,10): error TS2307: Cannot find module 'vitest'\n"
        )
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=2, stdout=tsc_output, stderr="",
        )
        with patch("darkfactory.scenarios.validator.subprocess.run", return_value=mock_result):
            result = _check_typescript_compilation([ts_file], self.tmp_dir)
        # Must FAIL — code error present even though import error also present.
        self.assertFalse(result.passed)
        self.assertIn("Cannot find name", result.detail)

    def test_tsc_skips_import_only_errors(self):
        """Phase 6: tsc output with only import/module errors → skip (PASS)."""
        ts_file = self.tmp_dir / "01-test.test.ts"
        ts_file.write_text("import { expect } from 'vitest';")
        tsc_output = (
            "01-test.test.ts(1,10): error TS2307: Cannot find module 'vitest'\n"
            "01-test.test.ts(2,1): error TS2688: Cannot find type definition file for 'node'\n"
        )
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=2, stdout=tsc_output, stderr="",
        )
        with patch("darkfactory.scenarios.validator.subprocess.run", return_value=mock_result):
            result = _check_typescript_compilation([ts_file], self.tmp_dir)
        self.assertTrue(result.passed)
        self.assertIn("type definitions not available", result.detail)

    def test_tsc_fails_on_code_errors_only(self):
        """Phase 6: tsc output with only code errors (no import errors) → FAIL."""
        ts_file = self.tmp_dir / "01-test.test.ts"
        ts_file.write_text("const x: string = 123;")
        tsc_output = (
            "01-test.test.ts(1,7): error TS2322: "
            "Type 'number' is not assignable to type 'string'.\n"
        )
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=2, stdout=tsc_output, stderr="",
        )
        with patch("darkfactory.scenarios.validator.subprocess.run", return_value=mock_result):
            result = _check_typescript_compilation([ts_file], self.tmp_dir)
        self.assertFalse(result.passed)
        self.assertIn("scenario code errors", result.detail)
        self.assertEqual(result.failing_files, ["01-test.test.ts"])

    # --- SX: per-file demotion of unresolved-import-downstream implicit-any ---
    # tsc output strings below are VERBATIM from a real `npx tsc --strict` run
    # (captured repro): a file importing an uninstalled `postgres` with a
    # contextually-typed `client.begin(async (tx) => …)` callback emits TS2307
    # (the unresolved import) + TS7006 (tx loses its contextual type). The same
    # code typechecks clean once postgres is installed.

    def test_tsc_demotes_same_file_implicit_any_from_unresolved_import(self):
        """SX: TS7006 in a file that ITSELF carries a TS2307 → advisory pass.

        passed=True (scenario validation accepts) but compile_status is
        "passed_unresolved_deps" (!= "passed") so the salvage gate still refuses.
        """
        ts_file = self.tmp_dir / "pg.test.ts"
        ts_file.write_text("import type { Sql } from 'postgres';\n")
        tsc_output = (
            "pg.test.ts(1,26): error TS2307: Cannot find module 'postgres' or "
            "its corresponding type declarations.\n"
            "pg.test.ts(3,29): error TS7006: Parameter 'tx' implicitly has an "
            "'any' type.\n"
        )
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=2, stdout=tsc_output, stderr="",
        )
        with patch("darkfactory.scenarios.validator.subprocess.run", return_value=mock_result):
            result = _check_typescript_compilation([ts_file], self.tmp_dir)
        self.assertTrue(result.passed)
        self.assertEqual(result.compile_status, "passed_unresolved_deps")
        self.assertNotEqual(result.compile_status, "passed")  # salvage-safety

    def test_tsc_does_not_demote_implicit_any_in_unrelated_file(self):
        """SX: an unrelated-file TS7006 still FAILS even if another file has a
        TS2307 (per-file correlation, never global)."""
        pg = self.tmp_dir / "pg.test.ts"
        pg.write_text("import type { Sql } from 'postgres';\n")
        other = self.tmp_dir / "other.test.ts"
        other.write_text("export const f = (req) => req;\n")
        tsc_output = (
            "pg.test.ts(1,26): error TS2307: Cannot find module 'postgres'.\n"
            "other.test.ts(1,21): error TS7006: Parameter 'req' implicitly has "
            "an 'any' type.\n"
        )
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=2, stdout=tsc_output, stderr="",
        )
        with patch("darkfactory.scenarios.validator.subprocess.run", return_value=mock_result):
            result = _check_typescript_compilation([pg, other], self.tmp_dir)
        self.assertFalse(result.passed)
        self.assertEqual(result.compile_status, "failed")
        self.assertEqual(result.failing_files, ["other.test.ts"])

    def test_tsc_demotion_still_fails_genuine_code_error(self):
        """SX: a genuine non-TS7006 code error survives demotion → FAIL."""
        pg = self.tmp_dir / "pg.test.ts"
        pg.write_text("import type { Sql } from 'postgres';\n")
        tsc_output = (
            "pg.test.ts(1,26): error TS2307: Cannot find module 'postgres'.\n"
            "pg.test.ts(3,29): error TS7006: Parameter 'tx' implicitly has an "
            "'any' type.\n"
            "pg.test.ts(5,7): error TS2322: Type 'number' is not assignable to "
            "type 'string'.\n"
        )
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=2, stdout=tsc_output, stderr="",
        )
        with patch("darkfactory.scenarios.validator.subprocess.run", return_value=mock_result):
            result = _check_typescript_compilation([pg], self.tmp_dir)
        self.assertFalse(result.passed)
        self.assertEqual(result.compile_status, "failed")
        self.assertIn("TS2322", result.detail)

    def test_tsc_demotion_is_ts7006_only(self):
        """SX: only TS7006 is demoted; other implicit-any-adjacent codes
        (e.g. TS2571 'object is of type unknown') still FAIL even same-file."""
        pg = self.tmp_dir / "pg.test.ts"
        pg.write_text("import type { Sql } from 'postgres';\n")
        tsc_output = (
            "pg.test.ts(1,26): error TS2307: Cannot find module 'postgres'.\n"
            "pg.test.ts(4,3): error TS2571: Object is of type 'unknown'.\n"
        )
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=2, stdout=tsc_output, stderr="",
        )
        with patch("darkfactory.scenarios.validator.subprocess.run", return_value=mock_result):
            result = _check_typescript_compilation([pg], self.tmp_dir)
        self.assertFalse(result.passed)
        self.assertEqual(result.compile_status, "failed")
        self.assertIn("TS2571", result.detail)

    def test_tsc_truncation_shape_is_not_passed_for_salvage(self):
        """SX salvage-safety: even the demoted advisory state is != 'passed', so
        a salvaged-and-possibly-truncated file carrying the TS2307+TS7006 shape
        cannot be shipped by the salvage backstop (which gates on == 'passed')."""
        ts_file = self.tmp_dir / "pg.test.ts"
        ts_file.write_text("import type { Sql } from 'postgres';\n")
        tsc_output = (
            "pg.test.ts(1,26): error TS2307: Cannot find module 'postgres'.\n"
            "pg.test.ts(3,29): error TS7006: Parameter 'tx' implicitly has an "
            "'any' type.\n"
        )
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=2, stdout=tsc_output, stderr="",
        )
        with patch("darkfactory.scenarios.validator.subprocess.run", return_value=mock_result):
            result = _check_typescript_compilation([ts_file], self.tmp_dir)
        # The salvage backstop ships ONLY on compile_status == "passed".
        self.assertNotEqual(result.compile_status, "passed")


class TestTypescriptCompilationRealTsc(TempDirTestCase):
    """SX regression guard: drives REAL `npx tsc` (no subprocess mock) against a
    fixture that imports an uninstalled dep with a contextually-typed callback —
    the exact shape that failed bb spec-4. This is the test that would have
    caught the original S0(b) over-fail.

    Linus+Codex review: it MUST NOT silently self-skip when tsc is in fact
    resolvable. It skips ONLY when real tsc cannot run (dev laptops without
    typescript); where tsc IS resolvable (the node-tools Docker image pins
    typescript) it exercises the demotion path and a ``compile_status="skipped"``
    is a FAILURE (a regression in the validator's tsc invocation), not a skip."""

    @staticmethod
    def _real_tsc_runs() -> bool:
        """True iff a real tsc invocation actually runs — distinguishes a genuine
        tsc from the npm ``npx tsc`` shim (which prints 'This is not the tsc
        command' with a non-zero exit). Mirrors the validator's resolution."""
        import shutil
        if shutil.which("tsc") is not None:
            cmd = ["tsc", "--version"]
        elif shutil.which("npx") is not None:
            cmd = ["npx", "--no-install", "tsc", "--version"]
        else:
            return False
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.returncode == 0 and "Version" in (proc.stdout + proc.stderr)

    def _run_real(self):
        # Minimal ambient type roots so tsc resolves the validator's
        # types:["vitest/globals","node"] WITHOUT installing postgres.
        nm = self.tmp_dir / "node_modules"
        (nm / "@types" / "node").mkdir(parents=True, exist_ok=True)
        (nm / "@types" / "node" / "index.d.ts").write_text("export {};\n")
        (nm / "@types" / "node" / "package.json").write_text(
            '{"name":"@types/node","version":"0.0.0","types":"index.d.ts"}\n'
        )
        (nm / "vitest").mkdir(parents=True, exist_ok=True)
        (nm / "vitest" / "globals.d.ts").write_text("export {};\n")
        (nm / "vitest" / "package.json").write_text(
            '{"name":"vitest","version":"0.0.0"}\n'
        )
        ts_file = self.tmp_dir / "pg.test.ts"
        ts_file.write_text(
            "import type { Sql } from 'postgres';\n"
            "export async function f(client: Sql) {\n"
            "  await client.begin(async (tx) => { await tx`SELECT 1`; });\n"
            "}\n"
        )
        return _check_typescript_compilation([ts_file], self.tmp_dir)

    def test_real_tsc_demotes_uninstalled_dep_implicit_any(self):
        if not self._real_tsc_runs():
            self.skipTest("real tsc not resolvable (runs in node-tools Docker image)")
        result = self._run_real()
        # tsc IS resolvable, so the validator MUST have reached body-checking. A
        # "skipped" here is a real regression — the original S0(b) over-fail
        # masqueraded as a green suite precisely because this guard self-skipped.
        self.assertNotEqual(
            result.compile_status, "skipped",
            f"tsc resolvable but validator skipped (regression): {result.detail}",
        )
        # postgres is NOT installed → TS2307 + TS7006 on tx → advisory pass.
        self.assertTrue(result.passed, result.detail)
        self.assertEqual(result.compile_status, "passed_unresolved_deps", result.detail)


class TestCheck9CompileStatus(TempDirTestCase):
    """S0(b): the structured compile_status field + @types/node resolution.

    compile_status retires the fragile ``"skipped" in detail`` coupling the S41
    salvage backstop relied on. These tests pin each status value and the
    truncation/skip discrimination that makes the backstop meaningful.
    """

    def _run(self, ts_src: str, tsc_output: str, returncode: int = 2,
             project_dir: Path | None = None):
        ts_file = self.tmp_dir / "01-test.test.ts"
        ts_file.write_text(ts_src)
        mock = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=tsc_output, stderr="",
        )
        with patch("darkfactory.scenarios.validator.subprocess.run", return_value=mock):
            return _check_typescript_compilation(
                [ts_file], self.tmp_dir, project_dir=project_dir,
            )

    def test_status_passed_on_clean_compile(self) -> None:
        r = self._run("expect(1).toBe(1)", "", returncode=0)
        self.assertTrue(r.passed)
        self.assertEqual(r.compile_status, "passed")

    def test_status_failed_on_code_errors(self) -> None:
        r = self._run(
            "const x: string = 123;",
            "01-test.test.ts(1,7): error TS2322: Type 'number' is not "
            "assignable to type 'string'.\n",
        )
        self.assertFalse(r.passed)
        self.assertEqual(r.compile_status, "failed")
        self.assertEqual(r.failing_files, ["01-test.test.ts"])

    def test_status_syntax_on_parser_error(self) -> None:
        # A SIGKILL-truncated file emits TS1xxx (parser) errors — ALWAYS fail,
        # even when import errors co-occur (the salvage-truncation hole).
        r = self._run(
            "describe('x', () => { const y = ",
            "01-test.test.ts(1,33): error TS1160: Unterminated template literal.\n"
            "01-test.test.ts(1,1): error TS2307: Cannot find module 'vitest'\n",
        )
        self.assertFalse(r.passed)
        self.assertEqual(r.compile_status, "syntax")
        self.assertIn("malformed", r.detail)

    def test_status_skipped_when_node_types_unresolved(self) -> None:
        # No project_dir → @types/node unresolved → TS2688 bail → import-only →
        # genuine no-op skip (bodies never type-checked).
        r = self._run(
            "import { x } from 'node:fs';",
            "error TS2688: Cannot find type definition file for 'node'.\n",
        )
        self.assertTrue(r.passed)
        self.assertEqual(r.compile_status, "skipped")

    def _run_distinct(self, ts_src: str, tsc_output: str, returncode: int = 2):
        # Real layout: scenarios_dir (project/.dark-factory/scenarios/<spec>) is
        # distinct from project_dir, so the check-local symlink target never
        # collides with the resolved @types/node.
        project_dir = self.tmp_dir / "proj"
        scen_dir = project_dir / ".dark-factory" / "scenarios" / "spec"
        scen_dir.mkdir(parents=True)
        fake_types = project_dir / "node_modules" / "@types" / "node"
        fake_types.mkdir(parents=True)
        (fake_types / "index.d.ts").write_text("// fake @types/node\n")
        ts_file = scen_dir / "01-test.test.ts"
        ts_file.write_text(ts_src)
        mock = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=tsc_output, stderr="",
        )
        with patch("darkfactory.scenarios.validator.subprocess.run", return_value=mock):
            r = _check_typescript_compilation(
                [ts_file], scen_dir, project_dir=project_dir,
            )
        return r, scen_dir

    def test_status_passed_when_node_types_resolved_only_masked_imports(self) -> None:
        # @types/node IS resolvable + only barrier-masked project-source imports
        # remain unresolved → bodies WERE type-checked → a real pass, not a skip.
        r, _ = self._run_distinct(
            "import { db } from '../../../apps/api/src/db/index';",
            "01-test.test.ts(1,1): error TS2307: Cannot find module "
            "'../../../apps/api/src/db/index'\n",
        )
        self.assertTrue(r.passed)
        self.assertEqual(r.compile_status, "passed")

    def test_node_types_symlink_cleaned_up(self) -> None:
        _, scen_dir = self._run_distinct("expect(1).toBe(1)", "", returncode=0)
        # The check-local symlink under scenarios_dir must be removed afterwards.
        self.assertFalse(
            (scen_dir / "node_modules" / "@types" / "node").is_symlink()
        )
        # And the scaffolding node_modules/@types we created must not litter the
        # Implementer-readable scenarios dir (Linus nit — empty-dir cleanup).
        self.assertFalse((scen_dir / "node_modules").exists())


class TestResolveProjectNodeTypes(TempDirTestCase):
    """S0(b): locating the project's installed @types/node across layouts."""

    def _mk(self, rel: str) -> Path:
        d = self.tmp_dir / rel
        d.mkdir(parents=True)
        (d / "index.d.ts").write_text("// types\n")
        return d

    def test_flat_layout(self) -> None:
        want = self._mk("node_modules/@types/node")
        got = _resolve_project_node_types(self.tmp_dir, None)
        self.assertEqual(got, want)

    def test_pnpm_virtual_store(self) -> None:
        want = self._mk("node_modules/.pnpm/@types+node@22.19.21/node_modules/@types/node")
        got = _resolve_project_node_types(self.tmp_dir, None)
        self.assertEqual(got, want)

    def test_per_app_layout(self) -> None:
        want = self._mk("apps/api/node_modules/@types/node")
        got = _resolve_project_node_types(self.tmp_dir, None)
        self.assertEqual(got, want)

    def test_none_when_absent(self) -> None:
        self.assertIsNone(_resolve_project_node_types(self.tmp_dir, None))

    def test_none_when_no_project_dir(self) -> None:
        self.assertIsNone(_resolve_project_node_types(None, None))

    def test_invalid_without_index_dts(self) -> None:
        # A node dir lacking index.d.ts is not usable.
        (self.tmp_dir / "node_modules" / "@types" / "node").mkdir(parents=True)
        self.assertIsNone(_resolve_project_node_types(self.tmp_dir, None))


class TestContractHelperUsage(TempDirTestCase):
    """Tests for Check 18: Contract helper import enforcement."""

    def test_no_contracts_dir(self) -> None:
        """No _contracts/ directory → pass."""
        f = self.tmp_dir / "test.test.ts"
        f.write_text("test('x', () => { expect(1).toBe(1) })")
        result = _check_contract_helper_usage([f], self.tmp_dir, "node")
        self.assertTrue(result.passed)
        self.assertIn("no contracts staged", result.detail)

    def test_contracts_dir_no_helper_files(self) -> None:
        """_contracts/ exists but only has README → pass."""
        contracts = self.tmp_dir / "_contracts" / "postgres"
        contracts.mkdir(parents=True)
        (contracts / "README.md").write_text("# Helpers")
        f = self.tmp_dir / "test.test.ts"
        f.write_text("test('x', () => { expect(1).toBe(1) })")
        result = _check_contract_helper_usage([f], self.tmp_dir, "node")
        self.assertTrue(result.passed)
        self.assertIn("no helper files", result.detail)

    def test_contracts_with_helpers_and_imports(self) -> None:
        """_contracts/ has .ts helpers and test file imports from them → pass."""
        contracts = self.tmp_dir / "_contracts" / "postgres"
        contracts.mkdir(parents=True)
        (contracts / "index.ts").write_text("export function assertRlsEnabled() {}")
        f = self.tmp_dir / "rls.test.ts"
        f.write_text(
            "import { assertRlsEnabled } from './_contracts/postgres/index.js';\n"
            "test('x', () => { assertRlsEnabled() })"
        )
        result = _check_contract_helper_usage([f], self.tmp_dir, "node")
        self.assertTrue(result.passed)
        self.assertIn("contracts imported", result.detail)

    def test_contracts_with_helpers_no_imports(self) -> None:
        """_contracts/ has .ts helpers but no test imports them → hard fail."""
        contracts = self.tmp_dir / "_contracts" / "postgres"
        contracts.mkdir(parents=True)
        (contracts / "index.ts").write_text("export function assertRlsEnabled() {}")
        f = self.tmp_dir / "rls.test.ts"
        f.write_text("test('x', () => { expect(1).toBe(1) })")
        result = _check_contract_helper_usage([f], self.tmp_dir, "node")
        self.assertFalse(result.passed)
        self.assertIn("MUST use them", result.detail)

    def test_import_from_parent_dir(self) -> None:
        """Import from ../_contracts/ (subdirectory test file) → recognized."""
        contracts = self.tmp_dir / "_contracts" / "postgres"
        contracts.mkdir(parents=True)
        (contracts / "index.ts").write_text("export function assertRlsEnabled() {}")
        subdir = self.tmp_dir / "acceptance"
        subdir.mkdir()
        f = subdir / "rls.test.ts"
        f.write_text(
            "import { assertRlsEnabled } from '../_contracts/postgres/index.js';\n"
            "test('x', () => {})"
        )
        result = _check_contract_helper_usage([f], self.tmp_dir, "node")
        self.assertTrue(result.passed)

    def test_python_contracts_with_import(self) -> None:
        """Python helper files are detected and Python imports recognized."""
        contracts = self.tmp_dir / "_contracts" / "postgres"
        contracts.mkdir(parents=True)
        (contracts / "helpers.py").write_text("def assert_rls(): pass")
        f = self.tmp_dir / "test_rls.py"
        f.write_text("from _contracts.postgres.helpers import assert_rls\n")
        result = _check_contract_helper_usage([f], self.tmp_dir, "python")
        self.assertTrue(result.passed)
        self.assertIn("contracts imported", result.detail)

    def test_python_contracts_without_import(self) -> None:
        """Python helper files staged but no import → hard fail."""
        contracts = self.tmp_dir / "_contracts" / "postgres"
        contracts.mkdir(parents=True)
        (contracts / "helpers.py").write_text("def assert_rls(): pass")
        f = self.tmp_dir / "test_rls.py"
        f.write_text("def test_rls():\n    assert True\n")
        result = _check_contract_helper_usage([f], self.tmp_dir, "python")
        self.assertFalse(result.passed)

    def test_unsupported_language_skips(self) -> None:
        """Unsupported language with staged helpers gracefully skips check."""
        contracts = self.tmp_dir / "_contracts" / "postgres"
        contracts.mkdir(parents=True)
        # Use .go files so has_helpers is False for go (not in helper_extensions),
        # which means the check returns "no helper files" — correct: no go
        # import pattern exists either.
        (contracts / "helpers.go").write_text("package helpers")
        f = self.tmp_dir / "rls_test.go"
        f.write_text("func TestRls(t *testing.T) {}")
        result = _check_contract_helper_usage([f], self.tmp_dir, "go")
        self.assertTrue(result.passed)

    def test_failing_files_lists_test_files(self) -> None:
        """Check 18 failing_files lists test files, not contracts dir."""
        contracts = self.tmp_dir / "_contracts" / "postgres"
        contracts.mkdir(parents=True)
        (contracts / "index.ts").write_text("export function f() {}")
        f = self.tmp_dir / "rls.test.ts"
        f.write_text("test('x', () => { expect(1).toBe(1) })")
        result = _check_contract_helper_usage([f], self.tmp_dir, "node")
        self.assertFalse(result.passed)
        self.assertEqual(result.failing_files, ["rls.test.ts"])


class TestProvisionIfaceContractTypes(TempDirTestCase):
    """Tests for _provision_iface_contract_types — provisions §5 type
    augmentations into the Check-9 typecheck without touching the source tree."""

    def _spec_with_augmentation(self) -> Path:
        # §5 Interface Contract with two filepath-tagged TS fences: a sibling
        # that defines a type, and the augmentation that uses it.
        spec = self.tmp_dir / "spec.md"
        spec.write_text(
            "# Spec\n\n## 5. Interface Contract\n\n"
            "```typescript\n// apps/api/src/db/index.ts\n"
            "export type DrizzleTx = { x: number };\n```\n\n"
            "```typescript\n// apps/api/src/db/ctx.ts\n"
            "import type { DrizzleTx } from './index';\n"
            "declare module 'fastify' {\n"
            "  interface FastifyRequest { withDb: (cb: (tx: DrizzleTx) => void) => void }\n"
            "}\n```\n",
            encoding="utf-8",
        )
        return spec

    def _scenarios_dir(self) -> Path:
        sd = self.tmp_dir / ".dark-factory" / "scenarios" / "s"
        sd.mkdir(parents=True)
        return sd

    def test_none_when_no_spec(self) -> None:
        sd = self._scenarios_dir()
        self.assertEqual(
            _provision_iface_contract_types(sd, self.tmp_dir, None), (None, [], []),
        )

    def test_none_when_no_iface_fences(self) -> None:
        sd = self._scenarios_dir()
        spec = self.tmp_dir / "plain.md"
        spec.write_text("# Spec\n\n## 2. Functional Requirements\n- FR-1\n")
        self.assertEqual(
            _provision_iface_contract_types(sd, self.tmp_dir, spec), (None, [], []),
        )

    def test_places_fences_as_dts_and_symlinks_augmented_pkg(self) -> None:
        """The augmentation's package gets symlinked into scenarios node_modules
        (so `declare module 'fastify'` resolves), fences land as .d.ts in the
        scratch dir, and NOTHING is written to the project source tree."""
        # resolution root = apps/api (has node_modules); fastify installed there.
        (self.tmp_dir / "apps" / "api" / "node_modules" / "fastify").mkdir(parents=True)
        sd = self._scenarios_dir()
        scratch, symlinks, includes = _provision_iface_contract_types(
            sd, self.tmp_dir, self._spec_with_augmentation()
        )
        # scratch dir holds the fences, rewritten to .d.ts (declaration-valid)
        self.assertEqual(scratch, sd / "__df_contracts")
        self.assertTrue((scratch / "apps/api/src/db/ctx.d.ts").is_file())
        self.assertTrue((scratch / "apps/api/src/db/index.d.ts").is_file())
        self.assertTrue(any("ctx.d.ts" in i for i in includes))
        # augmented package symlinked from the inferred resolution root
        link = sd / "node_modules" / "fastify"
        self.assertIn(link, symlinks)
        self.assertTrue(link.is_symlink())
        self.assertEqual(
            link.resolve(),
            (self.tmp_dir / "apps/api/node_modules/fastify").resolve(),
        )
        # source tree untouched
        self.assertFalse((self.tmp_dir / "apps/api/src/db").exists())

    def test_no_symlink_when_pkg_absent(self) -> None:
        """If the augmented package isn't installed, no symlink (no crash) — the
        fences still place; tsc will surface the unresolved augmentation. And no
        empty node_modules is left behind (Q2)."""
        sd = self._scenarios_dir()
        scratch, symlinks, includes = _provision_iface_contract_types(
            sd, self.tmp_dir, self._spec_with_augmentation()
        )
        self.assertIsNotNone(scratch)
        self.assertEqual(symlinks, [])  # fastify not installed anywhere
        self.assertFalse((sd / "node_modules").exists())  # Q2: no empty litter

    def test_fence_filepath_traversal_is_contained(self) -> None:
        """C1: a §5 fence whose `// <filepath>` escapes the scratch (../) must be
        skipped, never written outside scenarios_dir (the function's promise)."""
        sd = self._scenarios_dir()
        spec = self.tmp_dir / "evil.md"
        spec.write_text(
            "# Spec\n\n## 5. Interface Contract\n\n"
            "```typescript\n// ../../../escaped.ts\nexport const x: number;\n```\n",
            encoding="utf-8",
        )
        scratch, symlinks, includes = _provision_iface_contract_types(
            sd, self.tmp_dir, spec
        )
        self.assertEqual(includes, [])  # escaping fence skipped
        # nothing written outside the scratch dir
        self.assertFalse((self.tmp_dir / "escaped.d.ts").exists())
        self.assertFalse((self.tmp_dir.parent / "escaped.d.ts").exists())

    def test_scoped_package_augmentation(self) -> None:
        """Q1: `declare module '@scope/pkg'` keeps the full scoped name and
        symlinks @scope/pkg (not the corrupted @scope)."""
        (self.tmp_dir / "apps/api/node_modules/@fastify/jwt").mkdir(parents=True)
        sd = self._scenarios_dir()
        spec = self.tmp_dir / "scoped.md"
        spec.write_text(
            "# Spec\n\n## 5. Interface Contract\n\n"
            "```typescript\n// apps/api/src/x.ts\n"
            "declare module '@fastify/jwt' { interface FastifyJWT { x: number } }\n```\n",
            encoding="utf-8",
        )
        scratch, symlinks, includes = _provision_iface_contract_types(
            sd, self.tmp_dir, spec
        )
        link = sd / "node_modules" / "@fastify" / "jwt"
        self.assertIn(link, symlinks)
        self.assertTrue(link.is_symlink())
        self.assertEqual(
            link.resolve(),
            (self.tmp_dir / "apps/api/node_modules/@fastify/jwt").resolve(),
        )


class TestGenerateDeclareModuleStubs(TempDirTestCase):
    """Tests for _generate_declare_module_stubs."""

    def _write_manifest(self, manifest: dict) -> Path:
        import yaml
        path = self.tmp_dir / "manifest.yaml"
        path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
        return path

    def test_generates_stubs_from_entry_points(self) -> None:
        """Manifest with entry points → valid .d.ts with declare module blocks."""
        manifest_path = self._write_manifest({
            "test_integration": {
                "entry_points": [
                    {
                        "specifier": "../../../apps/api/src/db/index.js",
                        "exports": ["db", "connection", "withTenantDb"],
                    },
                ],
            },
        })
        stub_path = _generate_declare_module_stubs(self.tmp_dir, manifest_path)
        self.assertIsNotNone(stub_path)
        content = stub_path.read_text()
        self.assertIn("declare module '../../../apps/api/src/db/index.js'", content)
        self.assertIn("export const db: any;", content)
        self.assertIn("export const connection: any;", content)
        self.assertIn("export const withTenantDb: any;", content)
        # Cleanup
        stub_path.unlink()

    def test_default_export_emits_valid_form(self) -> None:
        """`default` in exports → `export default`, never the invalid
        `export const default` (TS1389). Regression for the notes-crud-ts abort:
        an architect-declared default-exported entry point (idiomatic Fastify
        `export default fp(...)`) made the scenario .d.ts fail tsc and abort the
        run. The negative assertion is what actually pins the bug."""
        manifest_path = self._write_manifest({
            "test_integration": {
                "entry_points": [
                    {"specifier": "./plugin.js", "exports": ["default", "build"]},
                ],
            },
        })
        stub_path = _generate_declare_module_stubs(self.tmp_dir, manifest_path)
        self.assertIsNotNone(stub_path)
        content = stub_path.read_text()
        self.assertNotIn("export const default", content)  # the bug
        self.assertIn("export default _df_default;", content)
        self.assertIn("export const build: any;", content)  # named sibling intact
        stub_path.unlink()

    def test_emits_extension_variants_for_resolution(self) -> None:
        """S0(b): a specifier must resolve whether the test imports it bare or
        with a .ts/.js extension — tsc matches `declare module` against the
        import as-written. Emit the bare + .ts + .js forms, deduped."""
        manifest_path = self._write_manifest({
            "test_integration": {
                "entry_points": [
                    {"specifier": "../../../apps/api/src/db/index", "exports": ["db"]},
                ],
            },
        })
        stub_path = _generate_declare_module_stubs(self.tmp_dir, manifest_path)
        content = stub_path.read_text()
        self.assertIn("declare module '../../../apps/api/src/db/index'", content)
        self.assertIn("declare module '../../../apps/api/src/db/index.ts'", content)
        self.assertIn("declare module '../../../apps/api/src/db/index.js'", content)
        stub_path.unlink()

    def test_extension_variants_deduped(self) -> None:
        """Two entry_points that normalize to the same base (one .ts, one .js)
        must not emit duplicate `declare module` blocks (TS2649)."""
        manifest_path = self._write_manifest({
            "test_integration": {
                "entry_points": [
                    {"specifier": "./db.ts", "exports": ["db"]},
                    {"specifier": "./db.js", "exports": ["db"]},
                ],
            },
        })
        stub_path = _generate_declare_module_stubs(self.tmp_dir, manifest_path)
        content = stub_path.read_text()
        self.assertEqual(content.count("declare module './db.ts'"), 1)
        self.assertEqual(content.count("declare module './db.js'"), 1)
        self.assertEqual(content.count("declare module './db'"), 1)
        stub_path.unlink()

    def test_no_test_integration_returns_none(self) -> None:
        """Manifest without test_integration section → None."""
        manifest_path = self._write_manifest({"some_other_key": "value"})
        result = _generate_declare_module_stubs(self.tmp_dir, manifest_path)
        self.assertIsNone(result)

    def test_empty_entry_points_returns_none(self) -> None:
        """Manifest with empty entry_points → None."""
        manifest_path = self._write_manifest({
            "test_integration": {"entry_points": []},
        })
        result = _generate_declare_module_stubs(self.tmp_dir, manifest_path)
        self.assertIsNone(result)

    def test_entry_point_with_no_exports(self) -> None:
        """Entry point with no exports → declare module with empty body."""
        manifest_path = self._write_manifest({
            "test_integration": {
                "entry_points": [
                    {"specifier": "./helper.js", "exports": []},
                ],
            },
        })
        stub_path = _generate_declare_module_stubs(self.tmp_dir, manifest_path)
        self.assertIsNotNone(stub_path)
        content = stub_path.read_text()
        self.assertIn("declare module './helper.js'", content)
        stub_path.unlink()

    def test_missing_manifest_returns_none(self) -> None:
        """Non-existent manifest file → None."""
        result = _generate_declare_module_stubs(
            self.tmp_dir, self.tmp_dir / "nonexistent.yaml",
        )
        self.assertIsNone(result)

    def test_malformed_manifest_returns_none(self) -> None:
        """Unparseable YAML → None without raising."""
        bad_path = self.tmp_dir / "bad.yaml"
        bad_path.write_text("{{{{not yaml", encoding="utf-8")
        result = _generate_declare_module_stubs(self.tmp_dir, bad_path)
        self.assertIsNone(result)


class TestViStubGlobal(TempDirTestCase):
    """Tests for Check 19: vi.stubGlobal anti-pattern."""

    def test_detects_stub_global(self) -> None:
        """File with vi.stubGlobal → warning emitted."""
        f = self.tmp_dir / "test.test.ts"
        f.write_text(
            "vi.stubGlobal('registerHealthCheck', mockFn);\n"
            "test('x', () => { expect(1).toBe(1) })"
        )
        result = _check_vi_stub_global([f])
        self.assertTrue(result.passed)  # Warning only
        self.assertEqual(len(result.failing_files), 1)
        self.assertIn("vi.stubGlobal", result.detail)

    def test_no_stub_global(self) -> None:
        """File without vi.stubGlobal → no warning."""
        f = self.tmp_dir / "test.test.ts"
        f.write_text("test('x', () => { expect(1).toBe(1) })")
        result = _check_vi_stub_global([f])
        self.assertTrue(result.passed)
        self.assertEqual(len(result.failing_files), 0)

    def test_non_ts_files_skipped(self) -> None:
        """Non-.ts/.js files are not checked."""
        f = self.tmp_dir / "test.test.py"
        f.write_text("vi.stubGlobal('x', y)")
        result = _check_vi_stub_global([f])
        self.assertTrue(result.passed)
        self.assertEqual(len(result.failing_files), 0)


if __name__ == "__main__":
    unittest.main()
