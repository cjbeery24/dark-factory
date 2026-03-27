"""Tests for notify/dispatcher.py — mock subprocess to avoid real HTTP calls."""

import unittest
from unittest.mock import patch

from darkfactory.models.config import PipelineConfig
from darkfactory.notify.dispatcher import notify_shipped, notify_failed
from darkfactory.utils.subprocess_runner import RunResult


class TestNotify(unittest.TestCase):
    @patch("darkfactory.notify.dispatcher.run")
    def test_slack_notification(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = RunResult(exit_code=0)
        config = PipelineConfig(slack_webhook_url="https://hooks.slack.com/test", enterprise_enabled=True)
        notify_shipped(config, "my-spec", "r1", 2, trust_score=75)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "curl")
        self.assertIn("https://hooks.slack.com/test", args)

    @patch("darkfactory.notify.dispatcher.run")
    def test_webhook_notification(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = RunResult(exit_code=0)
        config = PipelineConfig(webhook_url="https://example.com/hook", enterprise_enabled=True)
        notify_failed(config, "my-spec", "r1", "gate failed")
        mock_run.assert_called_once()

    def test_no_notification_when_unconfigured(self) -> None:
        config = PipelineConfig()
        # Should not raise or make any HTTP calls.
        notify_shipped(config, "s", "r", 1)

    @patch("darkfactory.notify.dispatcher.run")
    def test_both_slack_and_webhook(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = RunResult(exit_code=0)
        config = PipelineConfig(
            slack_webhook_url="https://hooks.slack.com/test",
            webhook_url="https://example.com/hook",
            enterprise_enabled=True,
        )
        notify_shipped(config, "s", "r", 1)
        self.assertEqual(mock_run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
