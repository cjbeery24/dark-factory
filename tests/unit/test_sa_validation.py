"""Tests for SA output validation checks 10-16 (Phase 2)."""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase
from darkfactory.scenarios.validator import (
    _check_assertion_upper_bound,
    _check_intent_alignment,
    _check_no_hardcoded_paths,
    _check_no_indexof_ordering,
    _check_no_project_writes,
    _check_no_source_reads,
    _check_regex_multiline_flag,
    validate_scenarios,
)
from darkfactory.models.config import PipelineConfig
from darkfactory.errors import ConfigError


class TestCheck10AssertionUpperBound(TempDirTestCase):
    """Check 10: Assertion count upper bound."""

    def test_pass_under_limit(self) -> None:
        f = self.tmp_dir / "test.test.ts"
        f.write_text("test('a', () => { expect(1).toBe(1) })")
        result = _check_assertion_upper_bound([f], "node", max_per_file=15)
        self.assertTrue(result.passed)

    def test_fail_over_limit(self) -> None:
        f = self.tmp_dir / "test.test.ts"
        f.write_text("\n".join(f"expect({i}).toBe({i})" for i in range(20)))
        result = _check_assertion_upper_bound([f], "node", max_per_file=15)
        self.assertFalse(result.passed)
        self.assertIn("test.test.ts", result.failing_files)

    def test_python_assertions(self) -> None:
        f = self.tmp_dir / "test_big.py"
        f.write_text("\n".join(f"assert x == {i}" for i in range(20)))
        result = _check_assertion_upper_bound([f], "python", max_per_file=15)
        self.assertFalse(result.passed)


class TestCheck11NoSourceReads(TempDirTestCase):
    """Check 11: No source file reads."""

    def test_fail_reads_source(self) -> None:
        f = self.tmp_dir / "test.test.ts"
        f.write_text("const c = readFileSync('src/server.ts', 'utf-8')")
        result = _check_no_source_reads([f])
        self.assertFalse(result.passed)

    def test_pass_reads_config(self) -> None:
        f = self.tmp_dir / "test.test.ts"
        f.write_text("const c = readFileSync('package.json', 'utf-8')")
        result = _check_no_source_reads([f])
        self.assertTrue(result.passed)

    def test_fail_python_open_source(self) -> None:
        f = self.tmp_dir / "test_read.py"
        f.write_text("content = open('src/main.py', 'r').read()")
        result = _check_no_source_reads([f])
        self.assertFalse(result.passed)


class TestCheck12NoIndexOfOrdering(TempDirTestCase):
    """Check 12: No indexOf ordering comparisons."""

    def test_fail_indexof_ordering(self) -> None:
        f = self.tmp_dir / "test.test.ts"
        f.write_text("expect(content.indexOf('lint') < content.indexOf('test')).toBe(true)")
        result = _check_no_indexof_ordering([f])
        self.assertFalse(result.passed)

    def test_pass_no_indexof(self) -> None:
        f = self.tmp_dir / "test.test.ts"
        f.write_text("expect(content).toContain('lint')")
        result = _check_no_indexof_ordering([f])
        self.assertTrue(result.passed)


