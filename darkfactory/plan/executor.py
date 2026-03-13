"""Plan executor — concurrent spec dispatch with ThreadPoolExecutor.

Replaces dispatch_plan() from dark-factory-plan.sh. Dispatches specs
in dependency order, respects concurrency limits, handles resume,
wall-time timeout, and budget enforcement.
"""

from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.config.schema import validate_spec_name
from darkfactory.errors import ConfigError, DarkFactoryError
from darkfactory.events.types import (
    emit_plan_complete,
    emit_plan_resume,
    emit_plan_start,
    emit_plan_timeout,
    emit_spec_dispatched,
)
from darkfactory.git import worktree
from darkfactory.models.config import PipelineConfig
from darkfactory.models.plan import PlanSpec, SpecDispatchStatus
from darkfactory.plan.conflict import detect_conflicts, serialize_conflicts
from darkfactory.plan.governor import check_budget_pre_dispatch, preflight
from darkfactory.plan.parser import parse_plan
from darkfactory.plan.topo_sort import topological_sort
from darkfactory.pipeline.orchestrator import run_pipeline
from darkfactory.state import plan_checkpoint
from darkfactory.utils.hash import sha256_file

log = logging.getLogger(__name__)


@dataclass
class PlanResult:
    """Result of a plan execution."""
    shipped: int = 0
    failed: int = 0
    skipped: int = 0
    wall_seconds: float = 0.0


def execute_plan(
    plan_name: str,
    config: PipelineConfig,
    project_dir: Path,
    *,
    resume: bool = False,
    optimize: bool = False,
    dry_run: bool = False,
    events_file: Path | None = None,
) -> PlanResult:
    """Execute a plan — parse, sort, dispatch specs concurrently.

    Returns PlanResult with counts.
    Raises ConfigError on invalid plans.
    """
    validate_spec_name(plan_name)
    plan_file = project_dir / ".dark-factory" / "plans" / f"{plan_name}.md"
    if not plan_file.exists():
        raise ConfigError(f"plan file not found: {plan_file}")

    # Parse plan.
    specs = parse_plan(plan_file, project_dir)

    # Topological sort.
    dispatch_order = topological_sort(specs)

    # Plan optimization (reorder within dependency tiers).
    if optimize:
        from darkfactory.plan.optimizer import optimize_plan
        dispatch_order = optimize_plan(dispatch_order, events_file)

    # Conflict detection.
    report = detect_conflicts(specs)
    if report.has_conflicts:
        strategy = config.conflict_strategy
        if strategy == "abort":
            raise ConfigError(
                "file conflicts detected — resolve by adjusting specs "
                "or set conflict_strategy = 'serialize'"
            )
        elif strategy == "serialize":
            log.warning("serializing conflicting specs")
            serialize_conflicts(specs, dispatch_order)
            # Re-sort with new edges.
            dispatch_order = topological_sort(specs)
        else:
            log.warning("proceeding despite file conflicts (strategy=%s)", strategy)

    # Resource preflight.
    warnings = preflight(config)
    for w in warnings:
        log.warning("governor: %s", w)

    plan_run_id = uuid.uuid4().hex[:16]
    state_dir = project_dir / ".dark-factory" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    if events_file:
        emit_plan_start(events_file, plan_run_id=plan_run_id, spec_count=len(specs))

    # Resume handling.
    completed: set[str] = set()
    if resume:
        completed = _handle_resume(plan_name, state_dir, project_dir, specs, events_file, plan_run_id)

    if dry_run:
        log.info("[dry-run] dispatch order: %s", [s.name for s in dispatch_order])
        return PlanResult()

    # Dispatch.
    start = time.monotonic()
    result = _dispatch(
        dispatch_order, config, project_dir,
        plan_name=plan_name, plan_run_id=plan_run_id,
        state_dir=state_dir, completed=completed,
        events_file=events_file,
    )
    result.wall_seconds = time.monotonic() - start

    if events_file:
        emit_plan_complete(
            events_file, plan_run_id=plan_run_id,
            shipped=result.shipped, failed=result.failed, skipped=result.skipped,
        )

    # Worktree cleanup for shipped specs.
    if config.worktree_cleanup_on_ship:
        for spec in dispatch_order:
            if spec.status == SpecDispatchStatus.SHIPPED and spec.worktree_path:
                worktree.remove(project_dir, Path(spec.worktree_path), force=True)

    return result


