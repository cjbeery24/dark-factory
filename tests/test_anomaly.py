"""Tests for intelligence/anomaly.py."""

import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase
from darkfactory.intelligence.anomaly import (
    check_agent_turns, check_spec_limits, compute_baseline,
    composite_score, COMPOSITE_BLOCK, COMPOSITE_WARN,
)
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.json_helpers import append_jsonl


class TestAgentTurns(TempDirTestCase):
    def test_within_limit(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        config = PipelineConfig(anomaly_max_agent_turns=150)
        self.assertFalse(check_agent_turns(100, config, ef, "s", "r"))

    def test_exceeds_limit(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        config = PipelineConfig(anomaly_max_agent_turns=150)
        self.assertTrue(check_agent_turns(200, config, ef, "s", "r"))


class TestSpecLimits(TempDirTestCase):
    def test_within_limits(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        config = PipelineConfig()
        self.assertFalse(check_spec_limits(100, 300.0, config, ef, "s", "r"))

    def test_turns_exceeded(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        config = PipelineConfig(anomaly_max_spec_turns=800)
        self.assertTrue(check_spec_limits(900, 300.0, config, ef, "s", "r"))

    def test_wall_exceeded(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        config = PipelineConfig(anomaly_max_spec_wall_s=7200)
        self.assertTrue(check_spec_limits(100, 8000.0, config, ef, "s", "r"))


class TestBaseline(TempDirTestCase):
    def test_insufficient_data(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        mean, stddev = compute_baseline(ef)
        self.assertEqual(mean, 0.0)
        self.assertEqual(stddev, 0.0)

    def test_with_data(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        for turns in [50, 60, 70, 80, 90]:
            append_jsonl(ef, {
                "event": "SPEC_SHIPPED",
                "data": {"total_turns": turns},
            })
        mean, stddev = compute_baseline(ef)
        self.assertAlmostEqual(mean, 70.0)
        self.assertGreater(stddev, 0)


class TestCompositeScore(unittest.TestCase):
    def test_no_anomalies(self) -> None:
        self.assertEqual(composite_score(
            turns_anomalous=False, wall_anomalous=False,
            trajectory_bad=False, files_anomalous=False,
            barrier_violation=False,
        ), 0)

    def test_barrier_alone_high(self) -> None:
        score = composite_score(
            turns_anomalous=False, wall_anomalous=False,
            trajectory_bad=False, files_anomalous=False,
            barrier_violation=True,
        )
        self.assertEqual(score, 40)

    def test_multiple_flags_block(self) -> None:
        score = composite_score(
            turns_anomalous=True, wall_anomalous=True,
            trajectory_bad=True, files_anomalous=False,
            barrier_violation=False,
        )
        # 30 + 20 + 25 = 75 >= COMPOSITE_BLOCK
        self.assertGreaterEqual(score, COMPOSITE_BLOCK)


if __name__ == "__main__":
    unittest.main()
