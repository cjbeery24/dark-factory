# Pipeline Run Report: 2026-03-28-database-foundation

**Target:** budget-brain-v2
**Date:** 2026-04-17
**Run ID:** 4140e3bfcc5a490a
**Outcome:** ESCALATED (0% across all 3 iterations — setup failure)
**Wall time:** ~69 minutes (4114s)
**Iterations:** 3 (0% → 0% → 0%)

## Framework Fixes Active This Run

All fixes from prior sessions were active and verified:

1. **Contracts RO mount on all 3 TE invocations** — Confirmed via audit log. The TE generate, validate, and coverage_regen phases all had `-v ..._contracts:.../_contracts:ro`. This was the critical fix from the previous session (coverage regen was missing the mount).

2. **`_clear_tier2_scenarios` preserves `_contracts/`** — The `_contracts/` directory survived the TE validation retry cleanup (10 test files intact at generation end).

3. **`scenario_max_assertions_per_file = 40`** — Configured in target project config.toml. No assertion-count validator rejections this run.

4. **Manifest dict-form services normalization** — Architect's dict-form `runtime.services` parsed without crash.

5. **ANSI CSI stripping** — No SCENARIO_CRASHED events.

## State File

```
spec=2026-03-28-database-foundation
run_id=4140e3bfcc5a490a
spec_hash=1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662
completed_step=2
last_decision=NO-SHIP
iteration=3
server_error=
smoke_result=passed
phase=review
pass_rate=0
gate_exit_code=2
status=escalated
```

## Per-Iteration Summary

### Iteration 1: 0% (setup failure)

| Gate | Status |
|------|--------|
| Install | PASS |
| Setup | FAIL |

**Implementer ran 2 attempts** (pre-gate retry). Attempt 1: 577s, 8 files. Attempt 2: 456s, 8 files. Total: 400 turns.

**Root cause:** `drizzle-kit migrate` hangs at "applying migrations..." because the implementer created `apps/api/src/db/migrations/0000_baseline_rls_infrastructure.sql` (raw SQL) without drizzle-kit metadata (`meta/_journal.json` + snapshot). Without the journal, drizzle-kit can't determine migration state.

**Smoke test passed** — server boots and responds to health check. The setup failure is specific to the quality gate's `setup` command (`cd apps/api && npx drizzle-kit migrate`).

### Iteration 2: 0% (setup failure, PLATEAU)

Same root cause. Implementer ran 1 attempt (218s, 200 turns). No `meta/` directory created. Trajectory: PLATEAU.

### Iteration 3: 0% (setup failure, PLATEAU → ESCALATED)

Same root cause, but **worse**: the implementer deleted the migration SQL file (empty `migrations/` directory at run end) and still didn't create `meta/_journal.json`. 522s, 200 turns. Trajectory: PLATEAU → ESCALATED.

## Quality Gate Report (Final)

```
# Quality Gate Summary: 2026-03-28-database-foundation
Date: 2026-04-17T04:44:50Z

| Gate | Status |
|------|--------|
| Install | PASS |
| Setup | FAIL |

### Setup Failure Details
No config path provided, using default 'drizzle.config.ts'
Reading config file '/project/apps/api/drizzle.config.ts'
Using 'postgres' driver for database querying
[⣷] applying migrations...

## Result: FAIL (setup)
```

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

Same known gap as run #5 — psycopg not in the gate Python environment.

## Scenario Generation Quality (Unmeasured)

The TE produced 10 test files + `_contracts/` intact. Scenario quality was significantly improved compared to run #5 but **could not be measured** because the implementer never passed setup:

