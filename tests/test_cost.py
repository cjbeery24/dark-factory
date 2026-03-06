"""Tests for intelligence/cost.py."""

import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase
from darkfactory.errors import BudgetExceeded
from darkfactory.intelligence.cost import estimate_cost, check_budget
from darkfactory.models.config import PipelineConfig


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


if __name__ == "__main__":
    unittest.main()
