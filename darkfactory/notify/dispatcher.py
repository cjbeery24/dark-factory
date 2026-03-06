"""Slack and webhook notifications.

Replaces lib/notify.sh — fire-and-forget HTTP POSTs via subprocess curl.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import run

log = logging.getLogger(__name__)


def notify_shipped(
    config: PipelineConfig,
    spec_name: str,
    run_id: str,
    iterations: int,
    trust_score: int = 0,
) -> None:
    """Notify that a spec has shipped."""
    text = f"SHIPPED: {spec_name} (iter {iterations}, trust {trust_score})"
    _send(config, text, {"spec": spec_name, "run_id": run_id,
                          "status": "shipped", "iterations": iterations,
                          "trust": trust_score})


def notify_escalated(
    config: PipelineConfig,
    spec_name: str,
    run_id: str,
    iterations: int,
) -> None:
    """Notify that a spec has been escalated."""
    text = f"ESCALATED: {spec_name} after {iterations} iterations"
    _send(config, text, {"spec": spec_name, "run_id": run_id,
                          "status": "escalated", "iterations": iterations})


def notify_failed(
    config: PipelineConfig,
    spec_name: str,
    run_id: str,
    reason: str,
) -> None:
    """Notify that a spec has failed."""
    text = f"FAILED: {spec_name} — {reason}"
    _send(config, text, {"spec": spec_name, "run_id": run_id,
                          "status": "failed", "reason": reason})


def notify_pr_created(
    config: PipelineConfig,
    spec_name: str,
    pr_url: str,
) -> None:
    """Notify that a PR was created."""
    text = f"PR created for {spec_name}: {pr_url}"
    _send(config, text, {"spec": spec_name, "pr_url": pr_url})


def notify_budget_alert(
    config: PipelineConfig,
    spec_name: str,
    percent: float,
    spent: float,
    budget: float,
) -> None:
    """Notify of a budget alert."""
    text = f"BUDGET ALERT: {spec_name} at {percent:.1f}% (${spent:.4f}/${budget:.4f})"
    _send(config, text, {"spec": spec_name, "percent": percent,
                          "spent": spent, "budget": budget})


def notify_anomaly(
    config: PipelineConfig,
    spec_name: str,
    anomaly_type: str,
    score: int,
) -> None:
    """Notify of an anomaly detection."""
    text = f"ANOMALY: {spec_name} — {anomaly_type} (score {score})"
    _send(config, text, {"spec": spec_name, "anomaly_type": anomaly_type,
                          "score": score})


def notify_trust_change(
    config: PipelineConfig,
    spec_name: str,
    trust_score: int,
    autonomy_level: int,
) -> None:
    """Notify of a trust score change."""
    text = f"TRUST: {spec_name} trust={trust_score} autonomy={autonomy_level}"
    _send(config, text, {"spec": spec_name, "trust": trust_score,
                          "autonomy": autonomy_level})


def notify_regression_blocked(
    config: PipelineConfig,
    spec_name: str,
    passed: int,
    failed: int,
) -> None:
    """Notify that regression testing blocked a ship."""
    text = f"REGRESSION BLOCKED: {spec_name} ({passed} passed, {failed} failed)"
    _send(config, text, {"spec": spec_name, "passed": passed, "failed": failed})


def _send(config: PipelineConfig, text: str, payload: dict[str, Any]) -> None:
    """Send notification via Slack and/or generic webhook (fire-and-forget)."""
    if config.slack_webhook_url:
        _post_slack(config.slack_webhook_url, text)
    if config.webhook_url:
        _post_webhook(config.webhook_url, payload)


def _post_slack(url: str, text: str) -> None:
    """POST to Slack webhook."""
    body = json.dumps({"text": text})
    result = run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "-X", "POST", "-H", "Content-Type: application/json",
         "-d", body, url],
        timeout=10,
    )
    if result.exit_code != 0:
        log.warning("slack notification failed: exit %d", result.exit_code)


def _post_webhook(url: str, payload: dict[str, Any]) -> None:
    """POST to generic webhook."""
    body = json.dumps(payload)
    result = run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "-X", "POST", "-H", "Content-Type: application/json",
         "-d", body, url],
        timeout=10,
    )
    if result.exit_code != 0:
        log.warning("webhook notification failed: exit %d", result.exit_code)
