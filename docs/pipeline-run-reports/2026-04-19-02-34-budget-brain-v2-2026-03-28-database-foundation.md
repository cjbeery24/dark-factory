# Pipeline Run Report: database-foundation — Phase 6 V1 Run 3/3

**Target:** budget-brain-v2
**Spec:** 2026-03-28-database-foundation
**Run ID:** `d793d508e9504006`
**Date:** 2026-04-19 02:34 UTC → 03:21 UTC (≈47 min)
**Outcome:** ESCALATED
**Purpose:** Phase 6 V1 free-run baseline, Run 3 of 3 (second attempt; first attempt `3f074f4f71e6410a` aborted on Claude API quota exhaustion ~11 min in, after architect completed — partial artifacts archived at `archived-runs/3f074f4f71e6410a-aborted-quota/`). No anchor flags.

## Iteration summary

| Iter | Pass rate | Satisfaction | Scenarios | Crashed | Trajectory | Cost (est USD) |
|------|-----------|--------------|-----------|---------|------------|----------------|
| 1    | 68%       | 68%          | 19        | 0       | —          | $42            |
| 2    | 68%       | 68%          | 19        | 0       | PLATEAU    | $42            |

Escalation fired after iter 2 (PLATEAU trajectory triggered SPEC_ESCALATED via the escalation agent; iter 3 implementer never ran).

**Total cost:** ~$84 (lowest of V1 baseline). **Wall time:** 2830s (47.2 min). **Total turns:** 400.

## State file

```
spec=2026-03-28-database-foundation
run_id=d793d508e9504006
spec_hash=1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662
completed_step=2
iteration=2
smoke_result=passed
pass_rate=68
gate_exit_code=1
last_decision=NO-SHIP
phase=oracle_complete
status=escalated
```

## V-dimension instrumentation (I1/I2/I3/I4)

- **I1 architect_manifest_hash:** `713f8c1ae01ef94a5216bbf1d8b7d2f9402ccf863ac64aeee130df50b090d202`
  - **4th distinct manifest hash across V1 attempts:** Run 1 `58fb4bbebd35…`, Run 2 `2986fed1c571…`, Run 3-aborted `7a7ce9bd05dd…`, Run 3-retry `713f8c1ae01e…`. All four produced with identical frozen spec. Architect non-determinism confirmed at the hash level.
- **I2 scenario_fingerprint count:** 17 fingerprints across initial + regen.
- **I3 classifier_io_hash:**
  - Iter 1: input `217a58bc407b44d7…`, output `ae64d311e7a2c2b2…`, defects=6, failing=6
  - Iter 2: input `217a58bc407b44d7…` (**identical to iter 1** — same 6 failing tests), output `b9ae917f22a8c201…`, defects=6, failing=6
  - **Classifier output differs despite identical input.** See §classifier drift below.
- **I4 iteration_temporal_signature:**
  - Iter 0→1: `tests_moved_to_fail=6`, delta `{ENV_ISSUE: +6}`
  - Iter 1→2: `tests_moved_to_pass=0`, `tests_moved_to_fail=0`, `unchanged=6`, delta `{IMPL_BUG: +6, ENV_ISSUE: -6}` — **same 6 failing tests reclassified wholesale** from ENV_ISSUE to IMPL_BUG with no code or test changes between the iterations.

## Classifier drift (non-determinism) — new V-dimension signal

Iter 1 and iter 2 had **identical failing test sets** (current_failure_set_hash `b9d8d5f992276bb0…` unchanged across iterations; classifier input_hash `217a58bc407b44d7…` identical). Yet:
- Iter 1 classifier: 6× `ENV_ISSUE`
- Iter 2 classifier: 6× `IMPL_BUG`

This is the same spec, same scenarios, same test results, same classifier prompt — only the non-determinism of the model run produced a different categorization. This hasn't been visible in prior runs because earlier runs had at least some tests moving between iterations, masking classifier variance in the delta noise.

