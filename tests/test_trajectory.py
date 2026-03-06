"""Tests for scenarios/trajectory.py — classification and layer analysis."""

import unittest

from darkfactory.models.satisfaction import CategoryResult, TrajectoryType
from darkfactory.scenarios.trajectory import (
    classify_trajectory,
    build_layer_trajectories,
    strategy_recommendation,
    build_oracle_analysis,
)


class TestTrajectoryClassification(unittest.TestCase):
    def test_insufficient(self) -> None:
        self.assertEqual(classify_trajectory([80]), TrajectoryType.INSUFFICIENT)

    def test_improving(self) -> None:
        self.assertEqual(classify_trajectory([50, 70, 90]), TrajectoryType.IMPROVING)

    def test_degrading(self) -> None:
        self.assertEqual(classify_trajectory([90, 70, 50]), TrajectoryType.DEGRADING)

    def test_plateau(self) -> None:
        self.assertEqual(classify_trajectory([80, 82, 79]), TrajectoryType.PLATEAU)

    def test_oscillating(self) -> None:
        self.assertEqual(classify_trajectory([50, 80, 50, 80]), TrajectoryType.OSCILLATING)
        # 3-point oscillation matches bash behavior.
        self.assertEqual(classify_trajectory([50, 70, 50]), TrajectoryType.OSCILLATING)

    def test_recovering(self) -> None:
        # Needs non-alternating pairs so oscillating doesn't trigger first.
        # [80,70,60,70]: deltas [-10,-10,10] — not all pairs alternate,
        # last delta positive with previous negative → RECOVERING.
        self.assertEqual(classify_trajectory([80, 70, 60, 70]), TrajectoryType.RECOVERING)

    def test_mixed(self) -> None:
        # Up-up-down — not all pairs alternate, last delta negative → MIXED.
        self.assertEqual(classify_trajectory([50, 70, 80, 60]), TrajectoryType.MIXED)
        # Down-down-up-down — not recovering (last negative), not oscillating.
        self.assertEqual(classify_trajectory([80, 70, 60, 70, 50]), TrajectoryType.MIXED)

    def test_empty(self) -> None:
        self.assertEqual(classify_trajectory([]), TrajectoryType.INSUFFICIENT)


class TestLayerTrajectories(unittest.TestCase):
    def test_builds_trajectories(self) -> None:
        iter1 = [
            CategoryResult(name="contract", passed=5, total=5, score=100),
            CategoryResult(name="security", passed=0, total=3, score=0),
        ]
        iter2 = [
            CategoryResult(name="contract", passed=5, total=5, score=100),
            CategoryResult(name="security", passed=0, total=3, score=0),
        ]
        layers = build_layer_trajectories([iter1, iter2])
        by_name = {lt.category: lt for lt in layers}
        self.assertEqual(by_name["contract"].scores, [100, 100])
        self.assertFalse(by_name["contract"].stuck)
        self.assertEqual(by_name["security"].scores, [0, 0])
        self.assertTrue(by_name["security"].stuck)


class TestStrategyRecommendation(unittest.TestCase):
    def test_all_types_have_recommendation(self) -> None:
        for t in TrajectoryType:
            rec = strategy_recommendation(t)
            self.assertTrue(len(rec) > 0, f"no recommendation for {t}")


class TestOracleAnalysis(unittest.TestCase):
    def test_builds_analysis(self) -> None:
        from darkfactory.scenarios.trajectory import LayerTrajectory
        layers = [
            LayerTrajectory(category="contract", scores=[100, 100]),
            LayerTrajectory(category="security", scores=[0, 0], stuck=True),
        ]
        analysis = build_oracle_analysis(
            scores=[60, 70],
            layer_trajectories=layers,
            trajectory=TrajectoryType.IMPROVING,
            config_weights={"contract": 100, "security": 120},
        )
        self.assertIn("IMPROVING", analysis)
        self.assertIn("STUCK", analysis)
        self.assertIn("security", analysis)


if __name__ == "__main__":
    unittest.main()
