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
    SATISFACTION_SCORED = "SATISFACTION_SCORED"
    TRAJECTORY_CLASSIFIED = "TRAJECTORY_CLASSIFIED"
    REGRESSION_RUN = "REGRESSION_RUN"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    BUDGET_ALERT = "BUDGET_ALERT"
    BARRIER_VIOLATION = "BARRIER_VIOLATION"
    COST_SUMMARY = "COST_SUMMARY"
    ANOMALY_DETECTED = "ANOMALY_DETECTED"
    TRUST_SCORED = "TRUST_SCORED"
    ITER_COST = "ITER_COST"
    FILES_CHANGED = "FILES_CHANGED"


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
