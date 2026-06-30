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
    TEST_ENGINEER = "test-engineer"
    QA_HOLDOUT = "qa-holdout"
    PROPOSAL_REHEARSAL = "proposal-rehearsal"
    # Phase 5 Stage 3 (2026-04-25): owns the "is the substrate ready
    # before code runs?" question. Inspects installed deps + existing
    # files, identifies tool-prerequisite gaps, emits idempotent shell
    # commands the gate runs as `pre_setup` before `setup`.
    SUBSTRATE_OWNER = "substrate-owner"
    # 2026-06-29: judges whether the Test Engineer's scenarios actually VERIFY
    # each anchor's behavior (vs. lexical tag-match). Form-agnostic + catches
    # vacuous/wrong coverage the deterministic floor credits. Read-only analysis
    # (spec + scenarios), masked from impl/holdouts/reports — its verdict is
    # consumed host-side by the coverage floor, never read by the Implementer
    # (no d2fecbf promotion). Routes per ModelProfile like any role (local/offline
    # supported); measured safe to localize (Qwen==Claude 13/14, 0 false-credit).
    COVERAGE_JUDGE = "coverage-judge"


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
