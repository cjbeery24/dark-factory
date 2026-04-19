# Pipeline Run Report: database-foundation — Phase 6 V2 Run 1/3 (anchor-manifest)

**Target:** budget-brain-v2
**Spec:** 2026-03-28-database-foundation
**Run ID:** `d75dcaeecc4949ec`
**Date:** 2026-04-19 03:37 UTC → 04:33 UTC (≈56 min)
**Outcome:** ESCALATED @ 77% PLATEAU (iter 3 → iter 4 escalation)
**Purpose:** Phase 6 V2 anchor-manifest baseline, Run 1 of 3. Anchor: Run 3's manifest (I1=`713f8c1ae01e…`).
**Anchor flags:** `--anchor-manifest .dark-factory/archived-runs/d793d508e9504006/architecture/2026-03-28-database-foundation-manifest.yaml`

## ⚠️ Anchor-manifest flag DID NOT lock I1

The primary hypothesis this run was designed to test — "does anchoring the architect manifest collapse downstream variance?" — **could not be measured** because the anchor did not hold:

```
ARCHITECT_CACHE_INVALIDATED
  reason: substrate_manifest_missing
  prior_adapters: [postgres, typescript-node]
  current_adapters: [postgres, typescript-node]
```

The pipeline loaded the anchor, then invalidated the cache because no substrate manifest was found in the clean working directory. The architect ran fresh and produced a **new manifest hash** `c6bbb0496b5e8c0e…` — this is the **5th distinct I1 hash** across database-foundation attempts (V1×3 + V1-Run3-aborted + this), not the anchored `713f8c1ae01e…`.

**What this means:**
- V2 Run 1 is effectively a V1-style free run with a minor contamination (the pipeline briefly loaded the anchor before discarding it).
- The `--anchor-manifest` flag does not anchor on its own — it requires the substrate manifest to already exist at the target path for the cache to hit.
- Either substrate manifests need to be seeded alongside the main manifest, or the invalidation check should be relaxed when the adapter set matches (as it did here — prior and current adapter lists were identical).

**Follow-up needed before continuing V2 batch** — see §follow-ups. Running V2 Runs 2/3 with the same anchor will reproduce the same invalidation. Either fix the anchoring mechanism or change approach (copy substrate-manifests/ alongside the anchor manifest as a manual workaround).

## Iteration summary

| Iter | Pass rate | Satisfaction | Scenarios | Crashed | Trajectory | Cost (est USD) |
|------|-----------|--------------|-----------|---------|------------|----------------|
| 1    | 0%        | 0%           | 0         | 0       | —          | $84            |
| 2    | 77%       | 77%          | 31        | 0       | IMPROVING  | $42            |
| 3    | 77%       | 77%          | 31        | 0       | PLATEAU    | $42            |

Escalation agent fired at iter 4 after PLATEAU at iter 3; no iter-4 implementer ran. **Total cost:** ~$168. **Wall time:** 3341s (55.7 min). **Total turns:** 800.

## State file

```
spec=2026-03-28-database-foundation
run_id=d75dcaeecc4949ec
spec_hash=1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662
iteration=3
last_decision=NO-SHIP
smoke_result=passed
pass_rate=77
gate_exit_code=1
phase=review
status=escalated
```

## V-dimension instrumentation

- **I1 architect_manifest_hash:** `c6bbb0496b5e8c0ec08c808a0151808bb8f685a000e5311c6331ef124ed90ee7` — **cache_hit=False**, anchor invalidated.
- **I2 scenario_fingerprint count:** 17 (6 initial + 11 regen after architect amend).
- **I3 classifier_io_hash:**
  - Iter 1: N/A (classifier skipped — 0 scenarios registered)
  - Iter 2: input `0925795a27570699…`, output `4ddcb67d8fd08d10…`, defects=7, failing=7
  - Iter 3: N/A (classifier skipped — failing test set unchanged from iter 2)
- **I4 iteration_temporal_signature:**
  - Iter 0→1: no-op (empty failure set — vitest found 0 tests)
  - Iter 1→2: `tests_moved_to_fail=7`, delta `{IMPL_BUG: +7}`
  - Iter 2→3: `tests_unchanged=7` (identical failing set)

## Iter 1 — vitest no-tests-found (recurring)

Same failure shape as **V1 Run 2 iter 1**: Install/Setup/Build/Lint/Type Check/Self-Tests all PASS, blind scenarios registered 0 tests (exit_code=2 → vitest "no tests found"). Pre-gate R3 retry fired once (implementer attempt 2 ran), produced 9 files, gate still saw 0 scenarios.

Iter 2 implementer's changes made vitest discover all 31 scenarios — consistent with the Run 2 pattern (a tsconfig/vitest-config drift between iter 1 and iter 2).

## Stable failures (iter 2 = iter 3)

Seven tests failed across both iterations, none passing:

1. `Edge cases > [EC-5] transaction rollback behavior maintains isolation`
2. `Migration behavior with existing data > [EC-9] database maintains consistency when migrations run against existing data`
3. `Migration behavior with existing data > [EC-9] migration system handles additive schema changes`
4. `Database health check timeout > [EC-8] database query timeout is handled gracefully`
5. `Database health check timeout > [EC-8] health check timeout behavior with AbortSignal`
6. `Connection pool behavior > [EC-6] handles connection pool exhaustion gracefully`
7. `Environment validation > [EC-2] database connection fails when DATABASE_URL points to unreachable host`

