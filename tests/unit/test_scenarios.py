"""Tests for scenarios/validator.py and scenarios/runner.py."""

import unittest

from tests.helpers import TempDirTestCase
from darkfactory.scenarios.validator import (
    validate_scenarios,
    _discover_files,
    _check_assertions,
    _check_trivial,
    _check_empty_bodies,
)


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


if __name__ == "__main__":
    unittest.main()
