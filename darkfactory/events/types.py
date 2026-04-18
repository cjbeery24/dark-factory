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


def emit_scenario_import_violations(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    specifier_violations: int,
    symbol_violations: int,
    details: list[str],
) -> Event:
    """G2: Log scenario import/export mismatches against manifest."""
    return _emit(
        ef,
        EventType.SCENARIO_IMPORT_VIOLATIONS,
        spec=spec,
        run_id=run_id,
        specifier_violations=specifier_violations,
        symbol_violations=symbol_violations,
        details=details,
    )


def emit_oracle_metric_delta(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    iteration: int,
    claimed_pass_rate: int,
    actual_pass_rate: int,
    delta: int,
    reprompted: bool,
) -> Event:
    """M3: Log delta between Oracle's claimed and actual pass_rate."""
    return _emit(
        ef,
        EventType.ORACLE_METRIC_DELTA,
        spec=spec,
        run_id=run_id,
        iteration=iteration,
        claimed_pass_rate=claimed_pass_rate,
        actual_pass_rate=actual_pass_rate,
        delta=delta,
        reprompted=reprompted,
    )


def emit_missing_manifest_files(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    iteration: int,
    missing_count: int,
    missing_files: list[str],
) -> Event:
    """M4: Log missing manifest files after implementation."""
    return _emit(
        ef,
        EventType.MISSING_MANIFEST_FILES,
        spec=spec,
        run_id=run_id,
        iteration=iteration,
        missing_count=missing_count,
        missing_files=missing_files,
    )


def emit_prompt_word_count(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    iteration: int,
    role: str,
    prompt_word_count: int,
    task_prompt_words: int,
    system_prompt_words: int,
) -> Event:
    """M5: Log total assembled prompt word count per agent invocation."""
    return _emit(
        ef,
        EventType.PROMPT_WORD_COUNT,
        spec=spec,
        run_id=run_id,
        iteration=iteration,
        role=role,
        prompt_word_count=prompt_word_count,
        task_prompt_words=task_prompt_words,
        system_prompt_words=system_prompt_words,
    )


def emit_monorepo_scoping_violation(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    violations: list[dict[str, str]],
) -> Event:
    """M1: Log commands.* fields that target wrong directory."""
    return _emit(
        ef,
        EventType.MONOREPO_SCOPING_VIOLATION,
        spec=spec,
        run_id=run_id,
        violations=violations,
    )


def emit_manifest_validation_retry(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    attempt: int,
    max_attempts: int,
    errors: list[str],
) -> Event:
    """G1: Log architect manifest retry after validation failure."""
    return _emit(
        ef,
        EventType.MANIFEST_VALIDATION_RETRY,
        spec=spec,
        run_id=run_id,
        attempt=attempt,
        max_attempts=max_attempts,
        errors=errors,
    )


def emit_architect_cache_invalidated(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    reason: str,
    prior_adapters: list[str] | None = None,
    current_adapters: list[str] | None = None,
) -> Event:
    """Emit when the architect cache gate rejects a cached manifest.

    ``reason`` is a closed vocabulary — one of ``spec_hash_drift``,
    ``adapter_set_drift``, ``substrate_manifest_missing``,
    ``manifest_unreadable``. Extending it requires a paired docstring
    note in ``models/events.py`` so downstream audit tooling stays in
    sync with the vocabulary.

    ``prior_adapters`` / ``current_adapters`` are populated for
    adapter-set drift so an auditor can reconstruct "what changed"
    without diffing git history. Optional because other reasons don't
    need them.
    """
    payload: dict[str, Any] = {"reason": reason}
    if prior_adapters is not None:
        payload["prior_adapters"] = prior_adapters
    if current_adapters is not None:
        payload["current_adapters"] = current_adapters
    return _emit(
        ef,
        EventType.ARCHITECT_CACHE_INVALIDATED,
        spec=spec,
        run_id=run_id,
        **payload,
    )


def emit_iteration_regression(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    iteration: int,
    prev_iteration: int,
    prev_pass_rate: int,
    prev_exit_code: int,
    curr_pass_rate: int,
    curr_exit_code: int,
    rollback_ref: str,
) -> Event:
    """R1: Log iteration regression and rollback."""
    return _emit(
        ef,
        EventType.ITERATION_REGRESSION,
        spec=spec,
        run_id=run_id,
        iteration=iteration,
        prev_iteration=prev_iteration,
        prev_pass_rate=prev_pass_rate,
        prev_exit_code=prev_exit_code,
        curr_pass_rate=curr_pass_rate,
        curr_exit_code=curr_exit_code,
        rollback_ref=rollback_ref,
    )


def emit_feedback_word_count(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    iteration: int,
    word_count: int,
) -> Event:
    """R2: Log Oracle feedback word count per iteration."""
    return _emit(
        ef,
        EventType.FEEDBACK_WORD_COUNT,
        spec=spec,
        run_id=run_id,
        iteration=iteration,
        word_count=word_count,
    )


