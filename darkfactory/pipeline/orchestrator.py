"""Main pipeline orchestrator — the 7-step pipeline.

Replaces scripts/dark-factory.sh. Coordinates spec validation,
scenario generation, the iteration loop, and post-pipeline actions
(registry, regression, git, notifications).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from darkfactory.config.schema import validate_spec_name
from darkfactory.docker.barriers import configure_barriers
from darkfactory.docker.client import require_docker
from darkfactory.docker.networks import setup_networks, setup_proxies
from darkfactory.errors import BudgetExceeded, DarkFactoryError, HardAbort, HumanHoldRequired
from darkfactory.events.types import (
    emit_cost_summary,
    emit_spec_escalated,
    emit_spec_failed,
    emit_spec_shipped,
    emit_spec_start,
    emit_trust_scored,
)
from darkfactory.git import ops as git_ops
from darkfactory.intelligence.cost import estimate_cost
from darkfactory.intelligence.metrics import record_end, record_start
from darkfactory.intelligence.trust import calculate_trust, predict_iterations, trust_to_autonomy
from darkfactory.models.agent import AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import PipelineStatus, SpecRun
from darkfactory.notify.dispatcher import notify_escalated, notify_failed, notify_ship_review, notify_shipped
from darkfactory.pipeline.agents import invoke_agent
from darkfactory.pipeline.cleanup import CleanupManager
from darkfactory.pipeline.iteration import run_iteration_loop
from darkfactory.scenarios.detection import has_scenario_files, validate_spec_result
from darkfactory.scenarios.regression import run_regression
from darkfactory.state.registry import register as registry_register
from darkfactory.state.store import read_state, write_state, write_state_or_warn
from darkfactory.utils.hash import sha256_file

log = logging.getLogger(__name__)


def run_pipeline(
    spec_name: str,
    config: PipelineConfig,
    project_dir: Path,
    *,
    resume: bool = False,
    regen_scenarios: bool = False,
    run_regression_on_ship: bool = False,
    events_file: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Run the full 7-step pipeline for a spec.

    Returns exit code: 0=shipped, 1=failed, 2=escalated, 3=aborted.
    """
    validate_spec_name(spec_name)

    # Resolve paths.
    spec_file = project_dir / ".dark-factory" / "specs" / f"{spec_name}.md"
    if not spec_file.exists():
        raise DarkFactoryError(f"spec file not found: {spec_file}")

    state_dir = project_dir / ".dark-factory" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    audit_dir = project_dir / ".dark-factory" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    # Create spec run.
    spec_run = SpecRun(spec_name=spec_name)
    state_file = state_dir / f"{spec_name}.state"
    audit_path = audit_dir / f"{spec_name}-{spec_run.run_id}.log"

    # Spec hash for change detection.
    spec_run.spec_hash = sha256_file(spec_file)

    # Resume detection.
    if resume and state_file.exists():
        _handle_resume(spec_run, state_file, spec_run.spec_hash)

    # Configure barriers.
    barriers = configure_barriers(config.source_dirs)

    # Docker pre-flight.
    if not dry_run:
        require_docker(config.language)

    # Cleanup manager.
    cleanup = CleanupManager(
        run_id=spec_run.run_id,
        dry_run=dry_run,
        deploy_teardown_cmd=config.deploy_teardown_cmd,
        on_interrupt=lambda: _write_interrupted_state(state_file, spec_run),
    )
    cleanup.register()

    # Setup Docker networks and proxies.
    if not dry_run:
        setup_networks(spec_run.run_id)
        setup_proxies(spec_run.run_id, config, dry_run=dry_run)

    # PR preflight.
    if config.auto_pr and not dry_run:
        git_ops.preflight(config, project_dir)

    # Write initial state.
    write_state(state_file, "spec", spec_name)
    write_state(state_file, "status", "running")
    write_state(state_file, "run_id", spec_run.run_id)
    write_state(state_file, "spec_hash", spec_run.spec_hash)
    spec_run.status = PipelineStatus.RUNNING

    if events_file:
        emit_spec_start(events_file, spec=spec_name, run_id=spec_run.run_id)

    # Dynamic autonomy — stored on SpecRun, not mutated on config.
    spec_run.effective_autonomy = config.autonomy_level
    if config.dynamic_autonomy and events_file:
        trust = calculate_trust(events_file, config)
        autonomy = trust_to_autonomy(trust.total)
        spec_run.trust_score = trust.total
        if autonomy < config.autonomy_level:
            log.info("dynamic autonomy: lowering from %d to %d (trust=%d)",
                     config.autonomy_level, autonomy, trust.total)
            spec_run.effective_autonomy = autonomy

        emit_trust_scored(
            events_file, spec=spec_name, run_id=spec_run.run_id,
            trust=trust.total, autonomy=spec_run.effective_autonomy,
        )

    # Dynamic iterations — predict from history.
    if config.dynamic_iterations and events_file:
        predicted = predict_iterations(events_file, config)
        spec_run.effective_max_iterations = min(predicted + 1, config.max_iterations)

    # Metrics.
    metrics_file = project_dir / ".dark-factory" / "metrics.jsonl"
    record_start(metrics_file, spec_name, spec_run.run_id)

    try:
        # Step 1: Spec validation.
        if spec_run.completed_step < 1:
            _step_spec_validation(
                spec_run, config, barriers, project_dir,
                events_file=events_file, audit_path=audit_path, dry_run=dry_run,
            )
            write_state_or_warn(state_file, "completed_step", "1")
            spec_run.completed_step = 1

        # Step 2: Scenario generation.
        if spec_run.completed_step < 2:
            _step_scenario_generation(
                spec_run, config, barriers, project_dir,
                regen=regen_scenarios,
                events_file=events_file, audit_path=audit_path, dry_run=dry_run,
            )
            write_state_or_warn(state_file, "completed_step", "2")
            spec_run.completed_step = 2

        # Resume from hold: oracle already said SHIP, human confirmed.
        # Skip the iteration loop and go straight to ship.
        if spec_run.completed_step >= 6:
            # Human confirmed the hold — elevate autonomy so hold
            # does not re-trigger if _handle_ship somehow re-enters.
            spec_run.effective_autonomy = max(spec_run.effective_autonomy, 2)
            log.info("resuming from hold — proceeding to ship")
            return _handle_ship(
                spec_run, config, project_dir,
                state_file=state_file, events_file=events_file,
                metrics_file=metrics_file,
                run_regression_on_ship=run_regression_on_ship,
                dry_run=dry_run,
            )

        # Steps 3-6: Iteration loop.
        shipped = run_iteration_loop(
            spec_run, config, barriers, project_dir,
            events_file=events_file, audit_path=audit_path,
            state_file=state_file, dry_run=dry_run,
        )

        if shipped:
            return _handle_ship(
                spec_run, config, project_dir,
                state_file=state_file, events_file=events_file,
                metrics_file=metrics_file,
                run_regression_on_ship=run_regression_on_ship,
                dry_run=dry_run,
            )
        else:
            return _handle_escalation(
                spec_run, config, barriers, project_dir,
                state_file=state_file, events_file=events_file,
                audit_path=audit_path, metrics_file=metrics_file,
                dry_run=dry_run,
            )

    except HumanHoldRequired:
        spec_run.status = PipelineStatus.HOLD
        write_state(state_file, "status", "hold")
        write_state_or_warn(state_file, "completed_step", "6")
        log.info("HOLD: %s — awaiting human confirmation", spec_run.spec_name)
        return 4

    except (HardAbort, BudgetExceeded) as e:
        return _handle_abort(
            spec_run, config, project_dir, str(e),
            state_file=state_file, events_file=events_file,
            metrics_file=metrics_file,
        )

    finally:
        cleanup.cleanup()