### Contract Helper Usage
- **2/10 test files** import from `_contracts/` (up from 1/13 in run #5):
  - `substrate-guc.test.ts` — imports `assertUnsetGucIsNullish`
  - `rls-policies.test.ts` — imports `assertUnsetGucIsNullish`, `assertTenantSeesOwnRows`, `assertTenantCannotSeeOtherRows`

### NOBYPASSRLS Compliance
- **2/2 RLS test files** correctly create `NOBYPASSRLS` roles for RLS testing (0/2 in run #5):
  - `rls-policies.test.ts` — `CREATE ROLE IF NOT EXISTS rls_test_user NOBYPASSRLS LOGIN PASSWORD 'testpass'`
  - `rls-infrastructure.test.ts` — `CREATE ROLE test_rls_user LOGIN PASSWORD 'test_pass' NOBYPASSRLS`

### Remaining Scenario Defects (From Pre-Gate Analysis)
Even though scenarios never ran, static analysis revealed:

1. **Cross-connection bug** (3 files): `tenant-context.test.ts`, `edge-cases.test.ts`, `rls-infrastructure.test.ts` query `testConnection` (separate pool) inside `withTenantDb` callbacks, expecting to see `SET LOCAL` from a different connection's transaction. Structurally broken.

2. **GUC wire-value ambiguity** (2 files): `edge-cases.test.ts` lines 48, 74 use `.toBe('')` for unset GUCs instead of the contract helper's nullish-tolerant assertion. postgres.js returns `null`, not `''`.

3. **`DrizzleTx` as runtime value** (1 file): `drizzle-orm-configuration.test.ts` line 46 expects `DrizzleTx` to be a defined runtime value, but it's a TypeScript type alias (undefined at runtime).

### Predicted Pass Rate (If Setup Had Passed)
Based on static analysis: **60-70%** (6-7/10 files passing), up from 50% in run #5.

## Contracts RO Mount Verification

All 3 TE invocations confirmed with RO mount in audit log:
```
-v .../scenarios/2026-03-28-database-foundation/_contracts:
   /project/.dark-factory/scenarios/2026-03-28-database-foundation/_contracts:ro
```

- TE generate (03:47:37): RO mount present
- TE validate (03:52:08): RO mount present
- Coverage regen (03:58:43): RO mount present

**`_contracts/` intact at run end:** All 7 original files preserved.

## Event Timeline

| Time | Event | Details |
|------|-------|---------|
| 03:37:31 | SPEC_START | |
| 03:37:31 | AGENT_START | spec-validator |
| 03:39:37 | AGENT_COMPLETED | spec-validator (126s) |
| 03:39:37 | AGENT_START | architect |
| 03:42:47 | AGENT_COMPLETED | architect (190s) |
| 03:42:47 | AGENT_START | qa-holdout |
| 03:47:37 | AGENT_COMPLETED | qa-holdout (290s) |
| 03:47:37 | CONTRACTS_STAGED | postgres adapter |
| 03:47:37 | AGENT_START | test-engineer (generate) |
| 03:52:07 | AGENT_COMPLETED | test-engineer generate (269s) |
| 03:52:08 | AGENT_START | test-engineer (validate) |
| 03:58:42 | AGENT_COMPLETED | test-engineer validate (395s) |
| 03:58:43 | COVERAGE_GAP | FR-1, FR-2 uncovered |
| 03:58:43 | AGENT_START | test-engineer (coverage_regen) |
| 04:01:56 | AGENT_COMPLETED | test-engineer regen (193s) |
| 04:01:56 | COVERAGE_PASS | 28 anchors |
| 04:01:57 | AGENT_START | implementer iter 1 attempt 1 |
| 04:11:33 | AGENT_COMPLETED | implementer iter 1 attempt 1 (577s, 8 files) |
| 04:11:34 | AGENT_START | implementer iter 1 attempt 2 |
| 04:19:10 | AGENT_COMPLETED | implementer iter 1 attempt 2 (456s, 8 files) |
| 04:19:13 | SMOKE_COMPLETED | iter 1 |
| 04:19:17 | GATE_COMPLETED | iter 1: 0% (setup fail) |
| 04:19:18 | AGENT_START | reviewer iter 1 |
| 04:22:35 | AGENT_COMPLETED | reviewer iter 1 (197s) |
| 04:22:35 | AGENT_START | oracle iter 1 attempt 1 |
| 04:25:03 | AGENT_COMPLETED | oracle iter 1 attempt 1 (148s) |
| 04:25:03 | AGENT_START | oracle iter 1 attempt 2 |
| 04:26:12 | AGENT_COMPLETED | oracle iter 1 attempt 2 (69s) |
| 04:26:13 | AGENT_START | implementer iter 2 |
| 04:29:50 | AGENT_COMPLETED | implementer iter 2 (218s, 8 files) |
| 04:29:55 | SMOKE_COMPLETED | iter 2 |
| 04:29:59 | GATE_COMPLETED | iter 2: 0% (setup fail), PLATEAU |
| 04:29:59 | AGENT_START | reviewer iter 2 |
| 04:34:13 | AGENT_COMPLETED | reviewer iter 2 (254s) |
| 04:34:13 | AGENT_START | oracle iter 2 |
| 04:35:59 | AGENT_COMPLETED | oracle iter 2 (106s) |
| 04:36:00 | AGENT_START | implementer iter 3 |
| 04:44:41 | AGENT_COMPLETED | implementer iter 3 (522s, 8 files) |
| 04:44:48 | SMOKE_COMPLETED | iter 3 |
| 04:44:53 | GATE_COMPLETED | iter 3: 0% (setup fail), PLATEAU |
| 04:44:53 | AGENT_START | escalation |
| 04:46:05 | AGENT_COMPLETED | escalation (72s) |
| 04:46:05 | SPEC_ESCALATED | |

## Estimated Cost

| Phase | Turns | Est. USD |
|-------|-------|----------|
| Pre-impl (SV+Arch+QA+TE) | ~1000 | ~$210 |
| Iter 1 (impl×2 + reviewer + oracle×2) | 600 | $126 |
| Iter 2 (impl + reviewer + oracle) | 400 | $84 |
| Iter 3 (impl + escalation) | 400 | $84 |
| **Total** | **~2400** | **~$504** |

## Key Observations

### 1. Implementer Cannot Produce drizzle-kit Metadata (Blocking)

The implementer has now failed this in **6 consecutive iterations** across 2 runs (run #5 iters 1-2, run #6 iters 1-3). The failure pattern:
- Creates raw SQL migration files (correct content)
- Never generates `meta/_journal.json` or `meta/0000_snapshot.json`
- `drizzle-kit migrate` requires this metadata to track migration state
- Gate setup hangs indefinitely at "applying migrations..."

The oracle feedback says "setup failed: drizzle-kit migrate hangs at 'applying migrations'" but doesn't pinpoint the root cause (missing `meta/_journal.json`). The implementer doesn't understand that `drizzle-kit generate` (not manual SQL file creation) is how migrations are produced.

**Recommendation:** This is a spec-level or oracle-feedback-level problem. Options:
- A. Add explicit "drizzle-kit generate must be used to produce migrations (creates both SQL + meta/_journal.json)" to the spec
- B. Improve the gate setup failure detection to identify missing drizzle-kit metadata specifically
- C. Change the setup command to use raw SQL (`psql -f migrations/*.sql`) instead of drizzle-kit's migration system

### 2. TE Scenario Quality Improvements Are Working (Unmeasured)

The TE fixes (contracts RO mount, NOBYPASSRLS addendum, contract helper usage) produced measurably better scenarios:
- Contract helper imports: 2/10 files (up from 1/13)
- NOBYPASSRLS usage: 2/2 RLS files (up from 0/2)
- `rls-policies.test.ts` is now structurally correct (used all 3 contract helpers + non-privileged connection)

**However:** These improvements couldn't be validated against actual test execution because the implementer never passed setup.

### 3. Cross-Connection Bug Is a New Defect Class

3/10 test files query a separate connection pool inside `withTenantDb` callbacks, expecting to see `SET LOCAL` changes from the inner transaction. This is a Postgres session-state misunderstanding, not a superuser bypass or wire-value issue. The contract helpers don't address this pattern.

### 4. Run #5 vs Run #6 Comparison

| Metric | Run #5 | Run #6 |
|--------|--------|--------|
| Total iterations | 3 | 3 |
| Setup failures | 2/3 | 3/3 |
| Max pass rate | 50% (iter 3) | 0% (never got past setup) |
| TE contract imports | 1/13 | 2/10 |
| NOBYPASSRLS usage | 0/2 | 2/2 |
| Outcome | INTERRUPTED | ESCALATED |

Run #5 iteration 3 managed to create meta/ — run #6 never did. The TE quality improved but the implementer regressed.

### 5. ~$504 Spent With Zero Scenario Execution

The entire cost was consumed by the implementer repeatedly failing to produce valid drizzle-kit migrations, plus the review/oracle loop. No blind scenarios were ever executed against the implementation.
