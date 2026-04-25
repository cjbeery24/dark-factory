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
    # Emitted when a deterministic host-side preflight check intentionally
    # aborts the iteration (forbidden_patterns persisted across focused
    # retry, scenario infrastructure failure, etc.). The raise that
    # follows routes through ``_handle_abort``, so this event gives
    # Monitor tails a structured signal that stdout-only logging
    # previously hid (run d717e915 post-mortem).
    PREFLIGHT_VIOLATION = "PREFLIGHT_VIOLATION"
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
    # Emitted when an adapter is added to ``active_adapters`` for this
    # spec-run because ``adapter.spec_triggers(spec_text)`` returned True
    # even though the project-level ``[detected].active_adapters`` list
    # omitted it (G1 backstop — per-spec activation). Silent activation
    # is what the 2026-04-16 run exposed as a blind spot; this event
    # makes every non-declared activation a named, auditable decision.
    SPEC_ADAPTER_ACTIVATED = "SPEC_ADAPTER_ACTIVATED"
    # Emitted when the architect cache gate rejects a cached manifest
    # and forces a regenerate. ``reason`` distinguishes which cache axis
    # drifted:
    #   ``spec_hash_drift``           — spec text changed
    #   ``adapter_set_drift``         — stamped vs current adapter names
    #                                   differ (or the stamp is absent)
    #   ``substrate_manifest_missing`` — a triggered adapter's substrate
    #                                    TOML is absent on disk
    #   ``manifest_unreadable``       — YAML parse failure, shape
    #                                    mismatch, or ``stamp_integrity``
    #                                    mismatch (treat as stale rather
    #                                    than trust a hand-edited stamp)
    # The event is what turns a silent regenerate — burned architect
    # cost with no surface signal — into an auditable decision for
    # post-run forensics.
    ARCHITECT_CACHE_INVALIDATED = "ARCHITECT_CACHE_INVALIDATED"
    # M5: Prompt word count per agent invocation. Logs the assembled input
    # size so prompt reduction work (Phase 3) has a measurable baseline.
    # data: {role, prompt_word_count, task_prompt_words, system_prompt_words}
    PROMPT_WORD_COUNT = "PROMPT_WORD_COUNT"
    # M4: Post-implementer file existence preflight. Emitted when manifest
    # files with action:create are missing after implementation.
    # data: {missing_count, missing_files}
    MISSING_MANIFEST_FILES = "MISSING_MANIFEST_FILES"
    # M3: Oracle metric accuracy. Emitted on every Oracle invocation with
    # the delta between claimed and actual pass_rate. Used to detect and
    # correct metric fabrication.
    # data: {claimed_pass_rate, actual_pass_rate, delta, reprompted}
    ORACLE_METRIC_DELTA = "ORACLE_METRIC_DELTA"
    # G2: Scenario import validation. Emitted after TE writes scenarios,
    # reports specifier and symbol mismatches against manifest entry_points.
    # data: {specifier_violations, symbol_violations, details}
    SCENARIO_IMPORT_VIOLATIONS = "SCENARIO_IMPORT_VIOLATIONS"
    # M1: Monorepo command scoping. Emitted when a commands.* amendment op
    # targets a directory inconsistent with the spec's source_dirs.
    # data: {violations: [{command_key, command_value, reason}]}
    MONOREPO_SCOPING_VIOLATION = "MONOREPO_SCOPING_VIOLATION"
    # G1: Architect manifest retry. Emitted when manifest validation fails
    # and the architect is re-prompted with the error list.
    # data: {attempt, max_attempts, errors}
    MANIFEST_VALIDATION_RETRY = "MANIFEST_VALIDATION_RETRY"
    # R1: Iteration regression. Emitted when iteration N+1 regresses below
    # iteration N (higher exit code OR lower pass rate). The pipeline rolls
    # back to the previous iteration's git state.
    # data: {prev_iteration, prev_pass_rate, prev_exit_code,
    #        curr_pass_rate, curr_exit_code, rollback_ref}
    ITERATION_REGRESSION = "ITERATION_REGRESSION"
    # R2: Feedback word count. Emitted after Oracle writes NO-SHIP feedback.
    # Measures feedback volume to detect overcorrection risk.
    # data: {word_count}
    FEEDBACK_WORD_COUNT = "FEEDBACK_WORD_COUNT"
    # A1-esc: Agent definition injected via --append-system-prompt. Replaces
    # the prior prompt-level "read your agent definition" instruction for
    # implementer, reviewer, and oracle. Emitted once per agent invocation
    # where injection occurs so Phase 6 can verify injection happened.
    # data: {role, word_count}
    AGENT_DEF_INJECTED = "AGENT_DEF_INJECTED"
    # Phase 5.6 B4: Defect classifier input/output hashes. Emitted after
    # each classifier invocation (success or self-reported error) so
    # post-run analysis can compute ``classifier_determinism`` — the
    # fraction of (iter N, iter N+1) pairs with identical input_hash but
    # differing output_hash. No caching; the signal is the whole point.
    # data: {role, attempt, input_hash, output_hash, defects_count,
    #        failing_test_count, error}
    CLASSIFIER_IO_HASH = "CLASSIFIER_IO_HASH"
    # Phase 6 I4: Per-iteration temporal signature. Emitted after the
    # defect classifier runs (or after the gate, when there are no
    # failing tests to classify). Captures how the failing-test set and
    # per-category classifier output moved between this iteration and
    # the previous one — catches zero-gain iterations and classifier
    # drift on unchanged test sets in-band.
    # data: {prior_failure_set_hash, current_failure_set_hash,
    #        tests_moved_to_pass, tests_moved_to_fail, tests_unchanged,
    #        classification_distribution_delta}
    ITERATION_TEMPORAL_SIGNATURE = "ITERATION_TEMPORAL_SIGNATURE"
    # Phase 6 I1: architect manifest hash + canonical-JSON snapshot.
    # Emitted after the architect step (both on cache-hit and fresh-
    # validated paths). The hash is over a canonical JSON form of the
    # architect's semantic output — orchestrator-added stamp fields
    # (active_adapters, stamp_integrity) are stripped so runs on the
    # same spec + adapter set produce comparable hashes. Enables
    # manifest_diff_distance in scripts/variance-diff.py.
    # data: {manifest_hash, canonical_json, cache_hit}
    ARCHITECT_MANIFEST_HASH = "ARCHITECT_MANIFEST_HASH"
    # Phase 6 I2: per-scenario fingerprint. Emitted once per scenario
    # file after TE generate and each coverage_regen phase. Enables
    # scenario_diff_distance in scripts/variance-diff.py — fingerprint
    # fields are structural (import_set, symbol_set, assertion_count,
    # test_names) so prose-only drift in scenarios does not register.
    # data: {scenario_path, phase, import_set, symbol_set,
    #        assertion_count, test_names}
    SCENARIO_FINGERPRINT = "SCENARIO_FINGERPRINT"
    # Priority 4 (2026-04-20 plan revision): implementer invoked with
    # temperature=0 on iter >= 2. One event per affected invocation so
    # validation-run telemetry can confirm the policy fired and correlate
    # it with downstream plateau-breaking behavior.
    # data: {} (iteration + role=implementer are on the Event envelope)
    IMPLEMENTER_TEMP_ZERO = "IMPLEMENTER_TEMP_ZERO"
    # Priority 1.5 (2026-04-20 plan revision): self-tests went from PASS
    # on iter N-1 to FAIL on iter N. The iteration's changes are rolled
    # back and the loop advances with the prior iteration's state, matching
    # the V2-R3 failure mode where an architectural patch broke visible
    # scenarios.
    # data: {prev_status, curr_status, rollback_ref, rolled_back}
    SELF_TEST_REGRESSION_REJECTED = "SELF_TEST_REGRESSION_REJECTED"
    # 2026-04-22 iteration-steering-invariants Task 1: purge of stale
    # per-iter artifacts from a prior run of the same spec, emitted once
    # at the start of a non-resume ``dark-factory run``. Audit surface
    # for post-mortem ("what did the run sweep before starting?").
    # data: {purged_count, purged_files: [str, ...]}
    RUN_START_CLEANUP = "RUN_START_CLEANUP"
    # 2026-04-22 iteration-steering-invariants Task 2: iteration exited
    # via a pre-review escape hatch (self-test regression, gate
    # regression, B1 early escalation, or trajectory plateau). Pairs
    # with the synthetic feedback file written at the same site.
    # data: {reason, prior_feedback_inlined}
    ESCAPE_HATCH_FEEDBACK = "ESCAPE_HATCH_FEEDBACK"

    # Phase 5 Stage 3 (2026-04-25): substrate-owner agent emitted a
    # validated pre_setup command. data: {command}
    SUBSTRATE_PREP_EMITTED = "SUBSTRATE_PREP_EMITTED"

    # Phase 5 Stage 3: validator rejected an emitted command; the
    # orchestrator will re-prompt the agent with the rule + offending
    # entry up to the bounded retry count.
    # data: {attempt, step, command, rule}
    SUBSTRATE_PREP_VALIDATOR_REJECT = "SUBSTRATE_PREP_VALIDATOR_REJECT"

    # Phase 5 Stage 3: bounded retry loop bottomed out before all
    # rejections cleared. Caller proceeds with whatever passed
    # (best-effort) or skips pre_setup entirely.
    # data: {attempts, unresolved_rejections}
    SUBSTRATE_PREP_RETRIES_EXHAUSTED = "SUBSTRATE_PREP_RETRIES_EXHAUSTED"


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
