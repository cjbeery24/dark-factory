# Iter-1 Artifact Verification + Implementer-Side Retry Variance — Plan

**Date**: 2026-04-18
**Status**: DRAFT — pending neo review
**Extends**: `docs/plans/2026-04-16-agent-reliability-and-measurement.md` (Phase 5.6 in flight, Phase 6 pending)
**Target**: `~/sites/dark-factory`

---

## Motivation

Investigation of iter-1 pass-rate variance (0% → 55% → 91% across Phase 5 Runs 1/2/3 on frozen spec) surfaced two categories of concern that the 2026-04-16 plan does not cover:

1. **Pre-Phase-6 blind spot — is Run 3's 91% earned or artifactual?** Phase 6's pre-registered decision rules treat Run 3 as the anchor for V2/V3/V4 runs and as the "existence proof" that iter-1 convergence is achievable. If Run 3's test suite was materially softer than Runs 1/2 (fewer scenarios, thinner coverage-map), the 91% conflates implementer quality with test-volume variance, and Phase 6's V-dimension conclusions inherit that confound. **Neo flagged this as the dominant risk to Phase 6's validity.**

2. **Implementer-side retry is under-instrumented.** Phase 5.6 shipped `temperature=0` for the classifier; the implementer's retry calls still use default sampling. Plateau detection (`trajectory.py`) escalates on PLATEAU but never re-scopes. The implementer on iteration N+1 sees only `feedback-iter{N}.md` — not a dedupe-collapsed history of prior iterations' persistent failures. These are genuine Implementer-layer variance sources that Phase 7's architect/TE-focused interventions will not address.

This plan adds a prerequisite (Phase 5.7) before Phase 6 execution and a follow-on (Phase 8) after Phase 7. Neither duplicates shipped work; both close gaps neo identified in the 2026-04-17 review of the Phase 6 design.

## Non-goals

- Does not modify Phase 5.6 (classifier determinism) or Phase 6 (V1-V4 runs) design. Phase 5.7 gates Phase 6 execution but leaves its protocol unchanged.
- Does not add ensemble sampling (multiple implementer candidates) — rejected by neo; doubles cost with weak ROI while upstream variance is unaddressed.
- Does not add a separate Fix Planner agent — Oracle already writes priority-ordered fixes; splitting adds a hop without new information under the barrier.
- Does not add cross-run lessons-learned registry — barrier-risky; deferred until Phase 7 residual variance is characterized.

---

## Phase 5.7 — Iter-1 Artifact Measurement & TE Output Floor (ABSORBS INTO PHASE 6 V1)

**Effort**: S | **Timeline**: 1 day of plumbing + artifact review after Phase 6 V1 completes | **Dependency**: Phase 5.6 + Phase 6 V1 n=3 runs | **Blocks**: Phase 6 V2/V3/V4 execution (not V1)

### Problem

Phase 6's Prerequisite triage classifies Run 3's iter-2 *failing* tests but does not verify Run 3's iter-1 *suite composition*. If TE produced fewer scenarios or thinner coverage in Run 3, the 91% pass rate partially reflects measurement variance, not implementer skill. V2/V3 anchored runs would inherit that bias: V3's "implementer-only variance" would be measured against a softer target than V1's "free-run variance," manufacturing a false signal that the implementer converged.

### Prior runs are unrecoverable — pivot to forward-looking measurement

Phase 5 Run 1 and Run 2 scenario/holdout artifacts were cleared by subsequent reset cycles before the archive mechanism (commit 51d11b9) was in place or could accumulate them. The archive mechanism now accumulates per-`<run_id>` and will preserve every run from this point forward, but it cannot recover what was wiped.

**Decision:** 5.7a no longer tries to reconstruct Phase 5 Runs 1/2. Instead, it piggybacks on Phase 6 V1, which requires n=3 fresh runs anyway. Each V1 run is archived by the existing mechanism under `archived-runs/<run_id>/`. 5.7a becomes the post-V1 artifact comparison across those 3 fresh runs, and selects the V2/V3 anchor from among them.

This removes 5.7a from the critical path before V1 (V1 is already planned) and places it squarely between V1 and V2/V3 as a deliverable gate.

### 5.7a · V1 artifact comparison + anchor selection

**Target**: `docs/analysis/2026-04-18-v1-artifact-comparison.md` (produced after V1 n=3 completes).