def _handle_resume(
    plan_name: str,
    state_dir: Path,
    project_dir: Path,
    specs: list[PlanSpec],
    events_file: Path | None,
    plan_run_id: str,
) -> set[str]:
    """Resume from checkpoint — mark completed specs. Returns set of completed spec names."""
    ckpt = plan_checkpoint.load_latest(state_dir, plan_name)
    if not ckpt:
        log.info("no checkpoint found for %r, running all specs", plan_name)
        return set()

    completed_names = plan_checkpoint.completed_specs(ckpt)
    spec_names = {s.name for s in specs}
    resumed = 0
    rerun = 0

    for name in completed_names:
        if name not in spec_names:
            continue

        # Verify spec hash unchanged.
        old_hash = plan_checkpoint.spec_hash(ckpt, name)
        spec_file = project_dir / ".dark-factory" / "specs" / f"{name}.md"
        new_hash = sha256_file(spec_file) if spec_file.exists() else ""

        if old_hash and old_hash == new_hash:
            # Mark as shipped in spec list.
            for s in specs:
                if s.name == name:
                    s.status = SpecDispatchStatus.SHIPPED
            resumed += 1
        else:
            rerun += 1

    actually_resumed = {s.name for s in specs if s.status == SpecDispatchStatus.SHIPPED}

    if resumed or rerun:
        log.info("plan resume: skipping %d completed, %d changed (will re-run)", resumed, rerun)
        if events_file:
            emit_plan_resume(
                events_file, plan_run_id=plan_run_id,
                completed_specs=list(actually_resumed),
            )

    return actually_resumed


