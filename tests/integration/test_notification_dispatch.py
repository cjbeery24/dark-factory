"""Integration tests for notification dispatch.

Tests verify that notification functions produce correct payloads
and respect config (no webhooks = no-op). Mocks subprocess.run
to avoid actual HTTP calls.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from darkfactory.models.config import PipelineConfig
from darkfactory.notify.dispatcher import (
    _send,
    notify_budget_alert,
    notify_canary_failed,
    notify_canary_promoted,
    notify_escalated,
    notify_failed,
    notify_flag_killed,
    notify_hold,
    notify_pr_created,
    notify_rollback,
    notify_rollback_failed,
    notify_shipped,
)


@pytest.mark.integration
class TestSendDispatch:
    """_send routes to Slack and/or webhook based on config."""

    @patch("darkfactory.notify.dispatcher._post_slack")
    @patch("darkfactory.notify.dispatcher._post_webhook")
    def test_both_configured(self, mock_webhook, mock_slack):
        config = PipelineConfig(
            slack_webhook_url="https://hooks.slack.com/test",
            webhook_url="https://example.com/hook",
        )
        _send(config, "test message", {"key": "value"})
        mock_slack.assert_called_once_with("https://hooks.slack.com/test", "test message")
        mock_webhook.assert_called_once_with("https://example.com/hook", {"key": "value"})

    @patch("darkfactory.notify.dispatcher._post_slack")
    @patch("darkfactory.notify.dispatcher._post_webhook")
    def test_slack_only(self, mock_webhook, mock_slack):
        config = PipelineConfig(slack_webhook_url="https://hooks.slack.com/test")
        _send(config, "test", {})
        mock_slack.assert_called_once()
        mock_webhook.assert_not_called()

    @patch("darkfactory.notify.dispatcher._post_slack")
    @patch("darkfactory.notify.dispatcher._post_webhook")
    def test_webhook_only(self, mock_webhook, mock_slack):
        config = PipelineConfig(webhook_url="https://example.com/hook")
        _send(config, "test", {"k": "v"})
        mock_slack.assert_not_called()
        mock_webhook.assert_called_once()

    @patch("darkfactory.notify.dispatcher._post_slack")
    @patch("darkfactory.notify.dispatcher._post_webhook")
    def test_no_config_noop(self, mock_webhook, mock_slack):
        config = PipelineConfig()
        _send(config, "test", {})
        mock_slack.assert_not_called()
        mock_webhook.assert_not_called()


@pytest.mark.integration
class TestNotifyFunctions:
    """Each notify function constructs correct text and payload."""

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_shipped(self, mock_send):
        config = PipelineConfig()
        notify_shipped(config, "add-auth", "run1", 2, trust_score=85)
        mock_send.assert_called_once()
        call_config, text, payload = mock_send.call_args[0]
        assert call_config is config
        assert "SHIPPED" in text
        assert "add-auth" in text
        assert payload["spec"] == "add-auth"
        assert payload["run_id"] == "run1"
        assert payload["iterations"] == 2
        assert payload["trust"] == 85
        assert payload["status"] == "shipped"

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_escalated(self, mock_send):
        config = PipelineConfig()
        notify_escalated(config, "add-auth", "run1", 3)
        call_config, text, payload = mock_send.call_args[0]
        assert call_config is config
        assert "ESCALATED" in text
        assert "add-auth" in text
        assert payload["spec"] == "add-auth"
        assert payload["iterations"] == 3
        assert payload["status"] == "escalated"

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_failed(self, mock_send):
        config = PipelineConfig()
        notify_failed(config, "add-auth", "run1", "build error")
        call_config, text, payload = mock_send.call_args[0]
        assert call_config is config
        assert "FAILED" in text
        assert "build error" in text
        assert payload["spec"] == "add-auth"
        assert payload["reason"] == "build error"
        assert payload["status"] == "failed"

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_pr_created(self, mock_send):
        config = PipelineConfig()
        notify_pr_created(config, "add-auth", "https://github.com/pr/1")
        call_config, text, payload = mock_send.call_args[0]
        assert call_config is config
        assert "add-auth" in text
        assert "https://github.com/pr/1" in text
        assert payload["spec"] == "add-auth"
        assert payload["pr_url"] == "https://github.com/pr/1"

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_budget_alert(self, mock_send):
        config = PipelineConfig()
        notify_budget_alert(config, "add-auth", 80.0, 8.0, 10.0)
        call_config, text, payload = mock_send.call_args[0]
        assert call_config is config
        assert "BUDGET" in text
        assert "80.0%" in text
        assert payload["spec"] == "add-auth"
        assert payload["percent"] == 80.0
        assert payload["spent"] == 8.0
        assert payload["budget"] == 10.0

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_hold(self, mock_send):
        config = PipelineConfig()
        notify_hold(config, "add-auth", "run1", 45)
        call_config, text, payload = mock_send.call_args[0]
        assert call_config is config
        assert "HOLD" in text
        assert "add-auth" in text
        assert payload["spec"] == "add-auth"
        assert payload["trust"] == 45
        assert payload["status"] == "hold"

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_canary_promoted(self, mock_send):
        config = PipelineConfig()
        notify_canary_promoted(config, "add-auth", "run1", 4)
        call_config, text, payload = mock_send.call_args[0]
        assert call_config is config
        assert "CANARY PROMOTED" in text
        assert payload["spec"] == "add-auth"
        assert payload["stages"] == 4
        assert payload["status"] == "canary_promoted"

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_canary_failed(self, mock_send):
        config = PipelineConfig()
        notify_canary_failed(config, "add-auth", "run1", "metrics breach")
        call_config, text, payload = mock_send.call_args[0]
        assert call_config is config
        assert "CANARY FAILED" in text
        assert payload["reason"] == "metrics breach"
        assert payload["status"] == "canary_failed"

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_rollback(self, mock_send):
        config = PipelineConfig()
        notify_rollback(config, "add-auth", "run1", "TRAFFIC_REVERT", "health check failed")
        call_config, text, payload = mock_send.call_args[0]
        assert call_config is config
        assert "ROLLBACK" in text
        assert "TRAFFIC_REVERT" in text
        assert payload["layer"] == "TRAFFIC_REVERT"
        assert payload["reason"] == "health check failed"
        assert payload["status"] == "rollback"

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_rollback_failed(self, mock_send):
        config = PipelineConfig()
        notify_rollback_failed(config, "add-auth", "run1", "all layers exhausted")
        call_config, text, payload = mock_send.call_args[0]
        assert call_config is config
        assert "ROLLBACK FAILED" in text
        assert payload["reason"] == "all layers exhausted"
        assert payload["status"] == "rollback_failed"

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_flag_killed(self, mock_send):
        config = PipelineConfig()
        notify_flag_killed(config, "add-auth", "run1")
        call_config, text, payload = mock_send.call_args[0]
        assert call_config is config
        assert "FLAG KILLED" in text
        assert payload["spec"] == "add-auth"
        assert payload["status"] == "flag_killed"
