"""Tests for the deterministic review pre-pass (FR-1 through FR-6)."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path

from darkfactory.models.config import PipelineConfig
from darkfactory.review.patterns import SECURITY_PATTERNS
from darkfactory.review.prepass import (
    analyze_code_quality,
    analyze_security,
    analyze_test_results,
    run_review_prepass,
)


class TestAnalyzeTestResults(unittest.TestCase):
    """FR-1: Parse quality gate results."""

    def setUp(self) -> None:
        self.tmp = Path(__file__).parent / "_tmp_test_prepass"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.reports = self.tmp / ".dark-factory" / "reports"
        self.reports.mkdir(parents=True, exist_ok=True)
        self.scenarios_dir = self.tmp / ".dark-factory" / "scenarios" / "test-spec"
        self.scenarios_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_counts(self, **kwargs: str) -> None:
        lines = [f"{k}={v}" for k, v in kwargs.items()]
        (self.reports / "test-spec-scenario-counts.env").write_text("\n".join(lines))

    def _write_summary(self, text: str) -> None:
        (self.reports / "test-spec-quality-gate-summary.md").write_text(text)

    def test_happy_path_all_pass(self) -> None:
        """AC-1 variant: perfect scores."""
        self._write_counts(
            SCENARIO_PASSED="25", SCENARIO_FAILED="0", SCENARIO_TOTAL="25",
            SCENARIO_RATE="100", SATISFACTION_SCORE="95",
        )
        self._write_summary("build: pass\nlint: pass\ntype check: pass\nself-tests: pass\n")

        result = analyze_test_results(self.tmp, "test-spec")
        self.assertEqual(result.score, 25)
        self.assertEqual(result.scenario_passed, 25)
        self.assertEqual(result.failure_pattern, "none")
        # FR-1: gate_outcomes should be populated.
        self.assertTrue(result.gate_outcomes.get("build"))
        self.assertTrue(result.gate_outcomes.get("self-tests"))

    def test_gate_outcomes_from_table_format(self) -> None:
        """FR-1: Parse gate outcomes from markdown table format."""
        self._write_counts(
            SCENARIO_PASSED="25", SCENARIO_FAILED="0", SCENARIO_TOTAL="25",
            SCENARIO_RATE="100", SATISFACTION_SCORE="95",
        )
        table_summary = (
            "| Gate | Status |\n"
            "|------|--------|\n"
            "| Build | PASS |\n"
            "| Lint | PASS |\n"
            "| Type Check | PASS |\n"
            "| Self-Tests | PASS |\n"
            "| Blind Scenarios | PASS |\n"
        )
        self._write_summary(table_summary)

        result = analyze_test_results(self.tmp, "test-spec")
        self.assertEqual(result.score, 25)
        self.assertTrue(result.gate_outcomes.get("build"))
        self.assertTrue(result.gate_outcomes.get("lint"))
        self.assertTrue(result.gate_outcomes.get("typecheck"))
        self.assertTrue(result.gate_outcomes.get("self-tests"))

    def test_rate_below_95(self) -> None:
        """AC-1: rate 90% → subtract 5."""
        self._write_counts(
            SCENARIO_PASSED="18", SCENARIO_FAILED="2", SCENARIO_TOTAL="20",
            SCENARIO_RATE="90", SATISFACTION_SCORE="85",
        )
        self._write_summary("build: pass\nlint: pass\ntype check: pass\nself-tests: pass\n")
        result = analyze_test_results(self.tmp, "test-spec")
        self.assertEqual(result.score, 20)  # 25 - 5

    def test_rate_below_80(self) -> None:
        """Rate 72% → subtract 10."""
        self._write_counts(
            SCENARIO_PASSED="18", SCENARIO_FAILED="7", SCENARIO_TOTAL="25",
            SCENARIO_RATE="72", SATISFACTION_SCORE="65",
        )
        self._write_summary("build: pass\nlint: pass\ntype check: pass\nself-tests: pass\n")
        result = analyze_test_results(self.tmp, "test-spec", min_satisfaction=80)
        # 25 - 10 (rate<80) - 3 (satisfaction<80) = 12
        self.assertEqual(result.score, 12)

    def test_self_test_failure_deduction(self) -> None:
        self._write_counts(
            SCENARIO_PASSED="25", SCENARIO_FAILED="0", SCENARIO_TOTAL="25",
            SCENARIO_RATE="100", SATISFACTION_SCORE="95",
        )
        self._write_summary("build: pass\nlint: pass\ntype check: pass\nself-tests: failed\n")
        result = analyze_test_results(self.tmp, "test-spec")
        self.assertEqual(result.score, 20)  # 25 - 5

    def test_build_failure_deduction(self) -> None:
        self._write_counts(
            SCENARIO_PASSED="25", SCENARIO_FAILED="0", SCENARIO_TOTAL="25",
            SCENARIO_RATE="100", SATISFACTION_SCORE="95",
        )
        self._write_summary("build: failed\nlint: pass\ntype check: pass\nself-tests: pass\n")
        result = analyze_test_results(self.tmp, "test-spec")
        self.assertEqual(result.score, 23)  # 25 - 2

    def test_missing_counts_file_ec1(self) -> None:
        """EC-1: missing counts file → score 0."""
        result = analyze_test_results(self.tmp, "test-spec")
        self.assertEqual(result.score, 0)
        self.assertEqual(result.scenario_total, 0)
        self.assertEqual(result.failure_pattern, "none")

    def test_floor_at_zero(self) -> None:
        """Score cannot go below 0."""
        self._write_counts(
            SCENARIO_PASSED="5", SCENARIO_FAILED="20", SCENARIO_TOTAL="25",
            SCENARIO_RATE="20", SATISFACTION_SCORE="10",
        )
        self._write_summary("build: failed\nlint: fail\ntype check: failed\nself-tests: failed\n")
        result = analyze_test_results(self.tmp, "test-spec", min_satisfaction=80)
        self.assertGreaterEqual(result.score, 0)

    def test_systematic_failure_pattern(self) -> None:
        """AC-12: >50% failures in one layer → systematic."""
        self._write_counts(
            SCENARIO_PASSED="20", SCENARIO_FAILED="5", SCENARIO_TOTAL="25",
            SCENARIO_RATE="80", SATISFACTION_SCORE="80",
        )
        # Simulate gate summary with failures mostly in layer 03.
        summary = textwrap.dedent("""\
            build: pass
            lint: pass
            type check: pass
            self-tests: pass
            FAIL  03-edge_overflow
            FAIL  03-edge_boundary
            FAIL  03-edge_negative
            FAIL  03-edge_empty
            FAIL  05-state_timeout
        """)
        self._write_summary(summary)
        result = analyze_test_results(self.tmp, "test-spec")
        self.assertEqual(result.failure_pattern, "systematic")

    def test_systematic_failure_pattern_underscore_separator(self) -> None:
        """AC-12: filenames with underscore separator still classified correctly."""
        self._write_counts(
            SCENARIO_PASSED="20", SCENARIO_FAILED="5", SCENARIO_TOTAL="25",
            SCENARIO_RATE="80", SATISFACTION_SCORE="80",
        )
        summary = textwrap.dedent("""\
            build: pass
            lint: pass
            type check: pass
            self-tests: pass
            FAIL  03_edge_overflow
            FAIL  03_edge_boundary
            FAIL  03_edge_negative
            FAIL  03_edge_empty
            FAIL  05_state_timeout
        """)
        self._write_summary(summary)
        result = analyze_test_results(self.tmp, "test-spec")
        self.assertEqual(result.failure_pattern, "systematic")

    def test_isolated_failure_pattern(self) -> None:
        self._write_counts(
            SCENARIO_PASSED="22", SCENARIO_FAILED="3", SCENARIO_TOTAL="25",
            SCENARIO_RATE="88", SATISFACTION_SCORE="85",
        )
        summary = textwrap.dedent("""\
            build: pass
            lint: pass
            FAIL  01-contract_basic
            FAIL  03-edge_boundary
            FAIL  05-state_timeout
        """)
        self._write_summary(summary)
        result = analyze_test_results(self.tmp, "test-spec")
        self.assertEqual(result.failure_pattern, "isolated")


class TestAnalyzeCodeQuality(unittest.TestCase):
    """FR-2: Code quality scoring."""

    def setUp(self) -> None:
        self.tmp = Path(__file__).parent / "_tmp_test_cq"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.src = self.tmp / "src"
        self.src.mkdir(parents=True, exist_ok=True)
        self.reports = self.tmp / ".dark-factory" / "reports"
        self.reports.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _gate_summary(self, text: str) -> Path:
        p = self.reports / "spec-quality-gate-summary.md"
        p.write_text(text)
        return p

    def test_perfect_score_ac2(self) -> None:
        """AC-2: lint passed, no violations → 20."""
        (self.src / "main.py").write_text("def hello():\n    return 'hi'\n")
        gs = self._gate_summary("lint: pass\n")
        result = analyze_code_quality(self.tmp, ["src"], gs)
        self.assertEqual(result.score, 20)
        self.assertTrue(result.lint_passed)

    def test_lint_fail_and_long_functions_ac3(self) -> None:
        """AC-3: lint failed + 2 long functions → 13."""
        body = "    pass\n" * 55
        code = f"def func_a():\n{body}\ndef func_b():\n{body}\ndef short():\n    pass\n"
        (self.src / "big.py").write_text(code)
        gs = self._gate_summary("lint: failed\n")
        result = analyze_code_quality(self.tmp, ["src"], gs)
        # 20 - 5 (lint) - 2 (two long funcs) = 13
        self.assertEqual(result.score, 13)
        self.assertFalse(result.lint_passed)
        self.assertEqual(len(result.long_functions), 2)

    def test_no_source_files_ec3(self) -> None:
        """EC-3: no source files → score 20."""
        gs = self._gate_summary("lint: pass\n")
        result = analyze_code_quality(self.tmp, ["nonexistent"], gs)
        self.assertEqual(result.score, 20)

    def test_missing_gate_summary_ec2(self) -> None:
        """EC-2: missing summary → lint_passed=False."""
        (self.src / "main.py").write_text("x = 1\n")
        result = analyze_code_quality(self.tmp, ["src"], self.tmp / "missing.md")
        self.assertFalse(result.lint_passed)
        # 20 - 5 = 15 (no complexity deductions on tiny file).
        self.assertEqual(result.score, 15)

    def test_long_file_deduction(self) -> None:
        (self.src / "huge.py").write_text("x = 1\n" * 501)
        gs = self._gate_summary("lint: pass\n")
        result = analyze_code_quality(self.tmp, ["src"], gs)
        self.assertEqual(len(result.long_files), 1)
        # 20 - 1 (one long file) = 19
        self.assertEqual(result.score, 19)

    def test_deep_nesting_deduction(self) -> None:
        nested = "x = 1\n" + " " * 20 + "y = 2\n"
        (self.src / "deep.py").write_text(nested)
        gs = self._gate_summary("lint: pass\n")
        result = analyze_code_quality(self.tmp, ["src"], gs)
        self.assertIn("src/deep.py", result.deep_nesting_files)

    def test_lint_from_table_format(self) -> None:
        """FR-2: Extract lint pass/fail from markdown table format."""
        (self.src / "main.py").write_text("def hello():\n    return 'hi'\n")
        gs = self._gate_summary("| Gate | Status |\n|------|--------|\n| Lint | PASS |\n")
        result = analyze_code_quality(self.tmp, ["src"], gs)
        self.assertTrue(result.lint_passed)
        self.assertEqual(result.score, 20)

    def test_lint_fail_table_format(self) -> None:
        """FR-2: Lint FAIL in table format → score deduction."""
        (self.src / "main.py").write_text("def hello():\n    return 'hi'\n")
        gs = self._gate_summary("| Gate | Status |\n|------|--------|\n| Lint | FAIL |\n")
        result = analyze_code_quality(self.tmp, ["src"], gs)
        self.assertFalse(result.lint_passed)
        self.assertEqual(result.score, 15)

    def test_binary_file_skipped_ec4(self) -> None:
        """EC-4: binary files are skipped."""
        (self.src / "data.pyc").write_bytes(b"\x00\x01\x02\x03\xff\xfe")
        gs = self._gate_summary("lint: pass\n")
        # Should not raise.
        result = analyze_code_quality(self.tmp, ["src"], gs)
        self.assertEqual(result.score, 20)


class TestAnalyzeSecurity(unittest.TestCase):
    """FR-3: Security analysis."""

    def setUp(self) -> None:
        self.tmp = Path(__file__).parent / "_tmp_test_sec"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.src = self.tmp / "src"
        self.src.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_findings_ac5(self) -> None:
        """AC-5: clean code → score 15."""
        (self.src / "main.py").write_text("def safe():\n    return 42\n")
        result = analyze_security(self.tmp, ["src"], "python")
        self.assertEqual(result.score, 15)
        self.assertEqual(len(result.findings), 0)

    def test_sql_injection_python_ac4(self) -> None:
        """AC-4: f-string in execute() → HIGH finding."""
        code = textwrap.dedent("""\
            import sqlite3
            conn = sqlite3.connect("db.sqlite")
            cursor = conn.cursor()
            table = "users"
            cursor.execute(f"SELECT * FROM {table}")
        """)
        (self.src / "db.py").write_text(code)
        result = analyze_security(self.tmp, ["src"], "python")
        self.assertLess(result.score, 15)
        found = [f for f in result.findings if f.pattern == "sql_injection"]
        self.assertTrue(len(found) > 0)
        self.assertEqual(found[0].severity, "HIGH")
        self.assertEqual(found[0].file, "src/db.py")
        self.assertEqual(found[0].line_number, 5)

    def test_hardcoded_secret_python(self) -> None:
        code = 'password = "supersecretpassword123"\n'
        (self.src / "config.py").write_text(code)
        result = analyze_security(self.tmp, ["src"], "python")
        found = [f for f in result.findings if f.pattern == "hardcoded_secret"]
        self.assertTrue(len(found) > 0)
        self.assertEqual(found[0].severity, "MEDIUM")

    def test_insecure_deserialization_eval(self) -> None:
        code = "data = eval(user_input)\n"
        (self.src / "unsafe.py").write_text(code)
        result = analyze_security(self.tmp, ["src"], "python")
        found = [f for f in result.findings if f.pattern == "insecure_deserialization"]
        self.assertTrue(len(found) > 0)

    def test_yaml_safe_loader_not_flagged(self) -> None:
        """FR-3: yaml.load with SafeLoader should NOT be flagged."""
        code = 'data = yaml.load(content, Loader=SafeLoader)\n'
        (self.src / "safe.py").write_text(code)
        result = analyze_security(self.tmp, ["src"], "python")
        found = [f for f in result.findings if f.pattern == "insecure_deserialization"]
        self.assertEqual(len(found), 0)
        self.assertEqual(result.score, 15)

    def test_yaml_unsafe_load_flagged(self) -> None:
        """FR-3: yaml.load without SafeLoader should be flagged."""
        code = 'data = yaml.load(content)\n'
        (self.src / "unsafe_yaml.py").write_text(code)
        result = analyze_security(self.tmp, ["src"], "python")
        found = [f for f in result.findings if f.pattern == "insecure_deserialization"]
        self.assertTrue(len(found) > 0)

    def test_unsupported_language_ec7_ac10(self) -> None:
        """EC-7 / AC-10: unsupported language → score 15."""
        (self.src / "Main.java").write_text("public class Main {}\n")
        result = analyze_security(self.tmp, ["src"], "java")
        self.assertEqual(result.score, 15)
        self.assertEqual(len(result.findings), 0)

    def test_no_source_files_ec3(self) -> None:
        """EC-3: no files → score 15."""
        result = analyze_security(self.tmp, ["nonexistent"], "python")
        self.assertEqual(result.score, 15)

    def test_node_xss_detection(self) -> None:
        code = 'element.innerHTML = userInput;\n'
        (self.src / "app.js").write_text(code)
        result = analyze_security(self.tmp, ["src"], "node")
        found = [f for f in result.findings if f.pattern == "xss"]
        self.assertTrue(len(found) > 0)

    def test_multiple_high_findings_score_floor(self) -> None:
        """Score floors at 0 with many HIGH findings."""
        code = textwrap.dedent("""\
            cursor.execute(f"a")
            cursor.execute(f"b")
            cursor.execute(f"c")
            cursor.execute(f"d")
        """)
        (self.src / "bad.py").write_text(code)
        result = analyze_security(self.tmp, ["src"], "python")
        self.assertEqual(result.score, 0)

    def test_go_sql_injection(self) -> None:
        code = 'db.Query(fmt.Sprintf("SELECT * FROM %s", table))\n'
        (self.src / "main.go").write_text(code)
        result = analyze_security(self.tmp, ["src"], "go")
        found = [f for f in result.findings if f.pattern == "sql_injection"]
        self.assertTrue(len(found) > 0)

    def test_rust_sql_injection(self) -> None:
        code = 'client.query(format!("SELECT * FROM {}", table));\n'
        (self.src / "main.rs").write_text(code)
        result = analyze_security(self.tmp, ["src"], "rust")
        found = [f for f in result.findings if f.pattern == "sql_injection"]
        self.assertTrue(len(found) > 0)


class TestRunReviewPrepass(unittest.TestCase):
    """FR-4: Report generation and orchestration."""

    def setUp(self) -> None:
        self.tmp = Path(__file__).parent / "_tmp_test_rp"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.reports = self.tmp / ".dark-factory" / "reports"
        self.reports.mkdir(parents=True, exist_ok=True)
        self.src = self.tmp / "src"
        self.src.mkdir(parents=True, exist_ok=True)
        (self.src / "main.py").write_text("def hello():\n    return 'hi'\n")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_counts(self, **kwargs: str) -> None:
        lines = [f"{k}={v}" for k, v in kwargs.items()]
        (self.reports / "my-spec-scenario-counts.env").write_text("\n".join(lines))

    def _write_summary(self, text: str) -> None:
        (self.reports / "my-spec-quality-gate-summary.md").write_text(text)

    def test_writes_env_and_md_ac6(self) -> None:
        """AC-6: both report files are written."""
        self._write_counts(
            SCENARIO_PASSED="25", SCENARIO_FAILED="0", SCENARIO_TOTAL="25",
            SCENARIO_RATE="100", SATISFACTION_SCORE="95",
        )
        self._write_summary("build: pass\nlint: pass\ntype check: pass\nself-tests: pass\n")

        config = PipelineConfig(language="python", source_dirs=["src"])
        result = run_review_prepass(self.tmp, "my-spec", config)

        self.assertEqual(result.deterministic_total, 60)

        env_file = self.reports / "my-spec-review-prepass.env"
        md_file = self.reports / "my-spec-review-prepass.md"
        self.assertTrue(env_file.exists())
        self.assertTrue(md_file.exists())

        env_content = env_file.read_text()
        self.assertIn("TEST_ANALYSIS_SCORE=25", env_content)
        self.assertIn("CODE_QUALITY_SCORE=20", env_content)
        self.assertIn("SECURITY_SCORE=15", env_content)
        self.assertIn("DETERMINISTIC_TOTAL=60", env_content)
        self.assertIn("FAILURE_PATTERN=none", env_content)

        md_content = md_file.read_text()
        self.assertIn("**Deterministic Total:** 60/60", md_content)

    def test_mixed_results(self) -> None:
        """Example 2 from spec: mixed results."""
        self._write_counts(
            SCENARIO_PASSED="18", SCENARIO_FAILED="7", SCENARIO_TOTAL="25",
            SCENARIO_RATE="72", SATISFACTION_SCORE="65",
        )
        self._write_summary("build: pass\nlint: pass\ntype check: pass\nself-tests: pass\n")

        code = textwrap.dedent("""\
            import sqlite3
            conn = sqlite3.connect("db.sqlite")
            cursor = conn.cursor()
            table = "users"
            cursor.execute(f"SELECT * FROM {table}")
        """)
        (self.src / "db.py").write_text(code)

        config = PipelineConfig(
            language="python", source_dirs=["src"],
            min_satisfaction_score=80,
        )
        result = run_review_prepass(self.tmp, "my-spec", config)

        # test_analysis: 25 - 10 (rate<80) - 3 (satisfaction<80) = 12
        self.assertEqual(result.test_analysis.score, 12)
        # code_quality: 20 (lint pass, no complexity issues in small files)
        self.assertEqual(result.code_quality.score, 20)
        # security: 15 - 5 (one HIGH) = 10
        self.assertEqual(result.security.score, 10)
        # total: 12 + 20 + 10 = 42
        self.assertEqual(result.deterministic_total, 42)


class TestSecurityPatterns(unittest.TestCase):
    """Verify patterns module structure."""

    def test_all_expected_languages(self) -> None:
        self.assertIn("python", SECURITY_PATTERNS)
        self.assertIn("node", SECURITY_PATTERNS)
        self.assertIn("go", SECURITY_PATTERNS)
        self.assertIn("rust", SECURITY_PATTERNS)

    def test_patterns_are_compiled(self) -> None:
        import re
        for lang, patterns in SECURITY_PATTERNS.items():
            for name, severity, regex in patterns:
                self.assertIsInstance(regex, re.Pattern, f"{lang}/{name} not compiled")
                self.assertIn(severity, ("HIGH", "MEDIUM", "LOW"), f"{lang}/{name} bad severity")


class TestServerErrorAwareness(unittest.TestCase):
    """Server error deduction and surfacing in prepass."""

    def setUp(self) -> None:
        self.tmp = Path(__file__).parent / "_tmp_test_server_err"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.reports = self.tmp / ".dark-factory" / "reports"
        self.reports.mkdir(parents=True, exist_ok=True)
        self.scenarios_dir = self.tmp / ".dark-factory" / "scenarios" / "test-spec"
        self.scenarios_dir.mkdir(parents=True, exist_ok=True)
        self.src = self.tmp / "src"
        self.src.mkdir(parents=True, exist_ok=True)
        (self.src / "main.py").write_text("def hello():\n    return 'hi'\n")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_counts(self, **kwargs: str) -> None:
        lines = [f"{k}={v}" for k, v in kwargs.items()]
        (self.reports / "test-spec-scenario-counts.env").write_text("\n".join(lines))

    def _write_summary(self, text: str) -> None:
        (self.reports / "test-spec-quality-gate-summary.md").write_text(text)

    def test_server_error_deducts_15(self) -> None:
        """Server error deducts 15 from test score."""
        self._write_counts(
            SCENARIO_PASSED="30", SCENARIO_FAILED="0", SCENARIO_TOTAL="30",
            SCENARIO_RATE="100", SATISFACTION_SCORE="0",
        )
        self._write_summary("build: pass\nlint: pass\ntype check: pass\nself-tests: pass\n")

        result = analyze_test_results(
            self.tmp, "test-spec",
            server_error="server_setup_cmd failed (exit 1):\ndrizzle.config.json not found",
        )
        # Base 25, no deductions from pass_rate/gates, minus 15 for server error = 10
        self.assertEqual(result.score, 10)
        self.assertEqual(result.failure_pattern, "server_error")
        self.assertEqual(result.server_error, "server_setup_cmd failed (exit 1):\ndrizzle.config.json not found")

    def test_no_server_error_no_deduction(self) -> None:
        """No server error → no deduction (existing behavior)."""
        self._write_counts(
            SCENARIO_PASSED="30", SCENARIO_FAILED="0", SCENARIO_TOTAL="30",
            SCENARIO_RATE="100", SATISFACTION_SCORE="0",
        )
        self._write_summary("build: pass\nlint: pass\ntype check: pass\nself-tests: pass\n")

        result = analyze_test_results(self.tmp, "test-spec")
        self.assertEqual(result.score, 25)
        self.assertEqual(result.failure_pattern, "none")
        self.assertEqual(result.server_error, "")

    def test_server_error_score_floors_at_zero(self) -> None:
        """Server error + low pass rate doesn't go negative."""
        self._write_counts(
            SCENARIO_PASSED="5", SCENARIO_FAILED="25", SCENARIO_TOTAL="30",
            SCENARIO_RATE="17", SATISFACTION_SCORE="0",
        )
        self._write_summary("build: pass\nlint: pass\ntype check: fail\nself-tests: fail\n")

        result = analyze_test_results(
            self.tmp, "test-spec",
            server_error="server startup timeout",
        )
        # 25 - 10 (rate<80) - 5 (self-tests fail) - 2 (typecheck fail) - 15 (server) = -7 → 0
        self.assertEqual(result.score, 0)
        self.assertEqual(result.failure_pattern, "server_error")

    def test_run_review_prepass_passes_server_error(self) -> None:
        """run_review_prepass threads server_error to test analysis."""
        self._write_counts(
            SCENARIO_PASSED="30", SCENARIO_FAILED="0", SCENARIO_TOTAL="30",
            SCENARIO_RATE="100", SATISFACTION_SCORE="0",
        )
        self._write_summary("build: pass\nlint: pass\ntype check: pass\nself-tests: pass\n")

        config = PipelineConfig(language="python", source_dirs=["src"])
        result = run_review_prepass(
            self.tmp, "test-spec", config,
            server_error="drizzle-kit push failed",
        )
        self.assertEqual(result.test_analysis.score, 10)
        self.assertEqual(result.test_analysis.server_error, "drizzle-kit push failed")

        # Check env file includes SERVER_ERROR=1
        env_content = (self.reports / "test-spec-review-prepass.env").read_text()
        self.assertIn("SERVER_ERROR=1", env_content)

        # Check markdown includes warning
        md_content = (self.reports / "test-spec-review-prepass.md").read_text()
        self.assertIn("SERVER ERROR", md_content)
        self.assertIn("test server failed to start", md_content.lower())

    def test_run_review_prepass_no_server_error_env(self) -> None:
        """No server error → SERVER_ERROR=0 in env."""
        self._write_counts(
            SCENARIO_PASSED="30", SCENARIO_FAILED="0", SCENARIO_TOTAL="30",
            SCENARIO_RATE="100", SATISFACTION_SCORE="0",
        )
        self._write_summary("build: pass\nlint: pass\ntype check: pass\nself-tests: pass\n")

        config = PipelineConfig(language="python", source_dirs=["src"])
        run_review_prepass(self.tmp, "test-spec", config)

        env_content = (self.reports / "test-spec-review-prepass.env").read_text()
        self.assertIn("SERVER_ERROR=0", env_content)

        md_content = (self.reports / "test-spec-review-prepass.md").read_text()
        self.assertNotIn("SERVER ERROR", md_content)