# --- Pipeline steps ---

def _step_spec_validation(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, object],
    project_dir: Path,
    *,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
) -> None:
    """Step 1: Validate the spec."""
    spec_file = project_dir / ".dark-factory" / "specs" / f"{spec_run.spec_name}.md"

    prompt = (
        f"Validate the spec at {spec_file}. "
        f"Write validation result to .dark-factory/reviews/{spec_run.spec_name}-validation.md "
        f"and .dark-factory/reviews/{spec_run.spec_name}-validation.status"
    )

    result = invoke_agent(
        AgentRole.SPEC_VALIDATOR, prompt,
        config=config, barriers=barriers,
        run_id=spec_run.run_id, project_dir=project_dir,
        spec_name=spec_run.spec_name, iteration=0,
        events_file=events_file, audit_path=audit_path, dry_run=dry_run,
    )

    if result.exit_code != 0:
        raise DarkFactoryError(f"spec validation failed (exit {result.exit_code})")

    # Verify validation result.
    reviews_dir = project_dir / ".dark-factory" / "reviews"
    passed, reason = validate_spec_result(reviews_dir, spec_run.spec_name)
    if not passed:
        raise DarkFactoryError(f"spec validation failed: {reason}")

    log.info("spec validation passed")


def _step_scenario_generation(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, object],
    project_dir: Path,
    *,
    regen: bool = False,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
) -> None:
    """Step 2: Generate blind test scenarios."""
    scenarios_dir = project_dir / ".dark-factory" / "scenarios" / spec_run.spec_name

    if regen and scenarios_dir.exists():
        import shutil
        shutil.rmtree(scenarios_dir)
        log.info("regenerating scenarios")

    if has_scenario_files(scenarios_dir):
        log.info("scenarios already exist, skipping generation")
        return

    scenarios_dir.mkdir(parents=True, exist_ok=True)
    spec_file = project_dir / ".dark-factory" / "specs" / f"{spec_run.spec_name}.md"

    prompt = (
        f"Read the spec at {spec_file}. "
        f"Generate comprehensive blind test scenarios in {scenarios_dir}/. "
        f"Write a scenario report to .dark-factory/reports/{spec_run.spec_name}-scenarios.md"
    )

    result = invoke_agent(
        AgentRole.SCENARIO_ARCHITECT, prompt,
        config=config, barriers=barriers,
        run_id=spec_run.run_id, project_dir=project_dir,
        spec_name=spec_run.spec_name, iteration=0,
        events_file=events_file, audit_path=audit_path, dry_run=dry_run,
    )

    if result.exit_code != 0:
        log.warning("scenario generation failed (exit %d)", result.exit_code)


