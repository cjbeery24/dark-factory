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