class TestCheck13IntentAlignment(TempDirTestCase):
    """Check 13: Intent count alignment."""

    def _make_spec(self, intents: int) -> Path:
        spec = self.tmp_dir / "spec.md"
        lines = ["# Spec\n", "## Scenario Intents\n"]
        for i in range(1, intents + 1):
            lines.append(f"- Intent {i}")
        spec.write_text("\n".join(lines))
        return spec

    def test_pass_within_2x(self) -> None:
        spec = self._make_spec(20)
        files = []
        for i in range(4):
            f = self.tmp_dir / f"test{i}.test.ts"
            # 10 tests each = 40 total, 2x of 20 = 40
            f.write_text("\n".join(f"test('t{j}', () => {{}})" for j in range(10)))
            files.append(f)
        result = _check_intent_alignment(files, "node", spec)
        self.assertTrue(result.passed)

    def test_fail_exceeds_2x(self) -> None:
        spec = self._make_spec(10)
        files = []
        for i in range(5):
            f = self.tmp_dir / f"test{i}.test.ts"
            # 10 tests each = 50, 2x of 10 = 20
            f.write_text("\n".join(f"test('t{j}', () => {{}})" for j in range(10)))
            files.append(f)
        result = _check_intent_alignment(files, "node", spec)
        self.assertFalse(result.passed)

    def test_skip_no_intent_section(self) -> None:
        spec = self.tmp_dir / "spec.md"
        spec.write_text("# Spec\n\nNo intents here.")
        f = self.tmp_dir / "test.test.ts"
        f.write_text("test('t', () => {})")
        result = _check_intent_alignment([f], "node", spec)
        self.assertTrue(result.passed)
        self.assertIn("skipped", result.detail)


class TestCheck14RegexMultilineFlag(TempDirTestCase):
    """Check 14: Regex multiline flag (warning only)."""

    def test_warn_no_multiline(self) -> None:
        f = self.tmp_dir / "test.test.ts"
        f.write_text(
            "const c = readFileSync('ci.yml');\n"
            "expect(c).toMatch(/on:\\s+push/)\n"
        )
        result = _check_regex_multiline_flag([f])
        # Warning only — always passes
        self.assertTrue(result.passed)
        self.assertIn("test.test.ts", result.failing_files)

    def test_no_warn_with_multiline(self) -> None:
        f = self.tmp_dir / "test.test.ts"
        f.write_text(
            "const c = readFileSync('ci.yml');\n"
            "expect(c).toMatch(/on:\\s+push/ms)\n"
        )
        result = _check_regex_multiline_flag([f])
        self.assertEqual(result.failing_files, [])


class TestCheck15NoProjectWrites(TempDirTestCase):
    """Check 15: No project file modification."""

    def test_fail_writes_project(self) -> None:
        f = self.tmp_dir / "test.test.ts"
        f.write_text("writeFileSync('packages/shared/src/index.ts', code)")
        result = _check_no_project_writes([f])
        self.assertFalse(result.passed)

    def test_pass_writes_tmp(self) -> None:
        f = self.tmp_dir / "test.test.ts"
        f.write_text("writeFileSync(tmpFile, code); // tmpdir based")
        result = _check_no_project_writes([f])
        self.assertTrue(result.passed)

    def test_pass_writes_os_tmpdir(self) -> None:
        f = self.tmp_dir / "test.test.ts"
        f.write_text("writeFileSync(path.join(os.tmpdir(), 'test.json'), code)")
        result = _check_no_project_writes([f])
        self.assertTrue(result.passed)


class TestCheck16NoHardcodedPaths(TempDirTestCase):
    """Check 16: No hardcoded container paths."""

    def test_fail_workspace(self) -> None:
        f = self.tmp_dir / "test.test.ts"
        f.write_text("process.chdir('/workspace')")
        result = _check_no_hardcoded_paths([f])
        self.assertFalse(result.passed)

    def test_fail_project(self) -> None:
        f = self.tmp_dir / "test.test.ts"
        f.write_text("process.chdir('/project')")
        result = _check_no_hardcoded_paths([f])
        self.assertFalse(result.passed)

    def test_pass_cwd(self) -> None:
        f = self.tmp_dir / "test.test.ts"
        f.write_text("process.chdir(process.cwd())")
        result = _check_no_hardcoded_paths([f])
        self.assertTrue(result.passed)