**What it does**: Mechanical comparison across the three V1 runs (call them V1-a, V1-b, V1-c) on four axes:

| Axis | Source | What to compare |
|------|--------|-----------------|
| Scenario count | `archived-runs/<run_id>/scenarios/<spec>/` | Total file count, test function count |
| Holdout count | `archived-runs/<run_id>/holdouts/<spec>/` | Total file count, test function count |
| Coverage-map breadth | I2 scenario_fingerprint event from `archived-runs/<run_id>/events.jsonl` | FR/AC/EC anchors covered per run |
| Assertion count | Parse test files in archived scenarios | Total assertions per run; assertions per anchor |

Each axis gets a numeric value per run.

**Anchor selection rule (feeds V2/V3)**:
1. Rank the three V1 runs on coverage-map breadth (widest first).
2. The widest-coverage run is the anchor for V2 and V3.
3. If two runs tie on breadth, tiebreaker: higher scenario count; then higher assertion count.
4. Record anchor run_id in the analysis doc. Update Phase 6 V2/V3 invocation to use `--anchor-manifest archived-runs/<anchor_id>/architecture/<spec>-manifest.yaml --anchor-scenarios archived-runs/<anchor_id>/scenarios/<spec>/`.

**Dispersion report (feeds TE floor calibration and Phase 6 decision rules)**:
1. Report the range (max − min) for each axis across V1-a/b/c.
2. Report pass-rate for each V1 run.
3. Call out any run where an axis value is < 0.8× the V1 mean — that run is flagged as a "soft suite" candidate; TE floor calibration uses it.

**Tiebreaker when axes disagree about "softness"**: coverage-map breadth wins over raw scenario count. A run with more raw scenarios but thinner anchor coverage is treated as softer, because anchor coverage is what V2/V3 anchored-run experiments depend on.

**Acceptance**: Analysis doc shipped. Anchor run_id recorded. V2/V3 Phase 6 plan sections updated with the selected anchor before V2/V3 executes.

**Effort**: low. ~2 hours after V1 n=3 completes.

---

### 5.7b · TE output-volume floor

**Target**: `darkfactory/scenarios/te_floor.py` (new) + wire into `darkfactory/pipeline/iteration.py` after TE `coverage_regen` phase.

**What it does**: Deterministic check after TE completes. Pipeline fails pre-implementer (not pre-gate) if any of:

- Scenario count < `config.scenarios.min_count` (default `max(3, ceil(0.5 × anchor_count))` where anchor_count is the number of FR+AC+EC anchors in the manifest)
- Per-anchor coverage breadth < 1 scenario asserting against that anchor (for FR/AC anchors; EC anchors exempt — edge cases are optional at TE time)
- **Anchor-tagged assertion floor**: each scenario must contain ≥1 assertion whose subject-anchor identifier (FR-N/AC-N/EC-N tag in the assertion message, scenario docstring, or test name) matches the scenario's declared anchor. A scenario declared for FR-7 must have at least one assertion that references FR-7. Gameable (the agent can produce anchor-tagged assertions), but gaming it requires producing anchor-tagged assertions — which is exactly the convention we want.

**Explicit non-floor rejected by review**: an earlier draft used `assertion_count ≥ 2 × scenario_count` (two assertions per test). Rejected because it is trivially gameable with `expect(true).toBe(true)` padding and produces false positives on legitimate single-assertion property tests.

**Calibration prerequisite**: threshold values (`min_count` formula, the 0.5 multiplier) MUST be calibrated against Phase 6 V1 n=3 artifacts before shipping (Phase 5 Runs 1/2/3 artifacts are unrecoverable; see 5.7a revision). Required outcome: **floor fires on the V1 run with lowest iter-1 pass rate AND does not fire on the V1 run with highest iter-1 pass rate**, provided those runs span a ≥20pp pass-rate gap. If V1 pass rates are bunched (<20pp range), calibration is deferred until a run with a clear low-performing outlier is available — no fabricated thresholds ship. Do not declare thresholds from first principles.

**Ship ordering implication**: 5.7b implementation (the module + unit tests + wiring) can ship before V1 completes, gated on the default threshold formula above. The threshold *values* stay in `config.toml` as overridable. After V1, the calibration step either confirms the defaults or overrides them via config — no code change needed to retune.

