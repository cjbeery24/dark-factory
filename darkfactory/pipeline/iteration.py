"""Steps 3–6 iteration loop — implement, gate, review, oracle.

Replaces the main iteration loop in dark-factory.sh (steps 3-6).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from darkfactory.audit.checker import validate_oracle_feedback
from darkfactory.docker.barriers import BarrierConfig
from darkfactory.errors import BudgetExceeded, HardAbort, HumanHoldRequired
from darkfactory.events.types import (
    emit_files_changed,
    emit_gate_completed,
    emit_iter_cost,
    emit_trajectory_classified,
)
from darkfactory.git.ops import count_files_changed
from darkfactory.intelligence.anomaly import (
    COMPOSITE_BLOCK,
    COMPOSITE_ESCALATE,
    check_baseline,
    check_spec_limits,
    composite_score,
)
from darkfactory.intelligence.cost import check_budget, estimate_cost
from darkfactory.models.agent import AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import IterationState, PipelineStatus, SpecRun
from darkfactory.models.satisfaction import TrajectoryType
from darkfactory.pipeline.agents import invoke_agent
from darkfactory.pipeline.evaluator import deploy, health_check, teardown
from darkfactory.pipeline.quality_gate import GateResult, run_quality_gate
from darkfactory.notify.dispatcher import notify_anomaly, notify_hold
from darkfactory.scenarios.trajectory import (
    StrategyActionType,
    classify_trajectory,
    strategy_action,
    strategy_recommendation,
)
from darkfactory.state.store import write_state_or_warn

log = logging.getLogger(__name__)

_INLINE_MAX_CHARS = 8_000  # per file — prevent runaway context


def _read_for_inline(path: Path) -> str:
    """Read a file for prompt inlining. Returns empty string if missing/unreadable."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if len(text) > _INLINE_MAX_CHARS:
            text = text[:_INLINE_MAX_CHARS] + "\n\n[... truncated — content exceeds limit ...]"
        return text
    except OSError as exc:
        log.debug("could not read %s for inlining: %s", path, exc)
        return ""


# Trajectory classes that trigger early escalation at iteration >= 3.
_ESCALATION_TRAJECTORIES = {
    TrajectoryType.DEGRADING,
    TrajectoryType.OSCILLATING,
    TrajectoryType.PLATEAU,
}