**Implication for Phase 6:** classifier output is itself a V-dimension source, and defect categorization shouldn't be treated as a stable signal across iterations without aggregation. The oracle's trajectory classifier relies on this — if PLATEAU detection is based on delta distribution rather than absolute test movement, classifier drift could flip the trajectory classification.

## Stable failures (iter 2, final — all IMPL_BUG per iter-2 classifier)

Six tests failed across both iterations, none passing in either:

1. `RLS Policy Patterns > [AC-7] strict RLS policy pattern returns only matching tenant rows`
2. `RLS Policy Patterns > [AC-8] nullable RLS policy pattern returns tenant and null-tenant rows`
3. `RLS Policy Patterns > [FR-3] baseline migration documents RLS policy patterns without creating tables`
4. `RLS Policy Patterns > [AC-12] demonstrates fail-closed behavior when app.tenant_id is unset`
5. `Database Connection > [AC-1] connects to PostgreSQL with valid DATABASE_URL and health check returns ok`
6. `Database Connection > [EC-6] handles connection pool exhaustion gracefully`

Concentrated in the RLS policy pattern suite (4 of 6). Scenario_counts: `SCENARIO_REGISTERED=19 SCENARIO_PASSED=13 SCENARIO_FAILED=6`.

## Coverage amend flow

Single COVERAGE cycle:
- `COVERAGE_GAP` (attempt 1) → `COVERAGE_REGEN` → `COVERAGE_PASS` after regen. No architect amend needed.
- TE produced 8 fingerprints initially, 9 after regen = 17 total.

## Architect manifest validation retries (G1)

**Zero retries.** Architect succeeded on attempt 1 (252s wall). Cleanest architect phase across V1 n=3 (Run 1 used 2/3, Run 2 used 1/2).

## Quality gate summary (iter 2, final)

```
Install: PASS | Setup: PASS | Build: PASS | Lint: PASS | Type Check: PASS
Self-Tests: PASS | Blind Scenarios: FAIL | Mutation Testing: SKIP

Tests: 6 failed | 13 passed | 0 skipped (19 total)
Failures: AC-1, AC-7, AC-8, AC-12, FR-3, EC-6 (all IMPL_BUG per iter-2 classifier;
all ENV_ISSUE per iter-1 classifier — see §classifier drift)
```

## Notable events

- **`BARRIER_VIOLATION` ×2 (test-engineer, both attempts):** TE tried to read `apps/api/src` (source directory, masked tmpfs). Defense-in-depth caught it; no data leaked. Subsequent TE attempts still succeeded — the barrier check is a probe, not a fatal error. Run 1 had BARRIER_VIOLATION on implementer→`.git` for the same reason.
- **PLATEAU-triggered early escalation:** SPEC_ESCALATED fired at iter 2 rather than iter 3 because `TRAJECTORY_CLASSIFIED=PLATEAU` (two identical pass rates → no forward progress). The escalation agent ran for 60s and emitted the terminal event; iter 3 implementer was never invoked. This saved ~10 min and $42 vs. continuing through a third iteration.
- **No `COVERAGE_ARCHITECT_AMEND`** (unlike Run 1). Architect entry_points were correct first pass.
- **No G1 manifest retries** (unlike Run 1 ×2 and Run 2 ×1).
- **`ORACLE_METRIC_DELTA`** delta=0 on both iterations.
- **`SMOKE_COMPLETED`** success=true both iterations.
- **Scenario count (19) is the lowest of V1 n=3** — Run 1 peaked at 10 (iter-3 after regen), Run 2 peaked at 41, Run 3 at 19. Wide variance in TE output volume for the same spec (Phase 5.7b "TE output-volume floor" directly addresses this dimension; currently pending).

## Observations across V1 n=3

