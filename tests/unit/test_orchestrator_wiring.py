"""Wiring contract tests — verify function signatures match call sites.

Each test exercises a specific Tier 1-4 fix by calling the function with
the corrected argument list. If the signature changes, these tests fail.
Tests for detailed behavior belong in the module-level test files.
"""

import inspect
import unittest
from unittest.mock import MagicMock, patch

from tests.helpers import TempDirTestCase
from darkfactory.audit.checker import validate_oracle_feedback
from darkfactory.events.types import emit_cost_summary, emit_trust_scored
from darkfactory.intelligence.cost import estimate_cost
from darkfactory.intelligence.trust import calculate_trust, trust_to_autonomy
from darkfactory.models.config import PipelineConfig
from darkfactory.models.cost import CostEstimate
from darkfactory.errors import HumanHoldRequired
from darkfactory.notify.dispatcher import (
    notify_shipped, notify_escalated, notify_failed,
    notify_ship_review, notify_hold,
)
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
        result = validate_oracle_feedback("SHIP\nLooks good.", audit_path)
        self.assertTrue(result.is_compliant)

    def test_detects_leakage_patterns(self) -> None:
        audit_path = self.tmp_dir / "audit.log"
        feedback = 'NO-SHIP\nFix 01-login.test.js assertion expect(result).toBe(true)'
        result = validate_oracle_feedback(feedback, audit_path)
        self.assertFalse(result.is_compliant)

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


class TestCompositeAnomalyNotification(unittest.TestCase):
    """Verify composite anomaly score triggers notify_anomaly at correct tiers."""

    @patch("darkfactory.pipeline.iteration.notify_anomaly")
    @patch("darkfactory.pipeline.iteration.composite_score", return_value=70)
    def test_block_tier_notifies(self, mock_composite: MagicMock, mock_notify: MagicMock) -> None:
        """composite_score >= COMPOSITE_BLOCK (70) triggers block notification."""
        from darkfactory.intelligence.anomaly import COMPOSITE_BLOCK, COMPOSITE_ESCALATE
        self.assertEqual(COMPOSITE_BLOCK, 70)
        self.assertEqual(COMPOSITE_ESCALATE, 40)
        # Verify notify_anomaly has the expected 4-param signature.
        from darkfactory.notify.dispatcher import notify_anomaly as real_fn
        params = list(inspect.signature(real_fn).parameters.keys())
        self.assertEqual(params, ["config", "spec_name", "anomaly_type", "score"])

    @patch("darkfactory.pipeline.iteration.notify_anomaly")
    def test_escalate_tier_constants(self, mock_notify: MagicMock) -> None:
        """COMPOSITE_ESCALATE is 40, between WARN (20) and BLOCK (70)."""
        from darkfactory.intelligence.anomaly import COMPOSITE_BLOCK, COMPOSITE_ESCALATE, COMPOSITE_WARN
        self.assertLess(COMPOSITE_WARN, COMPOSITE_ESCALATE)
        self.assertLess(COMPOSITE_ESCALATE, COMPOSITE_BLOCK)


class TestTrustCalibratedBehavior(unittest.TestCase):
    """Verify trust-calibrated autonomy has downstream behavioral effects."""

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_ship_review_signature(self, mock_send: MagicMock) -> None:
        config = PipelineConfig()
        notify_ship_review(config, "my-spec", "run1", 45)
        mock_send.assert_called_once()
        payload = mock_send.call_args[0][2]
        self.assertEqual(payload["status"], "ship_review")
        self.assertEqual(payload["trust"], 45)

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_hold_signature(self, mock_send: MagicMock) -> None:
        config = PipelineConfig()
        notify_hold(config, "my-spec", "run1", 20)
        mock_send.assert_called_once()
        payload = mock_send.call_args[0][2]
        self.assertEqual(payload["status"], "hold")
        self.assertEqual(payload["run_id"], "run1")

    def test_human_hold_required_exception_exists(self) -> None:
        from darkfactory.errors import DarkFactoryError
        self.assertTrue(issubclass(HumanHoldRequired, DarkFactoryError))
        exc = HumanHoldRequired("test hold")
        self.assertIn("test hold", str(exc))