# --- Post-pipeline handlers ---

def _handle_ship(
    spec_run: SpecRun,
    config: PipelineConfig,
    project_dir: Path,
    *,
    state_file: Path,
    events_file: Path | None = None,
    metrics_file: Path | None = None,
    run_regression_on_ship: bool = False,
    dry_run: bool = False,
) -> int:
    """Handle SHIP decision — registry, regression, git, notification."""
    spec_run.status = PipelineStatus.SHIPPED

    # Register scenarios.
    registry_register(project_dir, spec_run.spec_name)

    # Regression testing — mandatory when trust-derived autonomy < 3.
    trust_requires_regression = spec_run.effective_autonomy < 3
    if run_regression_on_ship or config.regression_on_ship or trust_requires_regression:
        passed, failed, total = run_regression(
            config, project_dir,
            exclude=[spec_run.spec_name],
            events_file=events_file,
            run_id=spec_run.run_id,
        )
        if failed > 0 and config.regression_policy == "block":
            log.warning("regression failed (%d/%d), blocking ship", failed, total)
            write_state(state_file, "status", "regression-failed")
            return 2

    # Git operations.
    if config.auto_pr and not dry_run:
        branch = git_ops.create_branch(
            spec_run.spec_name, spec_run.run_id, config, project_dir,
        )
        git_ops.commit_changes(
            spec_run.spec_name, config.source_dirs, config.test_dir,
            project_dir,
        )
        git_ops.push_branch(branch, config, project_dir)

        last_iter = spec_run.current_iteration
        pr_url = git_ops.create_pr(
            spec_run.spec_name, config, project_dir,
            run_id=spec_run.run_id,
            satisfaction_score=last_iter.satisfaction_score if last_iter else 0,
            trust_score=spec_run.trust_score,
        )

        if config.merge_queue:
            git_ops.merge_queue_add(project_dir)
        if config.auto_merge_high_trust:
            git_ops.auto_merge_high_trust(project_dir, spec_run.trust_score)

    # State and events.
    write_state(state_file, "status", "shipped")
    write_state_or_warn(state_file, "completed_step", "99")

    if events_file:
        last_iter = spec_run.current_iteration
        emit_spec_shipped(
            events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
            iterations=len(spec_run.iterations),
            satisfaction=last_iter.satisfaction_score if last_iter else 0,
        )
        emit_cost_summary(
            events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
            total_turns=spec_run.total_turns,
            estimated_usd=estimate_cost(spec_run.total_turns, config).estimated_usd,
        )

    if metrics_file:
        record_end(
            metrics_file, spec_run.spec_name, spec_run.run_id, "shipped",
            spec_run.iteration - 1, spec_run.total_turns, spec_run.wall_elapsed,
        )

    notify_shipped(config, spec_run.spec_name, spec_run.run_id,
                   len(spec_run.iterations), spec_run.trust_score)
    # Autonomy 1 only reaches here after human confirmation (elevated to 2).
    # Autonomy 2 reaches here directly. Either way, flag for review.
    if spec_run.effective_autonomy <= 2:
        notify_ship_review(config, spec_run.spec_name, spec_run.run_id,
                           spec_run.trust_score)
    log.info("SHIPPED: %s", spec_run.spec_name)
    return 0