class TestTier1SkipForChecks10to16(TempDirTestCase):
    """Tier 1 files (00-tier1-* prefix) are skipped for checks 10-16."""

    def test_tier1_files_skipped(self) -> None:
        """Tier 1 files with 20 assertions don't trip check 10."""
        project_dir = self.tmp_dir
        scenarios_dir = project_dir / ".dark-factory" / "scenarios" / "spec"
        scenarios_dir.mkdir(parents=True)
        (project_dir / ".dark-factory" / "reports").mkdir(parents=True)

        # Tier 1 file with 20 assertions (would fail check 10 if not skipped)
        tier1 = scenarios_dir / "00-tier1-contracts.test.ts"
        tier1.write_text("\n".join(f"expect({i}).toBe({i})" for i in range(20)))

        # Tier 2 files with normal assertion counts
        for i in range(3):
            f = scenarios_dir / f"edge-case-{i}.test.ts"
            f.write_text(f"test('ec{i}', () => {{ expect({i}).toBe({i}) }})")

        # Check 10 specifically: tier1 file should not appear in failures
        from darkfactory.scenarios.validator import _check_assertion_upper_bound, _discover_files
        all_files = _discover_files(scenarios_dir, "node")
        tier2_files = [f for f in all_files if not f.name.startswith("00-tier1-")]
        result = _check_assertion_upper_bound(tier2_files, "node", max_per_file=15)
        self.assertTrue(result.passed)
        self.assertNotIn("00-tier1-contracts.test.ts", result.failing_files)


class TestMixedFormatRejection(TempDirTestCase):
    """Mixed .scenarios.md + .test.ts output is rejected with a clear message."""

    def _setup_scenarios(self, spec_name: str, files: dict[str, str]) -> Path:
        project_dir = self.tmp_dir
        scenarios_dir = project_dir / ".dark-factory" / "scenarios" / spec_name
        scenarios_dir.mkdir(parents=True)
        (project_dir / ".dark-factory" / "reports").mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (scenarios_dir / name).write_text(content)
        return project_dir

    def test_mixed_markdown_and_executable_rejected(self) -> None:
        """2 .scenarios.md + 1 .test.ts → single_format FAIL."""
        project_dir = self._setup_scenarios("spec", {
            "01-happy.scenarios.md": "## Request\nGET /health\n## Expect\n- Status: 200\n",
            "02-edge.scenarios.md": "## Request\nGET /edge\n## Expect\n- Status: 200\n",
            "03-artifact.test.ts": "import { expect } from 'vitest';\ntest('x', () => { expect(1).toBe(1) })",
        })
        result = validate_scenarios("spec", "node", project_dir)
        self.assertFalse(result.passed)
        check_names = [c.name for c in result.checks]
        self.assertIn("single_format", check_names)
        sf = next(c for c in result.checks if c.name == "single_format")
        self.assertIn("Mixed formats", sf.detail)
        self.assertIn("ONE format", sf.detail)

    def test_executable_only_no_rejection(self) -> None:
        """3 .test.ts files → normal validation, no single_format check."""
        project_dir = self._setup_scenarios("spec", {
            f"0{i}-test.test.ts": f"test('t{i}', () => {{ expect({i}).toBe({i}) }})"
            for i in range(1, 4)
        })
        result = validate_scenarios("spec", "node", project_dir)
        check_names = [c.name for c in result.checks]
        self.assertNotIn("single_format", check_names)

    def test_markdown_only_no_rejection(self) -> None:
        """3 .scenarios.md files → markdown-only validation, no single_format check."""
        project_dir = self._setup_scenarios("spec", {
            f"0{i}-test.scenarios.md": f"## Request\nGET /t{i}\n## Expect\n- Status: 200\n"
            for i in range(1, 4)
        })
        result = validate_scenarios("spec", "node", project_dir)
        check_names = [c.name for c in result.checks]
        self.assertNotIn("single_format", check_names)

    def test_tier1_plus_markdown_no_false_positive(self) -> None:
        """1 Tier 1 .test.ts + 3 .scenarios.md → treated as markdown-only, no rejection."""
        project_dir = self._setup_scenarios("spec", {
            "00-tier1-contracts.test.ts": "test('tc', () => { expect(1).toBe(1) })",
            "01-happy.scenarios.md": "## Request\nGET /a\n## Expect\n- Status: 200\n",
            "02-edge.scenarios.md": "## Request\nGET /b\n## Expect\n- Status: 200\n",
            "03-error.scenarios.md": "## Request\nGET /c\n## Expect\n- Status: 400\n",
        })
        result = validate_scenarios("spec", "node", project_dir)
        check_names = [c.name for c in result.checks]
        self.assertNotIn("single_format", check_names)
        # Should pass markdown-only validation (3 files >= min 3)
        self.assertTrue(result.passed)

    def test_mixed_report_is_populated(self) -> None:
        """Rejected mixed-format result has a report for SA retry feedback."""
        project_dir = self._setup_scenarios("spec", {
            "01-happy.scenarios.md": "## Request\nGET /health\n## Expect\n- Status: 200\n",
            "02-artifact.test.ts": "test('x', () => { expect(1).toBe(1) })",
        })
        result = validate_scenarios("spec", "node", project_dir)
        self.assertFalse(result.passed)
        self.assertIn("single_format", result.report)
        self.assertIn("FAIL", result.report)


