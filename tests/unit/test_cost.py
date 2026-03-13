"""Tests for intelligence/cost.py."""

import unittest
from unittest.mock import patch, MagicMock

from tests.helpers import TempDirTestCase
from darkfactory.errors import BudgetExceeded
from darkfactory.intelligence.cost import estimate_cost, check_budget, emit_spec_cost_summary
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.json_helpers import read_jsonl


class TestEstimateCost(unittest.TestCase):
    def test_basic_estimate(self) -> None:
        config = PipelineConfig()
        est = estimate_cost(100, config)
        # 100 * (4000 * 0.000015 + 2000 * 0.000075) = 100 * (0.06 + 0.15) = 21.0
        self.assertAlmostEqual(est.estimated_usd, 21.0)
        self.assertEqual(est.turns, 100)

    def test_zero_turns(self) -> None:
        config = PipelineConfig()
        est = estimate_cost(0, config)
        self.assertEqual(est.estimated_usd, 0.0)

    def test_custom_token_rates(self) -> None:
        config = PipelineConfig(
            cost_est_input_tokens_per_turn=8000,
            cost_est_output_tokens_per_turn=4000,
        )
        est = estimate_cost(10, config)
        # 10 * (8000 * 0.000015 + 4000 * 0.000075) = 10 * (0.12 + 0.30) = 4.2
        self.assertAlmostEqual(est.estimated_usd, 4.2)

    def test_returns_token_counts(self) -> None:
        config = PipelineConfig()
        est = estimate_cost(5, config)
        self.assertEqual(est.input_tokens, 5 * 4000)
        self.assertEqual(est.output_tokens, 5 * 2000)


class TestCheckBudget(TempDirTestCase):
    def test_no_budget(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        config = PipelineConfig(cost_budget_usd=0.0)
        status = check_budget(100, config, ef, "s", "r")
        self.assertFalse(status.exceeded)

    def test_budget_exceeded(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        config = PipelineConfig(cost_budget_usd=1.0)
        with self.assertRaises(BudgetExceeded):
            check_budget(1000, config, ef, "s", "r")

    def test_all_5_args_required(self) -> None:
        """Verify check_budget with all 5 positional args."""
        ef = self.tmp_dir / "events.jsonl"
        config = PipelineConfig(cost_budget_usd=100.0)
        status = check_budget(10, config, ef, "my-spec", "run-123")
        self.assertFalse(status.exceeded)
        self.assertGreater(status.remaining_usd, 0)

    def test_alert_dedup(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        config = PipelineConfig(cost_budget_usd=100.0)
        alerted: set[int] = set()
        # 50 turns ≈ $10.50 = 10.5% of $100.
        check_budget(50, config, ef, "s", "r", alerted=alerted)
        self.assertEqual(len(alerted), 0)  # Below 50% threshold.

        # 300 turns ≈ $63 = 63%.
        check_budget(300, config, ef, "s", "r", alerted=alerted)
        self.assertIn(50, alerted)

    def test_alert_threshold_80(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        config = PipelineConfig(cost_budget_usd=100.0, cost_alert_thresholds=[50, 80, 100])
        alerted: set[int] = set()
        # 400 turns ≈ $84 = 84%
        check_budget(400, config, ef, "s", "r", alerted=alerted)
        self.assertIn(50, alerted)
        self.assertIn(80, alerted)

    def test_alerted_set_prevents_duplicate_alerts(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        config = PipelineConfig(cost_budget_usd=100.0, cost_alert_thresholds=[50, 80, 100])
        alerted: set[int] = set()
        check_budget(300, config, ef, "s", "r", alerted=alerted)
        self.assertIn(50, alerted)
        # Call again — 50% should NOT re-fire
        with patch("darkfactory.intelligence.cost.emit_budget_alert") as mock_alert:
            check_budget(300, config, ef, "s", "r", alerted=alerted)
            mock_alert.assert_not_called()

    @patch("darkfactory.intelligence.cost.notify_budget_alert")
    def test_notify_budget_alert_called(self, mock_notify: MagicMock) -> None:
        ef = self.tmp_dir / "events.jsonl"
        config = PipelineConfig(cost_budget_usd=100.0, cost_alert_thresholds=[50, 80, 100])
        alerted: set[int] = set()
        # 300 turns ≈ $63 → triggers 50% alert
        check_budget(300, config, ef, "s", "r", alerted=alerted)
        mock_notify.assert_called_once()
        args = mock_notify.call_args[0]
        self.assertEqual(args[0], config)
        self.assertEqual(args[1], "s")


class TestEmitSpecCostSummary(TempDirTestCase):
    def test_emits_event(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        config = PipelineConfig()
        emit_spec_cost_summary(ef, "my-spec", "run1", 100, config)
        records = read_jsonl(ef)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["event"], "COST_SUMMARY")
        self.assertAlmostEqual(records[0]["data"]["estimated_usd"], 21.0)


if __name__ == "__main__":
    unittest.main()
