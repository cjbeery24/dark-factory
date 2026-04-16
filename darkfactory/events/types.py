"""Convenience emit helpers for structured event types.

Each function calls emit_event with the appropriate EventType and data payload.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from darkfactory.events.emitter import emit_event
from darkfactory.models.events import Event, EventType


def _emit(
    events_file: Path,
    event_type: EventType,
    spec: str = "",
    run_id: str = "",
    plan_run_id: str = "",
    iteration: int = 0,
    **data: Any,
) -> Event:
    return emit_event(
        events_file, event_type,
        spec_name=spec, run_id=run_id, plan_run_id=plan_run_id,
        iteration=iteration, data=data if data else None,
    )


def emit_plan_start(ef: Path, *, plan_run_id: str, spec_count: int) -> Event:
    return _emit(ef, EventType.PLAN_START, plan_run_id=plan_run_id, spec_count=spec_count)

def emit_plan_complete(ef: Path, *, plan_run_id: str, shipped: int, failed: int, skipped: int) -> Event:
    return _emit(ef, EventType.PLAN_COMPLETE, plan_run_id=plan_run_id, shipped=shipped, failed=failed, skipped=skipped)

def emit_plan_timeout(ef: Path, *, plan_run_id: str, wall_s: float) -> Event:
    return _emit(ef, EventType.PLAN_TIMEOUT, plan_run_id=plan_run_id, wall_s=wall_s)

def emit_plan_resume(ef: Path, *, plan_run_id: str, completed_specs: list[str]) -> Event:
    return _emit(ef, EventType.PLAN_RESUME, plan_run_id=plan_run_id, completed_specs=completed_specs)

def emit_spec_start(ef: Path, *, spec: str, run_id: str) -> Event:
    return _emit(ef, EventType.SPEC_START, spec=spec, run_id=run_id)

def emit_spec_dispatched(ef: Path, *, spec: str, run_id: str, plan_run_id: str, worktree: str = "") -> Event:
    return _emit(ef, EventType.SPEC_DISPATCHED, spec=spec, run_id=run_id, plan_run_id=plan_run_id, worktree=worktree)

def emit_spec_shipped(ef: Path, *, spec: str, run_id: str, iterations: int, satisfaction: int = 0) -> Event:
    return _emit(ef, EventType.SPEC_SHIPPED, spec=spec, run_id=run_id, iterations=iterations, satisfaction=satisfaction)

def emit_spec_failed(ef: Path, *, spec: str, run_id: str, reason: str) -> Event:
    return _emit(ef, EventType.SPEC_FAILED, spec=spec, run_id=run_id, reason=reason)

def emit_spec_escalated(ef: Path, *, spec: str, run_id: str, iterations: int) -> Event:
    return _emit(ef, EventType.SPEC_ESCALATED, spec=spec, run_id=run_id, iterations=iterations)

def emit_agent_start(ef: Path, *, spec: str, run_id: str, iteration: int, role: str, attempt: int = 1, phase: str = "") -> Event:
    return _emit(ef, EventType.AGENT_START, spec=spec, run_id=run_id, iteration=iteration, role=role, attempt=attempt, **({"phase": phase} if phase else {}))

def emit_agent_completed(ef: Path, *, spec: str, run_id: str, iteration: int, role: str, turns: int, exit_code: int, model_id: str = "", est_input_tokens: int = 0, est_output_tokens: int = 0, wall_seconds: float = 0.0, phase: str = "") -> Event:
    return _emit(ef, EventType.AGENT_COMPLETED, spec=spec, run_id=run_id, iteration=iteration, role=role, turns=turns, exit_code=exit_code, model_id=model_id, est_input_tokens=est_input_tokens, est_output_tokens=est_output_tokens, wall_seconds=wall_seconds, **({"phase": phase} if phase else {}))

def emit_agent_failed(ef: Path, *, spec: str, run_id: str, iteration: int, role: str, exit_code: int, reason: str = "", model_id: str = "", est_input_tokens: int = 0, est_output_tokens: int = 0, wall_seconds: float = 0.0, phase: str = "") -> Event:
    return _emit(ef, EventType.AGENT_FAILED, spec=spec, run_id=run_id, iteration=iteration, role=role, exit_code=exit_code, reason=reason, model_id=model_id, est_input_tokens=est_input_tokens, est_output_tokens=est_output_tokens, wall_seconds=wall_seconds, **({"phase": phase} if phase else {}))


def emit_gate_completed(ef: Path, *, spec: str, run_id: str, iteration: int, exit_code: int, pass_rate: int = 0, satisfaction: int = 0, server_error: str = "", agent_wall_seconds: float = 0.0, scenario_total: int = 0, scenario_crashed: int = 0, reason: str = "") -> Event:
    return _emit(ef, EventType.GATE_COMPLETED, spec=spec, run_id=run_id, iteration=iteration, exit_code=exit_code, pass_rate=pass_rate, satisfaction=satisfaction, server_error=server_error, agent_wall_seconds=agent_wall_seconds, scenario_total=scenario_total, scenario_crashed=scenario_crashed, reason=reason)

def emit_smoke_completed(ef: Path, *, spec: str, run_id: str, iteration: int, success: bool, attempt: int = 1, server_error: str = "", policy: str = "") -> Event:
    return _emit(ef, EventType.SMOKE_COMPLETED, spec=spec, run_id=run_id, iteration=iteration, success=success, attempt=attempt, server_error=server_error, policy=policy)

def emit_satisfaction_scored(ef: Path, *, spec: str, run_id: str, iteration: int, score: int, pass_rate: int, categories: dict[str, Any] | None = None) -> Event:
    kwargs: dict[str, Any] = {"score": score, "pass_rate": pass_rate}
    if categories is not None:
        kwargs["categories"] = categories
    return _emit(ef, EventType.SATISFACTION_SCORED, spec=spec, run_id=run_id, iteration=iteration, **kwargs)

def emit_trajectory_classified(ef: Path, *, spec: str, run_id: str, iteration: int, trajectory: str, strategy: str = "") -> Event:
    return _emit(ef, EventType.TRAJECTORY_CLASSIFIED, spec=spec, run_id=run_id, iteration=iteration, trajectory=trajectory, strategy=strategy)

def emit_regression_run(ef: Path, *, spec: str, run_id: str, passed: int, failed: int, total: int, wall_s: float = 0.0) -> Event:
    return _emit(ef, EventType.REGRESSION_RUN, spec=spec, run_id=run_id, passed=passed, failed=failed, total=total, wall_s=wall_s)

def emit_budget_exceeded(ef: Path, *, spec: str, run_id: str, spent: float, budget: float) -> Event:
    return _emit(ef, EventType.BUDGET_EXCEEDED, spec=spec, run_id=run_id, spent=spent, budget=budget)

def emit_budget_alert(ef: Path, *, spec: str, run_id: str, percent: float, spent: float, budget: float) -> Event:
    return _emit(ef, EventType.BUDGET_ALERT, spec=spec, run_id=run_id, percent=percent, spent=spent, budget=budget)

def emit_barrier_violation(ef: Path, *, spec: str, run_id: str, role: str, path: str) -> Event:
    return _emit(ef, EventType.BARRIER_VIOLATION, spec=spec, run_id=run_id, role=role, path=path)

def emit_barrier_path_echo_detected(ef: Path, *, spec: str, run_id: str, role: str, matched_paths: list[str]) -> Event:
    return _emit(ef, EventType.BARRIER_PATH_ECHO_DETECTED, spec=spec, run_id=run_id, role=role, matched_paths=matched_paths)

def emit_cost_summary(ef: Path, *, spec: str, run_id: str, total_turns: int, estimated_usd: float) -> Event:
    return _emit(ef, EventType.COST_SUMMARY, spec=spec, run_id=run_id, total_turns=total_turns, estimated_usd=estimated_usd)

def emit_anomaly_detected(ef: Path, *, spec: str, run_id: str, anomaly_type: str, score: int = 0, detail: str = "") -> Event:
    return _emit(ef, EventType.ANOMALY_DETECTED, spec=spec, run_id=run_id, anomaly_type=anomaly_type, score=score, detail=detail)

def emit_trust_scored(ef: Path, *, spec: str, run_id: str, trust: int, autonomy: int) -> Event:
    return _emit(ef, EventType.TRUST_SCORED, spec=spec, run_id=run_id, trust=trust, autonomy=autonomy)

def emit_iter_cost(ef: Path, *, spec: str, run_id: str, iteration: int, turns: int, estimated_usd: float) -> Event:
    return _emit(ef, EventType.ITER_COST, spec=spec, run_id=run_id, iteration=iteration, turns=turns, estimated_usd=estimated_usd)

def emit_files_changed(ef: Path, *, spec: str, run_id: str, iteration: int, count: int) -> Event:
    return _emit(ef, EventType.FILES_CHANGED, spec=spec, run_id=run_id, iteration=iteration, count=count)

# --- Phase 6: Deployment events ---

def emit_deploy_provision(ef: Path, *, spec: str, run_id: str, compose_project: str = "") -> Event:
    return _emit(ef, EventType.DEPLOY_PROVISION, spec=spec, run_id=run_id, compose_project=compose_project)

def emit_deploy_health_ok(ef: Path, *, spec: str, run_id: str, url: str = "") -> Event:
    return _emit(ef, EventType.DEPLOY_HEALTH_OK, spec=spec, run_id=run_id, url=url)

def emit_deploy_health_fail(ef: Path, *, spec: str, run_id: str, url: str = "", timeout: int = 0) -> Event:
    return _emit(ef, EventType.DEPLOY_HEALTH_FAIL, spec=spec, run_id=run_id, url=url, timeout=timeout)

def emit_deploy_teardown(ef: Path, *, spec: str, run_id: str, compose_project: str = "") -> Event:
    return _emit(ef, EventType.DEPLOY_TEARDOWN, spec=spec, run_id=run_id, compose_project=compose_project)

def emit_migration_check_pass(ef: Path, *, spec: str, run_id: str, files_checked: int = 0) -> Event:
    return _emit(ef, EventType.MIGRATION_CHECK_PASS, spec=spec, run_id=run_id, files_checked=files_checked)

def emit_migration_check_fail(ef: Path, *, spec: str, run_id: str, reason: str = "") -> Event:
    return _emit(ef, EventType.MIGRATION_CHECK_FAIL, spec=spec, run_id=run_id, reason=reason)

def emit_flag_created(ef: Path, *, spec: str, run_id: str, flag_name: str = "") -> Event:
    return _emit(ef, EventType.FLAG_CREATED, spec=spec, run_id=run_id, flag_name=flag_name)

def emit_flag_enabled(ef: Path, *, spec: str, run_id: str, flag_name: str = "", percent: int = 100) -> Event:
    return _emit(ef, EventType.FLAG_ENABLED, spec=spec, run_id=run_id, flag_name=flag_name, percent=percent)

def emit_flag_disabled(ef: Path, *, spec: str, run_id: str, flag_name: str = "") -> Event:
    return _emit(ef, EventType.FLAG_DISABLED, spec=spec, run_id=run_id, flag_name=flag_name)

def emit_canary_stage_start(ef: Path, *, spec: str, run_id: str, stage_name: str = "", percent: int = 0) -> Event:
    return _emit(ef, EventType.CANARY_STAGE_START, spec=spec, run_id=run_id, stage_name=stage_name, percent=percent)

def emit_canary_stage_passed(ef: Path, *, spec: str, run_id: str, stage_name: str = "", percent: int = 0) -> Event:
    return _emit(ef, EventType.CANARY_STAGE_PASSED, spec=spec, run_id=run_id, stage_name=stage_name, percent=percent)

def emit_canary_stage_failed(ef: Path, *, spec: str, run_id: str, stage_name: str = "", percent: int = 0, breached: str = "") -> Event:
    return _emit(ef, EventType.CANARY_STAGE_FAILED, spec=spec, run_id=run_id, stage_name=stage_name, percent=percent, breached=breached)

def emit_canary_promoted(ef: Path, *, spec: str, run_id: str, stages_completed: int = 0) -> Event:
    return _emit(ef, EventType.CANARY_PROMOTED, spec=spec, run_id=run_id, stages_completed=stages_completed)

def emit_canary_lock_timeout(ef: Path, *, spec: str, run_id: str) -> Event:
    return _emit(ef, EventType.CANARY_LOCK_TIMEOUT, spec=spec, run_id=run_id)

def emit_rollback_started(ef: Path, *, spec: str, run_id: str, layer: str = "", reason: str = "") -> Event:
    return _emit(ef, EventType.ROLLBACK_STARTED, spec=spec, run_id=run_id, layer=layer, reason=reason)

def emit_rollback_success(ef: Path, *, spec: str, run_id: str, layer: str = "") -> Event:
    return _emit(ef, EventType.ROLLBACK_SUCCESS, spec=spec, run_id=run_id, layer=layer)

def emit_rollback_layer_failed(ef: Path, *, spec: str, run_id: str, layer: str = "", error: str = "") -> Event:
    return _emit(ef, EventType.ROLLBACK_LAYER_FAILED, spec=spec, run_id=run_id, layer=layer, error=error)

def emit_rollback_all_failed(ef: Path, *, spec: str, run_id: str, reason: str = "") -> Event:
    return _emit(ef, EventType.ROLLBACK_ALL_FAILED, spec=spec, run_id=run_id, reason=reason)

def emit_deploy_complete(ef: Path, *, spec: str, run_id: str, status: str = "") -> Event:
    return _emit(ef, EventType.DEPLOY_COMPLETE, spec=spec, run_id=run_id, status=status)

def emit_rollback_window_expired(ef: Path, *, spec: str, run_id: str) -> Event:
    return _emit(ef, EventType.ROLLBACK_WINDOW_EXPIRED, spec=spec, run_id=run_id)

def emit_ship_with_gaps(ef: Path, *, spec: str, run_id: str, iteration: int, stable_failures: int, stable_failure_ratio: float, pass_rate: int, oracle_original: str) -> Event:
    return _emit(ef, EventType.SHIP_WITH_GAPS, spec=spec, run_id=run_id, iteration=iteration, stable_failures=stable_failures, stable_failure_ratio=stable_failure_ratio, pass_rate=pass_rate, oracle_original=oracle_original)


def emit_coverage_pass(ef: Path, *, spec: str, run_id: str, anchor_count: int) -> Event:
    return _emit(ef, EventType.COVERAGE_PASS, spec=spec, run_id=run_id, anchor_count=anchor_count)


def emit_coverage_gap(ef: Path, *, spec: str, run_id: str, uncovered: list[str], attempt: int) -> Event:
    return _emit(ef, EventType.COVERAGE_GAP, spec=spec, run_id=run_id, uncovered=uncovered, attempt=attempt)


def emit_coverage_regen(ef: Path, *, spec: str, run_id: str, attempt: int, uncovered: list[str]) -> Event:
    return _emit(ef, EventType.COVERAGE_REGEN, spec=spec, run_id=run_id, attempt=attempt, uncovered=uncovered)


def emit_coverage_floor_violation(ef: Path, *, spec: str, run_id: str, uncovered: list[str], untestable: dict[str, str] | None = None) -> Event:
    data: dict[str, Any] = {"uncovered": uncovered}
    if untestable:
        data["untestable"] = untestable
    return _emit(ef, EventType.COVERAGE_FLOOR_VIOLATION, spec=spec, run_id=run_id, **data)


def emit_coverage_duplicate_tags(ef: Path, *, spec: str, run_id: str, duplicates: dict[str, int]) -> Event:
    return _emit(ef, EventType.COVERAGE_DUPLICATE_TAGS, spec=spec, run_id=run_id, duplicates=duplicates)


def emit_coverage_untestable_waived(ef: Path, *, spec: str, run_id: str, waived: dict[str, str], attempt: int) -> Event:
    return _emit(ef, EventType.COVERAGE_UNTESTABLE_WAIVED, spec=spec, run_id=run_id, waived=waived, attempt=attempt)


def emit_coverage_architect_amend(ef: Path, *, spec: str, run_id: str, amend_number: int, gaps: list[dict[str, str]]) -> Event:
    return _emit(ef, EventType.COVERAGE_ARCHITECT_AMEND, spec=spec, run_id=run_id, amend_number=amend_number, gaps=gaps)


def emit_coverage_architect_amend_completed(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    amend_number: int,
    outcome: str,
    new_entry_points: list[str],
) -> Event:
    return _emit(
        ef, EventType.COVERAGE_ARCHITECT_AMEND_COMPLETED,
        spec=spec, run_id=run_id,
        amend_number=amend_number, outcome=outcome,
        new_entry_points=new_entry_points,
    )


def emit_coverage_architect_amend_failed(ef: Path, *, spec: str, run_id: str, amend_number: int, reason: str) -> Event:
    return _emit(ef, EventType.COVERAGE_ARCHITECT_AMEND_FAILED, spec=spec, run_id=run_id, amend_number=amend_number, reason=reason)


def emit_coverage_spec_vehicle_mismatch(ef: Path, *, spec: str, run_id: str, anchors: list[dict[str, str]]) -> Event:
    return _emit(ef, EventType.COVERAGE_SPEC_VEHICLE_MISMATCH, spec=spec, run_id=run_id, anchors=anchors)


def emit_coverage_gap_category_missing(ef: Path, *, spec: str, run_id: str, anchors: list[str]) -> Event:
    return _emit(ef, EventType.COVERAGE_GAP_CATEGORY_MISSING, spec=spec, run_id=run_id, anchors=anchors)


def emit_preflight_fail_open(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    iteration: int,
    check: str,
    reason: str,
    detail: str = "",
) -> Event:
    """Emit when a deterministic preflight check fails open due to
    infrastructure error (e.g. git invocation failed).

    ``check`` is the check name (``forbidden_patterns`` or
    ``test_presence``). ``reason`` is a short machine-friendly tag
    (e.g. ``git_diff_exit_nonzero``). ``detail`` carries a short
    human-readable suffix (typically a truncated stderr).
    """
    return _emit(
        ef,
        EventType.PREFLIGHT_FAIL_OPEN,
        spec=spec,
        run_id=run_id,
        iteration=iteration,
        check=check,
        reason=reason,
        detail=detail,
    )


def emit_contracts_staged(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    adapter: str,
    src: str,
    dest: str,
    file_count: int,
) -> Event:
    """Emit when an adapter's wire-contract helpers are staged into the
    per-spec scenarios directory.

    Audit signal: lets post-run analysis correlate "scenarios failed
    even though adapter X's helpers were available" vs. "scenarios
    failed because no helpers were staged."
    """
    return _emit(
        ef,
        EventType.CONTRACTS_STAGED,
        spec=spec,
        run_id=run_id,
        adapter=adapter,
        src=src,
        dest=dest,
        file_count=file_count,
    )


def emit_spec_adapter_activated(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    adapter: str,
    reason: str = "spec_triggers",
) -> Event:
    """Emit when an adapter is unioned into ``active_adapters`` for this
    spec-run via the per-spec trigger hook — i.e. the project-scan did
    NOT list this adapter, but ``adapter.spec_triggers(spec_text)``
    returned True and the orchestrator activated it anyway.

    ``reason`` distinguishes the activation source for future audit
    tooling (``spec_triggers`` today; manifest-derived or CLI-override
    entries would get distinct tags). Defaulted so the common case
    stays terse."""
    return _emit(
        ef,
        EventType.SPEC_ADAPTER_ACTIVATED,
        spec=spec,
        run_id=run_id,
        adapter=adapter,
        reason=reason,
    )
