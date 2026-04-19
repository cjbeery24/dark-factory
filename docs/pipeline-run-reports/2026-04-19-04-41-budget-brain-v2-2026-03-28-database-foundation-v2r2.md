# Pipeline Run Report: database-foundation — Phase 6 V2 Run 2/3 (anchor-manifest, workaround applied)

**Target:** budget-brain-v2
**Spec:** 2026-03-28-database-foundation
**Run ID:** `75277dc433604b10`
**Date:** 2026-04-19 04:41 UTC → 05:37 UTC (≈55.7 min)
**Outcome:** ESCALATED @ 66% PLATEAU (iter 3 escalation agent)
**Purpose:** Phase 6 V2 anchor-manifest baseline, Run 2 of 3. Anchor: Run 3's manifest (`713f8c1ae01e…`). **Workaround applied per V2 Run 1 follow-up (b):** substrate-manifests copied from Run 3's archive to runtime before launch.
**Anchor flags:** `--anchor-manifest .dark-factory/archived-runs/d793d508e9504006/architecture/2026-03-28-database-foundation-manifest.yaml`
**Pre-launch prep:** `cp archived-runs/d793d508e9504006/architecture/substrate-manifests/* .dark-factory/architecture/substrate-manifests/`

## Anchor held — I1 locked

**First successful anchor-lock across the V2 batch.** Architect emitted:

```
ARCHITECT_MANIFEST_HASH
  manifest_hash: 713f8c1ae01ef94a5216bbf1d8b7d2f9402ccf863ac64aeee130df50b090d202
  cache_hit: true
```

Matches Run 3's I1 exactly. Architect wall time 167s (vs. ~220s fresh in V2 Run 1), consistent with a cache-path load. The substrate-manifests workaround was sufficient; no `ARCHITECT_CACHE_INVALIDATED` this run.

## Iteration summary

| Iter | Pass rate | Satisfaction | Scenarios | Crashed | Trajectory | Cost (est USD) |
|------|-----------|--------------|-----------|---------|------------|----------------|
| 1    | 66%       | 66%          | 30        | 0       | —          | $42            |
| 2    | 66%       | 66%          | 30        | 0       | PLATEAU    | $42            |

Escalation agent fired at iter 3 after PLATEAU at iter 2. No iter 3 implementer ran. **Total cost:** ~$84. **Wall time:** 3329s (55.5 min). **Total turns:** 400.

## State file

```
spec=2026-03-28-database-foundation
run_id=75277dc433604b10
spec_hash=1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662
completed_step=2
iteration=2
smoke_result=passed
pass_rate=66
gate_exit_code=1
last_decision=NO-SHIP
phase=oracle_complete
status=escalated
```

## V-dimension instrumentation

- **I1 architect_manifest_hash:** `713f8c1ae01ef94a5216bbf1d8b7d2f9402ccf863ac64aeee130df50b090d202` — **cache_hit=true**, matches Run 3.
- **I2 scenario_fingerprint count:** 30 scenarios registered (initial + regen + repair).
  - **Signal: I2 varies even with I1 locked.** V2-R2 registered 30 scenarios; Run 3 (same I1) registered 19. TE output volume is a V-source downstream of architect.
- **I3 classifier_io_hash (both iterations identical):**
  - Iter 1: input `a90e86dbd6cdbd29…`, output `404ab04604077c35…`, defects=10, failing=10
  - Iter 2: input `a90e86dbd6cdbd29…`, output `404ab04604077c35…`, defects=10, failing=10
  - **Classifier deterministic this run.** Contrast Run 3: same input hash across iters, *different* output hashes (classifier drift). Sampling: classifier drift is variable across runs, not universal.
- **I4 iteration_temporal_signature:**
  - Iter 0→1: `tests_moved_to_fail=10`, delta `{IMPL_BUG: +10}` (30 scenarios registered first attempt)
  - Iter 1→2: current_failure_set_hash `e2a647983f6414ec…` unchanged from iter 1 — same 10 failing tests, no movement

## Iter 1 no vitest-discovery issue

Unlike V2 Run 1 and V1 Run 2 (both hit iter-1 vitest `no tests found` at 0%), iter 1 here registered all 30 scenarios on first gate attempt. 66% pass rate directly. Implementer attempt 1 ran clean (no pre-gate retry); R3 budget untouched for iter 1.

