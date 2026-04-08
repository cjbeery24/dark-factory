"""Agent role definitions and result types."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class AgentRole(enum.Enum):
    """All agent roles in the pipeline."""
    SPEC_VALIDATOR = "spec-validator"
    ARCHITECT = "architect"
    SCENARIO_ARCHITECT = "scenario-architect"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    ORACLE = "oracle"
    ESCALATION = "escalation"
    EVALUATOR = "evaluator"
    DEFECT_CLASSIFIER = "defect-classifier"


@dataclass
class AgentResult:
    """Result of an agent invocation."""
    role: AgentRole
    exit_code: int
    turns_used: int = 0
    output: str = ""
    output_file: str = ""
    timed_out: bool = False
    wall_seconds: float = 0.0
    retries: int = 0
    files_written: list[str] = field(default_factory=list)
    barrier_violations: list[str] = field(default_factory=list)
