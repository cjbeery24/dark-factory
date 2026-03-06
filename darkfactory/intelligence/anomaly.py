"""Anomaly detection — threshold, baseline, and composite scoring.

Replaces lib/anomaly.sh — anomaly_check_agent, anomaly_check_spec,
anomaly_compute_baseline, anomaly_composite_score.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from darkfactory.events.types import emit_anomaly_detected
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.json_helpers import read_jsonl

log = logging.getLogger(__name__)


def check_agent_turns(
    turns: int,
    config: PipelineConfig,
    events_file: Path,
    spec_name: str,
    run_id: str,
) -> bool:
    """Check if agent turn count exceeds the hard threshold.

    Returns True if anomalous (should block).

    Note: Not currently wired — requires actual turn counts from Claude Code
    output parsing (Phase 7). The function is retained for that future use.
    """
    if turns > config.anomaly_max_agent_turns:
        emit_anomaly_detected(
            events_file, spec=spec_name, run_id=run_id,
            anomaly_type="agent_turns", score=0,
            detail=f"turns={turns} > max={config.anomaly_max_agent_turns}",
        )
        log.warning("anomaly: agent used %d turns (max %d)", turns, config.anomaly_max_agent_turns)
        return True
    return False


def check_spec_limits(
    total_turns: int,
    wall_seconds: float,
    config: PipelineConfig,
    events_file: Path,
    spec_name: str,
    run_id: str,
) -> bool:
    """Check if spec-level turn count or wall time exceed thresholds.

    Returns True if anomalous (should block).
    """
    if total_turns > config.anomaly_max_spec_turns:
        emit_anomaly_detected(
            events_file, spec=spec_name, run_id=run_id,
            anomaly_type="spec_turns", score=0,
            detail=f"turns={total_turns} > max={config.anomaly_max_spec_turns}",
        )
        log.warning("anomaly: spec used %d total turns (max %d)", total_turns, config.anomaly_max_spec_turns)
        return True

    if wall_seconds > config.anomaly_max_spec_wall_s:
        emit_anomaly_detected(
            events_file, spec=spec_name, run_id=run_id,
            anomaly_type="spec_wall_time", score=0,
            detail=f"wall={wall_seconds:.0f}s > max={config.anomaly_max_spec_wall_s}s",
        )
        log.warning("anomaly: spec wall time %.0fs (max %ds)", wall_seconds, config.anomaly_max_spec_wall_s)
        return True

    return False


def compute_baseline(events_file: Path, window: int = 20) -> tuple[float, float]:
    """Compute mean and stddev of turns from recent SPEC_SHIPPED/SPEC_FAILED events.

    Returns (mean, stddev). Returns (0.0, 0.0) if insufficient data.
    """
    records = read_jsonl(events_file)
    turn_counts: list[int] = []
    for rec in records:
        event = rec.get("event", "")
        if event in ("SPEC_SHIPPED", "SPEC_FAILED"):
            data = rec.get("data", {})
            turns = data.get("total_turns", 0)
            if turns > 0:
                turn_counts.append(turns)

    # Take last N.
    recent = turn_counts[-window:]
    if len(recent) < 3:
        return 0.0, 0.0

    mean = sum(recent) / len(recent)
    variance = sum((x - mean) ** 2 for x in recent) / len(recent)
    stddev = math.sqrt(variance)
    return mean, stddev


def check_baseline(
    total_turns: int,
    events_file: Path,
    config: PipelineConfig,
    spec_name: str = "",
    run_id: str = "",
) -> bool:
    """Check if total turns exceed 2 sigma from baseline.

    Emits ANOMALY_DETECTED event and logs if anomalous.
    Returns True if anomalous.
    """
    mean, stddev = compute_baseline(events_file, config.anomaly_baseline_window)
    if mean == 0:
        return False
    threshold = mean + 2 * stddev
    if total_turns > threshold:
        emit_anomaly_detected(
            events_file, spec=spec_name, run_id=run_id,
            anomaly_type="baseline_deviation",
            detail=f"turns={total_turns} > baseline={threshold:.0f} (mean={mean:.0f}, 2σ={2*stddev:.0f})",
        )
        log.warning(
            "anomaly: turns %d exceed baseline %.0f (mean=%.0f, 2σ=%.0f)",
            total_turns, threshold, mean, 2 * stddev,
        )
        return True
    return False


# Composite anomaly scoring weights.
_WEIGHT_TURNS = 30
_WEIGHT_WALL = 20
_WEIGHT_TRAJECTORY = 25
_WEIGHT_FILES = 15
_WEIGHT_BARRIER = 40

# Composite score thresholds.
COMPOSITE_BLOCK = 70
COMPOSITE_ESCALATE = 40
COMPOSITE_WARN = 20


def composite_score(
    *,
    turns_anomalous: bool,
    wall_anomalous: bool,
    trajectory_bad: bool,
    files_anomalous: bool,
    barrier_violation: bool,
) -> int:
    """Compute weighted composite anomaly score (0-130).

    Returns the score. Caller decides action based on thresholds:
    - >= 70: block (hard abort)
    - >= 40: escalate + notify
    - >= 20: warn
    """
    score = 0
    if turns_anomalous:
        score += _WEIGHT_TURNS
    if wall_anomalous:
        score += _WEIGHT_WALL
    if trajectory_bad:
        score += _WEIGHT_TRAJECTORY
    if files_anomalous:
        score += _WEIGHT_FILES
    if barrier_violation:
        score += _WEIGHT_BARRIER
    return score
