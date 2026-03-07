"""Tests for post-merge rollback monitor (Phase 7C)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from darkfactory.deployment.monitor import monitor_rollback_window, _poll_health
from darkfactory.models.config import PipelineConfig


class TestMonitorPollHealth(unittest.TestCase):
    @patch("darkfactory.deployment.monitor.run_subprocess")
    def test_healthy_returns_true(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(exit_code=0, stdout="ok", stderr="")
        ok, output = _poll_health("/bin/true", "/tmp", {}, 30)
        self.assertTrue(ok)

    @patch("darkfactory.deployment.monitor.run_subprocess")
    def test_unhealthy_returns_false(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(exit_code=1, stdout="", stderr="breach detected")
        ok, output = _poll_health("/bin/false", "/tmp", {}, 30)
        self.assertFalse(ok)
        self.assertIn("breach", output)

    @patch("darkfactory.deployment.monitor.run_subprocess")
    def test_exception_returns_false(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = Exception("timeout")
        ok, output = _poll_health("/bin/fail", "/tmp", {}, 30)
        self.assertFalse(ok)
        self.assertIn("timeout", output)


class TestMonitorWindow(unittest.TestCase):
    def test_no_monitor_cmd_returns_true(self) -> None:
        config = PipelineConfig(rollback_monitor_cmd="")
        with tempfile.NamedTemporaryFile(suffix=".jsonl") as f:
            result = monitor_rollback_window(
                config, "spec", "run1", Path(f.name), "/tmp",
            )
        self.assertTrue(result)

    @patch("darkfactory.deployment.monitor.run_subprocess")
    @patch("darkfactory.deployment.monitor.time")
    def test_clean_window_emits_event(self, mock_time: MagicMock, mock_run: MagicMock) -> None:
        config = PipelineConfig(
            rollback_monitor_cmd="/bin/true",
            rollback_window_seconds=5,
            rollback_poll_interval=2,
        )
        # Simulate time: 0s -> poll -> 3s -> poll -> 6s (past window).
        elapsed = [0.0]
        def monotonic():
            val = elapsed[0]
            elapsed[0] += 1.5  # Each call advances 1.5s
            return val
        mock_time.monotonic.side_effect = monotonic
        mock_time.sleep = MagicMock()

        mock_run.return_value = MagicMock(exit_code=0, stdout="ok", stderr="")

        events_file = Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            result = monitor_rollback_window(
                config, "spec", "run1", events_file, "/tmp",
            )
            self.assertTrue(result)
            if events_file.exists():
                content = events_file.read_text()
                self.assertIn("ROLLBACK_WINDOW_EXPIRED", content)
        finally:
            events_file.unlink(missing_ok=True)

    @patch("darkfactory.deployment.monitor.rollback")
    @patch("darkfactory.deployment.monitor.run_subprocess")
    @patch("darkfactory.deployment.monitor.time")
    def test_breach_triggers_rollback(self, mock_time: MagicMock, mock_run: MagicMock,
                                       mock_rollback: MagicMock) -> None:
        config = PipelineConfig(
            rollback_monitor_cmd="/bin/check",
            rollback_window_seconds=60,
            rollback_poll_interval=5,
        )
        mock_time.monotonic.return_value = 0.0
        mock_time.sleep = MagicMock()
        mock_run.return_value = MagicMock(exit_code=1, stdout="", stderr="error rate too high")
        mock_rollback.return_value = None

        events_file = Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            result = monitor_rollback_window(
                config, "spec", "run1", events_file, "/tmp",
            )
            self.assertFalse(result)
            mock_rollback.assert_called_once()
        finally:
            events_file.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
