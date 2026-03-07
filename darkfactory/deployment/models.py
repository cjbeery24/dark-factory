"""Deployment-specific data models."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class DeploymentStatus(enum.Enum):
    PENDING = "pending"
    PROVISIONING = "provisioning"
    EVALUATING = "evaluating"
    CANARY = "canary"
    PROMOTED = "promoted"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class RollbackLayer(enum.Enum):
    FLAG_KILL = "flag_kill"
    TRAFFIC_REVERT = "traffic_revert"
    CONTAINER_ROLLBACK = "container_rollback"
    GIT_REVERT = "git_revert"


@dataclass
class CanaryStage:
    """One stage in the canary rollout."""
    percent: int
    soak_seconds: int
    name: str = ""

    def __post_init__(self) -> None:
        if not 1 <= self.percent <= 100:
            raise ValueError(f"CanaryStage percent must be 1-100, got {self.percent}")
        if self.soak_seconds < 0:
            raise ValueError(f"CanaryStage soak_seconds must be >= 0, got {self.soak_seconds}")
        if not self.name:
            self.name = f"canary-{self.percent}pct"


@dataclass
class MetricThreshold:
    """A single metric threshold for canary evaluation."""
    name: str
    max_value: float | None = None
    min_value: float | None = None
    metric_cmd: str = ""

    def __post_init__(self) -> None:
        if self.max_value is None and self.min_value is None:
            raise ValueError(f"MetricThreshold '{self.name}' must have max_value, min_value, or both")


@dataclass
class CanaryResult:
    """Result of one canary stage evaluation."""
    stage: CanaryStage
    passed: bool
    metrics: dict[str, float] = field(default_factory=dict)
    breached: list[str] = field(default_factory=list)
    wall_seconds: float = 0.0


@dataclass
class DeploymentResult:
    """Overall deployment result."""
    status: DeploymentStatus
    stages_completed: int
    stages_total: int
    rollback_layer: RollbackLayer | None = None
    rollback_reason: str = ""
    wall_seconds: float = 0.0
    error: str = ""


@dataclass
class MigrationCheckResult:
    """Result of migration safety analysis."""
    safe: bool
    destructive_ops: list[str] = field(default_factory=list)
    additive_ops: list[str] = field(default_factory=list)
    mixed: bool = False
    files_checked: list[str] = field(default_factory=list)
