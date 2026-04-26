import type { Sql } from 'postgres';

class RollbackSentinel extends Error {
  constructor() {
    super('rollback');
    this.name = 'RollbackSentinel';
  }
}

/**
 * PROJECT STARTER — copy this file into your project's
 * `<project>/.dark-factory/contracts/postgres/` directory and edit the
 * conventions below to match your project's actual GUC name and tenant
 * column naming.
 *
 * What's project-specific in this file:
 *
 *   - The literal `'app.tenant_id'` GUC name on lines 51 and 99 is a
 *     project convention. Many projects use `app.tenant_id`; some use
 *     `app.org_id`, `app.account_id`, or another namespaced GUC.
 *     Edit both lines if your project uses a different name.
 *   - The `tenantColumn = 'tenant_id'` default on lines 47 and 95 is
 *     a project convention. Many projects name their tenant-scoping
 *     column `tenant_id`; some use `org_id`, `workspace_id`, etc.
 *     Edit the default (or always pass the column name explicitly
 *     from your scenarios).
 *
 * If your project's RLS scaffolding works differently (e.g., uses a
 * SECURITY DEFINER function instead of `SET LOCAL` + `current_setting`,
 * or tenants are scoped by JWT claim instead of GUC), these starters
 * may need substantial reshaping. The wire-truth assertions are stable
 * (RLS row counting, tenant equality), but the SET LOCAL plumbing is
 * postgres.js + GUC-convention specific.
 *
 * ROADMAP.md Phase 3 B9a: convention helpers move out of the framework
 * over a 2-minor-version deprecation window. v(N) (current): this
 * starter ships under `templates/contracts/postgres/project-starters/`,
 * the framework also ships a live copy under
 * `darkfactory/adapters/postgres/contracts/ts/isolation.ts`. v(N+1):
 * framework deletion; the starter is the only source.
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
  tenantColumn: string = 'tenant_id', // PROJECT CONVENTION — see top-of-file note.
): Promise<void> {
  assertSafeIdentifier('table', table);
  assertSafeIdentifier('tenantColumn', tenantColumn);

  await client.begin(async (tx) => {
    // PROJECT CONVENTION — edit `'app.tenant_id'` to match your GUC name.
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
  tenantColumn: string = 'tenant_id', // PROJECT CONVENTION — see top-of-file note.
): Promise<void> {
  assertSafeIdentifier('table', table);
  assertSafeIdentifier('tenantColumn', tenantColumn);

  await client.begin(async (tx) => {
    // PROJECT CONVENTION — edit `'app.tenant_id'` to match your GUC name.
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