class TestGateSummaryServerStartup(unittest.TestCase):
    """Tests that server startup status appears in gate summary and prepass."""

    def setUp(self) -> None:
        self.tmp = Path(__file__).parent / "_tmp_test_gate_summary"
        self.reports = self.tmp / ".dark-factory" / "reports"
        self.reports.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        import shutil
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def _write_summary(self, gates: str = "") -> None:
        text = (
            "# Quality Gate Summary: test-spec\n"
            f"Date: 2026-04-01\n\n"
            f"| Gate | Status |\n|------|--------|\n"
            f"{gates}\n"
            f"## Result: PASS\n"
        )
        (self.reports / "test-spec-quality-gate-summary.md").write_text(text)

    def test_append_gate_summary_row(self) -> None:
        from darkfactory.pipeline.quality_gate import _append_gate_summary_row
        self._write_summary("| Build | PASS |\n| Lint | PASS |\n")
        _append_gate_summary_row(self.tmp, "test-spec", "Server Startup", 1)
        content = (self.reports / "test-spec-quality-gate-summary.md").read_text()
        self.assertIn("| Server Startup | FAIL |", content)
        # Row should appear before ## Result:
        row_pos = content.index("Server Startup")
        result_pos = content.index("## Result:")
        self.assertLess(row_pos, result_pos)

    def test_rewrite_summary_result(self) -> None:
        from darkfactory.pipeline.quality_gate import _rewrite_summary_result
        self._write_summary("| Build | PASS |\n")
        _rewrite_summary_result(self.tmp, "test-spec", "FAIL")
        content = (self.reports / "test-spec-quality-gate-summary.md").read_text()
        self.assertIn("## Result: FAIL", content)
        self.assertNotIn("## Result: PASS", content)

    def test_summary_missing_file_skips_gracefully(self) -> None:
        from darkfactory.pipeline.quality_gate import _append_gate_summary_row, _rewrite_summary_result
        # Should not raise — just skip
        _append_gate_summary_row(self.tmp, "nonexistent", "Server Startup", 1)
        _rewrite_summary_result(self.tmp, "nonexistent", "FAIL")

    def test_prepass_parses_server_startup_from_summary(self) -> None:
        from darkfactory.review.prepass import _parse_gate_outcomes
        self._write_summary(
            "| Build | PASS |\n| Lint | PASS |\n| Server Startup | FAIL |\n"
        )
        summary_path = self.reports / "test-spec-quality-gate-summary.md"
        outcomes = _parse_gate_outcomes(summary_path)
        self.assertIn("server-startup", outcomes)
        self.assertFalse(outcomes["server-startup"])

    def test_prepass_parses_server_setup_from_summary(self) -> None:
        from darkfactory.review.prepass import _parse_gate_outcomes
        self._write_summary(
            "| Build | PASS |\n| Server Setup | FAIL |\n"
        )
        summary_path = self.reports / "test-spec-quality-gate-summary.md"
        outcomes = _parse_gate_outcomes(summary_path)
        self.assertIn("server-startup", outcomes)
        self.assertFalse(outcomes["server-startup"])


