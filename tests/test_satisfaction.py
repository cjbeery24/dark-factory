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