def emit_agent_def_injected(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    iteration: int,
    role: str,
    word_count: int,
) -> Event:
    """A1-esc: Log agent definition injected via --append-system-prompt."""
    return _emit(
        ef,
        EventType.AGENT_DEF_INJECTED,
        spec=spec,
        run_id=run_id,
        iteration=iteration,
        role=role,
        word_count=word_count,
    )


def emit_classifier_io_hash(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    iteration: int,
    role: str,
    attempt: int,
    input_hash: str,
    output_hash: str,
    defects_count: int,
    failing_test_count: int,
    error: str = "",
) -> Event:
    """Phase 5.6 B4: Log classifier input/output hashes.

    ``input_hash`` is SHA256 over the canonical JSON of
    ``{failing_test_names: sorted(...), scenario_failed, scenario_crashed,
    scenario_total}``. ``output_hash`` is SHA256 over the canonical JSON
    of the emitted defect records (sorted by ``test_name``; only the
    ``test_name`` + ``category`` + ``spec_section`` fields that define
    the classification — excludes free-prose justification so
    stylistic drift doesn't flag as output drift).
    """
    return _emit(
        ef,
        EventType.CLASSIFIER_IO_HASH,
        spec=spec,
        run_id=run_id,
        iteration=iteration,
        role=role,
        attempt=attempt,
        input_hash=input_hash,
        output_hash=output_hash,
        defects_count=defects_count,
        failing_test_count=failing_test_count,
        error=error,
    )


def emit_architect_manifest_hash(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    manifest_hash: str,
    canonical_json: str,
    cache_hit: bool,
) -> Event:
    """Phase 6 I1: Architect manifest hash + canonical-JSON snapshot.

    ``manifest_hash`` is SHA256 over ``canonical_json`` — the
    architect's semantic output with orchestrator-added stamp fields
    (``active_adapters``, ``stamp_integrity``) stripped so runs on the
    same spec + adapter set produce comparable hashes across
    invocations.

    ``cache_hit`` distinguishes a re-emission from the architect cache
    (the manifest was not regenerated this run) from a fresh/validated
    architect invocation. Runs with ``--fresh`` always produce
    ``cache_hit=False``; V-dimension analyses should filter on this.
    """
    return _emit(
        ef,
        EventType.ARCHITECT_MANIFEST_HASH,
        spec=spec,
        run_id=run_id,
        manifest_hash=manifest_hash,
        canonical_json=canonical_json,
        cache_hit=cache_hit,
    )


def emit_scenario_fingerprint(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    scenario_path: str,
    phase: str,
    import_set: list[str],
    symbol_set: list[str],
    assertion_count: int,
    test_names: list[str],
) -> Event:
    """Phase 6 I2: Per-scenario structural fingerprint.

    Emitted once per scenario file after the Test Engineer's
    ``generate`` phase and each ``coverage_regen`` phase
    (``phase`` names the emitting step). Fingerprint fields are
    structural — prose-only drift (assertion messages, comments) does
    not register as scenario divergence. ``test_names`` is sorted to
    keep the set comparison order-stable.
    """
    return _emit(
        ef,
        EventType.SCENARIO_FINGERPRINT,
        spec=spec,
        run_id=run_id,
        scenario_path=scenario_path,
        phase=phase,
        import_set=sorted(import_set),
        symbol_set=sorted(symbol_set),
        assertion_count=assertion_count,
        test_names=sorted(test_names),
    )


def emit_iteration_temporal_signature(
    ef: Path,
    *,
    spec: str,
    run_id: str,
    iteration: int,
    prior_failure_set_hash: str,
    current_failure_set_hash: str,
    tests_moved_to_pass: int,
    tests_moved_to_fail: int,
    tests_unchanged: int,
    classification_distribution_delta: dict[str, int],
) -> Event:
    """Phase 6 I4: Per-iteration temporal signature.

    Catches zero-gain iterations (``tests_moved_to_pass +
    tests_moved_to_fail == 0`` with ``tests_unchanged > 0``) and
    classifier drift on unchanged test sets (non-empty
    ``classification_distribution_delta`` when the failing-test set is
    byte-identical across iterations).
    """
    return _emit(
        ef,
        EventType.ITERATION_TEMPORAL_SIGNATURE,
        spec=spec,
        run_id=run_id,
        iteration=iteration,
        prior_failure_set_hash=prior_failure_set_hash,
        current_failure_set_hash=current_failure_set_hash,
        tests_moved_to_pass=tests_moved_to_pass,
        tests_moved_to_fail=tests_moved_to_fail,
        tests_unchanged=tests_unchanged,
        classification_distribution_delta=classification_distribution_delta,
    )