**Failure mode**: TE is re-prompted (up to `config.scenarios.max_floor_retries`, default 2) with the specific floor that failed and a reference to `agents/test-engineer.md`. On third failure, pipeline escalates with `TE_FLOOR_VIOLATION` event — no implementer invocation.

**Why not just warn**: Soft-suite variance is exactly the measurement-instrument problem Phase 5.6 is already fixing for the classifier. The implementer has no way to know the suite is soft; a warning does not change outcomes. Hard gate with retry budget mirrors G1 (architect manifest retry).

**Acceptance**:
- Synthetic unit test: TE output with 2 scenarios and 6-anchor manifest → floor fails with specific citation.
- Synthetic unit test: TE output with 8 scenarios covering all 6 anchors with anchor-tagged assertions → floor passes.
- Synthetic unit test: TE output with scenarios that contain assertions but no anchor tags → floor fails on anchor-tagged assertion rule.
- **Calibration test (ship-blocking, runs after V1 completes)**: replayed against the V1 run with the lowest iter-1 pass rate → floor fires. Replayed against the V1 run with the highest iter-1 pass rate → floor does not fire. Both must hold on V1 runs that span a ≥20pp pass-rate gap. If the V1 batch is pass-rate-bunched, calibration is deferred until a future run produces a clear low-performing outlier — the floor module can ship with default thresholds, but the "floor is calibrated" claim waits.

**Effort**: medium. New module (~150 LOC), unit tests, iteration-loop wiring, one config key. Mirrors `forbidden_patterns.py` / `test_presence.py` pattern from the 2026-04-14 preflight guardrails plan.

**Metric**: `te_floor_retries` per run, `te_floor_violations` per phase. Target: <5% of runs trigger any retry; 0% escalate.

**Adapter integration**: Count/breadth checks are ecosystem-blind (operates on file count and anchor IDs). Assertion counting is language-specific (`it(`, `test(`, `def test_`, etc.). Add `count_assertions(path: Path) -> int` to `EcosystemAdapter` with a default-implementation that greps for common patterns across all supported languages — adapters override when they need framework-specific logic (e.g., parameterized tests in pytest).

---

## Phase 8 — Implementer-Side Retry Variance Reduction (POST-PHASE-7)

**Effort**: M | **Timeline**: 3-4 days | **Dependency**: Phase 7 results (determines whether implementer-side intervention is warranted)

Phase 7's V3 "implementer-only variance" signal determines whether this phase ships at all.

**Gating metric (explicit)**: iter-1 pass-rate **range** (max − min) across V3 runs (n ≥ 3). V3 anchors both the architect manifest and TE scenario set from Run 3 (or the 5.7a-selected anchor), so any remaining variance in iter-1 pass rate is implementer-layer non-determinism.

- **V3 iter-1 pass-rate range < 10pp**: implementer is solid at fixed upstream; Phase 8 deferred indefinitely.
- **V3 iter-1 pass-rate range ≥ 10pp**: implementer-layer variance is material; Phase 8 executes in the order below.

Reason for range (not stddev or mean): with n=3, range is robust and interpretable; stddev is under-powered per the Phase 6 Statistical Power Note.

### 8a · Cumulative feedback history with anchor dedupe

**Target**: `darkfactory/pipeline/feedback.py` (new or extend existing feedback module) + `agents/oracle.md` prompt update.

**What it does**: On iteration N ≥ 2, the implementer receives a *dedupe-collapsed* history file in addition to the latest `feedback-iter{N-1}.md`:

```
# Persistent failures across iterations
- FR-7, boundary condition handling — failed in iters 1, 2 (current: still failing)
- EC-3, error propagation — failed in iter 1, passing in iter 2

# New failures introduced in iter N-1
- FR-2, input validation — NEW (was passing in iter N-2)
```

