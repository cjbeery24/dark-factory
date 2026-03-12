"""Tests for darkfactory.deployment.progressive — canary orchestration."""

from __future__ import annotations

import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from darkfactory.deployment.models import (
    CanaryStage,
    DeploymentStatus,
    MetricThreshold,
)
from darkfactory.deployment.progressive import (
    DEFAULT_STAGES,
    _soak,
    _shift_traffic,
    run_canary,
)
from darkfactory.models.config import PipelineConfig


def _make_config(**kwargs) -> PipelineConfig:
    defaults = dict(
        canary_enabled=True,
        canary_shift_cmd="scripts/shift.sh",
        canary_rollback_cmd="scripts/rollback.sh",
    )
    defaults.update(kwargs)
    return PipelineConfig(**defaults)


class TestRunCanary(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.events_file = Path(self.tmpdir) / "events.jsonl"
        self.events_file.touch()

    @patch("darkfactory.deployment.progressive.collect_and_evaluate")
    @patch("darkfactory.deployment.progressive.run_command")
    def test_full_pass(self, mock_cmd, mock_eval):
        mock_cmd.return_value = MagicMock(returncode=0)
        mock_eval.return_value = ({}, [])
        config = _make_config()
        stages = [CanaryStage(percent=50, soak_seconds=0), CanaryStage(percent=100, soak_seconds=0)]
        thresholds = [MetricThreshold(name="err", max_value=0.05, metric_cmd="scripts/check.sh")]

        result = run_canary(
            config, "spec1", "run123", stages, thresholds,
            self.events_file, self.tmpdir,
        )
        self.assertEqual(result.status, DeploymentStatus.PROMOTED)
        self.assertEqual(result.stages_completed, 2)

    @patch("darkfactory.deployment.progressive.collect_and_evaluate")
    @patch("darkfactory.deployment.progressive.run_command")
    def test_threshold_breach_at_first_stage(self, mock_cmd, mock_eval):
        mock_cmd.return_value = MagicMock(returncode=0)
        mock_eval.return_value = ({"err": 0.10}, ["err"])
        config = _make_config()
        stages = [CanaryStage(percent=10, soak_seconds=0)]
        thresholds = [MetricThreshold(name="err", max_value=0.05, metric_cmd="scripts/check.sh")]

        result = run_canary(
            config, "spec1", "run123", stages, thresholds,
            self.events_file, self.tmpdir,
        )
        self.assertEqual(result.status, DeploymentStatus.ROLLED_BACK)
        self.assertIn("err", result.rollback_reason)

    @patch("darkfactory.deployment.progressive.run_command")
    def test_traffic_shift_failure(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=1)
        config = _make_config()
        stages = [CanaryStage(percent=10, soak_seconds=0)]

        result = run_canary(
            config, "spec1", "run123", stages, [],
            self.events_file, self.tmpdir,
        )
        self.assertEqual(result.status, DeploymentStatus.FAILED)

    @patch("darkfactory.deployment.progressive.time.sleep")
    @patch("darkfactory.deployment.progressive.run_command")
    def test_uses_default_stages_when_none(self, mock_cmd, mock_sleep):
        mock_cmd.return_value = MagicMock(returncode=0)
        config = _make_config()

        result = run_canary(
            config, "spec1", "run123", None, [],
            self.events_file, self.tmpdir,
        )
        self.assertEqual(result.stages_total, len(DEFAULT_STAGES))

    def test_invalid_stages_raises(self):
        config = _make_config()
        stages = [CanaryStage(percent=100, soak_seconds=0), CanaryStage(percent=50, soak_seconds=0)]

        with self.assertRaises(ValueError):
            run_canary(
                config, "spec1", "run123", stages, [],
                self.events_file, self.tmpdir,
            )

    def test_duplicate_percents_raises(self):
        config = _make_config()
        stages = [CanaryStage(percent=50, soak_seconds=0), CanaryStage(percent=50, soak_seconds=0)]

        with self.assertRaises(ValueError):
            run_canary(
                config, "spec1", "run123", stages, [],
                self.events_file, self.tmpdir,
            )

    @patch("darkfactory.deployment.progressive.FileLock")
    @patch("darkfactory.deployment.progressive.run_command")
    def test_lock_timeout_degrades(self, mock_cmd, mock_lock_cls):
        from darkfactory.state.lock import LockError
        mock_lock = MagicMock()
        mock_lock.acquire.side_effect = LockError("timeout")
        mock_lock_cls.return_value = mock_lock

        config = _make_config()
        stages = [CanaryStage(percent=100, soak_seconds=0)]

        result = run_canary(
            config, "spec1", "run123", stages, [],
            self.events_file, self.tmpdir,
        )
        self.assertEqual(result.status, DeploymentStatus.FAILED)
        self.assertEqual(result.stages_completed, 0)
        self.assertIn("lock timeout", result.error)


class TestSoak(unittest.TestCase):
    @patch("darkfactory.deployment.progressive.time.sleep")
    def test_completes_normally(self, mock_sleep):
        self.assertTrue(_soak(3, None))
        self.assertEqual(mock_sleep.call_count, 3)

    @patch("darkfactory.deployment.progressive.time.sleep")
    def test_aborts_on_request(self, mock_sleep):
        cm = MagicMock()
        cm.abort_requested = True
        self.assertFalse(_soak(10, cm))

    @patch("darkfactory.deployment.progressive.time.sleep")
    def test_no_cleanup_manager(self, mock_sleep):
        self.assertTrue(_soak(2, None))


class TestShiftTraffic(unittest.TestCase):
    @patch("darkfactory.deployment.progressive.run_command")
    def test_success(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=0)
        config = _make_config()
        self.assertTrue(_shift_traffic(config, 50, "/tmp"))
        env = mock_cmd.call_args[1]["env"]
        self.assertEqual(env["CANARY_PERCENT"], "50")

    def test_no_cmd_configured(self):
        config = PipelineConfig()
        self.assertTrue(_shift_traffic(config, 50, "/tmp"))

    @patch("darkfactory.deployment.progressive.run_command")
    def test_failure(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=1)
        config = _make_config()
        self.assertFalse(_shift_traffic(config, 50, "/tmp"))


if __name__ == "__main__":
    unittest.main()
