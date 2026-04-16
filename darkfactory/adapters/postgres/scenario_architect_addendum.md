<!--
Postgres scenario architect addendum — substrate-aware test generation.

Loaded by `PostgresAdapter.scenario_architect_addendum()`. The
orchestrator ONLY injects this addendum when the substrate manifest at
`.dark-factory/architecture/substrate-manifests/<spec>.toml` exists for
the current spec, so the prose can assume the manifest is present.

Reason this exists: 2026-04-15 failure shipped a baseline migration
with zero executable SQL because every scenario routed assertions
through the ORM, which a stub implementation can satisfy without the
database doing anything. Substrate-layer assertions close the loop.

Governance cap: 80 non-blank lines. Enforced by the contract harness
(`test_scenario_architect_addendum_within_governance_line_cap`).
-->

## Postgres substrate-aware scenarios

The architect emitted a substrate manifest for this spec at:

    .dark-factory/architecture/substrate-manifests/<spec-id>.toml

Read it. For each entry, generate at least one scenario that exercises
the assertion **at the database layer**, not through the application
stack. ORM-mediated scenarios cannot distinguish a working
implementation from a stub that fakes the response shape.

### How to write substrate scenarios

For each entry in the substrate manifest, generate a scenario that:

1. **Obtains its connection directly from `DATABASE_URL`** using the
   ecosystem's PG client (`psycopg`, `node-postgres` / `pg`,
   `database/sql`+`lib/pq`). The connection MUST NOT be obtained from:
   - the application's DI container, repository layer, or ORM session,
   - any test helper that auto-applies tenant scoping, RLS context, or
     transaction management on behalf of application code,
   - a mock, in-memory shim, or recorded fixture.

   A test-suite-level pool opened directly against `DATABASE_URL` is
   fine — what's forbidden is application code mediating the connection.

2. **Executes the assertion as raw SQL.** Examples by primitive type:

   - `[[guc]] name="app.tenant_id"` →
     `SELECT current_setting('app.tenant_id', true)` returns the
     expected value after the application has set the GUC for a request.
   - `[[rls_table]] name="users"` →
     `SELECT rowsecurity FROM pg_tables WHERE tablename='users'` is
     `true`; `SELECT count(*) FROM pg_policies WHERE tablename='users'`
     is ≥ 1.
   - `[[tenant_isolation]] tenant_table="users"` → using whatever
     scoping mechanism the manifest declares (a GUC like `app.tenant_id`,
     a session variable, an RLS policy condition), set the session to
     tenant A, then `SELECT * FROM users` returns A's rows only — even
     when rows for tenant B exist in the table.

3. **Tags the scenario with the anchor it credits.** Every
   substrate-layer scenario MUST carry exactly one in-manifest tag
   (`[AC-N]`, `[FR-N]`, or `[EC-N]`) per the agent definition's Hard
   Rules. The substrate aspect does not exempt the scenario from
   anchor-alignment.

4. **Runs against the gate sidecar, not a mock.** The substrate
   manifest assumes the same `DATABASE_URL` the gate's setup phase
   migrated against. A mocked sidecar defeats the entire point.

### Tenant isolation: positive AND negative pair

For any rule firing `tenant_isolation`, you MUST emit BOTH:

- A **positive** scenario: as tenant A, the application's read returns
  A's rows.
- A **negative** scenario: as tenant A, the application's read does
  NOT return tenant B's rows even though those rows exist.

A positive-only suite gives no signal that isolation actually works —
a stub that returns all rows passes the positive test trivially.

### Wire-contract assertion helpers

Library-mediated surfaces (postgres.js, drizzle-orm) expose wire-level
truths that differ from SQL semantics — e.g. `current_setting(name,
true)` returns `''` at the postgres.js wire, not NULL. Hand-rolled
predicates repeatedly produce false-negatives the implementer cannot
satisfy (2026-04-16 escalation). Prefer the staged helpers under
`_contracts/postgres/` over hand-rolled matchers. From a scenarios-root
test:

    import { assertUnsetGucIsNullish, assertRlsEnabled,
             assertTenantSeesOwnRows, assertTenantCannotSeeOtherRows
           } from './_contracts/postgres';

From a subdirectory (e.g. `acceptance/foo.test.ts`), use
`../_contracts/postgres`. Helper → substrate-rule trigger:

- `[[guc]]` → `assertUnsetGucIsNullish`
- `[[rls_table]]` → `assertRlsEnabled`
- `[[tenant_isolation]]` → `assertTenantSeesOwnRows` (positive) AND
  `assertTenantCannotSeeOtherRows` (negative) — one helper per scenario.

Use helpers for the primary substrate assertion; hand-roll secondary
assertions only for behavior not covered.