def run_iteration_loop(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    state_file: Path | None = None,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> bool:
    """Run the steps 3-6 loop until SHIP or max iterations.

    Returns True if shipped, False if escalation needed.
    Raises HardAbort or BudgetExceeded on abort conditions.
    """
    def _progress(msg: str) -> None:
        if progress is not None:
            progress(msg)

    score_history: list[int] = []
    budget_alerted: set[int] = set()

    max_iters = spec_run.effective_max_iterations or config.max_iterations
    while spec_run.iteration <= max_iters:
        iter_state = IterationState(number=spec_run.iteration)
        spec_run.iterations.append(iter_state)
        turns_at_iter_start = spec_run.total_turns

        if state_file:
            write_state_or_warn(state_file, "iteration", str(spec_run.iteration))

        _progress(f"Iteration {spec_run.iteration}/{max_iters}:")

        # Step 3: Implementation.
        _progress("  Implementing...")
        _step_implementation(
            spec_run, iter_state, config, barriers, project_dir,
            events_file=events_file, audit_path=audit_path, dry_run=dry_run,
        )
        _progress("  Implementation complete")

        # Step 4: Quality gate.
        _progress("  Running quality gate...")
        gate_result = _step_quality_gate(
            spec_run, iter_state, config, project_dir,
            events_file=events_file, dry_run=dry_run,
        )
        _progress(f"  Quality gate: {gate_result.pass_rate}% pass rate")

        # Cost budget check.
        if events_file:
            check_budget(
                spec_run.total_turns, config, events_file,
                spec_run.spec_name, spec_run.run_id,
                alerted=budget_alerted,
            )

        # Anomaly checks.
        if events_file:
            check_spec_limits(
                spec_run.total_turns, spec_run.wall_elapsed, config,
                events_file, spec_run.spec_name, spec_run.run_id,
            )
            check_baseline(
                spec_run.total_turns, events_file, config,
                spec_run.spec_name, spec_run.run_id,
            )

        # Per-iteration cost event.
        if events_file:
            iter_turns = spec_run.total_turns - turns_at_iter_start
            iter_cost = estimate_cost(iter_turns, config)
            emit_iter_cost(
                events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
                iteration=spec_run.iteration, turns=iter_turns,
                estimated_usd=iter_cost.estimated_usd,
            )

        # Track scores for trajectory.
        score_history.append(iter_state.satisfaction_score)

        # Trajectory analysis (iteration >= 2).
        if len(score_history) >= 2:
            traj = classify_trajectory(score_history)
            spec_run.trajectory_class = traj
            spec_run.trajectory_strategy = strategy_recommendation(traj)

            if events_file:
                emit_trajectory_classified(
                    events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
                    iteration=spec_run.iteration, trajectory=traj.value,
                    strategy=spec_run.trajectory_strategy,
                )

            # Early escalation on bad trajectories at iteration >= 3.
            if spec_run.iteration >= 3 and traj in _ESCALATION_TRAJECTORIES:
                # Strategy switching: try alternative strategies before escalating.
                if config.strategy_switch_enabled and spec_run.strategy_switches < config.strategy_max_switches:
                    action = strategy_action(traj)
                    applied = _apply_strategy(spec_run, config, action.action, score_history)
                    if applied:
                        log.info(
                            "strategy switch %d/%d: %s at iteration %d (trajectory=%s)",
                            spec_run.strategy_switches, config.strategy_max_switches,
                            action.action.value, spec_run.iteration, traj.value,
                        )
                        spec_run.iteration += 1
                        continue
                log.warning("early escalation: trajectory=%s at iteration %d", traj, spec_run.iteration)
                spec_run.iteration += 1
                return False

        # Hard gate build failure.
        if gate_result.exit_code == 2 and config.hard_gate_build:
            _progress("  Quality gate: build failed, skipping review")
            spec_run.iteration += 1
            continue

        # Step 4.5: Evaluator (optional).
        if config.deploy_cmd:
            _progress("  Evaluating deployment...")
            _step_evaluator(
                spec_run, config, barriers, project_dir,
                events_file=events_file, audit_path=audit_path, dry_run=dry_run,
            )
            _progress("  Evaluation complete")

        # Step 5: Review.
        _progress("  Reviewing...")
        _step_review(
            spec_run, iter_state, config, barriers, project_dir,
            events_file=events_file, audit_path=audit_path, dry_run=dry_run,
        )

        _progress("  Review complete")

        # Step 6: Oracle decision.
        _progress("  Oracle deciding...")
        shipped = _step_oracle(
            spec_run, iter_state, config, barriers, project_dir,
            events_file=events_file, audit_path=audit_path,
            state_file=state_file, dry_run=dry_run,
        )

        if shipped:
            _progress("  Oracle: SHIP")
            return True

        _progress("  Oracle: NO-SHIP")
        spec_run.iteration += 1

    return False


def _apply_strategy(
    spec_run: SpecRun,
    config: PipelineConfig,
    action: StrategyActionType,
    score_history: list[int] | None = None,
) -> bool:
    """Apply a strategy switch. Returns True if a strategy was applied.

    For FRESH_START, clears score_history (if provided), spec_run.iterations,
    spec_run.trajectory_class, and resets the iteration counter.
    """
    if action == StrategyActionType.SWITCH_MODEL:
        # Pick a fallback model: use the first model_ids value that differs
        # from the current effective model.
        current = spec_run.model_override or config.model_id
        fallback = ""
        for role_model in config.model_ids.values():
            if role_model != current:
                fallback = role_model
                break
        if not fallback or fallback == current:
            # No alternative model available — can't switch.
            return False
        spec_run.model_override = fallback
        spec_run.strategy_switches += 1
        log.info("strategy: switching model from %s to %s", current, fallback)
        return True

    if action == StrategyActionType.FRESH_START:
        # Reset iteration to 1 with clean state.
        spec_run.iteration = 0  # Will be incremented to 1 by the loop.
        spec_run.iterations.clear()
        spec_run.trajectory_class = None
        spec_run.trajectory_strategy = ""
        if score_history is not None:
            score_history.clear()
        spec_run.strategy_switches += 1
        log.info("strategy: fresh start (resetting to iteration 1)")
        return True

    # CONTINUE and ESCALATE don't apply a strategy switch.
    return False


def _step_implementation(
    spec_run: SpecRun,
    iter_state: IterationState,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
) -> None:
    """Step 3: Invoke the implementer agent."""
    if spec_run.iteration == 1:
        prompt = (
            f"Read the spec at /project/.dark-factory/specs/{spec_run.spec_name}.md and implement it. "
            f"Write code in the source directories: {config.source_dirs}."
        )
    else:
        feedback_host = (
            project_dir / ".dark-factory" / "reviews"
            / f"{spec_run.spec_name}-feedback-iter{spec_run.iteration - 1}.md"
        )
        if feedback_host.exists():
            prompt = (
                f"Read the spec at /project/.dark-factory/specs/{spec_run.spec_name}.md. "
                f"Read the feedback at /project/.dark-factory/reviews/{spec_run.spec_name}-feedback-iter{spec_run.iteration - 1}.md and fix the issues. "
                f"This is iteration {spec_run.iteration}."
            )
        else:
            log.warning("feedback file missing for iteration %d, implementing from spec only", spec_run.iteration)
            prompt = (
                f"Read the spec at /project/.dark-factory/specs/{spec_run.spec_name}.md and implement it. "
                f"This is iteration {spec_run.iteration} — no feedback available from the previous iteration."
            )

    result = invoke_agent(
        AgentRole.IMPLEMENTER, prompt,
        config=config, barriers=barriers, run_id=spec_run.run_id,
        project_dir=project_dir, spec_name=spec_run.spec_name,
        iteration=spec_run.iteration, events_file=events_file,
        audit_path=audit_path, dry_run=dry_run,
        model_override=spec_run.model_override,
    )
    # Track turns as upper-bound estimate (max_turns per invocation).
    # Actual turn count is not available from the container output.
    spec_run.total_turns += config.agent_max_turns
    iter_state.wall_seconds += result.wall_seconds

    # Track files changed and barrier violations.
    if not dry_run:
        iter_state.files_changed = count_files_changed(project_dir, config.source_dirs)
    if events_file and iter_state.files_changed:
        emit_files_changed(
            events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
            iteration=spec_run.iteration, count=iter_state.files_changed,
        )
    if result.barrier_violations:
        iter_state.barrier_violations = len(result.barrier_violations)

    if result.exit_code != 0:
        log.warning("implementer failed at iteration %d (exit %d)", spec_run.iteration, result.exit_code)


def _step_quality_gate(
    spec_run: SpecRun,
    iter_state: IterationState,
    config: PipelineConfig,
    project_dir: Path,
    *,
    events_file: Path | None = None,
    dry_run: bool = False,
) -> GateResult:
    """Step 4: Run the quality gate."""
    gate_result = run_quality_gate(
        spec_run.spec_name, config, spec_run.run_id, project_dir,
        iteration=spec_run.iteration, dry_run=dry_run,
    )

    iter_state.gate_exit_code = gate_result.exit_code
    iter_state.scenario_pass_count = gate_result.scenario_passed
    iter_state.scenario_fail_count = gate_result.scenario_failed
    iter_state.scenario_total = gate_result.scenario_total
    iter_state.pass_rate = gate_result.pass_rate
    iter_state.satisfaction_score = gate_result.satisfaction_score

    # Floor enforcement.
    if config.min_satisfaction_score > 0 and gate_result.satisfaction_score < config.min_satisfaction_score:
        if gate_result.exit_code == 0:
            gate_result.exit_code = 1
            log.info("satisfaction %d below floor %d, forcing gate failure",
                     gate_result.satisfaction_score, config.min_satisfaction_score)

    if config.min_ship_pass_rate > 0 and gate_result.pass_rate < config.min_ship_pass_rate:
        if gate_result.exit_code == 0:
            gate_result.exit_code = 1
            log.info("pass rate %d below floor %d, forcing gate failure",
                     gate_result.pass_rate, config.min_ship_pass_rate)

    if events_file:
        emit_gate_completed(
            events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
            iteration=spec_run.iteration, exit_code=gate_result.exit_code,
            pass_rate=gate_result.pass_rate, satisfaction=gate_result.satisfaction_score,
        )

    return gate_result


def _step_evaluator(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
) -> None:
    """Step 4.5: Deploy, evaluate, teardown."""
    try:
        if not deploy(config):
            return

        if not health_check(config):
            return

        # Ensure reports dir exists before bind-mounting it rw into the container.
        # Docker creates missing dirs as root-owned, making them unwritable by the agent user.
        (project_dir / ".dark-factory" / "reports").mkdir(parents=True, exist_ok=True)

        # Run evaluator agent if agent definition exists.
        evaluator_md = project_dir / "agents" / "evaluator.md"
        if evaluator_md.exists():
            prompt = (
                f"Exercise the deployed application at {config.deploy_base_url}. "
                f"Write evaluation report to .dark-factory/reports/"
                f"{spec_run.spec_name}-evaluation-iter{spec_run.iteration}.md"
            )
            invoke_agent(
                AgentRole.EVALUATOR, prompt,
                config=config, barriers=barriers, run_id=spec_run.run_id,
                project_dir=project_dir, spec_name=spec_run.spec_name,
                iteration=spec_run.iteration, events_file=events_file,
                audit_path=audit_path, dry_run=dry_run,
            )
    finally:
        teardown(config)


def _step_review(
    spec_run: SpecRun,
    iter_state: IterationState,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
) -> None:
    """Step 5: Run deterministic pre-pass then invoke the reviewer agent."""
    # Ensure reviews dir exists before bind-mounting it rw into the container.
    (project_dir / ".dark-factory" / "reviews").mkdir(parents=True, exist_ok=True)

    report_file = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_run.spec_name}-quality-gate-summary.md"
    )

    # FR-5: Run deterministic pre-pass before the reviewer (EC-11: catch all errors).
    prepass_result = None
    try:
        from darkfactory.review.prepass import run_review_prepass
        prepass_result = run_review_prepass(project_dir, spec_run.spec_name, config)
    except Exception as exc:
        log.error("review pre-pass failed, proceeding without: %s", exc)

    # Read on host before container launch — Reviewer's container has reports/ masked.
    gate_text = _read_for_inline(report_file)
    prepass_text = ""
    if prepass_result is not None:
        prepass_md = project_dir / ".dark-factory" / "reports" / f"{spec_run.spec_name}-review-prepass.md"
        prepass_text = _read_for_inline(prepass_md)

    prompt = f"Review the implementation for spec /project/.dark-factory/specs/{spec_run.spec_name}.md.\n\n"

    if gate_text:
        prompt += "```quality-gate-summary\n" + gate_text + "\n```\n\n"

    if prepass_text:
        prompt += "```review-prepass\n" + prepass_text + "\n```\n\n"

    prompt += f"Write review to /project/.dark-factory/reviews/{spec_run.spec_name}-review.md"

    # Gate the scoring instruction on prepass_text being non-empty — if the file was
    # written but unreadable, sending the instruction without the data is misleading.
    if prepass_text:
        prompt += (
            " Use pre-pass scores for Test Results (25%), Code Quality (20%),"
            " and Security (15%). Focus your analysis on Spec Compliance (30%)"
            " and Robustness (10%)."
        )

    result = invoke_agent(
        AgentRole.REVIEWER, prompt,
        config=config, barriers=barriers, run_id=spec_run.run_id,
        project_dir=project_dir, spec_name=spec_run.spec_name,
        iteration=spec_run.iteration, events_file=events_file,
        audit_path=audit_path, dry_run=dry_run,
    )

    if result.exit_code != 0:
        # FR-6 / EC-8: Fallback to deterministic scores on reviewer failure.
        log.warning("reviewer failed, using deterministic pre-pass scores as fallback")
        review_path = project_dir / ".dark-factory" / "reviews" / f"{spec_run.spec_name}-review.md"
        review_path.parent.mkdir(parents=True, exist_ok=True)

        if prepass_result is not None:
            pp = prepass_result
            review_path.write_text(
                f"# Review: PARTIAL (Deterministic Fallback)\n\n"
                f"Review agent failed (exit {result.exit_code}). "
                f"Deterministic pre-pass scores used as fallback.\n\n"
                f"| Dimension | Score | Source |\n"
                f"|-----------|-------|--------|\n"
                f"| Test Results | {pp.test_analysis.score}/25 | Deterministic pre-pass |\n"
                f"| Code Quality | {pp.code_quality.score}/20 | Deterministic pre-pass |\n"
                f"| Security | {pp.security.score}/15 | Deterministic pre-pass |\n"
                f"| Spec Compliance | 0/30 | Reviewer failed — no LLM assessment |\n"
                f"| Robustness | 0/10 | Reviewer failed — no LLM assessment |\n"
                f"| Total | {pp.deterministic_total}/100 |\n\n"
                f"VERDICT: REQUEST CHANGES\n"
            )
        else:
            review_path.write_text(
                f"# Review: FAILED\n\nReview agent failed (exit {result.exit_code}).\n\nVERDICT: REVIEW_FAILED\n"
            )


