"""Event type definitions."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class EventType(enum.Enum):
    """All structured event types emitted by the pipeline."""
    PLAN_START = "PLAN_START"
    PLAN_COMPLETE = "PLAN_COMPLETE"
    PLAN_TIMEOUT = "PLAN_TIMEOUT"
    PLAN_RESUME = "PLAN_RESUME"
    SPEC_START = "SPEC_START"
    SPEC_DISPATCHED = "SPEC_DISPATCHED"
    SPEC_SHIPPED = "SPEC_SHIPPED"
    SPEC_FAILED = "SPEC_FAILED"
    SPEC_ESCALATED = "SPEC_ESCALATED"
    AGENT_START = "AGENT_START"
    AGENT_COMPLETED = "AGENT_COMPLETED"
    AGENT_FAILED = "AGENT_FAILED"
    GATE_COMPLETED = "GATE_COMPLETED"
    SMOKE_COMPLETED = "SMOKE_COMPLETED"
    SATISFACTION_SCORED = "SATISFACTION_SCORED"
    TRAJECTORY_CLASSIFIED = "TRAJECTORY_CLASSIFIED"
    REGRESSION_RUN = "REGRESSION_RUN"
    REGRESSION_SELECTION = "REGRESSION_SELECTION"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    BUDGET_ALERT = "BUDGET_ALERT"
    BARRIER_VIOLATION = "BARRIER_VIOLATION"
    BARRIER_PATH_ECHO_DETECTED = "BARRIER_PATH_ECHO_DETECTED"
    COST_SUMMARY = "COST_SUMMARY"
    ANOMALY_DETECTED = "ANOMALY_DETECTED"
    TRUST_SCORED = "TRUST_SCORED"
    ITER_COST = "ITER_COST"
    FILES_CHANGED = "FILES_CHANGED"
    # Deployment events (Phase 6)
    DEPLOY_PROVISION = "DEPLOY_PROVISION"
    DEPLOY_HEALTH_OK = "DEPLOY_HEALTH_OK"
    DEPLOY_HEALTH_FAIL = "DEPLOY_HEALTH_FAIL"
    DEPLOY_TEARDOWN = "DEPLOY_TEARDOWN"
    MIGRATION_CHECK_PASS = "MIGRATION_CHECK_PASS"
    MIGRATION_CHECK_FAIL = "MIGRATION_CHECK_FAIL"
    FLAG_CREATED = "FLAG_CREATED"
    FLAG_ENABLED = "FLAG_ENABLED"
    FLAG_DISABLED = "FLAG_DISABLED"
    CANARY_STAGE_START = "CANARY_STAGE_START"
    CANARY_STAGE_PASSED = "CANARY_STAGE_PASSED"
    CANARY_STAGE_FAILED = "CANARY_STAGE_FAILED"
    CANARY_PROMOTED = "CANARY_PROMOTED"
    CANARY_LOCK_TIMEOUT = "CANARY_LOCK_TIMEOUT"
    ROLLBACK_STARTED = "ROLLBACK_STARTED"
    ROLLBACK_SUCCESS = "ROLLBACK_SUCCESS"
    ROLLBACK_LAYER_FAILED = "ROLLBACK_LAYER_FAILED"
    ROLLBACK_ALL_FAILED = "ROLLBACK_ALL_FAILED"
    DEPLOY_COMPLETE = "DEPLOY_COMPLETE"
    ROLLBACK_WINDOW_EXPIRED = "ROLLBACK_WINDOW_EXPIRED"
    # Ship-with-gaps decision events
    SHIP_WITH_GAPS = "SHIP_WITH_GAPS"
    # Anchor-coverage floor (scenario-gen gate) events
    COVERAGE_PASS = "COVERAGE_PASS"
    COVERAGE_GAP = "COVERAGE_GAP"
    COVERAGE_REGEN = "COVERAGE_REGEN"
    COVERAGE_FLOOR_VIOLATION = "COVERAGE_FLOOR_VIOLATION"
    COVERAGE_DUPLICATE_TAGS = "COVERAGE_DUPLICATE_TAGS"
    COVERAGE_UNTESTABLE_WAIVED = "COVERAGE_UNTESTABLE_WAIVED"
    # Architect-amend flow — route architect-fixable coverage gaps back to
    # the architect instead of burning TE retries on structurally-unclosable
    # gaps.
    COVERAGE_ARCHITECT_AMEND = "COVERAGE_ARCHITECT_AMEND"
    COVERAGE_ARCHITECT_AMEND_COMPLETED = "COVERAGE_ARCHITECT_AMEND_COMPLETED"
    COVERAGE_ARCHITECT_AMEND_FAILED = "COVERAGE_ARCHITECT_AMEND_FAILED"
    # Distinct signal for gaps the architect cannot fix — the spec author
    # needs to amend the spec (e.g. add ``vehicle: subprocess``, split the
    # AC, or use a different test strategy).
    COVERAGE_SPEC_VEHICLE_MISMATCH = "COVERAGE_SPEC_VEHICLE_MISMATCH"
    # Emitted when a COVERAGE-GAPS.md entry lacks the required
    # ``- **Category:**`` line. Signals TE drift or prompt mismatch; the
    # gap is not routed to the architect (no guessing).
    COVERAGE_GAP_CATEGORY_MISSING = "COVERAGE_GAP_CATEGORY_MISSING"
    # Deterministic preflight (forbidden_patterns, test_presence) fail-open
    # — emitted when a host-side git command fails and the check cannot
    # fabricate a violation. Lets operators see silent skips instead of
    # learning about them only via correlated pipeline failures.
    PREFLIGHT_FAIL_OPEN = "PREFLIGHT_FAIL_OPEN"
    # C1: remediation loop — every pre-implementer gate that fails emits a
    # REMEDIATION_TARGET describing the fix; the *next* run emits a paired
    # REMEDIATION_OUTCOME classifying whether the applied fix worked. The
    # paired schema is what C5's policy auto-graduation consumes.
    REMEDIATION_TARGET = "REMEDIATION_TARGET"
    REMEDIATION_OUTCOME = "REMEDIATION_OUTCOME"
    # C5: emitted when a human operator uses ``--accept-novel`` to bypass
    # the first-occurrence guard on ``dark-factory remediate --apply``.
    # Audits the degraded-run decision so a later reviewer can reconstruct
    # which novel rules were installed without human y/n confirmation.
    REMEDIATION_ACCEPTED_NOVEL = "REMEDIATION_ACCEPTED_NOVEL"
    # Emitted when an adapter's ``contracts_dir()`` is staged under
    # ``.dark-factory/scenarios/<spec>/_contracts/<adapter>/`` before
    # scenario-generation. Distinguishes "scenarios failed because the
    # test-engineer hand-rolled wire predicates" from "scenarios failed
    # even with staged helpers available" in post-run audits.
    CONTRACTS_STAGED = "CONTRACTS_STAGED"


@dataclass
class Event:
    """A single pipeline event."""
    event_type: EventType
    timestamp: str
    spec_name: str = ""
    run_id: str = ""
    plan_run_id: str = ""
    iteration: int = 0
    data: dict[str, Any] = field(default_factory=dict)
