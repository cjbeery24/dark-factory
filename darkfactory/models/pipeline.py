"""Pipeline execution state models."""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from darkfactory.models.satisfaction import TrajectoryType


class PipelineStatus(enum.Enum):
    """Overall pipeline status."""
    PENDING = "pending"
    RUNNING = "running"
    SHIPPED = "shipped"
    ESCALATED = "escalated"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass
class IterationState:
    """State tracked within a single iteration of the pipeline loop."""
    number: int
    gate_exit_code: int = 0
    scenario_pass_count: int = 0
    scenario_fail_count: int = 0
    scenario_total: int = 0
    satisfaction_score: int = 0
    pass_rate: int = 0
    reviewer_verdict: str = ""
    oracle_verdict: str = ""
    files_changed: int = 0
    wall_seconds: float = 0.0
    turns_used: int = 0


@dataclass
class SpecRun:
    """All state for a single spec execution through the pipeline."""
    spec_name: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    plan_run_id: str = ""
    status: PipelineStatus = PipelineStatus.PENDING
    completed_step: int = 0
    iteration: int = 1
    total_turns: int = 0
    total_wall_s: float = 0.0
    start_time: float = field(default_factory=time.time)
    iterations: list[IterationState] = field(default_factory=list)
    spec_hash: str = ""
    aborted: bool = False
    abort_reason: str = ""

    # Trajectory tracking (populated after iteration 2+).
    trajectory_class: TrajectoryType | None = None
    trajectory_strategy: str = ""

    # Cost tracking
    estimated_cost_usd: float = 0.0

    # Trust
    trust_score: int = 50
    effective_autonomy: int = 0  # 0 = use config default

    @property
    def wall_elapsed(self) -> float:
        return time.time() - self.start_time

    @property
    def current_iteration(self) -> IterationState | None:
        if self.iterations:
            return self.iterations[-1]
        return None
