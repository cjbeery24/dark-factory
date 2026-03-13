"""Tests for intelligence/anomaly.py."""

import unittest

from tests.helpers import TempDirTestCase
from darkfactory.intelligence.anomaly import (
    check_agent_turns, check_baseline, check_spec_limits, compute_baseline,
    composite_score, COMPOSITE_BLOCK, COMPOSITE_ESCALATE, COMPOSITE_WARN,
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


class TestCheckBaseline(TempDirTestCase):
    def test_insufficient_data_returns_false(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        config = PipelineConfig()
        self.assertFalse(check_baseline(1000, ef, config))

    def test_within_baseline(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        for turns in [50, 60, 70, 80, 90]:
            append_jsonl(ef, {"event": "SPEC_SHIPPED", "data": {"total_turns": turns}})
        config = PipelineConfig()
        # 70 is the mean, 70 is well within 2σ
        self.assertFalse(check_baseline(70, ef, config))

    def test_exceeds_baseline(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        # All same value → stddev=0 → threshold=mean → anything above is anomalous
        for _ in range(5):
            append_jsonl(ef, {"event": "SPEC_SHIPPED", "data": {"total_turns": 50}})
        config = PipelineConfig()
        self.assertTrue(check_baseline(51, ef, config))

    def test_2sigma_threshold_with_variance(self) -> None:
        """Test with actual variance: mean=70, stddev≈14.14, threshold≈98.28."""
        ef = self.tmp_dir / "events.jsonl"
        for turns in [50, 60, 70, 80, 90]:
            append_jsonl(ef, {"event": "SPEC_SHIPPED", "data": {"total_turns": turns}})
        config = PipelineConfig()
        # 98 is below threshold ≈ 98.28 → not anomalous
        self.assertFalse(check_baseline(98, ef, config))
        # 99 is above threshold ≈ 98.28 → anomalous
        self.assertTrue(check_baseline(99, ef, config))

    def test_emits_anomaly_event(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        for _ in range(5):
            append_jsonl(ef, {"event": "SPEC_SHIPPED", "data": {"total_turns": 50}})
        config = PipelineConfig()
        from darkfactory.utils.json_helpers import read_jsonl
        before_count = len(read_jsonl(ef))
        check_baseline(100, ef, config, spec_name="s", run_id="r")
        after_count = len(read_jsonl(ef))
        self.assertGreater(after_count, before_count)


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

    def test_warn_range(self) -> None:
        score = composite_score(
            turns_anomalous=True, wall_anomalous=False,
            trajectory_bad=False, files_anomalous=False,
            barrier_violation=False,
        )
        # 30 → warn range (20-39)
        self.assertGreaterEqual(score, COMPOSITE_WARN)
        self.assertLess(score, COMPOSITE_ESCALATE)

    def test_escalate_range(self) -> None:
        score = composite_score(
            turns_anomalous=False, wall_anomalous=False,
            trajectory_bad=False, files_anomalous=False,
            barrier_violation=True,
        )
        # 40 → escalate range (40-69)
        self.assertGreaterEqual(score, COMPOSITE_ESCALATE)
        self.assertLess(score, COMPOSITE_BLOCK)

    def test_files_anomalous_contribution(self) -> None:
        base = composite_score(
            turns_anomalous=False, wall_anomalous=False,
            trajectory_bad=False, files_anomalous=False,
            barrier_violation=False,
        )
        with_files = composite_score(
            turns_anomalous=False, wall_anomalous=False,
            trajectory_bad=False, files_anomalous=True,
            barrier_violation=False,
        )
        self.assertEqual(with_files - base, 15)

    def test_all_flags_max_score(self) -> None:
        score = composite_score(
            turns_anomalous=True, wall_anomalous=True,
            trajectory_bad=True, files_anomalous=True,
            barrier_violation=True,
        )
        # 30 + 20 + 25 + 15 + 40 = 130
        self.assertEqual(score, 130)


if __name__ == "__main__":
    unittest.main()
