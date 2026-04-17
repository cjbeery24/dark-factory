# Pipeline Run Report: 2026-03-28-database-foundation

**Target:** budget-brain-v2
**Date:** 2026-04-17
**Run ID:** 6d1fa441545a45d5
**Outcome:** INTERRUPTED (killed after iteration 3 gate results)
**Wall time:** ~58 minutes (3483s)
**Iterations:** 3 (0% → 0% → 50%)

## Framework Fixes Applied This Run

Three framework bugs were fixed before/during this run:

1. **Manifest parser: dict-form `runtime.services` normalization** — Architects write `services: { postgres: { image: ... } }` (dict keyed by name) but the parser expected a list with explicit `name` fields. Added normalization of dict-form → list-form, `environment` → `env` alias, and list-form `health_cmd` → joined string. (Prior attempt crashed at manifest parse.)

2. **`_clear_tier2_scenarios` preserves `_contracts/`** — The TE retry cleanup (`_clear_tier2_scenarios`) deleted the `_contracts/` directory because it doesn't start with the tier1 prefix. This caused the coverage regen TE to lose all staged contract helpers. Fixed by skipping `_contracts` in the cleanup loop.

3. **ANSI CSI stripping** (from prior commit `665e5c2`) — vitest cursor-control sequences (`\x1b[2K`, `\x1b[1A`) survived SGR-only stripping, blocking crash detection regex. Changed to `\x1b\[[0-9;]*[a-zA-Z]` in both `quality-gate.sh` and `quality_gate.py`.

## State File

```
spec=2026-03-28-database-foundation
run_id=6d1fa441545a45d5
spec_hash=1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662
last_decision=NO-SHIP
server_error=
smoke_result=passed
phase=review
pass_rate=55
gate_exit_code=1
status=interrupted
iteration=3
completed_step=2
```

## Per-Iteration Summary

### Iteration 1: 0% (0/0 scenarios — setup failure)

| Gate | Status |
|------|--------|
| Install | PASS |
| Setup | FAIL |

**Root cause:** `drizzle-kit migrate` hung trying to connect to Postgres. The implementer produced a raw SQL migration file (`0000_baseline_rls_infrastructure.sql`) without drizzle-kit metadata (`meta/_journal.json`). Without the journal, `drizzle-kit migrate` attempts to connect and apply but has no migration state to track.

**Note:** The Postgres service container was running and healthy. `DATABASE_URL` was injected correctly. This was an implementation gap, not infrastructure.

### Iteration 2: 0% (0/0 scenarios — setup failure)

Same root cause. Trajectory classified as `PLATEAU`. The implementer did not fix the drizzle-kit metadata issue.

### Iteration 3: 50% (6/12 passed, 6 failed)

| Gate | Status |
|------|--------|
| Install | PASS |
| Setup | PASS |
| Build | PASS |
| Lint | PASS |
| Type Check | PASS |
| Self-Tests | PASS |
| Blind Scenarios | FAIL (6/12, 50%) |

**Setup fixed:** The implementer finally produced proper drizzle-kit migration metadata so `drizzle-kit migrate` succeeded.

**SCENARIO_CRASHED=0** — ANSI stripping fix confirmed working.

### Failing Scenarios (Iteration 3)

From the gate report, the visible failures are in `rls-policies.test.ts`:

1. **`[AC-12] unset app.tenant_id GUC returns zero rows via fail-closed policy`** — Expected 0 rows, got 2. This is the **RLS superuser bypass**: the test uses the connection pool owner (superuser or table owner) which implicitly has `BYPASSRLS`. The test needs a `NOBYPASSRLS` non-owner role.

2. **`NULLIF converts empty string to NULL for fail-closed behavior`** — Expected `raw_setting` to be `''` (empty string), got `null`. This is a wire-level misunderstanding: `current_setting('app.tenant_id', true)` returns `null` at the SQL level for an unset GUC, but the test expected empty string.

3. **Other 4 failures**: `42P01` (relation does not exist) errors — tests reference tables (`test_nullable`, etc.) that the implementer didn't create. These are either missing DDL in the setup or the tests assume tables that don't exist at this spec scope.

## Substrate Verification

```json
{
    "status": "fail",
    "findings": [
        {
            "severity": "error",
            "code": "substrate-psycopg-missing",
            "message": "psycopg is required to run verify_substrate but is not installed in the gate environment"
        }
    ]
}
```

The substrate verifier runs in a Python container but needs `psycopg` to connect to Postgres. This is a known gap — the verifier image doesn't include psycopg.

## Contracts RO Mount Verification

**RO mount present in TE container:** Confirmed via audit log:
```
-v ~/sites/budget-brain-v2/.dark-factory/scenarios/2026-03-28-database-foundation/_contracts:/project/.dark-factory/scenarios/2026-03-28-database-foundation/_contracts:ro
```

