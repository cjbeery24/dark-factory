# Pipeline Run Report: database-foundation — Phase 6 V2 Run 3/3 (anchor-manifest, workaround applied)

**Target:** budget-brain-v2
**Spec:** 2026-03-28-database-foundation
**Run ID:** `f90861497182494c`
**Date:** 2026-04-19 12:57 UTC → 13:50 UTC (≈49.5 min)
**Outcome:** ESCALATED @ 76% IMPROVING (iter 3 escalation agent fired despite positive trajectory — self-test regression triggered NO-SHIP)
**Purpose:** Phase 6 V2 anchor-manifest baseline, Run 3 of 3. Anchor: Run 3's manifest (`713f8c1ae01e…`). Substrate-manifests workaround applied.
**Anchor flags:** `--anchor-manifest .dark-factory/archived-runs/d793d508e9504006/architecture/2026-03-28-database-foundation-manifest.yaml`
**Pre-launch prep:** `cp archived-runs/d793d508e9504006/architecture/substrate-manifests/* .dark-factory/architecture/substrate-manifests/`

## Anchor held — second consecutive cache_hit

```
ARCHITECT_MANIFEST_HASH
  manifest_hash: 713f8c1ae01ef94a5216bbf1d8b7d2f9402ccf863ac64aeee130df50b090d202
  cache_hit: true
```

Third consecutive load of the same I1 (Run 3 free, V2-R2 anchored, V2-R3 anchored). Architect wall 202s. Workaround is reliable.

## Iteration summary

| Iter | Pass rate | Satisfaction | Scenarios | Crashed | Trajectory | Cost (est USD) |
|------|-----------|--------------|-----------|---------|------------|----------------|
| 1    | 64%       | 64%          | 56        | 0       | —          | $42            |
| 2    | 76%       | 76%          | 56        | 0       | IMPROVING  | $42            |

Escalation agent fired at iter 3 despite IMPROVING trajectory. **Total cost:** ~$84. **Wall time:** 2969s (49.5 min). **Total turns:** 400.

## Why did escalation fire on IMPROVING?

The iter 2 gate was a mixed signal:

- **Blind scenarios:** 76% (up from 64%) — IMPROVING
- **Self-Tests:** FAIL (down from PASS iter 1) — regression

Self-test failure: `TypeError: tx is not a function` at `apps/api/src/db/index.ts:74:20` — the implementer's iter 2 refactor of `withTenantDb` broke the callback signature, which the implementer's own internal tests caught (13 self-test files, 12 failed, 73 passed). Blind scenarios were partly immune because they use `request.withDb` indirectly through the Fastify plugin, not the raw `withTenantDb` call site the self-tests exercise.

**This is a classic TDD-via-blind failure pattern.** The implementer optimized for blind-scenario pass rate and regressed internal invariants. Oracle saw IMPROVING blind but emitted NO-SHIP because self-tests regressed; escalation agent fired. New V-dimension signal: *internal-test-regression-despite-blind-improvement*.

## State file

```
spec=2026-03-28-database-foundation
run_id=f90861497182494c
spec_hash=1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662
completed_step=2
iteration=2
smoke_result=passed
pass_rate=76
gate_exit_code=1
last_decision=NO-SHIP
phase=oracle_complete
status=escalated
```

## V-dimension instrumentation

- **I1 architect_manifest_hash:** `713f8c1ae01ef94a5216bbf1d8b7d2f9402ccf863ac64aeee130df50b090d202` — cache_hit=true (same as V2-R2)
- **I2 scenario_fingerprint count:** 56 scenarios — **highest in entire batch** (V1: 10/41/19, V2: 31/30/56). With I1 fully anchored, TE still produces 30/56 = ~87% variance in output volume.
- **I3 classifier_io_hash:**
  - Iter 1: input `3b57017052ea605e…`, output `3b17f334b5e8fae6…`, defects=20, failing=20
  - Iter 2: input `023686a7b1c83501…`, output `8ed72a6ec060d913…`, defects=13, failing=13
  - Different input hashes iteration-to-iteration (failure sets changed: 20→13 defects). Can't detect classifier drift this run (no identical input pair).
- **I4 iteration_temporal_signature:**
  - Iter 0→1: `tests_moved_to_fail=20`, delta `{IMPL_BUG: +20}`
  - Iter 1→2: `tests_moved_to_pass=7`, current_failure_set_hash changed — real forward progress

## Stable failures (iter 2, final — 13 IMPL_BUG)