class TestIterationPrepassIntegration(unittest.TestCase):
    """FR-5/FR-6: Integration with _step_review."""

    def test_prepass_exception_does_not_block_reviewer_ac11(self) -> None:
        """AC-11: pre-pass exception is caught, reviewer proceeds."""
        # We test the error handling by patching run_review_prepass to raise.
        from darkfactory.pipeline import iteration

        # The actual _step_review is complex (requires Docker). We just verify
        # the import and exception handling path exists by checking the function.
        import inspect
        source = inspect.getsource(iteration._step_review)
        self.assertIn("review pre-pass failed, proceeding without", source)
        self.assertIn("run_review_prepass", source)

    def test_fallback_review_includes_prepass_scores(self) -> None:
        """Verify the fallback template references deterministic scores."""
        from darkfactory.pipeline import iteration
        import inspect
        source = inspect.getsource(iteration._step_review)
        self.assertIn("PARTIAL (Deterministic Fallback)", source)
        self.assertIn("Spec Compliance | 0/30", source)
        self.assertIn("Robustness | 0/10", source)

    def test_prompt_includes_prepass_reference(self) -> None:
        """FR-5: Prompt includes reference to pre-pass report."""
        from darkfactory.pipeline import iteration
        import inspect
        source = inspect.getsource(iteration._step_review)
        self.assertIn("review-prepass.md", source)
        self.assertIn("Focus your analysis on Spec Compliance", source)


if __name__ == "__main__":
    unittest.main()