| Dimension | Run 1 | Run 2 | Run 3 |
|-----------|-------|-------|-------|
| Outcome | ESCALATED @ iter 3 | SHIPPED @ iter 2 | ESCALATED @ iter 2 (PLATEAU) |
| Iter 1 pass rate | 44% | 0% | 68% |
| Peak pass rate | 80% | 92% | 68% |
| Iter count (impl runs) | 3 | 2 | 2 |
| Cost | $210 | $126 | $84 |
| Wall time | 73.8 min | 64.1 min | 47.2 min |
| I1 manifest hash | `58fb4bbebd35…` | `2986fed1c571…` | `713f8c1ae01e…` |
| Scenarios (peak) | 10 | 41 | 19 |
| Coverage amend | yes (architect amend) | yes (TE regen only) | yes (TE regen only) |
| G1 retries | 2/3 | 1/2 | 0/2 |
| BARRIER_VIOLATION | implementer→.git ×2 | none | test-engineer→apps/api/src ×2 |
| Stable failure class | 2× ENV_ISSUE | 3× {IMPL_BUG, BRITTLE×2} | 6× IMPL_BUG (iter 2) / 6× ENV_ISSUE (iter 1) |

**Across three identical starting conditions, the pipeline produced:**
- 3 distinct outcomes (1 ship, 2 escalate via different paths)
- 3 distinct final pass rates (80%, 92%, 68%)
- 3 distinct architect manifests (zero overlap on I1 hash)
- 3 distinct failure concentrations (env-container, impl+brittle mix, RLS-policy cluster)
- 3 distinct wall times (73.8m, 64.1m, 47.2m — 56% spread)
- 3 distinct costs ($210, $126, $84 — 150% spread)
- Classifier drift: iter 1 vs iter 2 on same failing set produced different defect categories

**This is the V1 free-run variance envelope.** Phase 6 V2 (anchor-manifest) will hold I1 constant across runs to measure how much of this envelope collapses when architect output is fixed.

## Follow-ups

1. **Classifier drift is a newly-exposed V-source** — classifier output varies across runs with identical input. Worth instrumenting explicit classifier non-determinism measurement (run classifier N times on same input, measure distribution). May want an "anchor-classifier" V-dimension if variance is load-bearing.
2. **BARRIER_VIOLATION pattern across V1:** Run 1 had implementer→.git, Run 3 had test-engineer→apps/api/src. Different roles hit different masked paths across runs. Worth confirming defense-in-depth holds under all role/path combinations (barriers aren't probabilistic, but the agents' attempts-to-cross are).
3. **PLATEAU at 68% may be a speed-vs-quality tradeoff** — Run 3 escalated fastest ($84) but at lowest quality. The trajectory classifier pulled the plug aggressively. Worth checking whether ship-with-gaps would have been inappropriate here (6 stable IMPL_BUG failures on RLS policy cluster is different-shaped from Run 1's 2× ENV_ISSUE or Run 2's 3 mixed-category failures at 92%).
4. **Scenario count variance (10/41/19)** — three different TE output volumes from the same spec. Phase 5.7b TE output-volume floor module (pending implementation) directly addresses this. V1 n=3 confirms this is worth prioritizing.
5. **Test-engineer BARRIER_VIOLATION on apps/api/src** — TE should never attempt source reads (scenarios are blind-to-impl by design). Worth a prompt-level reinforcement or post-hoc detection in the TE role.

## Variance sources (preserved for V1 n=3 analysis)

Run 3 artifacts will be preserved under `.dark-factory/archived-runs/d793d508e9504006/` via the next `/df-pipeline-run` archive step. Compare against:

- Run 1 (`65f81a73cf2646ed`) — ESCALATED @ 80%
- Run 2 (`c8f494d618704097`) — SHIPPED @ 92%
- Run 3-aborted (`3f074f4f71e6410a-aborted-quota`) — architect output only (I1 `7a7ce9bd05dd…`) before quota kill
- Run 3 (this report, `d793d508e9504006`) — ESCALATED @ 68% (PLATEAU)

## Next

V1 n=3 complete. V-dimension variance envelope characterized. Next phases: Run the Phase 6 V2 baseline (anchor-manifest against one of Run 1/2/3's I1 hashes) to measure how much downstream variance collapses when the architect output is locked. Also: Phase 5.7b TE output-volume floor remains pending (Task #3) — the 10/19/41 scenario spread across V1 is strong motivation.
