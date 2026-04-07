"""Phase 5.3 — deterministic regression selection under a wall-clock budget.

Given the list of shipped specs and a budget, pick which ones to actually
run on this regression pass and which ones get deferred. The selection
must be **reproducible**: same input set + same budget yields the same
selected subset, so a flake hunter can replay a regression run from
``shipped.jsonl`` and ``events.jsonl`` alone.

Ordering rule (per plan §5.3): newest-shipped first, then oldest. The
intuition: a regression in a *recently* shipped spec is most likely to
catch a fresh bug; a regression in an *ancient* spec is most likely to
catch foundational drift. The middle of the archive is the cheapest to
defer because it's both stable and not load-bearing.

Tiebreaker: ``shipped_at`` ties resolve by SHA-256 of ``spec_id`` so the
order is independent of filesystem order, dict insertion order, or
Python hash randomization.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpecMeta:
    """Minimal selection input — only what the selector needs."""
    spec_id: str
    shipped_at: datetime


@dataclass(frozen=True)
class Selection:
    """Result of a budget-bounded selection.

    ``selected`` is in run order. ``deferred`` is in archive (newest-first)
    order — useful for the gate report's "would have run next" list.
    """
    selected: list[str]
    deferred: list[str]
    budget_seconds: int
    avg_spec_seconds: int
    capacity: int  # max specs that fit in budget; selected may be shorter


def _stable_key(spec_id: str) -> str:
    return hashlib.sha256(spec_id.encode("utf-8")).hexdigest()


def _newest_first(specs: Iterable[SpecMeta]) -> list[SpecMeta]:
    """Sort newest-shipped first with stable tiebreaker."""
    # Negative timestamp gives descending; tiebreaker is sha256 of spec_id
    # so ordering is platform/PYTHONHASHSEED-independent.
    return sorted(
        specs,
        key=lambda s: (-s.shipped_at.timestamp(), _stable_key(s.spec_id)),
    )


def select_within_budget(
    specs: Iterable[SpecMeta],
    *,
    budget_seconds: int,
    avg_spec_seconds: int,
) -> Selection:
    """Pick specs to run under a wall-clock budget.

    Algorithm:
      1. Capacity = ``budget_seconds // avg_spec_seconds`` (floor).
      2. Order all specs newest-first.
      3. If everything fits, run everything.
      4. Else split capacity in half: take the newest ``ceil(cap/2)`` and
         the oldest ``cap - ceil(cap/2)`` — this gives "recent regressions
         + ancient drift" coverage and skips the stable middle.
      5. Run order = newest selected first, then ancient selected
         (chronological within each half) so the events log reads
         intuitively.

    Edge cases:
      - ``budget_seconds <= 0`` or ``avg_spec_seconds <= 0`` → run nothing
        (configuration bug; selector logs and defers all).
      - Empty input → empty selection.
    """
    spec_list = _newest_first(specs)
    n = len(spec_list)

    if budget_seconds <= 0 or avg_spec_seconds <= 0:
        log.warning(
            "regression selection: non-positive budget (%ds) or avg (%ds); "
            "deferring all %d spec(s)",
            budget_seconds, avg_spec_seconds, n,
        )
        return Selection(
            selected=[],
            deferred=[s.spec_id for s in spec_list],
            budget_seconds=budget_seconds,
            avg_spec_seconds=avg_spec_seconds,
            capacity=0,
        )

    capacity = budget_seconds // avg_spec_seconds

    if capacity >= n:
        return Selection(
            selected=[s.spec_id for s in spec_list],
            deferred=[],
            budget_seconds=budget_seconds,
            avg_spec_seconds=avg_spec_seconds,
            capacity=capacity,
        )

    if capacity == 0:
        return Selection(
            selected=[],
            deferred=[s.spec_id for s in spec_list],
            budget_seconds=budget_seconds,
            avg_spec_seconds=avg_spec_seconds,
            capacity=0,
        )

    # Split: ceil(cap/2) newest, rest oldest.
    newest_n = (capacity + 1) // 2
    oldest_n = capacity - newest_n
    newest = spec_list[:newest_n]
    oldest = spec_list[-oldest_n:] if oldest_n > 0 else []

    selected_set = {s.spec_id for s in newest} | {s.spec_id for s in oldest}
    selected = [s.spec_id for s in newest] + [
        s.spec_id for s in reversed(oldest)  # oldest-first within the tail
    ]
    deferred = [s.spec_id for s in spec_list if s.spec_id not in selected_set]

    return Selection(
        selected=selected,
        deferred=deferred,
        budget_seconds=budget_seconds,
        avg_spec_seconds=avg_spec_seconds,
        capacity=capacity,
    )