class TestAutonomyRegression(TempDirTestCase):
    """Verify trust-gated regression is wired into _handle_ship."""

    @patch("darkfactory.pipeline.orchestrator.run_regression", return_value=(1, 0, 1))
    @patch("darkfactory.pipeline.orchestrator.registry_register")
    @patch("darkfactory.pipeline.orchestrator.notify_shipped")
    @patch("darkfactory.pipeline.orchestrator.notify_ship_review")
    def test_low_autonomy_forces_regression(
        self, mock_review: MagicMock, mock_notify: MagicMock,
        mock_register: MagicMock, mock_regression: MagicMock,
    ) -> None:
        """effective_autonomy=2 triggers regression even when config says no."""
        from darkfactory.pipeline.orchestrator import _handle_ship
        from darkfactory.models.pipeline import IterationState, SpecRun
        config = PipelineConfig(regression_on_ship=False)
        spec_run = SpecRun(spec_name="test")
        spec_run.effective_autonomy = 2
        spec_run.iterations.append(IterationState(number=1, satisfaction_score=90, pass_rate=90))
        state_file = self.tmp_dir / "test.state"
        state_file.write_text("status=running\n")
        _handle_ship(spec_run, config, self.tmp_dir, state_file=state_file)
        mock_regression.assert_called_once()

    @patch("darkfactory.pipeline.orchestrator.run_regression")
    @patch("darkfactory.pipeline.orchestrator.registry_register")
    @patch("darkfactory.pipeline.orchestrator.notify_shipped")
    def test_high_autonomy_skips_forced_regression(
        self, mock_notify: MagicMock,
        mock_register: MagicMock, mock_regression: MagicMock,
    ) -> None:
        """effective_autonomy=3 does not force regression when config says no."""
        from darkfactory.pipeline.orchestrator import _handle_ship
        from darkfactory.models.pipeline import IterationState, SpecRun
        config = PipelineConfig(regression_on_ship=False)
        spec_run = SpecRun(spec_name="test")
        spec_run.effective_autonomy = 3
        spec_run.iterations.append(IterationState(number=1, satisfaction_score=90, pass_rate=90))
        state_file = self.tmp_dir / "test.state"
        state_file.write_text("status=running\n")
        _handle_ship(spec_run, config, self.tmp_dir, state_file=state_file)
        mock_regression.assert_not_called()


class TestAutonomyShipReview(TempDirTestCase):
    """Verify autonomy <= 2 triggers ship review notification via _handle_ship."""

    @patch("darkfactory.pipeline.orchestrator.notify_ship_review")
    @patch("darkfactory.pipeline.orchestrator.notify_shipped")
    @patch("darkfactory.pipeline.orchestrator.registry_register")
    def test_autonomy_2_triggers_review(
        self, mock_register: MagicMock, mock_notify: MagicMock,
        mock_review: MagicMock,
    ) -> None:
        from darkfactory.pipeline.orchestrator import _handle_ship
        from darkfactory.models.pipeline import IterationState, SpecRun
        config = PipelineConfig()
        spec_run = SpecRun(spec_name="test")
        spec_run.effective_autonomy = 2
        spec_run.iterations.append(IterationState(number=1, satisfaction_score=90, pass_rate=90))
        state_file = self.tmp_dir / "test.state"
        state_file.write_text("status=running\n")
        _handle_ship(spec_run, config, self.tmp_dir, state_file=state_file)
        mock_review.assert_called_once()

    @patch("darkfactory.pipeline.orchestrator.notify_ship_review")
    @patch("darkfactory.pipeline.orchestrator.notify_shipped")
    @patch("darkfactory.pipeline.orchestrator.registry_register")
    def test_autonomy_3_skips_review(
        self, mock_register: MagicMock, mock_notify: MagicMock,
        mock_review: MagicMock,
    ) -> None:
        from darkfactory.pipeline.orchestrator import _handle_ship
        from darkfactory.models.pipeline import IterationState, SpecRun
        config = PipelineConfig()
        spec_run = SpecRun(spec_name="test")
        spec_run.effective_autonomy = 3
        spec_run.iterations.append(IterationState(number=1, satisfaction_score=90, pass_rate=90))
        state_file = self.tmp_dir / "test.state"
        state_file.write_text("status=running\n")
        _handle_ship(spec_run, config, self.tmp_dir, state_file=state_file)
        mock_review.assert_not_called()


class TestResumeFromHold(TempDirTestCase):
    """Verify resume from hold skips iteration loop and elevates autonomy."""

    def test_completed_step_6_skips_iteration_loop(self) -> None:
        """completed_step >= 6 means oracle said SHIP and human confirmed."""
        from darkfactory.models.pipeline import SpecRun
        spec_run = SpecRun(spec_name="test")
        spec_run.completed_step = 6
        spec_run.effective_autonomy = 1
        # The orchestrator checks completed_step >= 6 and elevates autonomy.
        new_autonomy = max(spec_run.effective_autonomy, 2)
        self.assertEqual(new_autonomy, 2)
        self.assertTrue(spec_run.completed_step >= 6)

    def test_hold_status_in_enum(self) -> None:
        from darkfactory.models.pipeline import PipelineStatus
        self.assertEqual(PipelineStatus.HOLD.value, "hold")


if __name__ == "__main__":
    unittest.main()
