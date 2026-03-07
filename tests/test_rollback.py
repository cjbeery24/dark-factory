"""Tests for darkfactory.deployment.rollback — multi-layer rollback engine."""

from __future__ import annotations

import json
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from darkfactory.deployment.models import RollbackLayer
from darkfactory.deployment.rollback import rollback, _attempt_layer
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import RunResult


def _make_config(**kwargs) -> PipelineConfig:
    defaults = dict(
        canary_enabled=True,
        canary_rollback_cmd="scripts/canary-rollback.sh",
        flag_disable_cmd="scripts/flag-disable.sh",
        deploy_rollback_cmd="scripts/deploy-rollback.sh",
    )
    defaults.update(kwargs)
    return PipelineConfig(**defaults)


class TestRollbackCascade(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.events_file = Path(self.tmpdir) / "events.jsonl"
        self.events_file.touch()

    @patch("darkfactory.deployment.rollback.run_command")
    def test_flag_kill_succeeds(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=0)
        config = _make_config()
        layer = rollback(
            config, "spec1", "run123", "test reason",
            RollbackLayer.FLAG_KILL, self.tmpdir, self.events_file,
        )
        self.assertEqual(layer, RollbackLayer.FLAG_KILL)

    @patch("darkfactory.deployment.rollback.run_command")
    def test_cascade_to_traffic_on_flag_failure(self, mock_cmd):
        # First call (flag_disable) fails, second (canary_rollback) succeeds.
        mock_cmd.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=0),
        ]
        config = _make_config()
        layer = rollback(
            config, "spec1", "run123", "test reason",
            RollbackLayer.FLAG_KILL, self.tmpdir, self.events_file,
        )
        self.assertEqual(layer, RollbackLayer.TRAFFIC_REVERT)

    @patch("darkfactory.deployment.rollback.run_command")
    def test_cascade_to_container(self, mock_cmd):
        # Flag and traffic fail, container succeeds.
        mock_cmd.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=1),
            MagicMock(returncode=0),
        ]
        config = _make_config()
        layer = rollback(
            config, "spec1", "run123", "test reason",
            RollbackLayer.FLAG_KILL, self.tmpdir, self.events_file,
        )
        self.assertEqual(layer, RollbackLayer.CONTAINER_ROLLBACK)

    @patch("darkfactory.deployment.rollback.run")
    @patch("darkfactory.deployment.rollback.run_command")
    def test_all_fail_pre_merge(self, mock_cmd, mock_run):
        # All cmd-based layers fail. Git revert skipped (pre-merge).
        mock_cmd.return_value = MagicMock(returncode=1)
        config = _make_config()
        layer = rollback(
            config, "spec1", "run123", "test reason",
            RollbackLayer.FLAG_KILL, self.tmpdir, self.events_file,
            post_merge=False,
        )
        self.assertIsNone(layer)
        # Verify ROLLBACK_ALL_FAILED event emitted.
        events = self.events_file.read_text()
        self.assertIn("ROLLBACK_ALL_FAILED", events)

    @patch("darkfactory.deployment.rollback.run")
    @patch("darkfactory.deployment.rollback.run_command")
    def test_git_revert_only_post_merge(self, mock_cmd, mock_run):
        # All cmd layers fail, git revert succeeds.
        mock_cmd.return_value = MagicMock(returncode=1)
        mock_run.return_value = RunResult(exit_code=0, stdout="abc123\n")

        config = _make_config()
        layer = rollback(
            config, "spec1", "run123", "test reason",
            RollbackLayer.FLAG_KILL, self.tmpdir, self.events_file,
            post_merge=True,
        )
        self.assertEqual(layer, RollbackLayer.GIT_REVERT)

    @patch("darkfactory.deployment.rollback.run_command")
    def test_start_at_traffic(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=0)
        config = _make_config()
        layer = rollback(
            config, "spec1", "run123", "test reason",
            RollbackLayer.TRAFFIC_REVERT, self.tmpdir, self.events_file,
        )
        self.assertEqual(layer, RollbackLayer.TRAFFIC_REVERT)

    @patch("darkfactory.deployment.rollback.run_command")
    def test_skips_unconfigured_layers(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=0)
        # No flag_disable_cmd, no canary_rollback_cmd.
        config = PipelineConfig(deploy_rollback_cmd="scripts/deploy-rollback.sh")
        layer = rollback(
            config, "spec1", "run123", "test reason",
            RollbackLayer.FLAG_KILL, self.tmpdir, self.events_file,
        )
        self.assertEqual(layer, RollbackLayer.CONTAINER_ROLLBACK)

    @patch("darkfactory.deployment.rollback.run_command")
    def test_events_emitted(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=0)
        config = _make_config()
        rollback(
            config, "spec1", "run123", "test reason",
            RollbackLayer.FLAG_KILL, self.tmpdir, self.events_file,
        )
        events = self.events_file.read_text()
        self.assertIn("ROLLBACK_STARTED", events)
        self.assertIn("ROLLBACK_SUCCESS", events)


class TestAttemptLayer(unittest.TestCase):
    @patch("darkfactory.deployment.rollback.run_command")
    def test_flag_kill(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=0)
        config = _make_config()
        self.assertTrue(_attempt_layer(config, "spec1", RollbackLayer.FLAG_KILL, "/tmp", "reason"))

    @patch("darkfactory.deployment.rollback.run_command")
    def test_traffic_revert(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=0)
        config = _make_config()
        self.assertTrue(_attempt_layer(config, "spec1", RollbackLayer.TRAFFIC_REVERT, "/tmp", "reason"))

    @patch("darkfactory.deployment.rollback.run_command")
    def test_container_rollback(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=0)
        config = _make_config()
        self.assertTrue(_attempt_layer(config, "spec1", RollbackLayer.CONTAINER_ROLLBACK, "/tmp", "reason"))

    def test_unconfigured_flag_returns_false(self):
        config = PipelineConfig()
        self.assertFalse(_attempt_layer(config, "spec1", RollbackLayer.FLAG_KILL, "/tmp", "reason"))

    @patch("darkfactory.deployment.rollback.run_command")
    def test_exception_returns_false(self, mock_cmd):
        mock_cmd.side_effect = Exception("boom")
        config = _make_config()
        self.assertFalse(_attempt_layer(config, "spec1", RollbackLayer.FLAG_KILL, "/tmp", "reason"))


if __name__ == "__main__":
    unittest.main()
