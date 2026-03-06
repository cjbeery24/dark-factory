"""File conflict detection and serialization for concurrent spec execution.

Replaces conflict_detect() and serialize_conflicting_specs() from
dark-factory-plan.sh / worktree.sh.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from darkfactory.errors import ConfigError
from darkfactory.models.plan import PlanSpec

log = logging.getLogger(__name__)


@dataclass
class ConflictReport:
    """Result of conflict detection."""
    has_conflicts: bool = False
    # file -> list of spec names that target it.
    file_owners: dict[str, list[str]] = field(default_factory=dict)


def detect_conflicts(specs: list[PlanSpec]) -> ConflictReport:
    """Detect file overlap between specs based on target_files metadata.

    Returns a ConflictReport. has_conflicts is True if any file is
    targeted by more than one spec.
    """
    file_owners: dict[str, list[str]] = {}
    for spec in specs:
        for f in spec.target_files:
            file_owners.setdefault(f, []).append(spec.name)

    conflicts = {f: owners for f, owners in file_owners.items() if len(owners) > 1}
    if conflicts:
        for f, owners in conflicts.items():
            log.warning("file conflict: %s claimed by %s", f, ", ".join(owners))

    return ConflictReport(
        has_conflicts=bool(conflicts),
        file_owners=conflicts,
    )


def serialize_conflicts(
    specs: list[PlanSpec],
    dispatch_order: list[PlanSpec],
) -> list[PlanSpec]:
    """Add synthetic dependency edges between specs that share target files.

    Uses dispatch_order to determine "first owner" — the spec that appears
    first in the topological order owns contested files. Later specs get
    a dependency on the first owner.

    Modifies specs in place (adds deps) and returns the updated list.

    Raises ConfigError if serialization would create a cycle (detected
    by the caller when re-running topological sort).
    """
    # Build ordered name list for ownership precedence.
    order_index = {s.name: i for i, s in enumerate(dispatch_order)}

    # file -> first owner spec name (by dispatch order).
    file_first_owner: dict[str, str] = {}

    for spec in dispatch_order:
        for f in spec.target_files:
            if f in file_first_owner:
                first = file_first_owner[f]
                if first != spec.name and first not in spec.deps:
                    spec.deps.append(first)
                    log.info(
                        "serialized: %s now depends on %s (shared file: %s)",
                        spec.name, first, f,
                    )
            else:
                file_first_owner[f] = spec.name

    return specs