def _dispatch(
    dispatch_order: list[PlanSpec],
    config: PipelineConfig,
    project_dir: Path,
    *,
    plan_name: str,
    plan_run_id: str,
    state_dir: Path,
    completed: set[str],
    events_file: Path | None = None,
) -> PlanResult:
    """Dispatch specs respecting dependencies and concurrency limits."""
    max_concurrent = config.max_concurrent_specs
    plan_budget = config.plan_token_budget
    per_spec_turns = config.agent_max_turns
    allocated_turns = 0
    plan_start = time.monotonic()

    result = PlanResult()
    by_name = {s.name: s for s in dispatch_order}
    futures: dict[Future[int], PlanSpec] = {}

    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        while True:
            # Check wall-time timeout.
            if config.plan_max_wall_time_s > 0:
                elapsed = time.monotonic() - plan_start
                if elapsed > config.plan_max_wall_time_s:
                    log.warning("plan wall-time limit exceeded (%.0fs)", elapsed)
                    if events_file:
                        emit_plan_timeout(
                            events_file, plan_run_id=plan_run_id, wall_s=elapsed,
                        )
                    # Cancel pending futures.
                    for fut in futures:
                        fut.cancel()
                    # Mark remaining pending as failed.
                    for spec in dispatch_order:
                        if spec.status == SpecDispatchStatus.PENDING:
                            spec.status = SpecDispatchStatus.FAILED
                            result.failed += 1
                    break

            # Collect completed futures.
            done_futures = [f for f in futures if f.done()]
            for fut in done_futures:
                spec = futures.pop(fut)
                try:
                    exit_code = fut.result()
                except Exception as e:
                    log.warning("spec %s raised: %s", spec.name, e)
                    exit_code = 1

                spec.exit_code = exit_code

                if exit_code == 0:
                    spec.status = SpecDispatchStatus.SHIPPED
                    result.shipped += 1
                    plan_checkpoint.write(
                        state_dir, plan_name, plan_run_id,
                        spec.name, "shipped", exit_code,
                        spec_path=project_dir / ".dark-factory" / "specs" / f"{spec.name}.md",
                    )
                else:
                    spec.status = SpecDispatchStatus.FAILED
                    result.failed += 1
                    plan_checkpoint.write(
                        state_dir, plan_name, plan_run_id,
                        spec.name, "failed", exit_code,
                        spec_path=project_dir / ".dark-factory" / "specs" / f"{spec.name}.md",
                    )

            # Dispatch ready specs.
            active_count = len(futures)
            for spec in dispatch_order:
                if spec.status != SpecDispatchStatus.PENDING:
                    continue
                if spec.name in completed:
                    continue
                if active_count >= max_concurrent:
                    break

                # Check for failed dependencies.
                blocked = False
                for dep_name in spec.deps:
                    dep = by_name.get(dep_name)
                    if dep and dep.status in (
                        SpecDispatchStatus.FAILED,
                        SpecDispatchStatus.BUDGET_SKIPPED,
                    ):
                        spec.status = SpecDispatchStatus.FAILED
                        result.failed += 1
                        log.warning("spec %s blocked by failed dependency %s", spec.name, dep_name)
                        blocked = True
                        break
                if blocked:
                    continue

                # Check all deps satisfied.
                deps_met = all(
                    by_name[d].status == SpecDispatchStatus.SHIPPED
                    for d in spec.deps if d in by_name
                )
                if not deps_met:
                    continue

                # Budget check.
                if not check_budget_pre_dispatch(allocated_turns, per_spec_turns, plan_budget):
                    spec.status = SpecDispatchStatus.BUDGET_SKIPPED
                    result.skipped += 1
                    log.warning("spec %s skipped (budget: %d+%d > %d)",
                                spec.name, allocated_turns, per_spec_turns, plan_budget)
                    plan_checkpoint.write(
                        state_dir, plan_name, plan_run_id,
                        spec.name, "budget_skipped", 0,
                    )
                    continue

                # Create worktree and dispatch.
                run_id = f"{plan_run_id}-{uuid.uuid4().hex[:8]}"
                spec.exit_code = -1

                config_path = project_dir / ".dark-factory" / "config.toml"
                spec_path = project_dir / ".dark-factory" / "specs" / f"{spec.name}.md"
                try:
                    wt_path = worktree.create(
                        project_dir, spec.name, run_id,
                        base_branch=config.git_base_branch,
                        config_path=config_path if config_path.exists() else None,
                        spec_path=spec_path if spec_path.exists() else None,
                    )
                except DarkFactoryError as e:
                    log.warning("worktree creation failed for %s: %s", spec.name, e)
                    spec.status = SpecDispatchStatus.FAILED
                    result.failed += 1
                    continue

                spec.worktree_path = str(wt_path)
                spec.status = SpecDispatchStatus.RUNNING
                allocated_turns += per_spec_turns

                if events_file:
                    emit_spec_dispatched(
                        events_file, spec=spec.name, run_id=run_id,
                        plan_run_id=plan_run_id, worktree=str(wt_path),
                    )

                fut = executor.submit(
                    _run_spec, spec.name, config, wt_path, run_id,
                )
                futures[fut] = spec
                active_count += 1
                log.info("dispatched: %s (worktree: %s)", spec.name, wt_path)

            # Check termination.
            all_done = all(
                s.status not in (SpecDispatchStatus.PENDING, SpecDispatchStatus.RUNNING)
                for s in dispatch_order
                if s.name not in completed
            )
            if all_done and not futures:
                break

            # Brief sleep to avoid busy-wait.
            if futures:
                time.sleep(1)

    return result


def _run_spec(
    spec_name: str,
    config: PipelineConfig,
    worktree_path: Path,
    run_id: str,
) -> int:
    """Run a single spec in a worktree. Returns exit code.

    Wraps run_pipeline with transient failure retry at the plan level.
    """
    from darkfactory.errors import TransientFailure
    from darkfactory.utils.backoff import transient_backoff

    def _prefixed(msg: str) -> None:
        print(f"[{spec_name}] {msg}")

    max_retries = config.transient_retry_max
    for attempt in range(max_retries + 1):
        try:
            return run_pipeline(
                spec_name, config, worktree_path,
                progress=_prefixed,
            )
        except TransientFailure as e:
            if attempt < max_retries:
                delay = transient_backoff(attempt, config.transient_retry_base_s)
                log.warning(
                    "spec %s transient failure (plan-level retry %d/%d), "
                    "backing off %.1fs: %s",
                    spec_name, attempt + 1, max_retries, delay, e,
                )
                time.sleep(delay)
            else:
                log.error("spec %s transient failure after %d retries: %s", spec_name, max_retries, e)
                return 1
        except Exception as e:
            log.error("spec %s failed with exception: %s", spec_name, e)
            return 1
    return 1