1. `[AC-7] strict RLS pattern returns only matching rows`
2. `[AC-8] nullable RLS pattern returns tenant + null-tenant rows`
3. `[AC-12] unset SET LOCAL → zero rows via NULLIF`
4. `[AC-5] request.withDb propagates tenant context`
5. `[AC-10] concurrent requests maintain isolation`
6. `[AC-11] background job tenant scoping`
7. `[EC-4] GUC unset → NULLIF fail-closed`
8. `[EC-5] transaction failure preserves GUC state`
9. `[EC-10] concurrent tenant transactions`
10. `[AC-3] db:generate produces migration files`
11. `[FR-3] baseline migration documents RLS patterns`
12. `[FR-1] Connection respects DB_POOL_MAX`
13. `[EC-2] health check error when DATABASE_URL unreachable`

Failure cluster overlap with V2-R2: AC-7, AC-8, AC-10, AC-12, EC-4 (5 shared); V2-R3 adds AC-3, AC-5 propagation via request.withDb, AC-11 background job, EC-2 health-check, EC-5 tx rollback, EC-10 concurrent tx, FR-1 DB_POOL_MAX, FR-3 migration docs. **V2-R3 is broader than V2-R2 because TE exercised 56 vs 30 scenarios.**

Scenario_counts: `SCENARIO_REGISTERED=56 PASSED=43 FAILED=13 CRASHED=0`.

## Coverage amend flow

- **COVERAGE_GAP (attempt 1):** 10 uncovered — `FR-1, FR-2, FR-3, FR-4, EC-2, EC-3, EC-6, EC-7, EC-8, EC-9`
- **COVERAGE_REGEN (attempt 1):** TE rerun.
- **COVERAGE_PASS:** `anchor_count=28` (identical to Run 3, V2-R2).
- TE ran three phases (validate, coverage_regen, repair). BARRIER_VIOLATION fired on repair phase (see §notable events).

## Architect phase

- Anchor load successful (cache_hit=true, 202s)
- No G1 retries, no COVERAGE_ARCHITECT_AMEND

## Quality gate summary (iter 2, final)

```
Install: PASS | Setup: PASS | Build: PASS | Lint: PASS | Type Check: PASS
Self-Tests: FAIL | Blind Scenarios: FAIL | Mutation Testing: SKIP

Self-Tests: 12 failed | 73 passed (85 total across 13 files)
Blind Scenarios: 13 failed | 43 passed | 0 skipped (56 total)

Self-test failures: TypeError: tx is not a function (apps/api/src/db/index.ts:74)
Blind failures: AC-3/5/7/8/10/11/12 + EC-2/4/5/10 + FR-1/3 (all IMPL_BUG)
```

Oracle metric delta = 0 on both iterations.

## Notable events

- **`BARRIER_VIOLATION`** ×1 (test-engineer, repair phase, `apps/api/src`). Same shape as V1-R3. Defense-in-depth caught it; no data leaked.
- **No `MANIFEST_VALIDATION_RETRY` events** (cache hit path).
- **No `COVERAGE_ARCHITECT_AMEND`** (anchored architect produces correct entry points).
- **No pre-gate retry on iter 1** (implementer attempt 1 produced clean output directly).
- **No pre-gate retry on iter 2.**
- **`SPEC_ESCALATED`** iterations=2. Escalation agent fired at iter 3 despite trajectory=IMPROVING — see §"Why did escalation fire on IMPROVING?" above.

## V2 batch summary (n=3)

| Dimension | V2-R1 | V2-R2 | V2-R3 |
|-----------|-------|-------|-------|
| Anchor status | **invalidated** | held | held |
| I1 hash | `c6bb…` (5th distinct) | `713f…` (Run 3's) | `713f…` (Run 3's) |
| Outcome | ESCALATED @ 77% PLATEAU | ESCALATED @ 66% PLATEAU | ESCALATED @ 76% IMPROVING (self-test regression) |
| Iter 1 pass | 0% (vitest 0-tests) | 66% | 64% |
| Iter 2 pass | 77% | 66% | 76% |
| Peak pass rate | 77% | 66% | 76% |
| Iter count | 3 | 2 | 2 |
| Cost | $168 | $84 | $84 |
| Wall time | 56 min | 55 min | 50 min |
| Scenarios | 31 | 30 | **56** |
| Self-tests at final | PASS | PASS | **FAIL (regression)** |
| Failure cluster | edge-case (EC-2/5/6/8/9) | RLS/GUC (FR-2, AC-5/7/8/10/12) | broad mixed (13 anchors) |
| Classifier drift | n/a | none (deterministic) | can't measure (inputs differed) |
| Barrier violation | none | none | TE→apps/api/src |

## Cross-batch analysis: same I1=`713f…`, three runs

Three runs now resolved to I1 `713f8c1ae01e…` — Run 3 (free), V2-R2 (anchored), V2-R3 (anchored). Downstream variance held constant-architect:

