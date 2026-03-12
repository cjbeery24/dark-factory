"""Tests for intelligence/trust.py."""

import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase
from darkfactory.intelligence.trust import (
    _avg_iterations_score,
    _first_pass_rate,
    _module_familiarity,
    _regression_fail_rate,
    _spec_complexity_score,
    calculate_trust,
    predict_iterations,
    trust_to_autonomy,
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

    def test_low_trust_many_failures(self) -> None:
        ef = self._make_events(2, failed=8, iterations=1)
        config = PipelineConfig(trust_min_history=5)
        score = calculate_trust(ef, config)
        self.assertFalse(score.cold_start)
        # Many failures → lower trust than all-shipped.
        all_shipped = calculate_trust(self._make_events(10, iterations=1), config)
        self.assertLess(score.total, all_shipped.total)


class TestTrustToAutonomy(unittest.TestCase):
    def test_high(self) -> None:
        self.assertEqual(trust_to_autonomy(60), 3)

    def test_medium(self) -> None:
        self.assertEqual(trust_to_autonomy(30), 2)

    def test_low(self) -> None:
        self.assertEqual(trust_to_autonomy(10), 1)


class TestFirstPassRate(unittest.TestCase):
    def test_all_first_pass(self) -> None:
        shipped = [{"data": {"iterations": 1}} for _ in range(5)]
        self.assertEqual(_first_pass_rate(shipped), 100)

    def test_mixed(self) -> None:
        shipped = [
            {"data": {"iterations": 1}},
            {"data": {"iterations": 2}},
            {"data": {"iterations": 1}},
            {"data": {"iterations": 3}},
        ]
        self.assertEqual(_first_pass_rate(shipped), 50)

    def test_none_first_pass(self) -> None:
        shipped = [{"data": {"iterations": 3}} for _ in range(4)]
        self.assertEqual(_first_pass_rate(shipped), 0)

    def test_empty(self) -> None:
        self.assertEqual(_first_pass_rate([]), 0)


class TestAvgIterationsScore(unittest.TestCase):
    def test_all_one_iteration(self) -> None:
        shipped = [{"data": {"iterations": 1}} for _ in range(5)]
        # avg=1, rounded=1, score = (5-1)*25 = 100
        self.assertEqual(_avg_iterations_score(shipped), 100)

    def test_clamped_at_5(self) -> None:
        shipped = [{"data": {"iterations": 10}} for _ in range(5)]
        # avg=10, clamped to 5, score = (5-5)*25 = 0
        self.assertEqual(_avg_iterations_score(shipped), 0)

    def test_empty(self) -> None:
        self.assertEqual(_avg_iterations_score([]), 0)


class TestRegressionFailRate(unittest.TestCase):
    def test_no_regressions(self) -> None:
        # No regression events = perfect score.
        self.assertEqual(_regression_fail_rate([]), 100)

    def test_all_pass(self) -> None:
        records = [{"event": "REGRESSION_RUN", "data": {"passed": 10, "total": 10}}]
        self.assertEqual(_regression_fail_rate(records), 100)

    def test_some_failures(self) -> None:
        records = [{"event": "REGRESSION_RUN", "data": {"passed": 8, "total": 10}}]
        self.assertEqual(_regression_fail_rate(records), 80)


class TestSpecComplexityScore(unittest.TestCase):
    def test_zero_complexity(self) -> None:
        self.assertEqual(_spec_complexity_score(0, 0), 100)

    def test_scaling(self) -> None:
        # 5 files * 10 + 2 deps * 20 = 90, score = 100-90 = 10
        self.assertEqual(_spec_complexity_score(5, 2), 10)

    def test_floor_at_zero(self) -> None:
        self.assertEqual(_spec_complexity_score(20, 10), 0)


class TestModuleFamiliarity(unittest.TestCase):
    def test_cap_at_10(self) -> None:
        shipped = [{}] * 15
        self.assertEqual(_module_familiarity(shipped), 100)

    def test_few_shipped(self) -> None:
        shipped = [{}] * 3
        self.assertEqual(_module_familiarity(shipped), 30)


class TestPredictIterations(TempDirTestCase):
    def test_fallback_on_insufficient_data(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        config = PipelineConfig(trust_min_history=5, max_iterations=3)
        self.assertEqual(predict_iterations(ef, config), 3)

    def test_with_sufficient_history(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        for _ in range(10):
            append_jsonl(ef, {
                "event": "SPEC_SHIPPED",
                "data": {"iterations": 2, "total_turns": 50},
            })
        config = PipelineConfig(trust_min_history=5, max_iterations=5)
        result = predict_iterations(ef, config)
        self.assertEqual(result, 2)  # avg=2, rounded=2

    def test_bad_data_zero_iterations(self) -> None:
        """Events with iterations=0 should return 1 (clamped by max(1, ...))."""
        ef = self.tmp_dir / "events.jsonl"
        for _ in range(10):
            append_jsonl(ef, {
                "event": "SPEC_SHIPPED",
                "data": {"iterations": 0, "total_turns": 50},
            })
        config = PipelineConfig(trust_min_history=5, max_iterations=5)
        result = predict_iterations(ef, config)
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
