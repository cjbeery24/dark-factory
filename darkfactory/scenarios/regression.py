"""Regression test runner — run all shipped scenarios against current codebase.

Replaces run-regression.sh.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from darkfactory.events.types import emit_regression_run
from darkfactory.models.config import PipelineConfig
from darkfactory.plan.state import load_shipped
from darkfactory.scenarios.regression_select import (
    Selection,
    SpecMeta,
    select_within_budget,
)
from darkfactory.scenarios.runner import run_scenarios
from darkfactory.state.registry import list_specs, migrate_from_state, scenario_dir

log = logging.getLogger(__name__)


def run_regression(
    config: PipelineConfig,
    project_dir: Path,
    *,
    exclude: list[str] | None = None,
    changed_files: list[str] | None = None,
    timeout: int = 300,
    events_file: Path | None = None,
    run_id: str = "",
) -> tuple[int, int, int, list[str]]:
    """Run regression tests for shipped specs under a wall-clock budget.

    Selection is deterministic: shipped specs are ordered newest-first,
    capped at ``budget_seconds // avg_spec_seconds`` entries, split into
    newest+oldest halves so the middle of the archive is deferred. Same
    inputs always produce the same selected subset.

    If ``changed_files`` is provided, the candidate set is first filtered
    by `affected_specs` *before* budget selection — the budget applies
    to the affected subset.

    Returns ``(passed, failed, total, deferred_spec_ids)``.
    """
    # One-time migration from legacy state files.
    migrate_from_state(project_dir)

    exclude_set = set(exclude or [])
    specs = [s for s in list_specs(project_dir) if s not in exclude_set]

    if changed_files is not None:
        from darkfactory.scenarios.coverage import affected_specs
        affected = set(affected_specs(project_dir, set(changed_files)))
        specs = [s for s in specs if s in affected]

    if not specs:
        log.warning("no registered specs for regression")
        return (0, 0, 0, [])

    # Phase 5.3: budget-bounded deterministic selection.
    #
    # Specs missing from shipped.jsonl (registered before Phase 5.1 landed
    # or migrated from legacy state) get shipped_at=now so they rotate
    # through the *newest-first* priority half rather than starving
    # actually-old-but-tracked specs out of the oldest-tail slot. We log a
    # WARN naming the legacy specs so operators know to backfill.
    shipped_rows = {r.spec_id: r for r in load_shipped(project_dir, strict=False)}
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    legacy = [s for s in specs if s not in shipped_rows]
    if legacy:
        log.warning(
            "regression: %d spec(s) missing from shipped.jsonl, treating as "
            "newest for selection: %s",
            len(legacy), ",".join(legacy),
        )
    metas = [
        SpecMeta(
            spec_id=s,
            shipped_at=shipped_rows[s].shipped_at if s in shipped_rows else now,
        )
        for s in specs
    ]
    selection = select_within_budget(
        metas,
        budget_seconds=config.regression_budget_seconds,
        avg_spec_seconds=config.regression_avg_spec_seconds,
    )
    if selection.deferred:
        log.info(
            "regression: %d/%d specs deferred (budget=%ds, capacity=%d): %s",
            len(selection.deferred), len(metas),
            config.regression_budget_seconds, selection.capacity,
            ",".join(selection.deferred),
        )
    if events_file:
        try:
            from darkfactory.events.emitter import emit_event
            from darkfactory.models.events import EventType
            emit_event(
                events_file,
                EventType.REGRESSION_SELECTION,
                spec_name="regression",
                run_id=run_id,
                data={
                    "selected": selection.selected,
                    "deferred": selection.deferred,
                    "budget_seconds": config.regression_budget_seconds,
                    "avg_spec_seconds": config.regression_avg_spec_seconds,
                    "capacity": selection.capacity,
                    "total_candidates": len(metas),
                },
            )
        except Exception:  # noqa: BLE001 — events are best-effort
            log.exception("failed to emit regression_selection event")

    start = time.monotonic()
    passed = 0
    failed = 0
    runtime_deferred: list[str] = []
    durations: dict[str, float] = {}

    budget_s = config.regression_budget_seconds
    avg_s = config.regression_avg_spec_seconds

    for idx, spec_name in enumerate(selection.selected):
        # Runtime wall-clock kill switch: if elapsed + one more avg-cost
        # spec would blow the budget, stop dispatching and bank the rest
        # as deferred. The static capacity math is an estimate; this is
        # the bound that actually holds the line.
        elapsed = time.monotonic() - start
        if budget_s > 0 and elapsed + avg_s > budget_s and idx > 0:
            runtime_deferred = list(selection.selected[idx:])
            log.warning(
                "regression: wall-clock budget %ds exceeded after %.1fs; "
                "deferring %d remaining spec(s): %s",
                budget_s, elapsed, len(runtime_deferred), ",".join(runtime_deferred),
            )
            break

        sdir = scenario_dir(project_dir, spec_name)
        if not sdir:
            log.warning("no scenario dir for %s, skipping", spec_name)
            continue

        # Symlink registry scenarios into the expected location.
        target = project_dir / ".dark-factory" / "scenarios" / spec_name
        _link_scenarios(sdir, target)

        spec_start = time.monotonic()
        try:
            # Each archived spec runs in isolation here — regression.py
            # already iterates the archive itself, so we don't pass spec_type
            # (which would re-union the archive into every per-spec call).
            result = run_scenarios(spec_name, config, project_dir, timeout=timeout)
            if result.exit_code == 0:
                passed += 1
                log.info("regression PASS: %s", spec_name)
            else:
                failed += 1
                log.warning("regression FAIL: %s (exit %d)", spec_name, result.exit_code)
        finally:
            durations[spec_name] = round(time.monotonic() - spec_start, 2)
            _unlink_scenarios(target)

    total = passed + failed
    wall_s = round(time.monotonic() - start, 2)
    # Selection-time deferred + runtime-killswitch deferred = full deferred.
    all_deferred = list(selection.deferred) + runtime_deferred

    if events_file:
        emit_regression_run(
            events_file, spec="regression", run_id=run_id,
            passed=passed, failed=failed, total=total,
            wall_s=wall_s,
        )

    # Emit per-spec durations as a separate event so a future v1.1
    # selector can compute a rolling avg_spec_seconds from history.
    if events_file and durations:
        try:
            from darkfactory.events.emitter import emit_event
            from darkfactory.models.events import EventType
            emit_event(
                events_file,
                EventType.REGRESSION_SELECTION,
                spec_name="regression",
                run_id=run_id,
                data={"event_kind": "durations", "durations": durations,
                      "wall_s": wall_s},
            )
        except Exception:  # noqa: BLE001
            log.exception("failed to emit regression durations event")

    return (passed, failed, total, all_deferred)


def _link_scenarios(source: Path, target: Path) -> None:
    """Create a symlink from registry scenarios to expected location."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        return  # Don't overwrite existing scenarios.
    target.symlink_to(source)


def _unlink_scenarios(target: Path) -> None:
    """Remove the symlink created for regression."""
    if target.is_symlink():
        target.unlink()
