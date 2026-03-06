"""Plan execution models."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class SpecDispatchStatus(enum.Enum):
    """Status of a spec within a plan."""
    PENDING = "pending"
    RUNNING = "running"
    SHIPPED = "shipped"
    FAILED = "failed"
    ESCALATED = "escalated"
    SKIPPED = "skipped"
    BUDGET_SKIPPED = "budget_skipped"


@dataclass
class PlanSpec:
    """A spec entry within a plan."""
    name: str
    deps: list[str] = field(default_factory=list)
    priority: int = 99
    target_files: list[str] = field(default_factory=list)
    status: SpecDispatchStatus = SpecDispatchStatus.PENDING
    spec_hash: str = ""
    worktree_path: str = ""
    pid: int = 0
    exit_code: int = -1
