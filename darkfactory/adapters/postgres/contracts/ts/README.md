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
| `assertAppSetsTxGuc(client, gucName, expected, appCallback)` | Asserts the application's code path correctly issues a tx-local `SET LOCAL` (via `set_config`) for `gucName`. The helper opens a tx, runs `appCallback()` (which invokes a named app symbol — the scenario never sees the tx), reads `current_setting` on the same tx, asserts equality with `expected`, and rolls back. If the app opens a nested tx the assertion correctly fails. |
| `assertTxGucRoundTrips(client, gucName, value)` | Plumbing primitive: opens a tx, runs `SELECT set_config($1, $2, true)` with `(gucName, value)`, reads the GUC back, asserts `value` round-trips unchanged, rolls back. Use this when the scenario's concern is driver/wire plumbing, not app correctness. |
| `assertRlsEnabled(client, schemaQualified)` | Table exists in `pg_tables`, `rowsecurity = true`, and `pg_policies` count > 0. Each failure mode reported with a distinct message. |
| `assertTenantSeesOwnRows(client, table, tenant, tenantColumn?)` | With `SET LOCAL app.tenant_id = <tenant>` in a transaction, every returned row's tenant column equals `<tenant>`. |
| `assertTenantCannotSeeOtherRows(client, table, self, other, tenantColumn?)` | With `SET LOCAL app.tenant_id = <self>` in a transaction, `COUNT(*)` of rows WHERE tenant column = `<other>` is zero. |

Helper → substrate-rule trigger:

- `[[guc]]` → `assertUnsetGucIsNullish` (unset-GUC assertion) OR
  `assertAppSetsTxGuc` (app correctly issues SET LOCAL — pairs with
  the classification-hint `POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION` from
  the adapter, which flags peer-connection reads as non-verifiable)
  OR `assertTxGucRoundTrips` (plumbing-only GUC round-trip, when the
  scenario is not asserting app behavior).
- `[[rls_table]]` → `assertRlsEnabled`
- `[[tenant_isolation]]` → `assertTenantSeesOwnRows` (positive scenario)
  and `assertTenantCannotSeeOtherRows` (negative scenario). One helper
  per scenario so the reviewer/oracle can classify positive/negative
  coverage at the scenario-name layer.

Helpers run inside `client.begin(...)` transactions and roll back
unconditionally via a sentinel error — GUC mutations never leak across
scenarios.

## Peer types, not bundled dependencies

`postgres.Sql` is imported as a type only. Scenarios already open a
client against `DATABASE_URL`; bundling `postgres` here would create
two distinct `Sql` instances in one vitest suite. The test-engineer
passes their already-opened client into each helper.