All classified `IMPL_BUG` by iter-2 classifier. Heavy concentration on edge-case behavior (EC-2, EC-5, EC-6, EC-8, EC-9) — adversarial timing/pool/migration scenarios.

Scenario_counts: `SCENARIO_REGISTERED=31 PASSED=24 FAILED=7`.

## Coverage amend flow

- **COVERAGE_GAP (attempt 1):** 6 uncovered — `AC-6, EC-1, EC-2, EC-6, EC-8, EC-9`
- **COVERAGE_ARCHITECT_AMEND fired** for `AC-6` (added entry point `apps/api/src/db/tenant-context/testing.js` — note: path doesn't exist, hence `outcome=no_progress`)
- **COVERAGE_REGEN:** TE rerun covered AC-6 via alternative patterns despite the no-progress amend
- **COVERAGE_PASS:** `anchor_count=28` (28 covered of spec's total anchor set)

The amend reported `no_progress` but TE regen handled coverage anyway. Different shape from V1 Run 1 (where amend succeeded), V1 Runs 2/3 (which didn't need amend).

## Architect manifest behavior

- Anchor load attempted → invalidated (substrate_manifest_missing) → full architect run from scratch
- No G1 retries during architect re-run (clean first attempt, 220s)
- Coverage amend re-invoked architect mid-flow (83s, outcome=no_progress)
- **Total architect wall time:** 220 + 83 = 303s across 2 invocations

## Quality gate summary (iter 3, final)

```
Install: PASS | Setup: PASS | Build: PASS | Lint: PASS | Type Check: PASS
Self-Tests: PASS | Blind Scenarios: FAIL | Mutation Testing: SKIP

Tests: 7 failed | 24 passed | 0 skipped (31 total)
Failures: EC-2, EC-5, EC-6, EC-8×2, EC-9×2 (all IMPL_BUG from iter-2 classifier)
```

## Observations — V1 vs. (attempted) V2

Because the anchor didn't hold, this is arguably a **V1 Run 4** rather than a V2 run. Against V1 n=3:

| Dimension | V1-R1 | V1-R2 | V1-R3 | "V2-R1" |
|-----------|-------|-------|-------|---------|
| Outcome | ESCALATED | SHIPPED | ESCALATED (PLATEAU) | ESCALATED (PLATEAU) |
| Peak pass rate | 80% | 92% | 68% | 77% |
| Iter count | 3 | 2 | 2 | 3 |
| Cost | $210 | $126 | $84 | $168 |
| Wall time | 74 min | 64 min | 47 min | 56 min |
| I1 hash | `58fb…` | `2986…` | `713f…` | `c6bb…` (5th distinct) |
| Scenarios | 10 | 41 | 19 | 31 |
| Iter-1 failure shape | partial @ 44% | vitest 0-tests | partial @ 68% | vitest 0-tests |
| Stable failure cluster | env-container (drizzle-kit) | impl/brittle mix | RLS policy (6) | edge-case cluster (EC-2/5/6/8/9) |

**Extending the V1 variance envelope:**
- Architect non-determinism: now **5 distinct manifests** for same spec (V1-R1 `58fb…`, V1-R2 `2986…`, V1-R3-aborted `7a7c…`, V1-R3 `713f…`, "V2-R1" `c6bb…`)
- Failure concentrations: another new shape (edge-case cluster — neither RLS-policy nor drizzle-kit-env nor mixed)
- Iter-1 vitest-0-tests recurrence: 2 of 4 valid runs (V1-R2, "V2-R1") hit this; both recovered in iter 2

## Follow-ups

1. **PRIORITY: Fix or document anchor-manifest mechanism.** The substrate_manifest_missing invalidation makes the current anchor flag insufficient on its own. Options:
   - (a) Fix: when `--anchor-manifest` is passed, automatically seed substrate-manifests from the anchor's sibling directory.
   - (b) Workaround: always copy `archived-runs/<rid>/architecture/substrate-manifests/` alongside when using the anchor flag.
   - (c) Change invalidation rule: skip substrate_manifest_missing check when adapter set is unchanged (as was the case here).
2. **Before V2 Run 2, apply workaround (b)** — copy `archived-runs/d793d508e9504006/architecture/substrate-manifests/*` into `.dark-factory/architecture/substrate-manifests/` after the runtime wipe and before launch. This should let the anchor hold.
3. **Vitest 0-tests-found recurrence** — worth upgrading from "follow-up" to a pre-gate check. Both V1-R2 and V2-R1 iter 1 wasted $42+$42 on a discovery bug that iter 2 fixed immediately. A "scenarios registered ≥ minimum" gate could fail iter 1 faster or force a different retry shape.
4. **Edge-case cluster (EC-2/5/6/8/9)** — this run's failure mode is distinct from V1 runs. Edge cases involving timing/rollback/pool behavior. May indicate implementer under-covers edge tests when iter 1 has a discovery failure (lost signal on which ECs are flaky).

## Next

- Apply anchor workaround (b) above.
- Launch V2 Run 2 with anchor-manifest + pre-copied substrate-manifests.
- Verify I1 hash locks to `713f8c1ae01e…` in V2 Run 2 (cache_hit=True expected).