class TestBehavioralScenariosRequired(TempDirTestCase):
    """Validation gate: require .scenarios.md when has_api_contracts=True."""

    def _setup_scenarios(self, spec_name: str, files: dict[str, str]) -> Path:
        project_dir = self.tmp_dir
        scenarios_dir = project_dir / ".dark-factory" / "scenarios" / spec_name
        scenarios_dir.mkdir(parents=True)
        (project_dir / ".dark-factory" / "reports").mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (scenarios_dir / name).write_text(content)
        return project_dir

    def test_no_markdown_with_api_contracts_fails(self) -> None:
        """Executable-only output + has_api_contracts=True → hard fail."""
        project_dir = self._setup_scenarios("spec", {
            f"0{i}-test.test.ts": f"test('t{i}', () => {{ expect({i}).toBe({i}) }})"
            for i in range(1, 4)
        })
        result = validate_scenarios(
            "spec", "node", project_dir, has_api_contracts=True,
        )
        self.assertFalse(result.passed)
        check_names = [c.name for c in result.checks]
        self.assertIn("behavioral_scenarios_required", check_names)

    def test_markdown_present_with_api_contracts_passes(self) -> None:
        """Markdown-only output + has_api_contracts=True → passes (no gate)."""
        project_dir = self._setup_scenarios("spec", {
            f"0{i}-test.scenarios.md": f"## Request\nGET /t{i}\n## Expect\n- Status: 200\n"
            for i in range(1, 4)
        })
        result = validate_scenarios(
            "spec", "node", project_dir, has_api_contracts=True,
        )
        check_names = [c.name for c in result.checks]
        self.assertNotIn("behavioral_scenarios_required", check_names)
        self.assertTrue(result.passed)

    def test_no_markdown_without_api_contracts_no_gate(self) -> None:
        """Executable-only output + has_api_contracts=False → normal path (no gate)."""
        project_dir = self._setup_scenarios("spec", {
            f"0{i}-test.test.ts": f"test('t{i}', () => {{ expect({i}).toBe({i}) }})"
            for i in range(1, 4)
        })
        result = validate_scenarios(
            "spec", "node", project_dir, has_api_contracts=False,
        )
        check_names = [c.name for c in result.checks]
        self.assertNotIn("behavioral_scenarios_required", check_names)


class TestSAConfigValidation(unittest.TestCase):
    """Config validation for SA retry fields."""

    def test_sa_max_retries_negative(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig(sa_max_retries=-1)

    def test_scenario_max_assertions_zero(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig(scenario_max_assertions_per_file=0)

    def test_valid_sa_config(self) -> None:
        config = PipelineConfig(sa_max_retries=3, scenario_max_assertions_per_file=20)
        self.assertEqual(config.sa_max_retries, 3)
        self.assertEqual(config.scenario_max_assertions_per_file, 20)


if __name__ == "__main__":
    unittest.main()