**Construction rules** (barrier-safe per neo's review):
1. For each FR/EC anchor appearing in ≥2 prior feedback files, emit ONE line with the iteration list and current status.
2. For each anchor appearing in exactly one prior feedback file, emit one line with iteration number only.
3. **Never concatenate raw feedback text from prior iterations.** The dedupe step collapses repeated anchor-frequency into a single count; raw concatenation amplifies the coverage-frequency side channel (an anchor appearing 3×) that spec-section-anchoring already leaks mildly.
4. Diff-only: "NEW" tags introduced in iter N-1 vs iter N-2 rather than cumulative lists.

**Why**: Implementer on iteration 3 currently has no memory of what it tried on iteration 1. Oracle has this view (via B2, shipped); implementer does not. Dedupe + diff-only preserves "this keeps breaking — try a different approach" signal without amplifying the barrier leak.

**Acceptance**:
- Unit test: 3 prior feedback files with overlapping anchors → history contains deduped lines, no raw feedback text.
- Unit test: single prior feedback file → history file contains single-iteration entries.
- **Substring audit test (barrier regression guard)**: for every (prior_feedback_file, generated_history_file) pair under test, assert that no contiguous substring of length ≥ 30 characters from any prior feedback file appears in the generated history file. Ships with the module; runs in CI; failure means raw feedback text leaked into the dedupe output.
- End-to-end: Phase 5 Run 2 iter-3 implementer (which regressed) re-run with history file available — classify whether the regression category changes.

**Effort**: medium. New module, prompt wiring, feedback format parser (to extract anchors from prior iterations), unit tests.

**Metric**: `feedback_history_anchors_deduped` per retry; `persistent_anchor_count` per run.

---

### 8b · Auto-split on plateau

**Target**: `darkfactory/scenarios/trajectory.py` + orchestrator hook.

**What it does**: When `classify_trajectory` returns PLATEAU at iter ≥ 2 AND `detect_stable_failures` ratio > 0.7:

1. Extract the invariant failing-anchor set (FR/EC anchors present in every iter's failing set).
2. Emit `SUGGEST_DECOMPOSITION` event with the invariant anchors, spec ID, and current run ID.
3. Do **not** auto-execute decomposition. Escalate with a concrete re-scoping proposal:
   - "The following anchors failed in every iteration: FR-7, EC-3. Consider splitting the spec into: (a) core spec covering FR-1…FR-6 + EC-1, EC-2, EC-4 (likely to pass), and (b) targeted spec covering FR-7 + EC-3 (requires focused attention)."
4. Human approves, rejects, or edits the proposal.

**Why not auto-execute**: Spec decomposition is a domain judgment — the implementer and trajectory classifier lack the context to know whether FR-7 is genuinely orthogonal to FR-1…FR-6 or whether they're entangled. Machine proposes, human disposes.

**Why a gap**: Plateau detection currently escalates with "iteration budget exhausted" framing. The re-scoping proposal turns a NO-SHIP outcome into a concrete next action, and it's cheap — the invariant set is already computed.

**Acceptance**:
- Unit test: three iterations with failing sets `{FR-7, EC-3, AC-1}`, `{FR-7, EC-3, AC-2}`, `{FR-7, EC-3}` → invariant is `{FR-7, EC-3}`; proposal mentions those two anchors.
- End-to-end: Replay Phase 5 Run 1 or Run 2 trajectory → decomposition event fires; inspect whether the proposed split matches post-mortem intuition.

**Effort**: low-medium. Trajectory module extension, event schema addition, escalation message template update.

**Metric**: `decomposition_suggestions` per escalation; post-intervention: percentage accepted by human.

---

### 8c · Temperature=0 on Implementer retry calls

**Target**: `darkfactory/pipeline/iteration.py` — implementer `invoke_agent` call on iter ≥ 2.

**What it does**: On the first iteration, the implementer uses default temperature (exploration). On iteration ≥ 2, set `temperature=0` (exploitation against the specific failures surfaced in feedback).

**Why**: Retry calls are targeted fixes, not re-derivations. Default temperature sampling at retry time introduces variance orthogonal to feedback — the implementer might produce a different fix shape each time rather than converging on the one the feedback pointed at.

**Why iter-1 stays at default**: First iteration is an exploration step from a blank slate; deterministic sampling collapses the implementer's solution space. Variance is a feature at iter 1, a bug at iter 2+.

**Acceptance**:
- `events.jsonl` shows `implementer_temperature: 0` for iter ≥ 2 and `default` for iter 1.
- On a fixed-input test (same spec + same prior feedback), iter-2 implementer output is byte-identical across two invocations (modulo Claude infrastructure floor noted in Phase 5.6 — ~2-5% residual drift acceptable).

**Effort**: trivial. Single-line config change; unit test; event logging.

**Metric**: `iter2_plus_retry_determinism` — fraction of (run A, run B) pairs with same input producing byte-identical iter-2 implementer output. Target: ≥ 90% (accepting the 5% infra floor from Phase 5.6).

**Explicit non-goal**: Does not address iter-1 variance (that's Phase 6/7 territory). Addresses only the retry-loop's contribution to implementer non-determinism.

---

## Sequencing

```
  Phase 5.6 (classifier temp=0) ──┐
                                   │
  Phase 5.7b (TE floor module) ────┤
                                   │
                                   ▼
                          Phase 6 V1 (n=3 free runs, archive each)
                                   │
                                   ▼
                     Phase 5.7a (artifact comparison + anchor select)
                                   │
                     Phase 5.7b calibration (against V1 outliers)
                                   │
                                   ▼
                          Phase 6 V2/V3/V4
                                   │
                                   ▼
                          Phase 7 (targeted architect/TE fixes)
                                   │
                          V3 iter-1 pass-rate range ≥ 10pp?
                                   │
                                   ├── YES ──→ Phase 8 (8a, 8b, 8c independently)
                                   │
                                   └── NO ───→ Phase 8 deferred
```

**Decision gates**:
- Before V1: 5.7b module shipped with default thresholds (no calibration required yet); 5.6 complete.
- After V1 (n=3): 5.7a artifact comparison produces anchor for V2/V3; 5.7b calibration test runs against V1 outliers.
- Before V2/V3: anchor run_id selected; Phase 6 plan V2/V3 sections reference the chosen archive path.
- After V3: Read implementer variance. Gate Phase 8 on the iter-1 pass-rate range metric.

---

## Success Criteria

**Phase 5.7**:
- 5.7b TE floor module shipped with passing unit tests; wired into iteration loop; runs with default thresholds starting from Phase 6 V1.
- 5.7a artifact comparison doc shipped after Phase 6 V1 n=3 completes; anchor run_id recorded in the doc and referenced in Phase 6 V2/V3 invocations.
- 5.7b calibration test runs against V1 outliers after V1 completes; either confirms defaults or overrides via config.
- Zero Phase 6 V2/V3/V4 runs execute before 5.7a anchor selection is committed.

**Phase 8** (if gate trips — each sub-phase is independently shippable):
- 8a: Feedback history file present in `.dark-factory/reviews/<spec>-history-iter{N}.md` on every iter ≥ 2 invocation; no raw prior-feedback text present; dedupe rules + substring audit both verified by unit test.
- 8b: `SUGGEST_DECOMPOSITION` event fires on plateau + stable-failure condition; human receives a specific anchor-level proposal, not generic "consider splitting."
- 8c: Iter-2+ implementer calls log `temperature=0`; determinism metric ≥ 90%.

**Ship independence**: 8a, 8b, 8c share no code and can ship in any order. Each has its own unit test suite, metric, and rollback path. A half-shipped Phase 8 (e.g., only 8c landed) is a valid state; it does not block the remaining sub-phases.

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| 5.7a shows Run 3 was an outlier; no comparable anchor in Runs 1-3 | Synthesize an anchor by taking the best architect manifest from any run + the highest-breadth TE output from any run. Document as a *constructed* anchor. |
| TE floor causes cascading retry loops (every TE run fails floor) | Retry budget capped at 2; on third failure, hard escalate with the specific finding. Matches G1 pattern. |
| Cumulative history (8a) amplifies barrier leak despite dedupe | Pre-ship audit: compare dedupe-collapsed output against raw concatenation on 3 historical feedback chains; verify no additional scenario names, assertion text, or counts leak through. |
| Auto-split (8b) proposals are noisy or obviously wrong | Humans gate every suggestion. Track acceptance rate; if < 30% accepted over 10 escalations, revisit heuristic. |
| Temp=0 on retry collapses implementer into a broken-fix loop | Retry budget from existing pre-gate retry path (R3 in Phase 5) caps the loop; if implementer fails deterministically, feedback is the signal and must be enriched — this exposes the bug, doesn't create one. |

---

## Out of Scope (deferred or rejected)

- **Ensemble sampling on retries** — rejected by neo; doubles cost with weak ROI while upstream variance is the dominant contributor.
- **Fix Planner agent** — rejected; Oracle already writes priority-ordered fixes within barrier constraints, splitting adds a hop without new information.
- **Cross-run lessons-learned registry** — deferred; barrier-risky (cross-run memory could leak observed behavior), re-evaluate only after Phase 8 data shows residual variance.
- **Structured failure cards with observed-vs-expected text** — barrier-incompatible; richest version already shipped is spec-section + category from Defect Classifier.
