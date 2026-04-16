<!--
A4 migrated the DATABASE SCHEMA SETUP section out of
`agents/architect.md` into this file. The guidance is nominally
ecosystem-agnostic (many databases need migration commands), but the
worked examples it governs (Drizzle, Prisma, Django, Alembic) all
assume a relational DB active in the manifest — and Postgres is the
only relational adapter shipping today. If a non-Postgres relational
adapter lands, promote this guidance into a shared layer and leave a
cross-reference here.

Governance cap: 80 non-blank lines. Enforced by the contract harness
(`test_architect_addendum_within_governance_line_cap`).
-->

## Postgres addendum

### Database schema setup

When the spec requires database tables, schemas, or migrations
(Drizzle ORM, Prisma, Knex, Alembic, Django migrations, etc.), you
MUST declare `commands.setup` in the runtime block with the
migration command. The quality gate container runs `commands.setup`
after `commands.install` and before any test execution. The gate
starts with an empty database — if migrations don't run before
tests, all database-dependent tests silently skip.

Example runtime block with database setup:

```yaml
runtime:
  commands:
    install: "cd apps/api && pnpm install"
    setup: "cd apps/api && npx drizzle-kit migrate"
```

More examples for `commands.setup`:
- Prisma: `cd apps/api && npx prisma migrate deploy`
- Django: `python manage.py migrate`
- Alembic: `alembic upgrade head`
- Drizzle (push mode, dev-only / no migration journal): `cd apps/api && npx drizzle-kit push`
- Prisma (push mode, dev-only / no migration journal): `cd apps/api && npx prisma db push`

The database service MUST be declared under `runtime.services` with
`inject_env` mapping the connection URL (e.g. `DATABASE_URL`). The
service is healthy before `commands.setup` executes. If setup fails
the gate aborts with a clear Setup FAIL — this is correct and
preferred over running tests that silently skip. Do NOT chain
migrations into `commands.install`; use `commands.setup`.

### Migrate-mode vs push-mode: spec first, then project policy

When the spec explicitly names a migration command (`db:migrate`,
`alembic upgrade head`, `manage.py migrate`, etc.), mirror it
verbatim in `commands.setup`. When the spec is silent on mode,
project policy arbitrates via `.dark-factory/project-hints.toml`
(`[[policy.consistency_rules]]`) — a `commands.setup` amendment
that violates the project's declared migration discipline is
rejected before the implementer runs. The adapter does not
prescribe; spec text wins first, policy wins when the spec is
silent.

### Substrate manifest emission

If the spec contains a top-level `## Substrate Primitives` section
with a fenced TOML code block (the spec validator demands this when
the spec triggers a substrate rule), you MUST write the substrate
manifest to:
`/project/.dark-factory/architecture/substrate-manifests/<spec-id>.toml`

This is a near-mechanical TOML-to-TOML transform — no semantic
inference. Copy the spec's `## Substrate Primitives` block verbatim,
add a top-level `spec_id` line equal to the spec's filename stem, and
stamp `postgres_version` by extracting the version tag from
`runtime.services.postgres.image` in the **environment manifest you
just emitted** (the architecture YAML for this spec, NOT the
substrate manifest you are writing). For example, an environment
manifest with `image: "postgres:15"` yields `postgres_version = "15"`
in the substrate manifest. If `runtime.services.postgres` is absent
from the environment manifest, omit the `postgres_version` field; the
gate verification phase tolerates its absence. If the spec author's
fenced TOML block already contains `spec_id` or `postgres_version`,
overwrite with the architect-computed values — these two fields are
architect-owned, not author-owned.

Example output for a spec triggering `custom_guc` + `rls_policy`:

```toml
spec_id = "2026-03-28-database-foundation"
postgres_version = "15"

[[guc]]
name = "app.tenant_id"
default = ""

[[rls_table]]
name = "users"
expected_policies = ["users_tenant_isolation"]
```

If the spec has no `## Substrate Primitives` section, do not write
the manifest file. The gate's substrate-verification phase treats a
missing manifest as "no substrate to verify" and skips silently —
consistent with the validator: substrate rules are conditional, not
universal.
