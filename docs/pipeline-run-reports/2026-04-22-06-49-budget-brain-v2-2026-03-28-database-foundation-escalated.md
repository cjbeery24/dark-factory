# Pipeline Run: 2026-03-28-database-foundation — ESCALATED

**Date:** 2026-04-22
**Run ID:** `358d34f2c35c4b78`
**Target:** budget-brain-v2
**Outcome:** SPEC_ESCALATED after 3 iterations (75 min wall clock)
**Final pass rate:** 75% (33/46 scenarios), satisfaction 71%

## State excerpt

```
spec=2026-03-28-database-foundation
run_id=358d34f2c35c4b78
iteration=3
pass_rate=75
gate_exit_code=1
last_decision=NO-SHIP
status=escalated
```

## Fixes that landed before this run

1. `.js`→`.ts` architect-manifest specifier normalizer (commits `55fc7aa`, `565e35d`, `3e136dd`) — dropped `abs_source.is_file()` guard, added cache-hit path wiring, added structured logging.
2. `check_blocking_ac_family` FATAL precheck + §8a blocking ACs added to spec.

**Verified this run:** manifest shipped with `.ts` specifiers; prior Import Contract `.js` failure mode is dead.

## Gate results per iteration

| Iteration | Install | Setup | Build | Lint | Type | Substrate | Import Contract | Self-Tests | Scenario Lint | Blind Scenarios | pass_rate |
|-----------|---------|-------|-------|------|------|-----------|-----------------|------------|---------------|-----------------|-----------|
| 1 | PASS | **FAIL** (drizzle migrate output truncated) | — | — | — | — | — | — | — | — | — |
| 2 | PASS | PASS | PASS | PASS | PASS | **FAIL** | **FAIL** (vitest/dist/spy.js) | PASS | PASS | **FAIL** | 0% |
| 3 | PASS | PASS | PASS | PASS | PASS | **FAIL** | **FAIL** (DrizzleTx type-only) | PASS | PASS | **FAIL** | 75% |

## Terminal event

Escalation agent failed with `exit_code=1` after 1.4s. The file `.dark-factory/reviews/2026-03-28-database-foundation-escalation.md` reads `"You've hit your limit · resets 9am (UTC)"` — Claude subscription cap, not a pipeline bug. Any rerun before the reset window will hit the same wall at the first agent.

## Root causes (verified by file inspection)

**All four remaining failure modes are pipeline-infrastructure bugs, not implementer or spec bugs:**

### 1. Substrate Verification demands a table the spec excludes

Spec §FR-3 "Side effects: None (pattern documentation only; no database state changes)". But the substrate manifest at `.dark-factory/architecture/substrate-manifests/2026-03-28-database-foundation.toml` lists `[[rls_table]] name = "baseline_pattern"` with no `verifiable` key. `PostgresAdapter._check_rls_tables` at `darkfactory/adapters/postgres/adapter.py:791` contains zero occurrences of `verifiable` — the field has no handler.

**Consequence:** gate demands a table whose creation would violate the spec.

**Finding file:** `reports/2026-03-28-database-foundation-substrate-findings.json`
```json
{"code": "substrate-rls-table-missing", "message": "table 'baseline_pattern' does not exist in pg_tables"}
{"code": "substrate-guc-unregistered", "message": "GUC 'app.tenant_id' is not registered after setup"}
```

### 2. Import Contract gate synthesizes a runtime check for a TypeScript type alias

Architect manifest at `.dark-factory/architecture/2026-03-28-database-foundation-manifest.yaml:109`:
```yaml
- path: apps/api/src/db/index.ts
  exports: [db, connection, withTenantDb, DrizzleTx]
```

Source at `apps/api/src/db/index.ts:21`:
```typescript
export type DrizzleTx = Parameters<Parameters<typeof db.transaction>[0]>[0];
```

