import type { Sql } from 'postgres';

/**
 * Assert that a Postgres GUC is unset at the postgres.js wire surface.
 *
 * SQL semantics: `current_setting(name, true)` returns NULL when the GUC is
 * unset in the current session (second arg `true` = missing_ok).
 *
 * postgres.js wire surface: the result row field is the empty string `''`,
 * not NULL. Scenarios asserting `toBeNull()` against this wire value will
 * always fail against a correct implementation (2026-04-16
 * database-foundation escalation).
 *
 * This helper accepts either wire form (`null | ''`) so scenarios encode
 * the wire truth regardless of which surface the driver chooses.
 */
export async function assertUnsetGucIsNullish(
  client: Sql,
  gucName: string,
): Promise<void> {
  const rows = await client`SELECT current_setting(${gucName}, true) AS v`;
  const row = rows[0] as { v: string | null } | undefined;
  if (row === undefined) {
    throw new Error(
      `assertUnsetGucIsNullish: current_setting(${gucName}, true) ` +
        `returned no rows (driver or connection error)`,
    );
  }
  const value = row.v;
  if (value !== null && value !== '') {
    throw new Error(
      `assertUnsetGucIsNullish: current_setting(${gucName}, true) ` +
        `expected null | '', got ${JSON.stringify(value)}`,
    );
  }
}
