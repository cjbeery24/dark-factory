# `@df/contracts-postgres` — wire-contract helpers

Executable assertion helpers that encode **wire-level** truths the
`postgres.js` + `drizzle-orm` layer surfaces. Used by the dark-factory
test-engineer when authoring blind scenarios for Postgres-backed specs.

## Why this exists

The 2026-04-16 run of `2026-03-28-database-foundation` escalated
because the test-engineer authored scenarios whose predicates were
true at the SQL layer but wrong at the `postgres.js` wire surface.
Two defects, both unsatisfiable by any correct implementation:

1. `expect(result[0].tenant_id).toBeNull()` against
   `current_setting('app.tenant_id', true)` — the wire surface is
   the empty string `''`, not NULL.
2. `tx.execute(sql\`…\`)` mixing `drizzle-orm`'s `sql` tag with
   `postgres.js`'s tagged template — the two produce incompatible
   objects.

Helpers here encode each wire truth once, with a named function. The
test-engineer calls the helper instead of hand-rolling the predicate;
the wire truth is maintained in adapter-owned code, not replicated
across every scenario file.

## Barrier semantics

These helpers ship as **adapter source**, staged per-spec into
`.dark-factory/scenarios/<spec>/_contracts/postgres/` by the
orchestrator before the scenario-architect container runs. The
test-engineer sees the staged copy via a relative import; the
implementer cannot read the scenarios directory, so helpers remain on
the test-engineer's side of the barrier.

## Helper catalog

| Helper | Wire truth encoded |
|---|---|
| `assertUnsetGucIsNullish(client, gucName)` | `current_setting(name, true)` returns `null \| ''` when the GUC is unset; accept either form. |

Additional helpers (`assertRlsEnabled`, `assertTenantSeesOwnRows`,
`assertTenantCannotSeeOtherRows`) land in Step 3 of the wire-contracts
plan.

## Peer types, not bundled dependencies

`postgres.Sql` is imported as a type only. Scenarios already open a
client against `DATABASE_URL`; bundling `postgres` here would create
two distinct `Sql` instances in one vitest suite. The test-engineer
passes their already-opened client into each helper.
