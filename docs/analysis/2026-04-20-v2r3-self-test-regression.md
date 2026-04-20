---
title: V2-R3 Self-Test Regression — Root Cause Analysis
date: 2026-04-20
outcome: A
run_id: f90861497182494c
spec: 2026-03-28-database-foundation
target: budget-brain-v2
feeds: docs/plans/2026-04-20-implementer-self-correction-priority.md (Priority 1)
---

# V2-R3 Self-Test Regression Analysis

## Purpose

Forcing function for Priority 1 (reshaped Phase 8a) prompt design. Per the plan, this analysis exits with either:

- **Outcome A**: Architectural-patching hypothesis confirmed — produce worked example for root-cause-preamble template.
- **Outcome B**: Hypothesis falsified — reopen Priority 1 design.

**Outcome: A.** Hypothesis confirmed. Worked example below.

## The failure

V2-R3 iter 2 quality gate:
- Blind scenarios: 64% → 76% (IMPROVING)
- Self-Tests: PASS → FAIL (new regression)
- Trajectory classified IMPROVING despite self-test regression; pipeline NO-SHIPPED on the hard gate failure.

Failing tests at `apps/api/src/db/index.ts:74:20`:
```
TypeError: tx is not a function
     39|       withTenantDb(tenantA, async (tx) => {
     40|         const gucResult = await tx`SELECT current_setting('app.tenant_…
                                            ^
```

## What changed between iter 1 and iter 2

Source snapshot at iter 2 (from archived-runs/f90861497182494c, reconstructed from target repo state at escalation):

`apps/api/src/db/index.ts` (iter 2):
```ts
// Drizzle ORM instance
export const db = drizzle(connection, { schema });

export type DrizzleTx = Parameters<Parameters<typeof db.transaction>[0]>[0];

export async function withTenantDb<T>(
  tenantId: string,
  callback: (tx: DrizzleTx) => Promise<T>,
): Promise<T> {
  // ... validation ...
  return db.transaction(async (tx) => {
    await tx.execute(sql`SET LOCAL app.tenant_id = '${sql.raw(tenantId)}'`);
    return await callback(tx);  // <-- line 74
  });
}
```

The callback receives a Drizzle transaction object (`DrizzleTx`), not a postgres.js tagged-template function.

## Why self-tests failed

The **visible scenarios** at `.dark-factory/archived-runs/f90861497182494c/scenarios/2026-03-28-database-foundation/tenant-context.test.ts` expected the prior signature (iter 1 style, postgres.js):

```ts
withTenantDb(tenantA, async (tx) => {
  const gucResult = await tx`SELECT current_setting('app.tenant_id', true) as tenant_id`;
  return gucResult[0].tenant_id;
})
```

`await tx\`SELECT ...\`` is only valid when `tx` is a postgres.js `Sql` instance — a callable tagged template. Drizzle's `DrizzleTx` is NOT a tagged template; it exposes `.execute(sql\`...\`)` instead.

**The scenarios are locked** (TE runs once per pipeline run). Iter-2's signature rewrite silently made every visible scenario call `tx\`...\`` at runtime invalid. Every scenario that called `tx\`...\`` threw `TypeError: tx is not a function` at the `callback(tx)` site.

## Root cause

Iter 2 changed the architectural type of `withTenantDb`'s callback parameter — from a postgres.js tagged-template function to a Drizzle transaction object — without checking that the visible scenario callsites were compatible with the new signature.

**This is architectural patching without consumer reconciliation.** The implementer made a local code change (switching to Drizzle's transaction manager for SET LOCAL semantics, presumably to fix some RLS blind scenarios) without recognizing that the change invalidated the callback-signature contract that all visible scenarios were written against.

### Why it was categorized IMPROVING

Blind scenarios that happened to avoid the tagged-template pattern (or tested edge paths where the callback never ran) went from failing to passing. The classifier saw net-positive movement on blind scenarios and tagged the trajectory IMPROVING. Only the Self-Tests hard gate caught the regression.

**This is exactly the failure mode Priority 1.5 (self-test-regression hard gate) prevents:** rejecting any iter-N output whose Self-Tests regress from iter-(N-1), even if blind pass rate improved.

## Would the prior plan's "architectural permission" clause have helped?

Probably not. The iter-2 implementer **already did** perform an architectural change (switching from postgres.js transaction to Drizzle transaction). It did not decline to restructure — it restructured in an incompatible direction. A prompt clause saying "you may re-architect" authorizes behavior the implementer was already exhibiting.

What would have helped: forcing the implementer to **name the affected consumers** before writing the change. The Priority 1 structured root-cause preamble does this:

```
- Proposed fix scope: [local-patch | signature-change | module-rework]
- Why this differs from the iter N-1 attempt: <one sentence>
```

If iter 2 had been required to emit `Proposed fix scope: signature-change` with a preamble, the implementer would have been forced to confront the "what callers rely on this signature?" question. The visible scenarios would be top of mind because they're in the same file tree the implementer is iterating on.

This is not a proof that the preamble fixes the problem — it's a concrete mechanism that targets the specific failure pattern.

## Worked example for Priority 1 root-cause-preamble template

To be embedded in the prompt verbatim as an illustrative case:

> **Example of a signature-change failure mode (from V2-R3, database-foundation spec):**
> Iter 1 implementation used `withTenantDb(id, cb: (tx: PostgresSql) => ...)` where `tx` was a postgres.js tagged-template connection inside a transaction. Iter 2 switched to Drizzle's `db.transaction()`, changing the callback signature to `(tx: DrizzleTx) => ...`. This fixed some RLS blind scenarios but broke every visible scenario's `await tx\`SELECT current_setting(...)\`` — `DrizzleTx` is not a tagged template function.
>
> Lesson: when changing callback signatures, before writing the change, enumerate the callsites in `.dark-factory/scenarios/` AND in existing source files and confirm they remain valid under the new signature. If they don't, the signature change is wrong — or the callsites need an update bundled in the same iteration.

## Other findings

**Classifier determinism confirmed.** Iter-1 and iter-2 classifier hashes identical on unchanged inputs; classifier variance did not contribute to V2-R3 failure.

**Barrier defense-in-depth worked.** An earlier TE repair-phase attempt to read `apps/api/src` was blocked; the run continued under barrier protection without the TE gaining source access.

**TE output volume (56 scenarios) was not the bottleneck.** Priority 1 (implementer self-correction) is the correct intervention, not Priority 5.7b (TE volume floor).

## Impact on Priority 1 implementation

1. **Root-cause preamble template** includes the "what consumers rely on this signature/interface?" question explicitly when `Proposed fix scope ∈ {signature-change, module-rework}`.
2. **Worked example above** embedded in the prompt as a concrete illustration.
3. **Priority 1.5 (self-test-regression hard gate)** is validated as directly addressing V2-R3's specific pathology — blind-up/self-test-down.

No reopening of Priority 1 design required. Proceed to implementation.