| Dimension | Run 3 (free) | V2-R2 (locked) | V2-R3 (locked) |
|-----------|--------------|----------------|----------------|
| Scenarios | 19 | 30 | **56** |
| Iter 1 pass | 68% | 66% | 64% |
| Iter 2 pass | 68% (PLATEAU) | 66% (PLATEAU) | 76% (IMPROVING) |
| Failures | 6 | 10 | 13 |
| Self-tests final | PASS | PASS | FAIL |
| Wall | 47m | 55m | 50m |
| Outcome | ESCALATED | ESCALATED | ESCALATED |

**19/30/56 scenario spread from the same architect manifest.** This is the V2 batch's most emphatic finding: locking I1 does *not* collapse downstream variance, TE output volume is an independent axis with ~3x spread. All three runs escalated but via different paths (2× PLATEAU, 1× self-test regression despite IMPROVING).

## Full V1 + V2 variance envelope

| Run | I1 | Anchor | Outcome | Peak % | Iters | Cost | Wall | Scenarios | Cluster |
|-----|----|----|---------|--------|-------|------|------|-----------|---------|
| V1-R1 | `58fb…` | none | ESCALATED | 80% | 3 | $210 | 74m | 10 | env-container |
| V1-R2 | `2986…` | none | SHIPPED | 92% | 2 | $126 | 64m | 41 | mixed impl/brittle |
| V1-R3 | `713f…` | none | ESCALATED (PLATEAU) | 68% | 2 | $84 | 47m | 19 | RLS policy |
| V2-R1 | `c6bb…` | invalidated | ESCALATED (PLATEAU) | 77% | 3 | $168 | 56m | 31 | edge-case |
| V2-R2 | `713f…` | held | ESCALATED (PLATEAU) | 66% | 2 | $84 | 55m | 30 | RLS/GUC |
| V2-R3 | `713f…` | held | ESCALATED (self-test regression) | 76% | 2 | $84 | 50m | 56 | broad mixed |

**Across 6 runs:**
- 1 SHIPPED, 5 ESCALATED (including 2 of 2 anchor-holding V2 runs)
- Pass-rate spread: 66–92% (26 points)
- Scenario-count spread: 10–56 (**5.6×**)
- Cost spread: $84–$210 (2.5×)
- Wall spread: 47–74 min (57%)
- I1 distinct hashes: 4 (`58fb`, `2986`, `713f`, `c6bb`)
- I1 repeats: `713f` observed 3 times (Run 3, V2-R2, V2-R3) with very different downstream

**Anchor-manifest alone is insufficient** to collapse variance. Downstream TE, implementer, and classifier each contribute measurable variance. Phase 6 V3 (anchor-scenarios) and V4 (anchor-environment) will stack more anchors to see which combinations produce reproducible runs.

## Follow-ups

1. **Phase 5.7b (TE output-volume floor) is now mission-critical.** 19/30/56 scenario spread on identical architect manifest is the strongest signal yet for constraining TE. V2 data demonstrates that locking architect alone doesn't constrain scenario volume.
2. **Self-test regression as V-dimension.** V2-R3 is the first run where self-tests regressed while blind improved. This is the shape of "implementer over-optimizes for blind scenarios, breaks internal invariants." Worth instrumenting `self_test_delta` as I5 dimension — separate from I4 (blind test set movement).
3. **Escalation logic under mixed signal (IMPROVING blind + regressed self-tests).** Current behavior: escalate. This is probably correct — regression is a worse signal than forward movement on blind — but worth documenting in the trajectory classifier spec. Currently TRAJECTORY_CLASSIFIED emitted "IMPROVING" based on blind only, which is misleading.
4. **Anchor-manifest workaround documented and validated n=2.** Promote pipeline-level fix (auto-seed substrate-manifests) to Phase 7. Workaround is sufficient for V3/V4 batches.
5. **Broader RLS/GUC failure cluster confirmation.** V2-R2 and V2-R3 both concentrated on RLS/GUC anchors (AC-5/7/8/10/12, EC-4). This is the "hard part" of the spec under I1=`713f…`. Worth investigating whether the architect's I1 choice implicates RLS complexity specifically, or whether the implementer's struggle is independent.

## Next

- **V2 batch complete.** Full V1 + V2 baseline data in hand for Phase 6 analysis.
- Consider V3 (anchor-scenarios) next: add `--anchor-scenarios` alongside `--anchor-manifest` to pin TE output. This should collapse I2 variance and isolate implementer-only variance.
- Before V3: address Phase 5.7b TE output-volume floor — V2 batch data strongly motivates it independent of anchor work.
