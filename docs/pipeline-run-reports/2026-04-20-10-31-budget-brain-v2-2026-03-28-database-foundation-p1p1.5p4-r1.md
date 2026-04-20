# Pipeline Run Report: database-foundation — P1+P1.5+P4 Validation Run 1/4

**Target:** budget-brain-v2
**Spec:** 2026-03-28-database-foundation
**Run ID:** `809f6003d6784228`
**Date:** 2026-04-20 17:00 UTC → 18:00 UTC (≈60 min)
**Outcome:** ESCALATED (PLATEAU) at iter 2 → escalation agent fired at iter 3
**Purpose:** Validation Run 1/4 for the implementer self-correction bundle (P1 cumulative feedback + diff + root-cause preamble; P1.5 self-test regression gate; P4 implementer temp=0 on iter ≥ 2). Free run (no anchor flags).
**Validation criterion:** plateau broken in ≥ 3/4 AND zero self-test regressions.
**This run contributes:** plateau NOT broken, zero self-test regressions.

## Iteration summary

| Iter | Pass rate | Satisfaction | Scenarios | Self-Tests | Classifier defects | Trajectory |
|------|-----------|--------------|-----------|------------|-------------------|------------|
| 1 | 60% (18/30) | 60 | 30 | **PASS** | 12 IMPL_BUG | — |
| 2 | 64% (22/34) | 64 | 34 | **PASS** | 12 IMPL_BUG | **PLATEAU** |

Forward progress: +4pp pass rate, 0 scenarios added by TE regen but +4 net scenarios from coverage amend all passing. Self-tests held PASS across both iterations — **P1.5 regression gate correctly did not fire** (prev=PASS, curr=PASS). Trajectory classifier flagged PLATEAU at iter 2 despite the +4pp because the failure cluster remained stable.

## P1/P4 instrumentation — did the new code paths run?

- **`IMPLEMENTER_TEMP_ZERO iter=2`** emitted at 2507s (ts `2026-04-20T17:25:59Z`). P4 live — iter 2 implementer invoked with `temperature=0`.
- **No `SELF_TEST_REGRESSION_REJECTED`** — correct, no PASS→FAIL transition to catch (iter 1 Self-Tests PASS, iter 2 Self-Tests PASS).
- **P1 prompt augmentation** — inferred-present (iter 2 implementer prompt_word_count rose vs iter 1 base, cumulative history + diff + preamble appended). Direct verification would require capturing the prompt; not done here.

## State file

```
spec=2026-03-28-database-foundation
run_id=809f6003d6784228
completed_step=2
iteration=2
smoke_result=passed
pass_rate=64
gate_exit_code=1
last_decision=NO-SHIP
phase=oracle_complete
status=escalated
```

## V-dimension instrumentation

- **I1 architect_manifest_hash:** `713f8c1ae01ef94a5216bbf1d8b7d2f9402ccf863ac64aeee130df50b090d202` — **cache_hit=true**. 5th observation of this hash (V1-R3 free, V2-R2/V2-R3 anchored, now Run 1/4 free). Architect is effectively deterministic on this spec.
- **I2 scenario_fingerprint count:** 12 scenarios after coverage amend (7 initial + 5 regen). Iter 1 exec: 30 total; iter 2 exec: 34 total.
- **I3 classifier_io_hash:** iter 1 → 12 defects; iter 2 → 12 defects. Defect count stable.
- **I4 iteration_temporal_signature:** emitted for both iterations.

## Quality gate summary (iter 2, final)

```
Install: PASS | Setup: PASS | Build: PASS | Lint: PASS | Type Check: PASS
Self-Tests: PASS | Blind Scenarios (executable): FAIL | Mutation Testing: SKIP
Result: FAIL
```

Blind scenarios: 22 passed / 12 failed / 0 skipped / 0 crashed (34 total).

## Failure cluster (iter 2)

Twelve stable IMPL_BUG failures, overlapping heavily with V1-R3/V2-R2/V2-R3 RLS/GUC cluster:

