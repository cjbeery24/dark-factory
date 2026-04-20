"""Pipeline execution state models."""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from darkfactory.deployment.models import DeploymentResult
    from darkfactory.models.satisfaction import TrajectoryType


class PipelineStatus(enum.Enum):
    """Overall pipeline status."""
    PENDING = "pending"
    RUNNING = "running"
    SHIPPED = "shipped"
    DEPLOYING = "deploying"
    DEPLOYED = "deployed"
    ROLLED_BACK = "rolled_back"
    ESCALATED = "escalated"
    FAILED = "failed"
    ABORTED = "aborted"
    HOLD = "hold"


@dataclass
class IterationState:
    """State tracked within a single iteration of the pipeline loop."""
    number: int
    gate_exit_code: int = 0
    hard_gates_pass: bool = False
    scenario_pass_count: int = 0
    scenario_fail_count: int = 0
    scenario_total: int = 0
    satisfaction_score: int = 0
    pass_rate: int = 0
    reviewer_verdict: str = ""
    oracle_verdict: str = ""
    files_changed: int = 0
    barrier_violations: int = 0
    wall_seconds: float = 0.0
    turns_used: int = 0
    sentinel_present: bool = False  # True when SCENARIOS-NOT-APPLICABLE.md was found
    # Crash-vs-assert breakdown (P4: scenario quality reporting).
    scenario_crashed: int = 0
    scenario_assert_failed: int = 0
    # Phase 7: skip observability — tests that silently skip (e.g., DB unreachable).
    scenario_skipped: int = 0
    failing_test_names: list[str] = field(default_factory=list)
    # Failure classification counts (e.g. {"IMPL_BUG": 3, "SPEC_AMBIGUOUS": 1}).
    classification_counts: dict[str, int] = field(default_factory=dict)
    # Server container logs on startup failure (threaded from GateResult).
    server_error: str = ""
    # Phase 1.2: actual count of implementer invocations within this iteration,
    # including pre-gate and smoke retries. Used to thread a true attempt
    # number into AGENT_START events instead of reconstructing from config.
    implementer_invocations: int = 0
    # Phase 4.6c: locked-denominator scope filtering. When the scope filter
    # reclassifies failing tests as out-of-scope (no anchor / unknown anchor /
    # asserts on unspecified literal input), the *_raw fields preserve the
    # original gate counts and the *active* fields above are overridden with
    # the in-scope-only counts. Oracle and floor checks consume the active
    # fields; trajectory analysis can still inspect _raw if needed.
    scenario_fail_count_raw: int = 0
    scenario_total_raw: int = 0
    pass_rate_raw: int = 0
    out_of_scope_failing: list[tuple[str, str]] = field(default_factory=list)
    # Export contract violations detected by gate-time verification.
    # Populated by _run_export_verification in the iteration loop.
    export_violations_text: str = ""
    # B2: sha-like ref capturing the working tree at oracle-call time for
    # this iteration. Used by the NEXT iteration's oracle prompt to diff
    # ``implementer effort since last iter``. Produced via
    # ``git stash create -u``; empty string when capture failed or git
    # was unavailable. Never used for git operations that change state.
    iteration_end_sha: str = ""
    # Priority 1.5 (2026-04-20): Self-Tests gate status captured from the
    # quality-gate summary for this iteration. One of "PASS", "FAIL",
    # "SKIP", or "" when the summary was missing/unparseable. Consumed
    # by the self-test regression check in the iteration loop to detect
    # iter-(N-1) PASS -> iter-N FAIL transitions (the V2-R3 failure mode:
    # an architectural patch that breaks visible scenarios it was meant
    # to help with).
    self_tests_status: str = ""
    # S1: persistent implementer container for pre-gate retry session
    # continuation. Populated when max_pre_gate_retries > 1 — the
    # initial implementer invocation and each pre-gate retry both run
    # inside this container via ``docker exec`` with ``claude --continue``,
    # so session state (transcript, prior tool results) is preserved
    # across retries without cold-start tax. Cleared after the pre-gate
    # loop exits (pass or exhaustion). Empty string when session mode is
    # disabled (max_pre_gate_retries <= 1) or between iterations —
    # session mode is pre-gate only, never cross-iteration.
    session_container_id: str = ""


@dataclass
class SpecRun:
    """All state for a single spec execution through the pipeline."""
    spec_name: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    plan_run_id: str = ""
    # Phase 4.1: parsed from the spec's dark-factory-metadata frontmatter.
    # Always populated by run_pipeline before any work happens; the empty
    # default exists only so direct construction in tests/tools doesn't
    # silently confer bootstrap privileges (any consumer that branches on
    # spec_type must treat empty as "not bootstrap").
    spec_type: str = ""
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
    # Stable failure ratio: fraction of failures invariant across iterations.
    # > 0.7 suggests scenario-dominated plateau (P4).
    stable_failure_ratio: float = 0.0
    # Names of tests that fail identically across all iterations.
    stable_failure_names: set[str] = field(default_factory=set)
    # Per-failure count of iterations where the failure was targeted in feedback.
    # Key: test name, Value: number of iterations it has been in feedback.
    # Used for mechanical reclassification: >= 2 triggers SPEC_AMBIGUOUS.
    failure_feedback_iterations: dict[str, int] = field(default_factory=dict)

    # Failure classification (populated from Oracle feedback).
    escalation_recommended: bool = False
    escalation_category: str = ""  # "ENV_ISSUE", "SPEC_AMBIGUOUS", etc.
    escalation_reason: str = ""
    classification_history: list[dict[str, str]] = field(default_factory=list)
    consecutive_server_errors: int = 0

    # Cost tracking
    estimated_cost_usd: float = 0.0

    # Trust
    trust_score: int = 50
    effective_autonomy: int = 0  # 0 = use config default
    effective_max_iterations: int = 0  # 0 = use config.max_iterations

    # Export contract violations from the most recent gate-time verification.
    # Persists across iterations so the Implementer prompt can inject them.
    export_violations_text: str = ""

    # Stable-failure SHIP override
    ship_with_gaps: bool = False

    # Strategy switching (Phase 7A)
    model_override: str = ""
    strategy_switches: int = 0

    # Deployment (Phase 6)
    deployment_result: DeploymentResult | None = None
    deployment_step: int = 0  # 0=none, 100-105 for post-ship stages

    # Deterministic preflight (forbidden_patterns + test_presence).
    # Git ref captured at pipeline run start via `git stash create` —
    # diffed against for tracked-file changes. Empty when the working
    # tree was clean at run start; the preflight then falls back to
    # ``HEAD``.
    #
    # ``preflight_untracked_baseline`` is a parallel capture of paths
    # that were untracked at run start (`git ls-files --others
    # --exclude-standard`). ``git stash create -u`` does NOT reliably
    # encode untracked files into a tree on all git versions (tested on
    # 2.50.1: the untracked parent is missing when there's only a
    # handful of untracked files), so we must capture the list
    # separately. The preflight subtracts this set from the currently-
    # untracked set so pre-existing untracked files are not attributed
    # to the implementer.
    preflight_base_ref: str = ""
    preflight_untracked_baseline: frozenset[str] = field(default_factory=frozenset)

    @property
    def wall_elapsed(self) -> float:
        return time.time() - self.start_time

    @property
    def current_iteration(self) -> IterationState | None:
        if self.iterations:
            return self.iterations[-1]
        return None