## Stable failures (iter 2 = iter 1, all IMPL_BUG per classifier)

Ten tests failed across both iterations, identical set:

1. `RLS infrastructure and tenant context > [FR-2] SET LOCAL app.tenant_id is transaction-scoped and rolls back`
2. `RLS infrastructure and tenant context > [FR-2] SET LOCAL uses literal interpolation not parameterized query`
3. `RLS infrastructure and tenant context > [FR-2] multiple concurrent transactions maintain isolation`
4. `RLS infrastructure and tenant context > [EC-4] unset app.tenant_id GUC causes fail-closed behavior via NULLIF pattern`
5. `Substrate GUC behavior > [AC-12] unset GUC returns nullish / NULLIF yields SQL NULL for fail-closed`
6. `Substrate GUC behavior > [AC-5] SET LOCAL value retrievable via current_setting`
7. `Substrate GUC behavior > [AC-10] concurrent transactions see their own tenant ID without leakage`
8. `Substrate GUC behavior > [AC-7] strict RLS pattern returns only matching rows`
9. `Substrate GUC behavior > [AC-8] nullable RLS pattern returns matching + null-tenant rows`
10. `Database migrations > [EC-9] migration against existing data is additive`

Concentrated in **RLS/GUC policy cluster** (FR-2 ×3, AC-5/7/8/10/12 ×5, EC-4 ×1) + EC-9 migration ×1.

Scenario_counts: `SCENARIO_REGISTERED=30 PASSED=20 FAILED=10 CRASHED=0 SKIPPED=0`.

## Coverage amend flow

- **COVERAGE_GAP (attempt 1):** 12 uncovered — `FR-2, FR-4, EC-1, EC-2, EC-3, EC-4, EC-5, EC-6, EC-7, EC-8, EC-9, EC-10`
- **COVERAGE_REGEN (attempt 1):** TE rerun; no architect amend.
- **COVERAGE_PASS:** `anchor_count=28` (identical to Run 3 and V2-R1)

TE ran three phases: `validate`, `coverage_regen`, `repair` (three successive TE invocations visible in agent log). Total TE wall: ~880s across the three phases — more than Run 3 (~250s across validate+regen).

## Architect phase

- Anchor load successful (cache_hit=true, 167s wall)
- No G1 manifest validation retries
- No COVERAGE_ARCHITECT_AMEND

## Quality gate summary (iter 2, final)

```
Install: PASS | Setup: PASS | Build: PASS | Lint: PASS | Type Check: PASS
Self-Tests: PASS | Blind Scenarios: FAIL | Mutation Testing: SKIP

Tests: 10 failed | 20 passed | 0 skipped (30 total)
Failures: FR-2×3, AC-5/7/8/10/12×5, EC-4×1, EC-9×1 (all IMPL_BUG)
```

Oracle metric delta = 0 both iterations (claimed pass rate matched gate exactly).

## Notable events

- **No `BARRIER_VIOLATION` events.**
- **No `MANIFEST_VALIDATION_RETRY` events** (architect loaded from anchor cleanly).
- **No `COVERAGE_ARCHITECT_AMEND`** (entry points correct from anchor).
- **Pre-gate retry** fired on iter 2 (implementer attempt 2 ran). R3 budget consumed 1 of 3 on iter 2. R3 was NOT consumed on iter 1 (unlike V1 Run 1/2).
- **`SPEC_ESCALATED`** iterations=2 (iter 3 implementer never ran; escalation agent fired at iter 3 after PLATEAU classification at iter 2).

## V2 Run 1 vs V2 Run 2 comparison (anchor success vs failure)