`DrizzleTx` is a TypeScript type alias — zero runtime presence. `scripts/lib/render_import_contract_phase.py:167-180` synthesizes `Object.keys(_mod).includes('DrizzleTx')` which type exports cannot satisfy.

**Consequence:** gate fails every iteration with identical diagnostic regardless of implementer action.

### 3. Scenario tests assert transaction-local GUCs via a peer connection

`.dark-factory/scenarios/2026-03-28-database-foundation/tenant-context.test.ts:10-22`:
```typescript
client = postgres(process.env.DATABASE_URL!);    // module-level pool
// ...
await withTenantDb(testTenantId, async (tx) => { // SET LOCAL on tx's connection
  const gucResult = await client`SELECT current_setting('app.tenant_id', true)`;
  //                        ^^^^^^ peer connection, cannot observe tx-local GUC
});
```

`SET LOCAL` is transaction-scoped. The peer `client` connection has its own transaction context — the assertion always reads the GUC as unset. All 5 failing AC-5/AC-11/FR-2 scenarios exhibit this pattern.

**Consequence:** implementer's code is correct; tests are structurally unable to observe the invariant under test.

### 4. Scenario tests run RLS checks via the table owner

`.dark-factory/scenarios/2026-03-28-database-foundation/substrate-rls.test.ts:13-25`:
```typescript
client = postgres(process.env.DATABASE_URL!);           // table owner
await client`CREATE TABLE test_tenant_table (...)`;     // owner creates table
await client`ALTER TABLE ... ENABLE ROW LEVEL SECURITY`; // policies attached
// assertions run as owner — owner bypasses RLS unless FORCE is set
```

The addendum at `darkfactory/adapters/postgres/scenario_architect_addendum.md:40-43` mandates a NOBYPASSRLS role for exactly this scenario; it is prose, not a mechanical check. Test Engineer ignored it.

**Consequence:** RLS isolation assertions return all rows regardless of implementation. AC-7 / AC-10 cannot pass.

### 5. Defect classifier mislabels all 5 SCENARIO_BUGs as IMPL_BUG

`reports/2026-03-28-database-foundation-defects-iter3.json` labels every failure as `IMPL_BUG` with rationale "implementation likely missing …". The classifier looks at test name + expected/received but not at the test file's connection topology. Feedback to the next iteration's implementer is structurally misdirected.

## What the implementer actually did

Oracle iter-3 verbatim: *"Minimal changes (only `vitest.config.ts` modified) in iter-3; core infrastructure issues remain unresolved."*

Reviewer iter-3: *"Excellent RLS Security Implementation … Proper Transaction Isolation … Comprehensive Error Handling"* — security 15/15. Test Results 15/25 purely because tests failed. The implementer had nothing to change because their code is correct.

## ac_blocking veto — working as designed, not exercised

`should_ship_with_gaps` (`darkfactory/pipeline/iteration.py:169`) correctly vetoes SHIP_WITH_GAPS on any blocking-AC failure. This run never reached that branch — Oracle declared NO-SHIP directly. The veto mechanism remains structurally sound and the §8a amendments remain correct; they just weren't load-bearing on this particular run.

## Takeaway

The pipeline burned 75 minutes + ~180 million cache-read tokens on failures where the implementer's code was correct. The 10-point gap between "SHIP_WITH_GAPS at 85%" and "ESCALATE at 71%" is entirely in the pipeline infrastructure, not in the implementation.

## Follow-up work

- Propagate `verifiable = false` through architect → substrate-manifest → adapter checks
- Exclude TS type-only exports from `entry_points[*].exports` (architect-side rule)
- Scenario-lint: flag module-level peer-client asserting tx-local state
- Scenario-lint / architect: enforce NOBYPASSRLS role for RLS tests
- Defect-classifier: distinguish SCENARIO_BUG from IMPL_BUG via connection topology

Clean + rerun only after those land, and only outside the Claude usage cap window.
