"""Wiring contract tests — verify function signatures match call sites.

Each test exercises a specific Tier 1-4 fix by calling the function with
the corrected argument list. If the signature changes, these tests fail.
Tests for detailed behavior belong in the module-level test files.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.helpers import TempDirTestCase
from darkfactory.audit.checker import validate_oracle_feedback
from darkfactory.events.types import emit_cost_summary, emit_trust_scored
from darkfactory.intelligence.cost import estimate_cost
from darkfactory.intelligence.trust import calculate_trust, trust_to_autonomy
from darkfactory.models.config import PipelineConfig
from darkfactory.models.cost import CostEstimate
from darkfactory.notify.dispatcher import notify_shipped, notify_escalated, notify_failed
from darkfactory.utils.json_helpers import append_jsonl, read_jsonl


class TestNotifySignatures(unittest.TestCase):
    """Verify notify functions accept the corrected argument lists."""

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_shipped_5_args(self, mock_send: MagicMock) -> None:
        config = PipelineConfig()
        notify_shipped(config, "my-spec", "run1", 2, 75)
        mock_send.assert_called_once()

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_escalated_4_args(self, mock_send: MagicMock) -> None:
        config = PipelineConfig()
        notify_escalated(config, "my-spec", "run1", 3)
        mock_send.assert_called_once()

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_failed_4_args(self, mock_send: MagicMock) -> None:
        config = PipelineConfig()
        notify_failed(config, "my-spec", "run1", "budget exceeded")
        mock_send.assert_called_once()


class TestCostSummaryType(TempDirTestCase):
    """Verify estimate_cost().estimated_usd yields float for emit_cost_summary."""

    def test_estimated_usd_is_float(self) -> None:
        config = PipelineConfig()
        est = estimate_cost(100, config)
        self.assertIsInstance(est, CostEstimate)
        self.assertIsInstance(est.estimated_usd, float)
        ef = self.tmp_dir / "events.jsonl"
        emit_cost_summary(
            ef, spec="s", run_id="r",
            total_turns=100, estimated_usd=est.estimated_usd,
        )


class TestOracleFeedbackValidation(TempDirTestCase):
    """Verify validate_oracle_feedback(str, Path) signature."""

    def test_clean_feedback(self) -> None:
        audit_path = self.tmp_dir / "audit.log"
        leaks = validate_oracle_feedback("SHIP\nLooks good.", audit_path)
        self.assertEqual(leaks, [])

    def test_detects_leakage_patterns(self) -> None:
        audit_path = self.tmp_dir / "audit.log"
        feedback = 'NO-SHIP\nFix 01-login.test.js assertion expect(result).toBe(true)'
        leaks = validate_oracle_feedback(feedback, audit_path)
        self.assertGreater(len(leaks), 0)

    def test_leakage_logged_to_audit(self) -> None:
        audit_path = self.tmp_dir / "audit.log"
        feedback = 'NO-SHIP\nFix 01-login.test.js to pass'
        validate_oracle_feedback(feedback, audit_path)
        self.assertTrue(audit_path.exists())
        content = audit_path.read_text()
        self.assertIn("SEMANTIC LEAKAGE", content)


class TestTrustToAutonomyWiring(TempDirTestCase):
    """Verify trust.total feeds into trust_to_autonomy (not trust.overall)."""

    def test_trust_total_field_exists(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        for _ in range(10):
            append_jsonl(ef, {
                "event": "SPEC_SHIPPED",
                "data": {"iterations": 1, "total_turns": 50},
            })
        config = PipelineConfig(trust_min_history=5)
        trust = calculate_trust(ef, config)
        # This line mirrors the orchestrator wiring — would AttributeError if .overall
        autonomy = trust_to_autonomy(trust.total)
        self.assertIn(autonomy, (1, 2, 3))


class TestEmitTrustScored(TempDirTestCase):
    """Verify emit_trust_scored writes correct event structure."""

    def test_event_structure(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_trust_scored(ef, spec="s", run_id="r", trust=75, autonomy=3)
        records = read_jsonl(ef)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["event"], "TRUST_SCORED")
        self.assertEqual(records[0]["data"]["trust"], 75)
        self.assertEqual(records[0]["data"]["autonomy"], 3)


if __name__ == "__main__":
    unittest.main()
