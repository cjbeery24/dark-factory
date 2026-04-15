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
    setup: "cd apps/api && npx drizzle-kit push"
```

More examples for `commands.setup`:
- Prisma: `cd apps/api && npx prisma db push`
- Django: `python manage.py migrate`
- Alembic: `alembic upgrade head`
- Drizzle (migrate mode): `cd apps/api && npx drizzle-kit migrate`

The database service MUST be declared under `runtime.services` with
`inject_env` mapping the connection URL (e.g. `DATABASE_URL`). The
service is healthy before `commands.setup` executes. If setup fails
the gate aborts with a clear Setup FAIL — this is correct and
preferred over running tests that silently skip. Do NOT chain
migrations into `commands.install`; use `commands.setup`.

### Migrate-mode vs push-mode is project policy

The choice between `drizzle-kit migrate` and `drizzle-kit push` (and
their per-ORM equivalents) is project policy, not adapter policy.
You MUST respect the project's
`.dark-factory/project-hints.toml` (`[[policy.consistency_rules]]`)
— a `commands.setup` amendment that violates the project's declared
migration discipline is rejected before the implementer runs. The
adapter does not prescribe a mode; the policy layer does.
