"""Tests for audit/logger.py and audit/checker.py."""

import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase
from darkfactory.audit.logger import audit_log, audit_summary
from darkfactory.audit.checker import check_output_for_barriers, validate_oracle_feedback


class TestAuditLogger(TempDirTestCase):
    def test_log_creates_file(self) -> None:
        log_path = self.tmp_dir / "audit.log"
        audit_log(log_path, "test message")
        self.assertTrue(log_path.exists())
        content = log_path.read_text()
        self.assertIn("test message", content)
        self.assertIn("[20", content)  # Timestamp.

    def test_log_appends(self) -> None:
        log_path = self.tmp_dir / "audit.log"
        audit_log(log_path, "first")
        audit_log(log_path, "second")
        lines = log_path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 2)

    def test_summary(self) -> None:
        log_path = self.tmp_dir / "audit.log"
        audit_log(log_path, "test")
        summary = audit_summary(log_path)
        self.assertIn("test", summary)

    def test_summary_missing(self) -> None:
        self.assertEqual(audit_summary(self.tmp_dir / "nope.log"), "")


class TestBarrierChecker(TempDirTestCase):
    def test_no_violations(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        al = self.tmp_dir / "audit.log"
        violations = check_output_for_barriers(
            "clean output", [".dark-factory/scenarios"],
            "implementer", al, ef, "s", "r",
        )
        self.assertEqual(violations, [])

    def test_detects_violation(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        al = self.tmp_dir / "audit.log"
        violations = check_output_for_barriers(
            "I read .dark-factory/scenarios/my-spec/01-contract.test.ts",
            [".dark-factory/scenarios", ".dark-factory/reports"],
            "implementer", al, ef, "s", "r",
        )
        self.assertEqual(violations, [".dark-factory/scenarios"])


class TestOracleFeedback(TempDirTestCase):
    def test_clean_feedback(self) -> None:
        al = self.tmp_dir / "audit.log"
        leaks = validate_oracle_feedback("Focus on FR-1 compliance", al)
        self.assertEqual(leaks, [])

    def test_scenario_filename_leak(self) -> None:
        al = self.tmp_dir / "audit.log"
        leaks = validate_oracle_feedback(
            "The test 01-contract.test.ts is failing", al,
        )
        self.assertGreater(len(leaks), 0)

    def test_assertion_leak(self) -> None:
        al = self.tmp_dir / "audit.log"
        leaks = validate_oracle_feedback(
            "expect(result).toBe(true) is not passing", al,
        )
        self.assertGreater(len(leaks), 0)


if __name__ == "__main__":
    unittest.main()
