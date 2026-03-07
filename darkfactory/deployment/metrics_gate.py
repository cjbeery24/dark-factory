"""Metric collection and threshold-based deployment gating."""

from __future__ import annotations

import logging

from darkfactory.deployment.command import run_command
from darkfactory.deployment.models import MetricThreshold

log = logging.getLogger(__name__)


def parse_metric_output(stdout: str) -> float:
    """Parse a metric command's stdout into a float.

    Accepts any format Python's float() can parse: "0.05", "123.4", "-1.0", "1.5e-3".
    Strips whitespace. Rejects empty, multi-line, non-numeric.
    On parse failure: returns float('inf') (fail-safe — triggers max_value breach).
    """
    stripped = stdout.strip()
    if not stripped or "\n" in stripped:
        log.warning("unparseable metric output (empty or multi-line): %r", stdout)
        return float("inf")

    try:
        return float(stripped)
    except ValueError:
        log.warning("unparseable metric output: %r", stdout)
        return float("inf")


def collect_metrics(
    thresholds: list[MetricThreshold],
    project_dir: str,
    timeout: int = 30,
) -> dict[str, float]:
    """Run each threshold's metric_cmd and collect values.

    Commands run sequentially. On failure: value = float('inf').
    """
    metrics: dict[str, float] = {}

    for threshold in thresholds:
        if not threshold.metric_cmd:
            metrics[threshold.name] = float("inf")
            continue

        try:
            proc = run_command(
                threshold.metric_cmd, project_dir,
                timeout=timeout, capture=True,
            )
            if proc.returncode != 0:
                log.warning("metric cmd failed for %s (exit %d)", threshold.name, proc.returncode)
                metrics[threshold.name] = float("inf")
            else:
                metrics[threshold.name] = parse_metric_output(proc.stdout)
        except Exception as e:
            log.warning("metric cmd error for %s: %s", threshold.name, e)
            metrics[threshold.name] = float("inf")

    return metrics


def evaluate_thresholds(
    metrics: dict[str, float],
    thresholds: list[MetricThreshold],
) -> list[str]:
    """Compare collected metrics against thresholds.

    Returns list of breached threshold names (empty = all clear).
    """
    breached: list[str] = []

    for threshold in thresholds:
        value = metrics.get(threshold.name)
        if value is None:
            continue

        if threshold.max_value is not None and value > threshold.max_value:
            breached.append(threshold.name)
            log.warning(
                "threshold breach: %s = %.4f > max %.4f",
                threshold.name, value, threshold.max_value,
            )
        elif threshold.min_value is not None and value < threshold.min_value:
            breached.append(threshold.name)
            log.warning(
                "threshold breach: %s = %.4f < min %.4f",
                threshold.name, value, threshold.min_value,
            )

    return breached


def collect_and_evaluate(
    thresholds: list[MetricThreshold],
    project_dir: str,
    timeout: int = 30,
) -> tuple[dict[str, float], list[str]]:
    """Collect metrics and evaluate against thresholds."""
    metrics = collect_metrics(thresholds, project_dir, timeout)
    breached = evaluate_thresholds(metrics, thresholds)
    return metrics, breached