| Dimension | V2-R1 (anchor failed) | V2-R2 (anchor held) |
|-----------|-----------------------|---------------------|
| I1 cache_hit | false | **true** |
| I1 hash | `c6bbb0496b5e…` (5th distinct) | `713f8c1ae01e…` (Run 3's) |
| Outcome | ESCALATED @ 77% PLATEAU | ESCALATED @ 66% PLATEAU |
| Iter 1 pass rate | 0% (vitest no-tests) | 66% (30 scenarios registered) |
| Iter 2 pass rate | 77% | 66% |
| Peak pass rate | 77% | 66% |
| Iter count | 3 (impl ×3) | 2 (impl ×2) |
| Cost | $168 | $84 |
| Wall time | 55.7 min | 55.5 min |
| Scenarios | 31 | 30 |
| Failure cluster | edge-case cluster (EC-2/5/6/8/9) | **RLS/GUC cluster (FR-2, AC-5/7/8/10/12, EC-4)** |
| Classifier drift | n/a (single iter classified) | deterministic (identical hashes iter 1=iter 2) |

## Run 3 (unlocked, I1=`713f…`) vs V2-R2 (locked, I1=`713f…`)

Both have identical I1 hash. Compare downstream variance:

| Dimension | Run 3 (free, I1=`713f…`) | V2-R2 (anchored, I1=`713f…`) |
|-----------|--------------------------|-------------------------------|
| Scenarios | 19 | **30** |
| Iter 1 pass rate | 68% | 66% |
| Iter 2 pass rate | 68% | 66% |
| Failure count | 6 | 10 |
| Failure cluster | RLS policy (AC-7, AC-8, AC-12, FR-3) + AC-1, EC-6 | FR-2 ×3, AC-5/7/8/10/12 ×5, EC-4, EC-9 |
| Classifier drift | yes (iter 1→iter 2 reclassified) | no (deterministic) |
| Cost | $84 | $84 |
| Wall | 47 min | 55.5 min |
| Coverage path | validate + regen | validate + regen + repair |
| Pre-gate retry | none | iter 2 (1 of 3) |

**Observation: same architect output, materially different TE/impl/classifier downstream.** 30 vs 19 scenarios = +58% TE output volume despite identical spec + manifest. 10 vs 6 failures (both RLS-heavy but different specific anchors). This is the V2→V3 signal — anchoring architect alone leaves significant variance in TE (I2), implementer, and classifier.

## Variance envelope across V1 n=3 + V2 n=2

| Run | I1 hash | Anchor | Outcome | Peak % | Iters | Cost | Wall | Scenarios | Cluster |
|-----|---------|--------|---------|--------|-------|------|------|-----------|---------|
| V1-R1 | `58fb…` | none | ESCALATED | 80% | 3 | $210 | 74m | 10 | env-container (drizzle-kit) |
| V1-R2 | `2986…` | none | SHIPPED | 92% | 2 | $126 | 64m | 41 | mixed impl/brittle |
| V1-R3 | `713f…` | none | ESCALATED (PLATEAU) | 68% | 2 | $84 | 47m | 19 | RLS policy (6) |
| V2-R1 | `c6bb…` | anchor invalidated | ESCALATED (PLATEAU) | 77% | 3 | $168 | 56m | 31 | edge-case cluster |
| V2-R2 | `713f…` | **held** | ESCALATED (PLATEAU) | 66% | 2 | $84 | 55m | 30 | RLS/GUC cluster (FR-2, AC-5/7/8/10/12) |

Two runs now anchored on the same I1 (`713f…`) — Run 3 free, V2-R2 anchored. They produced different scenario counts (19 vs 30) and different failure sets, but both PLATEAU'd and both concentrated on RLS-adjacent failures. The architecture selects a failure *region*; TE/implementer variance selects specific failures inside that region.

## Follow-ups

1. **TE output-volume variance is now clearly an independent V-source.** 19 vs 30 scenarios on identical I1 confirms Phase 5.7b (TE output-volume floor module) is needed — this is the second pair of runs showing TE volume drift with architect fixed. Priority up.
2. **Classifier drift is non-universal.** Run 3 had it; V2-R2 did not. Worth instrumenting classifier variance across a larger sample (iter-identical input runs) rather than treating it as a fixed V-source.
3. **Anchor-manifest mechanism works but requires the substrate-manifests workaround.** Promote follow-up from V2-R1 (pipeline-level fix options a/b/c) to planned Phase 7 work — for now the manual workaround is validated and sufficient for V2-R3.
4. **RLS/GUC failure region is the stable "hard part" of this spec under I1=`713f…`.** Both Run 3 and V2-R2 concentrated here. Combined with V1-R1 (drizzle-kit env) and V1-R2 (mixed brittle/impl), the spec has ≥3 distinct failure regions depending on architect choice.

## Next

- V2 Run 3 of 3 (this batch) — same anchor + same workaround. Expect another `713f…` locked run. Compare across V2 n=2 already collected to isolate TE/impl-only variance.
- Archive V2-R2 artifacts under `archived-runs/75277dc433604b10/` (done as part of next-run prep).
