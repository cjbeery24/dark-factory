"""Topological sort — Kahn's algorithm with priority tie-breaking.

Replaces topological_sort() from dark-factory-plan.sh.
"""

from __future__ import annotations

import heapq

from darkfactory.errors import ConfigError
from darkfactory.models.plan import PlanSpec


def topological_sort(specs: list[PlanSpec]) -> list[PlanSpec]:
    """Sort specs in dependency order using Kahn's algorithm.

    Within each dependency tier, specs are sorted by priority (ascending,
    lower number = higher priority). Parse order is used as a stable
    tie-breaker for equal priorities.

    Raises ConfigError if a circular dependency is detected.
    """
    # Compute in-degree for each spec.
    in_degree: dict[str, int] = {s.name: 0 for s in specs}
    for spec in specs:
        for dep in spec.deps:
            if dep in in_degree:
                in_degree[spec.name] += 1

    by_name = {s.name: s for s in specs}
    parse_index = {s.name: i for i, s in enumerate(specs)}

    # Min-heap keyed by (priority, parse_index) for stable ordering.
    heap: list[tuple[int, int, str]] = []
    for spec in specs:
        if in_degree[spec.name] == 0:
            heapq.heappush(heap, (spec.priority, parse_index[spec.name], spec.name))

    result: list[PlanSpec] = []

    while heap:
        _prio, _idx, name = heapq.heappop(heap)
        current = by_name[name]
        result.append(current)

        # Find specs that depend on current and reduce in-degree.
        for spec in specs:
            if current.name in spec.deps:
                in_degree[spec.name] -= 1
                if in_degree[spec.name] == 0:
                    heapq.heappush(
                        heap, (spec.priority, parse_index[spec.name], spec.name)
                    )

    # Cycle detection.
    if len(result) != len(specs):
        in_cycle = [s.name for s in specs if s.name not in {r.name for r in result}]
        raise ConfigError(
            f"circular dependency detected in plan. "
            f"Specs involved: {', '.join(in_cycle)}"
        )

    return result
