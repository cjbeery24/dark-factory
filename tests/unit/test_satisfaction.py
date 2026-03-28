"""Tests for scenarios/satisfaction.py — weighted scoring with integer arithmetic."""

import unittest

from darkfactory.models.config import PipelineConfig
from darkfactory.scenarios.satisfaction import compute_satisfaction, multi_run_file


class TestSatisfaction(unittest.TestCase):
    def test_perfect_score(self) -> None:
        config = PipelineConfig()
        results = {
            "01-contract.test.ts": (5, 5),
            "02-happy.test.ts": (3, 3),
            "06-security.test.ts": (2, 2),
        }
        score = compute_satisfaction(results, config)
        self.assertEqual(score.total_score, 100)
        self.assertEqual(score.pass_rate, 100)
        self.assertEqual(score.total_passed, 10)

    def test_partial_score(self) -> None:
        config = PipelineConfig()
        results = {
            "01-contract.test.ts": (3, 5),   # 60%
            "06-security.test.ts": (1, 2),   # 50%
        }
        score = compute_satisfaction(results, config)
        # contract: weight=100, score=60
        # security: weight=120, score=50
        # total = (100*60 + 120*50) // (100+120) = (6000+6000) // 220 = 12000//220 = 54
        self.assertEqual(score.total_score, 54)
        self.assertEqual(score.total_passed, 4)
        self.assertEqual(score.total_failed, 3)

    def test_zero_scenarios(self) -> None:
        config = PipelineConfig()
        score = compute_satisfaction({}, config)
        self.assertEqual(score.total_score, 0)
        self.assertEqual(score.pass_rate, 0)

    def test_integer_arithmetic(self) -> None:
        """Verify floor division matches bash $(( )) behavior."""
        config = PipelineConfig()
        results = {
            "01-contract.test.ts": (1, 3),  # 33% (floor)
        }
        score = compute_satisfaction(results, config)
        # 1*100//3 = 33, not 33.33
        cat = score.categories[0]
        self.assertEqual(cat.score, 33)

    def test_unknown_category(self) -> None:
        config = PipelineConfig()
        results = {
            "custom-test.ts": (2, 3),
        }
        score = compute_satisfaction(results, config)
        self.assertEqual(len(score.categories), 1)
        self.assertEqual(score.categories[0].name, "unknown")


class TestCrashAdjustedSatisfaction(unittest.TestCase):
    """Tests for compute_satisfaction with crashed_per_file."""

    def test_no_crashes_unchanged(self) -> None:
        config = PipelineConfig()
        results = {"01-contract.test.ts": (3, 5)}
        score_without = compute_satisfaction(results, config)
        score_with = compute_satisfaction(results, config, crashed_per_file={})
        self.assertEqual(score_without.total_score, score_with.total_score)
        self.assertEqual(score_without.pass_rate, score_with.pass_rate)

    def test_crashes_excluded_from_denominator(self) -> None:
        config = PipelineConfig()
        # 3 passed, 2 total failed (5 total). 1 of the failures is a crash.
        results = {"01-contract.test.ts": (3, 5)}
        score = compute_satisfaction(
            results, config, crashed_per_file={"01-contract.test.ts": 1},
        )
        # effective total = 5-1 = 4, passed=3, score = 3*100//4 = 75
        cat = score.categories[0]
        self.assertEqual(cat.total, 4)
        self.assertEqual(cat.passed, 3)
        self.assertEqual(cat.score, 75)

    def test_all_crashed_yields_empty_category(self) -> None:
        config = PipelineConfig()
        # 0 passed, 3 total, all 3 crashed → adjusted total = 0
        results = {"01-contract.test.ts": (0, 3)}
        score = compute_satisfaction(
            results, config, crashed_per_file={"01-contract.test.ts": 3},
        )
        # Category has 0 total so it's excluded from weighted scoring
        self.assertEqual(score.total_score, 0)
        self.assertEqual(score.total_scenarios, 0)

    def test_crash_in_one_category_only(self) -> None:
        config = PipelineConfig()
        results = {
            "01-contract.test.ts": (2, 5),   # 2 crashes → effective 3, passed 2
            "06-security.test.ts": (2, 2),   # no crashes → score 100
        }
        score = compute_satisfaction(
            results, config,
            crashed_per_file={"01-contract.test.ts": 2},
        )
        # contract: 2/3 = 66, weight=100
        # security: 2/2 = 100, weight=120
        # total = (100*66 + 120*100) // (100+120) = (6600+12000) // 220 = 84
        self.assertEqual(score.total_score, 84)

    def test_passed_clamped_to_adjusted_total(self) -> None:
        """If crashed > failed (inconsistent data), passed is clamped."""
        config = PipelineConfig()
        # 4 passed, 5 total, but 3 crashed (only 1 truly failed)
        # adjusted_total = 5-3 = 2, adjusted_passed = min(4, 2) = 2
        results = {"01-contract.test.ts": (4, 5)}
        score = compute_satisfaction(
            results, config, crashed_per_file={"01-contract.test.ts": 3},
        )
        cat = score.categories[0]
        self.assertEqual(cat.total, 2)
        self.assertEqual(cat.passed, 2)
        self.assertEqual(cat.score, 100)


class TestMultiRun(unittest.TestCase):
    def test_all_pass(self) -> None:
        self.assertTrue(multi_run_file(lambda f: True, "test.ts", 3, 2))

    def test_all_fail(self) -> None:
        self.assertFalse(multi_run_file(lambda f: False, "test.ts", 3, 2))

    def test_exact_threshold(self) -> None:
        calls = [0]
        def run_fn(f: str) -> bool:
            calls[0] += 1
            return calls[0] <= 2  # First 2 pass, 3rd fails.
        self.assertTrue(multi_run_file(run_fn, "test.ts", 3, 2))

    def test_early_exit_on_success(self) -> None:
        calls = [0]
        def run_fn(f: str) -> bool:
            calls[0] += 1
            return True
        multi_run_file(run_fn, "test.ts", 5, 2)
        self.assertEqual(calls[0], 2)  # Stopped after 2 passes.

    def test_early_exit_on_failure(self) -> None:
        calls = [0]
        def run_fn(f: str) -> bool:
            calls[0] += 1
            return False
        multi_run_file(run_fn, "test.ts", 5, 4)
        # max_failures = 5 - 4 + 1 = 2, so stops after 2 failures.
        self.assertEqual(calls[0], 2)


if __name__ == "__main__":
    unittest.main()