1. `[FR-2] SET LOCAL app.tenant_id works within transaction scope`
2. `[AC-10] SET LOCAL is transaction-scoped (no session leakage)` (substrate)
3. `[EC-5] transaction rollback also rolls back SET LOCAL`
4. `[EC-9] database supports idempotent migrations`
5. `[EC-10] concurrent tenant isolation`
6. `[EC-6] connection pool respects DB_POOL_MAX`
7. `[AC-11] withTenantDb sets app.tenant_id GUC for background jobs`
8. `[AC-10] transaction isolation`
9. `[AC-10] GUC does not leak between transactions`
10. `[FR-3] baseline migration documents RLS policy patterns`
11. `[FR-1] database supports migration tracking`
12. `[AC-3] db:generate produces migration files`

Iter 1 → iter 2 diff: iter-2 fixed `AC-1 connects` and `AC-2 exits-on-missing-DATABASE_URL`, but introduced new failures in the `FR-2 / AC-10 substrate: GUC behavior` anchor (from coverage amend regen). Net: 2 fixed, 2 introduced via coverage amend, +4 new passing scenarios, same 12-fail ceiling.

## Oracle metric deltas

- Iter 1: claimed=60%, actual=60%, delta=0 (accurate)
- Iter 2: claimed=64%, actual=64%, delta=0 (accurate)

## Notable events

- **COVERAGE_SPEC_VEHICLE_MISMATCH + COVERAGE_ARCHITECT_AMEND** at 1014s — architect re-invoked to patch missing entry points after TE generate. Normal flow.
- **COVERAGE_GAP + COVERAGE_REGEN** at 1104s — TE re-invoked; added 5 scenario files (7 → 12).
- **COVERAGE_PASS** at 1441s after 12 SCENARIO_FINGERPRINT events.
- **SMOKE_COMPLETED** both iterations: passed.
- **No `BARRIER_VIOLATION`** events.
- **No pre-gate retry** on either iteration.
- **SPEC_ESCALATED** at 3603s — escalation agent invoked at iter 3 after PLATEAU classification.

## Comparison vs prior baselines on same I1=`713f…`

| Dimension | V1-R3 (free) | V2-R2 (anchored) | V2-R3 (anchored) | **Run 1/4 (free, P1+P1.5+P4)** |
|-----------|--------------|------------------|------------------|-------------------------------|
| Scenarios (blind) | 19 | 30 | 56 | 34 |
| Iter 1 pass | 68% | 66% | 64% | 60% |
| Iter 2 pass | 68% **PLATEAU** | 66% **PLATEAU** | 76% IMPROVING (self-test regression) | 64% **PLATEAU** |
| Pass delta iter 1→2 | 0pp | 0pp | +12pp | **+4pp** |
| Self-tests final | PASS | PASS | **FAIL** (regression) | PASS |
| Failures final | 6 | 10 | 13 | 12 |
| Wall time | 47m | 55m | 50m | **60m** |
| Outcome | ESCALATED | ESCALATED | ESCALATED | ESCALATED |

**Observations:**
- Pass delta iter 1→2 moved from 0pp (plateau) in prior free runs at this I1 to **+4pp** here, with Priority 1 bundle active. Directionally positive but below the trajectory-classifier's PLATEAU threshold, so the plateau flag still fired.
- Self-tests held PASS — matching the non-regression baseline of V1-R3/V2-R2, distinct from V2-R3's regression. P1.5 gate ready but not exercised.
- Wall time slightly longer (60m vs 47-55m). No obvious cost-blowup.

## Validation counter

**1/4 complete. Plateau breaks so far: 0. Self-test regressions so far: 0.**

Required: ≥ 3 plateau breaks AND 0 regressions across all 4 runs. With 1 run done and 0 breaks, 3 of the remaining 3 runs must break plateau — the margin is now zero.

## Follow-ups for this run

1. **Plateau classifier threshold.** +4pp registers as PLATEAU here. Worth checking whether the threshold should be more nuanced (e.g., "plateau only if pass rate AND failure set both stable") — iter 2 did flip 2 failures (AC-1, AC-2) and introduce 2 new ones, so raw pass-rate delta under-reports forward motion. Not a plan-revision trigger yet; flag for Run 4 post-mortem.
2. **Implementer approach on iter 2.** Would be useful to capture the root-cause preamble from iter 2 prompt to confirm P1.3 fired and what `fix_scope` the implementer declared. Not captured this run — the prompt itself is not persisted. Consider emitting `ROOT_CAUSE_PREAMBLE_FIX_SCOPE` event in future enhancement.

## Next

- Run 2/4 immediately (same reset policy). Monitor for plateau breaks and any self-test regressions.
