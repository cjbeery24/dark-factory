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

This is a near-mechanical TOML-to-TOML transform. Copy each spec
primitive verbatim — **including** optional fields like `verifiable
= false` on `[[rls_table]]` (stripping re-enrols tables the author
declared unverifiable). Add top-level `spec_id` (filename stem) and
`postgres_version` (from `runtime.services.postgres.image` —
`postgres:15` → `"15"`; omit if absent). These two fields are
architect-owned; overwrite any author-supplied values.

Every primitive MUST carry its gate-verification SQL, or the gate
emits `substrate-guc-no-check` / `substrate-rls-no-check` and fails
the iteration:

- `[[guc]]` MUST include `registration_check` — SQL that returns the
  GUC's current setting (NULL when unregistered), typically
  `SELECT current_setting('<name>', true)`.
- `[[rls_table]]` MUST include `rowsecurity_check`
  (`pg_tables.rowsecurity`) AND `policy_count_check`
  (`count(*) from pg_policies`); on `force = true` ALSO emit
  `force_check` against `pg_class.relforcerowsecurity`, schema-qualified with `AND relnamespace = 'public'::regnamespace`.
- For specs declaring `commands.setup` migration commands, ALSO emit `[[bootstrap_check]]` per migration system. Read the project's migration-tool config (drizzle.config.ts `migrations.table` default `__drizzle_migrations`; alembic `version_table` default `alembic_version`; etc.) and emit the literal name into `check_sql`. Without this, a Setup phase that exits 0 without satisfying its expected post-condition PASSes the gate and cascades into scenario crashes mislabeled as IMPL_BUG.

Example output structure for a spec triggering `custom_guc` + `rls_policy` plus a drizzle migration setup (substitute the spec's actual primitive names for `<placeholders>`):

```toml
spec_id = "<spec-filename-stem>"
postgres_version = "15"

[[guc]]
name = "<your_guc_name>"
default = ""
registration_check = "SELECT current_setting('<your_guc_name>', true)"

[[rls_table]]
name = "<your_table>"
expected_policies = ["<your_policy_name>"]
rowsecurity_check = "SELECT rowsecurity FROM pg_tables WHERE tablename = '<your_table>'"
policy_count_check = "SELECT count(*) FROM pg_policies WHERE tablename = '<your_table>'"

[[bootstrap_check]]
name = "drizzle-migrations-tracking-table"
check_sql = "SELECT 1 FROM information_schema.tables WHERE table_name='__drizzle_migrations'"
expect = "row_present"
```

If the spec has no `## Substrate Primitives` section, do not write
the manifest file. The gate's substrate-verification phase treats a
missing manifest as "no substrate to verify" and skips silently —
consistent with the validator: substrate rules are conditional, not
universal.
