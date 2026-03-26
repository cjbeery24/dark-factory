"""Trajectory classification and layer analysis.

Replaces lib/satisfaction.sh trajectory functions.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field

from darkfactory.models.satisfaction import CategoryResult, TrajectoryType

log = logging.getLogger(__name__)


class StrategyActionType(enum.Enum):
    """Actions the iteration loop can take based on trajectory."""
    CONTINUE = "continue"
    SWITCH_MODEL = "switch_model"
    FRESH_START = "fresh_start"
    ESCALATE = "escalate"


@dataclass
class StrategyAction:
    """Recommendation from trajectory analysis for the iteration loop."""
    action: StrategyActionType
    reason: str = ""

    def __str__(self) -> str:
        return self.reason


@dataclass
class LayerTrajectory:
    """Per-category trajectory across iterations."""
    category: str
    scores: list[int] = field(default_factory=list)
    stuck: bool = False  # True if score is 0% across all iterations.


def classify_trajectory(
    scores: list[int],
    plateau_threshold: int = 5,
) -> TrajectoryType:
    """Classify the score trajectory across iterations.

    Args:
        scores: Satisfaction scores per iteration (index 0 = iter 1).
        plateau_threshold: Max absolute difference to consider a plateau.

    Returns:
        TrajectoryType classification.

    Priority order matches bash: INSUFFICIENT → IMPROVING → DEGRADING →
    PLATEAU → OSCILLATING → RECOVERING → MIXED.
    """
    if len(scores) < 2:
        return TrajectoryType.INSUFFICIENT

    deltas = [scores[i] - scores[i - 1] for i in range(1, len(scores))]

    # All improving.
    if all(d > 0 for d in deltas):
        return TrajectoryType.IMPROVING

    # All degrading.
    if all(d < 0 for d in deltas):
        return TrajectoryType.DEGRADING

    # Plateau: all deltas within threshold.
    if all(abs(d) <= plateau_threshold for d in deltas):
        return TrajectoryType.PLATEAU

    # Oscillating: every consecutive delta pair alternates sign.
    # Matches bash behavior: with 2+ deltas, if all pairs alternate, it's oscillating.
    if len(deltas) >= 2:
        all_alternate = all(
            deltas[i] * deltas[i - 1] < 0
            for i in range(1, len(deltas))
        )
        if all_alternate:
            return TrajectoryType.OSCILLATING

    # Recovering: last delta is positive after a previous negative.
    if len(deltas) >= 2 and deltas[-1] > 0 and any(d < 0 for d in deltas[:-1]):
        return TrajectoryType.RECOVERING

    return TrajectoryType.MIXED


def build_layer_trajectories(
    iteration_categories: list[list[CategoryResult]],
) -> list[LayerTrajectory]:
    """Build per-category score trajectories across iterations.

    Args:
        iteration_categories: List of category results per iteration.
            iteration_categories[0] = categories from iteration 1, etc.

    Returns:
        List of LayerTrajectory, one per category observed.
    """
    all_cats: dict[str, LayerTrajectory] = {}

    for iter_cats in iteration_categories:
        for cat in iter_cats:
            if cat.name not in all_cats:
                all_cats[cat.name] = LayerTrajectory(category=cat.name)
            all_cats[cat.name].scores.append(cat.score)

    # Identify stuck layers (0% across all iterations).
    for lt in all_cats.values():
        if lt.scores and all(s == 0 for s in lt.scores):
            lt.stuck = True

    return list(all_cats.values())


def strategy_recommendation(trajectory: TrajectoryType) -> str:
    """Return a strategy recommendation string for the Oracle.

    Returns a string (backward compatible). For structured recommendations,
    use strategy_action().
    """
    return str(strategy_action(trajectory))


def strategy_action(trajectory: TrajectoryType) -> StrategyAction:
    """Return a structured StrategyAction for the given trajectory."""
    actions: dict[TrajectoryType, StrategyAction] = {
        TrajectoryType.IMPROVING: StrategyAction(
            StrategyActionType.CONTINUE,
            "Continue iterating — positive trajectory.",
        ),
        TrajectoryType.DEGRADING: StrategyAction(
            StrategyActionType.SWITCH_MODEL,
            "Consider reverting to previous iteration approach.",
        ),
        TrajectoryType.PLATEAU: StrategyAction(
            StrategyActionType.FRESH_START,
            "Fundamentally different approach needed — same strategy is not working.",
        ),
        TrajectoryType.OSCILLATING: StrategyAction(
            StrategyActionType.SWITCH_MODEL,
            "Stabilize approach — oscillating between strategies.",
        ),
        TrajectoryType.RECOVERING: StrategyAction(
            StrategyActionType.CONTINUE,
            "Recent improvement — continue with current approach.",
        ),
        TrajectoryType.MIXED: StrategyAction(
            StrategyActionType.ESCALATE,
            "Mixed signals — review per-category breakdown for stuck layers.",
        ),
        TrajectoryType.INSUFFICIENT: StrategyAction(
            StrategyActionType.CONTINUE,
            "Insufficient data for trajectory analysis.",
        ),
    }
    result = actions.get(trajectory)
    if result is None:
        raise ValueError(f"unhandled TrajectoryType: {trajectory}")
    return result


def detect_stable_failures(
    failing_sets: list[list[str]],
    threshold: float = 0.7,
    min_invariant_count: int = 2,
) -> tuple[float, set[str]]:
    """Detect invariant failure set across iterations.

    Args:
        failing_sets: List of failing test name lists, one per iteration.
        threshold: Ratio above which failures are considered scenario-dominated.
        min_invariant_count: Minimum absolute invariant failures for the
            lower-threshold OR-clause (catches small persistent failure sets).

    Returns:
        (invariant_ratio, invariant_test_names). Triggers when EITHER:
        - invariant_ratio > threshold, OR
        - invariant_count >= min_invariant_count AND invariant_ratio > 0.5
    """
    # Need at least 2 iterations with failure data.
    non_empty = [set(fs) for fs in failing_sets if fs]
    if len(non_empty) < 2:
        return 0.0, set()

    invariant = non_empty[0]
    all_failures: set[str] = set(non_empty[0])
    for fs in non_empty[1:]:
        invariant &= fs
        all_failures |= fs

    if not all_failures:
        return 0.0, set()

    ratio = len(invariant) / len(all_failures)

    # Dual-trigger: high ratio OR small-but-persistent set with moderate ratio.
    if ratio > threshold:
        return ratio, invariant
    if len(invariant) >= min_invariant_count and ratio > 0.5:
        return ratio, invariant

    return ratio, set()


def build_oracle_analysis(
    scores: list[int],
    layer_trajectories: list[LayerTrajectory],
    trajectory: TrajectoryType,
    config_weights: dict[str, int],
) -> str:
    """Build the satisfaction analysis block for the Oracle prompt.

    Returns a formatted string with score history, trajectory classification,
    per-category table, and strategy recommendation.
    """
    lines: list[str] = []
    lines.append("## Satisfaction Analysis")
    lines.append("")
    lines.append(f"Score trajectory: {' → '.join(str(s) for s in scores)}")
    lines.append(f"Classification: {trajectory.value}")
    lines.append(f"Strategy: {strategy_recommendation(trajectory)}")
    lines.append("")

    # Per-category table.
    if layer_trajectories:
        lines.append("| Category | Weight | Scores | Status |")
        lines.append("|----------|--------|--------|--------|")
        for lt in layer_trajectories:
            weight = config_weights.get(lt.category, 100)
            score_str = " → ".join(str(s) for s in lt.scores)
            status = "STUCK" if lt.stuck else "ok"
            lines.append(f"| {lt.category} | {weight} | {score_str} | {status} |")
        lines.append("")

    stuck = [lt for lt in layer_trajectories if lt.stuck]
    if stuck:
        lines.append(f"**Stuck layers ({len(stuck)}):** {', '.join(lt.category for lt in stuck)}")
        lines.append("")

    return "\n".join(lines)
