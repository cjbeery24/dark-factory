import type { Sql } from 'postgres';

/**
 * Assert that row-level security is enabled AND at least one policy is
 * attached for the named table.
 *
 * Mirrors the three failure modes the substrate-verifier's
 * `_check_rls_tables` distinguishes:
 *   - `table-missing`  — pg_tables has no row for schema.table
 *   - `rls-disabled`   — pg_tables.rowsecurity = false
 *   - `no-policies`    — rowsecurity = true but pg_policies count = 0
 *
 * All three fail silently at implementation time (no runtime error —
 * the table is simply un-restricted) which is why they need a positive
 * contract at the test-engineer layer.
 *
 * `schemaQualified` accepts either `'public.foo'` or bare `'foo'`
 * (defaulted to schema `public`), mirroring pg_tables' lookup.
 */
export async function assertRlsEnabled(
  client: Sql,
  schemaQualified: string,
): Promise<void> {
  const { schema, table } = splitSchemaTable(schemaQualified);

  const tableRows = await client<
    Array<{ rowsecurity: boolean }>
  >`SELECT rowsecurity FROM pg_tables WHERE schemaname = ${schema} AND tablename = ${table}`;

  if (tableRows.length === 0) {
    throw new Error(
      `assertRlsEnabled: table ${schema}.${table} not found in pg_tables`,
    );
  }

  if (tableRows[0]!.rowsecurity !== true) {
    throw new Error(
      `assertRlsEnabled: row-level security is disabled on ${schema}.${table} ` +
        `(pg_tables.rowsecurity = false; ALTER TABLE ... ENABLE ROW LEVEL SECURITY missing)`,
    );
  }

  const policyRows = await client<
    Array<{ policy_count: string }>
  >`SELECT COUNT(*)::text AS policy_count FROM pg_policies WHERE schemaname = ${schema} AND tablename = ${table}`;

  const count = Number(policyRows[0]?.policy_count ?? 0);
  if (count === 0) {
    throw new Error(
      `assertRlsEnabled: ${schema}.${table} has rowsecurity enabled but ` +
        `no policies attached (pg_policies count = 0); CREATE POLICY missing`,
    );
  }
}

function splitSchemaTable(qualified: string): { schema: string; table: string } {
  const dot = qualified.indexOf('.');
  if (dot < 0) {
    return { schema: 'public', table: qualified };
  }
  return {
    schema: qualified.slice(0, dot),
    table: qualified.slice(dot + 1),
  };
}
