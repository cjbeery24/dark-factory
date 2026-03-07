"""Plan optimization — reorder specs for minimum wall time."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from darkfactory.models.plan import PlanSpec

log = logging.getLogger(__name__)


@dataclass
class SpecHistory:
    """Historical performance data for a spec."""
    avg_wall_seconds: float = 0.0
    failure_rate: float = 0.0
    run_count: int = 0


def optimize_plan(
    specs: list[PlanSpec],
    events_file: Path | None = None,
) -> list[PlanSpec]:
    """Reorder specs for minimum wall time within dependency constraints.

    Strategies:
    1. Failure-likelihood ordering: historically-failing specs first for fast failure
    2. Critical-path scheduling: longest specs first when parallelizable

    Does not change dependency order — only reorders within the same
    dependency tier (specs with identical dependency sets).
    """
    if not specs or events_file is None:
        return specs

    history = _load_history(events_file)
    if not history:
        return specs

    # Group specs by dependency tier.
    tiers = _group_by_deps(specs)
    result: list[PlanSpec] = []

    for tier in tiers:
        if len(tier) <= 1:
            result.extend(tier)
            continue

        # Sort within tier: high failure rate first, then longest wall time first.
        tier.sort(key=lambda s: (
            -history.get(s.name, SpecHistory()).failure_rate,
            -history.get(s.name, SpecHistory()).avg_wall_seconds,
        ))
        result.extend(tier)

    return result


def _group_by_deps(specs: list[PlanSpec]) -> list[list[PlanSpec]]:
    """Group specs into dependency tiers.

    A tier is a set of specs that have the same resolved dependency depth.
    """
    by_name = {s.name: s for s in specs}
    depths: dict[str, int] = {}
    visiting: set[str] = set()

    def depth(name: str) -> int:
        if name in depths:
            return depths[name]
        if name in visiting:
            # Cycle detected — treat as depth 0 to avoid infinite recursion.
            return 0
        visiting.add(name)
        spec = by_name.get(name)
        if spec is None or not spec.deps:
            depths[name] = 0
            visiting.discard(name)
            return 0
        d = 1 + max(depth(dep) for dep in spec.deps if dep in by_name)
        depths[name] = d
        visiting.discard(name)
        return d

    for s in specs:
        depth(s.name)

    max_depth = max(depths.values()) if depths else 0
    tiers: list[list[PlanSpec]] = [[] for _ in range(max_depth + 1)]
    for s in specs:
        tiers[depths[s.name]].append(s)

    return [t for t in tiers if t]


def _load_history(events_file: Path) -> dict[str, SpecHistory]:
    """Load historical performance from events file."""
    if not events_file.exists():
        return {}

    stats: dict[str, dict] = {}  # name -> {total_wall: float, failures: int, runs: int}

    with events_file.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("event_type", "")
            spec = event.get("spec_name", "")
            data = event.get("data", {})

            if not spec:
                continue

            if spec not in stats:
                stats[spec] = {"total_wall": 0.0, "failures": 0, "runs": 0}

            if etype == "SPEC_SHIPPED":
                stats[spec]["runs"] += 1
                wall = data.get("wall_seconds", 0)
                stats[spec]["total_wall"] += wall

            elif etype in ("SPEC_FAILED", "SPEC_ESCALATED"):
                stats[spec]["runs"] += 1
                stats[spec]["failures"] += 1

    result: dict[str, SpecHistory] = {}
    for name, s in stats.items():
        runs = s["runs"]
        if runs == 0:
            continue
        result[name] = SpecHistory(
            avg_wall_seconds=s["total_wall"] / runs,
            failure_rate=s["failures"] / runs,
            run_count=runs,
        )

    return result
