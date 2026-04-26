import type { Sql } from 'postgres';

/**
 * DEPRECATION NOTICE — ROADMAP.md Phase 3 B9a.
 *
 * Both helpers in this file (`assertTenantSeesOwnRows`,
 * `assertTenantCannotSeeOtherRows`) are CONVENTION helpers — they
 * hardcode the project-specific GUC name `app.tenant_id` and the
 * default column name `tenant_id`. v(N) (current): the framework
 * continues to ship them for the deprecation window. v(N+1):
 * framework deletion; the project-starter copy under
 * `darkfactory/contracts/templates/postgres/project-starters/isolation.ts`
 * becomes the only source. Projects bootstrap via `dark-factory
 * contracts init postgres` (B9d staging picks the project copy up
 * under `_contracts/postgres/project/`).
 *
 * Maintainers: do not add NEW project-convention helpers to this
 * file. New convention work belongs in the starter dir from day one.
 */

/** Identifier guard — defense-in-depth for `tx.unsafe()` interpolation.
 *  Scenarios pin `table`/`tenantColumn` at authorship time (not user
 *  input), but a typo in a scenario file would otherwise be a SQL
 *  injection. Matches unqualified or schema.qualified identifiers. */
const IDENTIFIER_PATTERN = /^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)?$/;

function assertSafeIdentifier(kind: string, value: string): void {
  if (!IDENTIFIER_PATTERN.test(value)) {
    throw new Error(
      `${kind} contains unsafe characters: ${JSON.stringify(value)} ` +
        `(must match ${IDENTIFIER_PATTERN.source})`,
    );
  }
}

/**
 * Assert that a tenant sees its own rows when the tenant GUC is set.
 *
 * Opens a transaction, runs `SET LOCAL app.tenant_id = <tenant>`, then
 * SELECTs from the named table. All returned rows must have
 * `tenant_id === tenant`. Fails if any row's `tenant_id` differs from
 * the one set — that is the positive failure case the substrate-rule
 * `[[tenant_isolation]]` trigger targets.
 *
 * The transaction rolls back unconditionally so the GUC mutation does
 * not leak across scenarios.
 *
 * `table` and `tenantColumn` must be caller-supplied identifiers. They
 * are interpolated into the query text via `client.unsafe()` — NEVER
 * thread user-controlled input through this helper. Scenarios pin
 * these at authorship time.
 */
export async function assertTenantSeesOwnRows(
  client: Sql,
  table: string,
  tenant: string,
  tenantColumn: string = 'tenant_id',
): Promise<void> {
  assertSafeIdentifier('table', table);
  assertSafeIdentifier('tenantColumn', tenantColumn);

  await client.begin(async (tx) => {
    await tx`SELECT set_config('app.tenant_id', ${tenant}, true)`;

    const rows = await tx.unsafe<Array<Record<string, unknown>>>(
      `SELECT ${tenantColumn} AS tenant_col FROM ${table}`,
    );

    for (const row of rows) {
      if (row.tenant_col !== tenant) {
        throw new Error(
          `assertTenantSeesOwnRows: tenant=${tenant} saw row with ` +
            `${tenantColumn}=${JSON.stringify(row.tenant_col)} in table ${table} ` +
            `(RLS policy is not scoping SELECT to the tenant GUC)`,
        );
      }
    }

    throw new RollbackSentinel();
  }).catch((err: unknown) => {
    if (err instanceof RollbackSentinel) {
      return;
    }
    throw err;
  });
}

/**
 * Assert that a tenant CANNOT see another tenant's rows.
 *
 * Opens a transaction, runs `SET LOCAL app.tenant_id = <self>`, then
 * SELECTs rows WHERE `<tenantColumn>` = `<other>`. Row count MUST be 0.
 *
 * Pairs with `assertTenantSeesOwnRows` — separate helpers so the
 * positive/negative split is visible to the reviewer/oracle at the
 * scenario-call layer (one helper per scenario; embedding both checks
 * would hide the distinction).
 */
export async function assertTenantCannotSeeOtherRows(
  client: Sql,
  table: string,
  self: string,
  other: string,
  tenantColumn: string = 'tenant_id',
): Promise<void> {
  assertSafeIdentifier('table', table);
  assertSafeIdentifier('tenantColumn', tenantColumn);

  await client.begin(async (tx) => {
    await tx`SELECT set_config('app.tenant_id', ${self}, true)`;

    // $1 binds `other` as a value param; `${table}` / `${tenantColumn}`
    // are JS template interpolations that insert the (already validated)
    // identifiers directly into the SQL text — postgres.js never sees
    // them as bindable params.
    const rows = await tx.unsafe<Array<{ c: string }>>(
      `SELECT COUNT(*)::text AS c FROM ${table} WHERE ${tenantColumn} = $1`,
      [other],
    );

    const count = Number(rows[0]?.c ?? 0);
    if (count !== 0) {
      throw new Error(
        `assertTenantCannotSeeOtherRows: tenant=${self} saw ${count} row(s) ` +
          `where ${tenantColumn}=${other} in table ${table} ` +
          `(RLS policy is not filtering other tenants)`,
      );
    }

    throw new RollbackSentinel();
  }).catch((err: unknown) => {
    if (err instanceof RollbackSentinel) {
      return;
    }
    throw err;
  });
}

class RollbackSentinel extends Error {
  constructor() {
    super('rollback');
    this.name = 'RollbackSentinel';
  }
}
