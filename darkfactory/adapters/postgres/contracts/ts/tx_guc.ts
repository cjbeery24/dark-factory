import type { Sql } from 'postgres';

class RollbackSentinel extends Error {
  constructor() {
    super('rollback');
    this.name = 'RollbackSentinel';
  }
}

/**
 * Plumbing-only primitive: round-trip a Postgres tx-local GUC through
 * `set_config($1, $2, true)` and assert it reads back as the same
 * value. Does NOT involve the application — use this when the
 * scenario's purpose is verifying that `current_setting` / `set_config`
 * wire up correctly on this client, not that the app sets the GUC.
 *
 * Why the helper issues `set_config` itself (rather than exposing the
 * tx): the scenario's job is to pass a value in and read it back. The
 * "how is SET LOCAL written" mechanics belong to the primitive, not
 * the scenario — that's where authors reach for
 * ``tx`SET LOCAL app.x = '${val}'`` ``, which Postgres rejects with
 * "could not determine data type of parameter $1". Hiding the SQL
 * shape removes the foot-gun entirely.
 *
 * Refuses `value === ''` because the postgres.js wire surface for an
 * unset GUC is the empty string — an empty-string assertion cannot
 * be distinguished from "GUC is unset". For app-callback-style
 * verification (project convention), see
 * `_contracts/postgres/project/app_tx_guc.ts` (staged from
 * `<project>/.dark-factory/contracts/postgres/`, bootstrapped via
 * `dark-factory contracts init postgres`).
 */
export async function assertTxGucRoundTrips(
  client: Sql,
  gucName: string,
  value: string,
): Promise<void> {
  if (value === '') {
    throw new Error(
      `assertTxGucRoundTrips: value='' is ambiguous — '' is also the ` +
        `postgres.js wire form of an unset GUC. Use a non-empty ` +
        `sentinel value.`,
    );
  }
  await client.begin(async (tx) => {
    // `set_config(name, value, is_local)` is the bindable SQL
    // primitive for SET LOCAL; `is_local=true` makes it tx-scoped.
    // Both `${gucName}` and `${value}` are bound as parameters
    // ($1, $2) — no interpolation happens at the SQL-text layer.
    await tx`SELECT set_config(${gucName}, ${value}, true)`;

    const rows = await tx`SELECT current_setting(${gucName}, true) AS v`;
    const row = rows[0] as { v: string | null } | undefined;
    if (row === undefined) {
      throw new Error(
        `assertTxGucRoundTrips: current_setting(${gucName}, true) ` +
          `returned no rows (driver or connection error)`,
      );
    }
    const readback = row.v;
    if (readback !== value) {
      throw new Error(
        `assertTxGucRoundTrips: round-trip mismatch for ${gucName} — ` +
          `wrote ${JSON.stringify(value)}, read back ` +
          `${JSON.stringify(readback)} (this is a driver-level fault, ` +
          `not an app fault)`,
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
