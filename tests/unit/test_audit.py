"""Tests for audit/logger.py and audit/checker.py."""

import unittest

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
        fb = 'Focus on FR-1 compliance\n<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG"} -->'
        result = validate_oracle_feedback(fb, al)
        self.assertTrue(result.is_compliant)
        self.assertEqual(result.violations, [])

    def test_scenario_filename_leak(self) -> None:
        al = self.tmp_dir / "audit.log"
        result = validate_oracle_feedback(
            "The test 01-contract.test.ts is failing", al,
        )
        self.assertFalse(result.is_compliant)
        self.assertGreater(len(result.violations), 0)

    def test_assertion_leak(self) -> None:
        al = self.tmp_dir / "audit.log"
        result = validate_oracle_feedback(
            "expect(result).toBe(true) is not passing", al,
        )
        self.assertFalse(result.is_compliant)
        self.assertGreater(len(result.violations), 0)

    def test_count_leakage_fraction(self) -> None:
        al = self.tmp_dir / "audit.log"
        result = validate_oracle_feedback(
            "Only 64/73 scenarios passed in this iteration.", al,
        )
        self.assertFalse(result.is_compliant)

    def test_count_leakage_percentage(self) -> None:
        al = self.tmp_dir / "audit.log"
        result = validate_oracle_feedback(
            "The 87% pass rate is insufficient for shipping.", al,
        )
        self.assertFalse(result.is_compliant)

    def test_count_leakage_quantified(self) -> None:
        al = self.tmp_dir / "audit.log"
        result = validate_oracle_feedback(
            "All 73 scenarios must pass before shipping.", al,
        )
        self.assertFalse(result.is_compliant)

    def test_count_leakage_verb_based_no_count(self) -> None:
        """4.6b.1g: 'no scenarios passed' leaks distribution even without a count."""
        al = self.tmp_dir / "audit.log"
        result = validate_oracle_feedback(
            "Unfortunately no scenarios passed in this iteration.", al,
        )
        self.assertFalse(result.is_compliant)

    def test_count_leakage_verb_based_all_failed(self) -> None:
        """4.6b.1g: 'all scenarios failed' leaks distribution."""
        al = self.tmp_dir / "audit.log"
        result = validate_oracle_feedback(
            "Essentially all scenarios failed due to missing handler.", al,
        )
        self.assertFalse(result.is_compliant)

    def test_bare_all_scenarios_no_false_positive(self) -> None:
        """4.6b.1g regression (Run 11): bare 'all scenarios' with no count and
        no verb is generic English, not a leak."""
        al = self.tmp_dir / "audit.log"
        fb = (
            "Focus on AC-1: all scenarios covering the startup path "
            "exercise structured logging. FR-2 remains the bottleneck.\n"
            '<!-- CLASSIFICATIONS: {"AC-1": "IMPL_BUG"} -->'
        )
        result = validate_oracle_feedback(fb, al)
        self.assertTrue(result.is_compliant)

    def test_no_false_positive_on_spec_refs(self) -> None:
        """Spec section references like FR-1/FR-2 should not trigger."""
        al = self.tmp_dir / "audit.log"
        fb = (
            "Focus on FR-1 and EC-3. The deployment config needs attention.\n"
            '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG", "EC-3": "SPEC_AMBIGUOUS"} -->'
        )
        result = validate_oracle_feedback(fb, al)
        self.assertTrue(result.is_compliant)

    def test_violation_summary(self) -> None:
        al = self.tmp_dir / "audit.log"
        result = validate_oracle_feedback(
            "64/73 tests passed in 01-contract.test.ts", al,
        )
        self.assertFalse(result.is_compliant)
        self.assertIn("barrier rule", result.violation_summary)

    def test_classifications_block_present_no_warning(self) -> None:
        al = self.tmp_dir / "audit.log"
        fb = 'Focus on FR-1.\n<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG"} -->'
        validate_oracle_feedback(fb, al)
        log_text = al.read_text() if al.exists() else ""
        self.assertNotIn("CLASSIFICATIONS block absent", log_text)

    def test_classifications_block_absent_is_violation(self) -> None:
        al = self.tmp_dir / "audit.log"
        fb = "Focus on FR-1 compliance. Re-read the spec."
        result = validate_oracle_feedback(fb, al)
        self.assertFalse(result.is_compliant)
        self.assertTrue(any("CLASSIFICATIONS" in v for v in result.violations))
        log_text = al.read_text()
        self.assertIn("CLASSIFICATIONS block absent", log_text)


if __name__ == "__main__":
    unittest.main()