def _step_oracle(
    spec_run: SpecRun,
    iter_state: IterationState,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    state_file: Path | None = None,
    dry_run: bool = False,
) -> bool:
    """Step 6: Oracle decision. Returns True if SHIP."""
    prompt = (
        f"Make a SHIP or NO-SHIP decision for {spec_run.spec_name}. "
        f"Spec: /project/.dark-factory/specs/{spec_run.spec_name}.md. "
        f"Review: /project/.dark-factory/reviews/{spec_run.spec_name}-review.md. "
        f"Report: /project/.dark-factory/reports/{spec_run.spec_name}-quality-gate-summary.md. "
        f"Iteration: {spec_run.iteration}/{config.max_iterations}. "
        f"Satisfaction: {iter_state.satisfaction_score}. Pass rate: {iter_state.pass_rate}%. "
        f"Your first line MUST be either SHIP or NO-SHIP. "
        f"If NO-SHIP, write structured iteration feedback to "
        f"/project/.dark-factory/reviews/{spec_run.spec_name}-feedback-iter{spec_run.iteration}.md "
        f"following the format in your agent definition."
    )

    # Composite anomaly blocking — compute boolean flags from raw values.
    composite_blocked = False
    anomaly = composite_score(
        turns_anomalous=spec_run.total_turns > config.anomaly_max_spec_turns,
        wall_anomalous=spec_run.wall_elapsed > config.anomaly_max_spec_wall_s,
        trajectory_bad=spec_run.trajectory_class in _ESCALATION_TRAJECTORIES,
        files_anomalous=iter_state.files_changed > config.anomaly_max_files_written,
        barrier_violation=any(i.barrier_violations > 0 for i in spec_run.iterations),
    )
    if anomaly >= COMPOSITE_BLOCK:
        prompt += "\n\nCRITICAL: Anomaly score is high. You MUST NOT SHIP."
        composite_blocked = True
        notify_anomaly(config, spec_run.spec_name, "composite_block", anomaly)
    elif anomaly >= COMPOSITE_ESCALATE:
        notify_anomaly(config, spec_run.spec_name, "composite_escalate", anomaly)

    result = invoke_agent(
        AgentRole.ORACLE, prompt,
        config=config, barriers=barriers, run_id=spec_run.run_id,
        project_dir=project_dir, spec_name=spec_run.spec_name,
        iteration=spec_run.iteration, events_file=events_file,
        audit_path=audit_path, dry_run=dry_run,
    )

    # Parse decision from first line.
    decision = _parse_oracle_decision(result.output)

    if decision == "SHIP":
        # Override checks.
        if composite_blocked:
            log.warning("oracle said SHIP but composite blocked — forcing NO-SHIP")
            decision = "NO-SHIP"
        if config.min_satisfaction_score > 0 and iter_state.satisfaction_score < config.min_satisfaction_score:
            log.warning("oracle said SHIP but satisfaction below floor — forcing NO-SHIP")
            decision = "NO-SHIP"
        if config.min_ship_pass_rate > 0 and iter_state.pass_rate < config.min_ship_pass_rate:
            log.warning("oracle said SHIP but pass rate below floor — forcing NO-SHIP")
            decision = "NO-SHIP"

    # Record verdict after all overrides are applied.
    iter_state.oracle_verdict = decision

    if decision == "SHIP":
        # Autonomy level 1: require human confirmation before ship.
        if spec_run.effective_autonomy == 1:
            hold_file = (
                project_dir / ".dark-factory" / "state"
                / f"{spec_run.spec_name}.hold"
            )
            hold_file.parent.mkdir(parents=True, exist_ok=True)
            hold_file.write_text(
                f"spec={spec_run.spec_name}\n"
                f"run_id={spec_run.run_id}\n"
                f"trust={spec_run.trust_score}\n"
                f"iteration={spec_run.iteration}\n"
                f"satisfaction={iter_state.satisfaction_score}\n"
            )
            notify_hold(config, spec_run.spec_name, spec_run.run_id, spec_run.trust_score)
            raise HumanHoldRequired(
                f"{spec_run.spec_name}: SHIP pending human confirmation "
                f"(trust={spec_run.trust_score}, autonomy=1)"
            )
        return True

    # NO-SHIP path: validate feedback for leakage.
    feedback_file = (
        project_dir / ".dark-factory" / "reviews"
        / f"{spec_run.spec_name}-feedback-iter{spec_run.iteration}.md"
    )
    if feedback_file.exists() and audit_path:
        validate_oracle_feedback(feedback_file.read_text(), audit_path)

    if state_file:
        write_state_or_warn(state_file, "last_decision", decision)

    return False


def _parse_oracle_decision(output: str) -> str:
    """Parse SHIP or NO-SHIP from oracle output's first line.

    The oracle must start its response with exactly SHIP or NO-SHIP.
    """
    for line in output.splitlines():
        stripped = line.strip().upper()
        if not stripped:
            continue
        if stripped == "SHIP" or stripped.startswith("SHIP ") or stripped.startswith("SHIP:"):
            return "SHIP"
        return "NO-SHIP"
    return "NO-SHIP"
