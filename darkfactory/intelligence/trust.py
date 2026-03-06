"""Trust scoring — 5 dimensions, weighted composite.

Replaces lib/trust.sh — trust_calculate, trust_to_autonomy, trust_predict_iterations.
"""

from __future__ import annotations

import logging
from pathlib import Path

from darkfactory.models.config import PipelineConfig
from darkfactory.models.trust import TrustDimensions, TrustScore
from darkfactory.utils.json_helpers import read_jsonl

log = logging.getLogger(__name__)

# Dimension weights (must sum to 100).
_W_FIRST_PASS = 30
_W_AVG_ITER = 20
_W_REGRESSION = 20
_W_COMPLEXITY = 15
_W_FAMILIARITY = 15


def calculate_trust(
    events_file: Path,
    config: PipelineConfig,
    spec_target_files: int = 0,
    spec_deps: int = 0,
) -> TrustScore:
    """Calculate trust score from historical events.

    Returns cold-start score of 50 if insufficient history.
    """
    records = read_jsonl(events_file)

    shipped_events = [
        r for r in records
        if r.get("event") == "SPEC_SHIPPED"
    ]
    failed_events = [
        r for r in records
        if r.get("event") in ("SPEC_FAILED", "SPEC_ESCALATED")
    ]

    total_runs = len(shipped_events) + len(failed_events)
    if total_runs < config.trust_min_history:
        return TrustScore(total=50, cold_start=True)

    dims = TrustDimensions(
        first_pass_rate=_first_pass_rate(shipped_events),
        avg_iterations=_avg_iterations_score(shipped_events),
        regression_fail_rate=_regression_fail_rate(records),
        spec_complexity=_spec_complexity_score(spec_target_files, spec_deps),
        module_familiarity=_module_familiarity(shipped_events),
    )

    # Per-term floor division matches bash: score=$(( a*30/100 + b*20/100 + ... ))
    total = (
        dims.first_pass_rate * _W_FIRST_PASS // 100
        + dims.avg_iterations * _W_AVG_ITER // 100
        + dims.regression_fail_rate * _W_REGRESSION // 100
        + dims.spec_complexity * _W_COMPLEXITY // 100
        + dims.module_familiarity * _W_FAMILIARITY // 100
    )

    return TrustScore(total=min(100, max(0, total)), dimensions=dims, cold_start=False)


def trust_to_autonomy(trust_score: int) -> int:
    """Map trust score to autonomy level."""
    if trust_score >= 60:
        return 3
    if trust_score >= 30:
        return 2
    return 1


def predict_iterations(events_file: Path, config: PipelineConfig) -> int:
    """Predict number of iterations based on historical average.

    Falls back to config.max_iterations if insufficient data.
    """
    records = read_jsonl(events_file)
    shipped = [r for r in records if r.get("event") == "SPEC_SHIPPED"]
    if len(shipped) < config.trust_min_history:
        return config.max_iterations

    iter_counts = [r.get("data", {}).get("iterations", 1) for r in shipped]
    avg = sum(iter_counts) / len(iter_counts)
    # Round up, clamp to [1, max_iterations].
    return min(config.max_iterations, max(1, int(avg + 0.5)))


def _first_pass_rate(shipped: list[dict]) -> int:
    """Percentage of specs that shipped on iteration 1."""
    if not shipped:
        return 0
    first_pass = sum(
        1 for r in shipped
        if r.get("data", {}).get("iterations", 1) == 1
    )
    return (first_pass * 100) // len(shipped)


def _avg_iterations_score(shipped: list[dict]) -> int:
    """Score based on average iterations (1 → 100, 5+ → 0).

    Matches bash: rounds avg to integer first, then (5 - avg) * 25.
    """
    if not shipped:
        return 0
    iters = [r.get("data", {}).get("iterations", 1) for r in shipped]
    avg = sum(iters) / len(iters)
    avg_rounded = min(5, max(1, round(avg)))
    return (5 - avg_rounded) * 25


def _regression_fail_rate(records: list[dict]) -> int:
    """Inverted regression failure rate (fewer failures = higher score)."""
    regression_events = [r for r in records if r.get("event") == "REGRESSION_RUN"]
    if not regression_events:
        return 100  # No regressions = perfect score.
    total_passed = sum(r.get("data", {}).get("passed", 0) for r in regression_events)
    total_total = sum(r.get("data", {}).get("total", 0) for r in regression_events)
    if total_total == 0:
        return 100
    return (total_passed * 100) // total_total


def _spec_complexity_score(target_files: int, deps: int) -> int:
    """Score inversely proportional to complexity."""
    # target_files × 10 + deps × 20, inverted from 100.
    raw = target_files * 10 + deps * 20
    return max(0, 100 - raw)


def _module_familiarity(shipped: list[dict]) -> int:
    """Score based on number of shipped specs (capped at 10)."""
    count = min(len(shipped), 10)
    return count * 10
