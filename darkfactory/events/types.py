"""Convenience emit helpers for all 24 event types.

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

def emit_agent_start(ef: Path, *, spec: str, run_id: str, iteration: int, role: str, attempt: int = 1) -> Event:
    return _emit(ef, EventType.AGENT_START, spec=spec, run_id=run_id, iteration=iteration, role=role, attempt=attempt)

def emit_agent_completed(ef: Path, *, spec: str, run_id: str, iteration: int, role: str, turns: int, exit_code: int, model_id: str = "", est_input_tokens: int = 0, est_output_tokens: int = 0) -> Event:
    return _emit(ef, EventType.AGENT_COMPLETED, spec=spec, run_id=run_id, iteration=iteration, role=role, turns=turns, exit_code=exit_code, model_id=model_id, est_input_tokens=est_input_tokens, est_output_tokens=est_output_tokens)

def emit_agent_failed(ef: Path, *, spec: str, run_id: str, iteration: int, role: str, exit_code: int, reason: str = "", model_id: str = "", est_input_tokens: int = 0, est_output_tokens: int = 0) -> Event:
    return _emit(ef, EventType.AGENT_FAILED, spec=spec, run_id=run_id, iteration=iteration, role=role, exit_code=exit_code, reason=reason, model_id=model_id, est_input_tokens=est_input_tokens, est_output_tokens=est_output_tokens)

def emit_gate_completed(ef: Path, *, spec: str, run_id: str, iteration: int, exit_code: int, pass_rate: int = 0, satisfaction: int = 0) -> Event:
    return _emit(ef, EventType.GATE_COMPLETED, spec=spec, run_id=run_id, iteration=iteration, exit_code=exit_code, pass_rate=pass_rate, satisfaction=satisfaction)

def emit_satisfaction_scored(ef: Path, *, spec: str, run_id: str, iteration: int, score: int, pass_rate: int, categories: dict[str, Any] | None = None) -> Event:
    kwargs: dict[str, Any] = {"score": score, "pass_rate": pass_rate}
    if categories is not None:
        kwargs["categories"] = categories
    return _emit(ef, EventType.SATISFACTION_SCORED, spec=spec, run_id=run_id, iteration=iteration, **kwargs)

def emit_trajectory_classified(ef: Path, *, spec: str, run_id: str, iteration: int, trajectory: str, strategy: str = "") -> Event:
    return _emit(ef, EventType.TRAJECTORY_CLASSIFIED, spec=spec, run_id=run_id, iteration=iteration, trajectory=trajectory, strategy=strategy)

def emit_regression_run(ef: Path, *, spec: str, run_id: str, passed: int, failed: int, total: int) -> Event:
    return _emit(ef, EventType.REGRESSION_RUN, spec=spec, run_id=run_id, passed=passed, failed=failed, total=total)

def emit_budget_exceeded(ef: Path, *, spec: str, run_id: str, spent: float, budget: float) -> Event:
    return _emit(ef, EventType.BUDGET_EXCEEDED, spec=spec, run_id=run_id, spent=spent, budget=budget)

def emit_budget_alert(ef: Path, *, spec: str, run_id: str, percent: float, spent: float, budget: float) -> Event:
    return _emit(ef, EventType.BUDGET_ALERT, spec=spec, run_id=run_id, percent=percent, spent=spent, budget=budget)

def emit_barrier_violation(ef: Path, *, spec: str, run_id: str, role: str, path: str) -> Event:
    return _emit(ef, EventType.BARRIER_VIOLATION, spec=spec, run_id=run_id, role=role, path=path)

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
