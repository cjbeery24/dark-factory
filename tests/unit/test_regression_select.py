"""Phase 5.3 — deterministic regression selector under wall-clock budget."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from darkfactory.scenarios.regression_select import (
    SpecMeta,
    select_within_budget,
)


def _meta(spec_id: str, days_ago: int) -> SpecMeta:
    return SpecMeta(
        spec_id=spec_id,
        shipped_at=datetime(2026, 4, 1, tzinfo=timezone.utc) - timedelta(days=days_ago),
    )


def test_everything_fits():
    metas = [_meta(f"s{i}", i) for i in range(5)]
    sel = select_within_budget(metas, budget_seconds=600, avg_spec_seconds=60)
    assert len(sel.selected) == 5
    assert sel.deferred == []
    # Newest first.
    assert sel.selected == ["s0", "s1", "s2", "s3", "s4"]


def test_truncates_middle_when_over_budget():
    # 10 specs, capacity = 4 → keep 2 newest + 2 oldest, defer middle 6.
    metas = [_meta(f"s{i}", i) for i in range(10)]
    sel = select_within_budget(metas, budget_seconds=240, avg_spec_seconds=60)
    assert sel.capacity == 4
    # newest first half (ceil(4/2)=2): s0, s1; oldest tail: s8, s9 → run order
    # newest, then oldest chronologically (oldest-first within tail).
    assert sel.selected == ["s0", "s1", "s9", "s8"]
    assert set(sel.deferred) == {"s2", "s3", "s4", "s5", "s6", "s7"}


def test_zero_capacity_defers_all():
    metas = [_meta(f"s{i}", i) for i in range(3)]
    sel = select_within_budget(metas, budget_seconds=30, avg_spec_seconds=60)
    assert sel.capacity == 0
    assert sel.selected == []
    assert set(sel.deferred) == {"s0", "s1", "s2"}


def test_negative_budget_defers_all():
    metas = [_meta("a", 0), _meta("b", 1)]
    sel = select_within_budget(metas, budget_seconds=-1, avg_spec_seconds=60)
    assert sel.selected == []
    assert set(sel.deferred) == {"a", "b"}


def test_empty_input():
    sel = select_within_budget([], budget_seconds=600, avg_spec_seconds=60)
    assert sel.selected == [] and sel.deferred == []


def test_reproducible_with_shuffled_input():
    # Same input set + same budget = same selected subset, regardless of
    # iteration order. Tiebreaker is sha256 of spec_id, not dict insertion.
    metas = [_meta(f"s{i}", i) for i in range(8)]
    a = select_within_budget(metas, budget_seconds=180, avg_spec_seconds=60)
    b = select_within_budget(list(reversed(metas)), budget_seconds=180, avg_spec_seconds=60)
    assert a.selected == b.selected
    assert a.deferred == b.deferred


def test_capacity_one_picks_only_newest():
    metas = [_meta(f"s{i}", i) for i in range(5)]
    sel = select_within_budget(metas, budget_seconds=60, avg_spec_seconds=60)
    assert sel.capacity == 1
    assert sel.selected == ["s0"]
    assert set(sel.deferred) == {"s1", "s2", "s3", "s4"}


def test_stable_tiebreaker_on_equal_timestamps():
    # All shipped at the same instant — order must come from sha256(spec_id),
    # not list/dict order.
    same = datetime(2026, 4, 1, tzinfo=timezone.utc)
    metas = [SpecMeta(spec_id=x, shipped_at=same) for x in ["zebra", "alpha", "mango"]]
    a = select_within_budget(metas, budget_seconds=600, avg_spec_seconds=60)
    b = select_within_budget(list(reversed(metas)), budget_seconds=600, avg_spec_seconds=60)
    assert a.selected == b.selected
