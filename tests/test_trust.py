"""Tests for intelligence/trust.py."""

import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase
from darkfactory.intelligence.trust import (
    calculate_trust, trust_to_autonomy, predict_iterations,
)
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.json_helpers import append_jsonl


class TestTrust(TempDirTestCase):
    def _make_events(self, shipped: int, failed: int = 0, iterations: int = 1) -> Path:
        ef = self.tmp_dir / "events.jsonl"
        for _ in range(shipped):
            append_jsonl(ef, {
                "event": "SPEC_SHIPPED",
                "data": {"iterations": iterations, "total_turns": 50},
            })
        for _ in range(failed):
            append_jsonl(ef, {
                "event": "SPEC_FAILED",
                "data": {"total_turns": 50},
            })
        return ef

    def test_cold_start(self) -> None:
        ef = self._make_events(2)
        config = PipelineConfig(trust_min_history=5)
        score = calculate_trust(ef, config)
        self.assertEqual(score.total, 50)
        self.assertTrue(score.cold_start)

    def test_sufficient_history(self) -> None:
        ef = self._make_events(10, iterations=1)
        config = PipelineConfig(trust_min_history=5)
        score = calculate_trust(ef, config)
        self.assertFalse(score.cold_start)
        self.assertGreater(score.total, 0)

    def test_high_trust_first_pass(self) -> None:
        ef = self._make_events(10, iterations=1)
        config = PipelineConfig(trust_min_history=5)
        score = calculate_trust(ef, config)
        # All first-pass → high first_pass_rate dimension.
        self.assertEqual(score.dimensions.first_pass_rate, 100)


class TestTrustToAutonomy(unittest.TestCase):
    def test_high(self) -> None:
        self.assertEqual(trust_to_autonomy(60), 3)

    def test_medium(self) -> None:
        self.assertEqual(trust_to_autonomy(30), 2)

    def test_low(self) -> None:
        self.assertEqual(trust_to_autonomy(10), 1)


class TestPredictIterations(TempDirTestCase):
    def test_fallback_on_insufficient_data(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        config = PipelineConfig(trust_min_history=5, max_iterations=3)
        self.assertEqual(predict_iterations(ef, config), 3)


if __name__ == "__main__":
    unittest.main()
