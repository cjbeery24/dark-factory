# Postgres adapter — project-starter contracts

Starter templates for the **convention** half of the postgres adapter's
wire-contract helpers. Copy this directory's contents (excluding this
README) into your project's
`<project>/.dark-factory/contracts/postgres/` and edit the project
conventions (GUC name, tenant column) inline.

ROADMAP.md Phase 3 B9a's v(N) cycle (current): the framework continues
to ship live copies of the convention helpers under
`darkfactory/adapters/postgres/contracts/ts/` for the deprecation
window. v(N+1): framework deletion; these starters become the only
source.

## What lives where

| Helper                              | Where it lives                                              | Why                                                                                      |
|-------------------------------------|-------------------------------------------------------------|------------------------------------------------------------------------------------------|
| `assertUnsetGucIsNullish`           | **Framework** (`_contracts/postgres/guc.ts`)                | Pure postgres.js wire truth — no project conventions referenced.                         |
| `assertTxGucRoundTrips`             | **Framework** (`_contracts/postgres/tx_guc.ts`)             | Plumbing primitive parameterized by GUC name — no project conventions baked in.          |
| `assertRlsEnabled`                  | **Framework** (`_contracts/postgres/rls.ts`)                | Pure SQL-layer check on `pg_tables` / `pg_policies` — no project conventions referenced. |
| **`assertAppSetsTxGuc`**            | **Project** (this dir → `_contracts/postgres/project/`)     | Assumes app exposes a `withTenantDb`-style wrapper that issues `SET LOCAL`.              |
| **`assertTenantSeesOwnRows`**       | **Project** (this dir → `_contracts/postgres/project/`)     | Hardcodes `app.tenant_id` GUC + `tenant_id` column default — both are project naming.    |
| **`assertTenantCannotSeeOtherRows`**| **Project** (this dir → `_contracts/postgres/project/`)     | Same conventions as above.                                                               |

## How staging finds them

1. Place these files under `<project>/.dark-factory/contracts/postgres/`.
2. The orchestrator's `stage_adapter_contracts` (with B9d's
   `project_dir` parameter) calls
   `PostgresAdapter.project_contracts_dir(project_dir)`. The ABC
   default returns `<project>/.dark-factory/contracts/postgres/` if it
   exists, else None.
3. Returned dir's contents copied to
   `<scenarios_dir>/_contracts/postgres/project/<your-files>` — the
   staged tree is then visible to the test-engineer container as
   `/project/.dark-factory/scenarios/<spec>/_contracts/postgres/project/`.

## What you MUST edit

Two project-convention literals in `isolation.ts`. Grep the file for
`PROJECT CONVENTION` — every site that needs editing carries that
marker comment.

- **GUC name** (`'app.tenant_id'`) — change to your project's GUC
  (e.g., `'app.org_id'`).
- **Tenant column default** (`'tenant_id'`) — change if your project
  uses `org_id`, `workspace_id`, etc. (Or keep the default and pass
  an explicit value from every scenario.)

The `assertAppSetsTxGuc` helper in `app_tx_guc.ts` does NOT hardcode
the GUC name (it takes `gucName` as a parameter), but it ASSUMES an
`appCallback`-shaped contract — your app must have a wrapper like
`withTenantDb(tenant, fn)` that issues `SET LOCAL` from inside. If
your project's tenant-scoping pattern differs, the helper's docstring
explains the shape; either reshape the helper or write a
project-specific replacement.

## Forbidden patterns the helpers prevent

These conventions are written to AVOID scenario foot-guns flagged by
`scripts/lib/scenario_lint.py`:

- `parameterized-set-local`: do NOT write
  `tx``SET LOCAL app.x = '${val}'```. Postgres rejects bind-param
  substitution at the GUC value position. The helpers use
  `set_config(name, value, true)` which IS bindable.
- `peer-client-guc-read`: do NOT open a second client and read
  `current_setting('app.*')` on it. `SET LOCAL` is tx-scoped to the
  app's own transaction; a peer always sees `''`. The helpers stay
  inside `client.begin(...)` for both write and read.

If your project's helpers re-introduce either pattern, the scenario
lint will catch it (via either the built-in regex or a §4a
`forbidden_test_shapes` entry in the spec).