**Contract files intact after TE generation:** All 7 original `.ts` files preserved (guc.ts, isolation.ts, rls.ts, index.ts, README.md, package.json, tsconfig.json).

**Coverage regen TE missing RO mount:** The second TE launch (coverage regen) did NOT have the `_contracts` RO mount because `_clear_tier2_scenarios` had already deleted the `_contracts/` directory. This is the bug fixed in this session.

## Scenario Generation Quality

The TE produced 8 test files on initial generation, then 5 more during coverage regen (13 total). Only `substrate-postgres.test.ts` imported from `_contracts/`. The scenarios report claims all 14 ACs and 4 FRs and 10 ECs are covered, but only 12 tests ran (some files may have been excluded or consolidated).

**Contract helper usage:** 1/13 test files imports from `_contracts/`. The substrate-focused test uses the helpers correctly, but other files that test RLS behavior do not — they hand-roll assertions that hit the superuser bypass bug.

## Event Timeline

| Time | Event | Details |
|------|-------|---------|
| 0s | SPEC_START | |
| 0s | AGENT_START | spec-validator |
| 102s | AGENT_COMPLETED | spec-validator (112s wall) |
| 102s | AGENT_START | architect |
| 305s | AGENT_COMPLETED | architect (202s wall) |
| 305s | AGENT_START | qa-holdout |
| 739s | AGENT_COMPLETED | qa-holdout (433s wall) |
| 739s | CONTRACTS_STAGED | postgres, 7 files |
| 739s | AGENT_START | test-engineer (generate) |
| 1017s | AGENT_COMPLETED | test-engineer generate (278s wall) |
| 1018s | COVERAGE_GAP | FR-1..4, EC-1..10 uncovered |
| 1018s | COVERAGE_REGEN | |
| 1018s | AGENT_START | test-engineer (coverage_regen) |
| 1443s | AGENT_COMPLETED | test-engineer regen (425s wall) |
| 1443s | COVERAGE_PASS | 28 anchors |
| 1444s | AGENT_START | implementer iter 1 |
| 1880s | AGENT_COMPLETED | implementer iter 1 (436s wall) |
| 1881s | GATE_COMPLETED | iter 1: 0% (setup fail) |
| 2089s | AGENT_COMPLETED | reviewer iter 1 |
| 2202s | AGENT_COMPLETED | oracle iter 1 → NO-SHIP |
| 2202s | AGENT_START | implementer iter 2 |
| 2637s | AGENT_COMPLETED | implementer iter 2 (258s wall) |
| 2645s | GATE_COMPLETED | iter 2: 0% (setup fail) |
| 2645s | TRAJECTORY: PLATEAU | |
| 2908s | AGENT_COMPLETED | reviewer iter 2 |
| 3061s | AGENT_COMPLETED | oracle iter 2 → NO-SHIP |
| 3061s | AGENT_START | implementer iter 3 |
| 3117s | AGENT_COMPLETED | implementer iter 3 (354s wall) |
| 3483s | GATE_COMPLETED | iter 3: 50% (6/12) |
| 3483s | TRAJECTORY: MIXED | |
| — | INTERRUPTED | killed after gate results |

## Estimated Cost

| Iteration | Turns | Est. USD |
|-----------|-------|----------|
| Pre-impl (SV+Arch+QA+TE) | ~800 | ~168 |
| Iter 1 | 400 | $84 |
| Iter 2 | 200 | $42 |
| Iter 3 | 200 | $42 |
| **Total** | **~1600** | **~$336** |

## Key Observations

1. **Two wasted iterations on drizzle-kit metadata** — The implementer burned iterations 1-2 because `drizzle-kit migrate` needs a `meta/_journal.json` that the implementer didn't generate. The architect's `commands.setup = "cd apps/api && npx drizzle-kit migrate"` is correct, but the implementer needs to generate migrations with `drizzle-kit generate` first. The Oracle feedback wasn't specific enough to convey this.

2. **RLS superuser bypass still present** — `rls-policies.test.ts` uses the pool connection (superuser/owner) for RLS assertions. The addendum and Hard Rule 13 weren't sufficient; the TE wrote an RLS test file that doesn't use the `_contracts/` helpers and falls into the superuser trap.

3. **Contract helpers underutilized** — Only 1/13 test files imports from `_contracts/`. The TE wrote 7 other test files that could benefit from contract helpers but didn't use them.

4. **Coverage regen lost contracts** — The `_clear_tier2_scenarios` bug deleted `_contracts/` before the coverage regen TE launched, stripping contract helpers from the regen pass. Fixed in this session.

5. **`42P01` relation errors** — Multiple tests reference tables that don't exist. The tests assume DDL that the implementer didn't produce. This is a spec/test alignment issue — the scenarios assume more concrete tables than the spec's "baseline pattern" scope provides.
