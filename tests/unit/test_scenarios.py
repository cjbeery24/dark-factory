"""Tests for scenarios/validator.py and scenarios/detection.py."""

import subprocess
import unittest
from unittest.mock import patch

from tests.helpers import TempDirTestCase
from darkfactory.scenarios.validator import (
    validate_scenarios,
    _check_typescript_compilation,
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


if __name__ == "__main__":
    unittest.main()
