# Pipeline Run Report — 2026-03-28-database-foundation (hand-written scenarios, diagnostic run)

**Target project:** `budget-brain-v2`
**Spec:** `2026-03-28-database-foundation`
**Run ID:** `2edc139ab7fa4732` (resumed from prior escalated run)
**Spec hash:** `1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662`
**Launched:** 2026-04-16 21:13:48 UTC
**Interrupted:** 2026-04-16 ~21:42 UTC (operator kill after iter 2)
**Wall time:** ~28 min
**Preceded by:** `2026-04-16-11-38-budget-brain-v2-2026-03-28-database-foundation.md` (ESCALATED at iter 3, pass_rate=57)

## Purpose

This was a **controlled diagnostic run** to answer a single question: is the pipeline fundamentally broken, or is the Scenario Architect the only broken agent?

The prior run (run #2) escalated at 57% pass rate after 3 iterations. Post-mortem analysis identified two classes of structural scenario bugs that no amount of implementer iteration could fix:

1. **Pool-connection reads after tx-scoped SET LOCAL.** `tenant-context.test.ts` read `current_setting('app.tenant_id', true)` from the pool `connection` after `SET LOCAL` applied to a different checked-out connection (`tx`). Guaranteed miss — the pool connection never sees the GUC.

2. **PostgreSQL superuser RLS bypass.** `substrate-rls.test.ts` created tables as the `postgres` superuser and used `ENABLE ROW LEVEL SECURITY` without a non-owner role. Superusers bypass RLS unconditionally (`BYPASSRLS` is implicit) — even `FORCE ROW LEVEL SECURITY` does not re-enable enforcement for superusers. Every RLS assertion returned all rows.

Additionally: `database-health-check-registration.test.ts` used `vi.stubGlobal` (doesn't intercept ES-module imports), `drizzle-configuration.test.ts` asserted `DrizzleTx` at runtime (TypeScript type, erased at compile time), and `substrate-guc.test.ts` asserted `toBe('')` for an unset GUC whose wire value varies between `''` and `null`.

**Method:** Hand-write corrected scenarios (6 of 14 files), reset implementation code to committed baseline, edit the state file to `completed_step=2` / `iteration=0`, and run with `--resume`. This skips spec-validator, architect, and scenario-architect — the implementer runs fresh against the hand-written scenarios.

## Outcome: INTERRUPTED (operator kill) — diagnostic question answered

Killed mid-iter-2 reviewer phase. The diagnostic signal was clear after iter 1.

State file (final):

```
spec=2026-03-28-database-foundation
run_id=2edc139ab7fa4732
spec_hash=1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662
completed_step=2
iteration=1
last_decision=NO-SHIP
status=interrupted
phase=review
smoke_result=passed
pass_rate=95
gate_exit_code=1
```

## Results

| iter | implementer (s) | gate (s) | smoke | pass_rate | gate result | oracle |
|-----:|----------------:|---------:|:-----:|----------:|:-----------:|:------:|
| 1 | 539 | 91 | pass | 95 | FAIL (2 scenario bugs) | NO-SHIP |
| 2 | 281 | 54 | pass | 95 | FAIL (same 2) | — (killed during reviewer) |

### Iter 1 gate detail

```
Test Files  1 failed | 11 passed (12)
     Tests  2 failed | 38 passed (40)
```

| Suite | Tests | Status |
|-------|------:|:------:|
| substrate-rls.test.ts | 5/5 | PASS |
| tenant-context.test.ts | 9/9 | PASS |
| substrate-guc.test.ts | 4/4 | PASS |
| database-health-check-registration.test.ts | 4/4 | PASS |
| drizzle-configuration.test.ts | 4/4 | PASS |
| connection-pool-exhaustion.test.ts | 1/1 | PASS |
| environment-validation-missing-database-url.test.ts | 1/1 | PASS |
| server-startup-database-url-missing.test.ts | 1/1 | PASS |
| fastify-tenant-context.test.ts | 3/3 | PASS |
| schema-helpers.test.ts | 3/3 | PASS |
| database-connection.test.ts | 2/2 | PASS |
| **drizzle-migrations.test.ts** | **1/3** | **FAIL** |

**All 15 previously-failing tests now pass.** The 6 hand-written scenario rewrites fixed every structural bug from the prior run.

### The 2 remaining failures — scenario author error (mine)

Both in `drizzle-migrations.test.ts`:

- **AC-4:** `expect(rows.length).toBeGreaterThanOrEqual(1)` — asserts `drizzle.__drizzle_migrations` has >= 1 row.
- **EC-9:** `expect(total).toBeGreaterThan(0)` — same table, checks for duplicates (prerequisite: rows exist).

**Root cause:** The spec's FR-3 mandates the baseline migration is "pattern documentation only" — a SQL comment block with no executable DDL. The implementer correctly produced a comments-only `0000_baseline_rls_infrastructure.sql`. The `meta/_journal.json` has `"entries": []` because `drizzle-kit generate` was never run (no schema changes to generate from). When `drizzle-kit migrate` runs during setup, it creates the `drizzle.__drizzle_migrations` table but finds zero pending migrations → zero journal rows.

My test assumed drizzle-kit would record a journal entry for a comments-only migration file. It doesn't — the file isn't in the journal at all. This is the **same category of bug** the Scenario Architect made: scenario author misunderstands the spec-to-implementation mapping and writes an unsatisfiable assertion.

The defect classifier also misdiagnosed this: `"implementation creates migration table in wrong schema (public instead of drizzle)"` — wrong. The table IS in the `drizzle` schema (the EXISTS check passes). The classifier pattern-matched the anchor ID to a plausible-sounding explanation without investigating.

## The diagnostic answer

**The pipeline is fine. The Scenario Architect is the only broken agent.**

Evidence:

| Component | Verdict | Evidence |
|-----------|---------|----------|
| Implementer | Works | Produced correct code on iter 1 from spec alone — proper `withTenantDb` tx-scoped reads, tenant-context plugin, health check with 5s timeout, schema helpers. No `globalThis` escape hatches or test-contamination. |
| Gate | Works | Correctly ran 40 scenarios, reported 38/2, exit code 1. |
| Oracle | Works | Correctly rejected at 95% — 2 failures cite real anchors. |
| Iteration loop | Works | Fed anchor-level feedback, implementer re-ran, same result (because failures are in scenarios, not impl). |
| Defect classifier | Weak signal | Misdiagnosed root cause ("wrong schema") but correctly identified the failing anchors. Known limitation. |
| **Scenario Architect** | **Broken** | Prior run's 17 failures were ALL structural scenario bugs. 15 fixed by hand-rewrite; 2 remaining are the human scenario author (me) making the same category of mistake. |

## Compared to prior runs

| axis | run #2 (TE scenarios) | run #3 (hand-written, this one) |
|------|----------------------|-------------------------------|
| pass_rate (best) | 57% (iter 3) | 95% (iter 1) |
| tests failing | 17 | 2 |
| failure root | 15 structural scenario bugs + 2 spec-scenario tension | 2 scenario-author spec misread |
| implementer recovery possible | no (tests structurally broken) | no (tests assert wrong predicate) |
| impl quality | compromised (globalThis escape hatch) | clean (no test-contamination) |

## Scenario fixes applied (6 files)

| File | Bug in TE version | Fix |
|------|-------------------|-----|
| `tenant-context.test.ts` | Read GUC from pool `connection` after `SET LOCAL` on `tx` (different connection) | Read via `tx.execute(sql\`SELECT current_setting(...)\`)` inside the callback |
| `substrate-rls.test.ts` | Created tables as `postgres` superuser; `ENABLE ROW LEVEL SECURITY` without non-owner role; superuser bypasses RLS unconditionally | Created `NOBYPASSRLS` login role, opened second client, used staged contract helpers (`assertTenantSeesOwnRows`, etc.) |
| `substrate-guc.test.ts` | `toBe('')` for unset GUC (driver returns `''` or `null` depending on path) | Used staged `assertUnsetGucIsNullish` helper; `set_config(name, value, true)` instead of string-interpolated SET LOCAL |
| `drizzle-migrations.test.ts` | Queried `public.__drizzle_migrations` (wrong schema) | Changed to `drizzle.__drizzle_migrations` |
| `database-health-check-registration.test.ts` | `vi.stubGlobal` doesn't intercept ES-module imports | `vi.mock` with hoisted factory for both `registerHealthCheck` and `connection` |
| `drizzle-configuration.test.ts` | `expect(DrizzleTx).toBeDefined()` — TypeScript type erased at runtime | Replaced with `db.transaction(cb)` shape check (runtime-observable) |

## Root cause — the Scenario Architect has no feedback loop

Every other agent in the pipeline iterates against something:
- Implementer → oracle runs tests, feeds back anchor-level NO-SHIP signal
- Spec Validator → completeness checklist + divergence score
- Architect → apply-manifest ordering invariant + cache gate
- Oracle → gate pass rate + defect categories

The Scenario Architect writes tests once. They get staged. They become truth. There is no mechanism to detect structurally broken tests — tests that cannot pass against ANY correct implementation.

The prior run's 60 framework commits over 3 days were hand-crafted detectors for specific ways the TE failed: RLS-FORCE checks, substrate-manifest presence gates, consistency rules for migrate vs push, coverage routing for spec-vehicle mismatch. Each retired one failure mechanism. None addressed the root: nothing checks scenario integrity.

## Implications

1. **A Scenario Reviewer agent or scenario-smoke phase** is the missing primitive. Either run scenarios against staged contract helpers (a "reference implementation"), or add an agent that reads spec + scenarios + contracts and answers "does each test exercise the AC it cites."

2. **The staged contract helpers work.** The 3 helpers I used (`assertTenantSeesOwnRows`, `assertTenantCannotSeeOtherRows`, `assertUnsetGucIsNullish`) produced correct assertions. The TE didn't import them. The scenario-architect addendum (`scenario_architect_addendum.md`) actively discouraged their use by blessing owner-role pool connections.

3. **The scenario-architect addendum needs correction.** Lines 33-42 of `darkfactory/adapters/postgres/scenario_architect_addendum.md` say "a test-suite-level pool opened directly against `DATABASE_URL` is fine" — this guarantees owner-role connections and RLS bypass in any test environment where `DATABASE_URL` points at the superuser.

4. **This spec can ship** with 2 lines changed in `drizzle-migrations.test.ts` (assert file existence and setup-exit-code=0 instead of journal row count). The implementer's code is correct.

## Events (histogram)

```
  9 AGENT_START
  8 AGENT_COMPLETED
  3 FILES_CHANGED
  2 SMOKE_COMPLETED
  2 GATE_COMPLETED
  2 ITER_COST
  1 SPEC_START
  1 SPEC_ADAPTER_ACTIVATED
  1 TRAJECTORY_CLASSIFIED
```

No `BARRIER_VIOLATION`. No `REMEDIATION_TARGET`. No `BARRIER_PATH_ECHO_DETECTED`.