def _handle_escalation(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, object],
    project_dir: Path,
    *,
    state_file: Path,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    metrics_file: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Handle escalation — max iterations reached without SHIP."""
    spec_run.status = PipelineStatus.ESCALATED

    # Run escalation agent.
    prompt = (
        f"Write an escalation report for {spec_run.spec_name}. "
        f"The pipeline reached {spec_run.iteration - 1} iterations without shipping. "
        f"Write to .dark-factory/reviews/{spec_run.spec_name}-escalation.md"
    )
    invoke_agent(
        AgentRole.ESCALATION, prompt,
        config=config, barriers=barriers,
        run_id=spec_run.run_id, project_dir=project_dir,
        spec_name=spec_run.spec_name, iteration=spec_run.iteration,
        events_file=events_file, audit_path=audit_path,
        min_output=0, dry_run=dry_run,
    )

    write_state(state_file, "status", "escalated")

    if events_file:
        emit_spec_escalated(
            events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
            iterations=len(spec_run.iterations),
        )

    if metrics_file:
        record_end(
            metrics_file, spec_run.spec_name, spec_run.run_id, "escalated",
            spec_run.iteration - 1, spec_run.total_turns, spec_run.wall_elapsed,
        )

    notify_escalated(config, spec_run.spec_name, spec_run.run_id,
                     len(spec_run.iterations))
    log.info("ESCALATED: %s", spec_run.spec_name)
    return 2


def _handle_abort(
    spec_run: SpecRun,
    config: PipelineConfig,
    project_dir: Path,
    reason: str,
    *,
    state_file: Path,
    events_file: Path | None = None,
    metrics_file: Path | None = None,
) -> int:
    """Handle abort — budget or hard abort."""
    spec_run.status = PipelineStatus.ABORTED
    spec_run.abort_reason = reason

    write_state(state_file, "status", "failed")
    write_state_or_warn(state_file, "abort_reason", reason)

    if events_file:
        emit_spec_failed(
            events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
            reason=reason,
        )

    if metrics_file:
        record_end(
            metrics_file, spec_run.spec_name, spec_run.run_id, "aborted",
            spec_run.iteration - 1, spec_run.total_turns, spec_run.wall_elapsed,
        )

    notify_failed(config, spec_run.spec_name, spec_run.run_id, reason)
    log.warning("ABORTED: %s — %s", spec_run.spec_name, reason)
    return 3


# --- Helpers ---

def _handle_resume(
    spec_run: SpecRun,
    state_file: Path,
    current_spec_hash: str,
) -> None:
    """Restore state from previous run for resume."""
    stored_hash = read_state(state_file, "spec_hash")
    if stored_hash and stored_hash != current_spec_hash:
        raise DarkFactoryError(
            "spec has changed since last run — use --fresh to start over"
        )

    completed = read_state(state_file, "completed_step")
    if completed:
        spec_run.completed_step = int(completed)

    iteration = read_state(state_file, "iteration")
    if iteration:
        spec_run.iteration = int(iteration)

    stored_run_id = read_state(state_file, "run_id")
    if stored_run_id:
        spec_run.run_id = stored_run_id

    log.info("resuming from step %d, iteration %d", spec_run.completed_step, spec_run.iteration)


def _write_interrupted_state(state_file: Path, spec_run: SpecRun) -> None:
    """Write interrupted state for crash recovery."""
    write_state_or_warn(state_file, "status", "interrupted")
    write_state_or_warn(state_file, "iteration", str(spec_run.iteration))
    write_state_or_warn(state_file, "completed_step", str(spec_run.completed_step))
