-- Dark Factory: per-test DB clean-state reset for durable-state (Postgres) specs.
-- Single source of truth for the reset, read at runtime by _df_reset.setup.ts
-- (TS/vitest) and by the python conftest (follow-up). Catalog-driven so it works
-- regardless of how the architect named tables; excludes migration-tracking
-- tables so the schema/migration state survives; a single TRUNCATE ... CASCADE
-- handles inter-table FKs atomically; RESTART IDENTITY resets serial/identity
-- sequences so "first row id == 1 on a fresh table" holds every test. lock_timeout
-- so a stray open transaction surfaces a clear error instead of hanging the gate.
SET lock_timeout = '5s';
DO $df_reset$
DECLARE
  _tbls text;
BEGIN
  SELECT string_agg(format('%I.%I', schemaname, tablename), ', ')
    INTO _tbls
    FROM pg_tables
   WHERE schemaname = 'public'
     AND tablename NOT LIKE '\_\_%'          -- drizzle (__drizzle_migrations) et al.
     AND tablename <> 'alembic_version';     -- alembic (python)
  IF _tbls IS NOT NULL THEN
    EXECUTE 'TRUNCATE TABLE ' || _tbls || ' RESTART IDENTITY CASCADE';
  END IF;
END
$df_reset$;
