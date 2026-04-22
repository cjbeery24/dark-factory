import type { Sql } from 'postgres';

class RollbackSentinel extends Error {
  constructor() {
    super('rollback');
    this.name = 'RollbackSentinel';
  }
}

/**
 * Assert that a transaction-local Postgres GUC has the expected value,
 * read back inside the same transaction where it was set.
 *
 * `SET LOCAL` is transaction-scoped (Postgres docs: "the new value will
 * be in effect only until the current transaction"). A peer client
 * asking `current_setting` on its own connection sees nothing — the
 * classification-hint pattern `POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION`
 * catches tests that got this wrong by opening a second connection to
 * read back what the app just set. The only valid pattern is to read
 * inside the same tx the value was set on.
 *
 * `setupWithinTx` receives the same `tx` the assertion reads from.
 * Either call the app's code path that issues `SET LOCAL`, or issue
 * it directly from the test (e.g. ``tx`SELECT set_config(...)` ``).
 *
 * Wire surface note: `current_setting(name, true)` returns NULL at the
 * SQL layer when the GUC is unset, but postgres.js surfaces that as
 * the empty string `''`. The comparison is strict equality against
 * `expected` — callers expecting "unset" should use
 * `assertUnsetGucIsNullish`, which accepts both wire forms.
 *
 * The transaction rolls back unconditionally via a sentinel error so
 * the GUC mutation does not leak across scenarios.
 */
export async function assertTxGucEquals(
  client: Sql,
  gucName: string,
  expected: string,
  setupWithinTx: (tx: Sql) => Promise<void>,
): Promise<void> {
  // Reject ``expected === ''`` outright. The postgres.js wire surface
  // for an unset GUC is the empty string, so a scenario that wrote
  // ``assertTxGucEquals(..., '')`` would pass trivially even if the
  // app never issued ``SET LOCAL`` — exactly the wrong-signal failure
  // mode this helper was built to prevent. Authors asserting "unset"
  // should use ``assertUnsetGucIsNullish`` (accepts both ``null`` and
  // ``''`` without ambiguity). Authors asserting "cleared via RESET
  // LOCAL inside this tx" should pass a distinct sentinel value.
  if (expected === '') {
    throw new Error(
      `assertTxGucEquals: expected='' is ambiguous — '' is also the ` +
        `postgres.js wire form of an unset GUC, so this assertion ` +
        `would pass on an app that never issued SET LOCAL. Use ` +
        `assertUnsetGucIsNullish for "unset", or pass a non-empty ` +
        `sentinel value.`,
    );
  }
  await client.begin(async (tx) => {
    await setupWithinTx(tx);

    const rows = await tx`SELECT current_setting(${gucName}, true) AS v`;
    // ``v`` is typed ``string | null`` defensively, but the postgres.js
    // wire surface returns ``''`` (not ``null``) for an unset GUC —
    // see ``guc.ts`` for the documented precedent. The ``null`` arm is
    // retained so a future driver change that surfaces SQL NULL as JS
    // null still fails the strict-equality check below instead of
    // tripping an ``undefined``-property access.
    const row = rows[0] as { v: string | null } | undefined;
    if (row === undefined) {
      throw new Error(
        `assertTxGucEquals: current_setting(${gucName}, true) returned ` +
          `no rows (driver or connection error)`,
      );
    }
    const value = row.v;
    if (value !== expected) {
      throw new Error(
        `assertTxGucEquals: current_setting(${gucName}, true) expected ` +
          `${JSON.stringify(expected)}, got ${JSON.stringify(value)} ` +
          `(reminder: SET LOCAL is tx-local — a peer connection ` +
          `reading this GUC will see null/'' regardless of impl ` +
          `correctness; assertions must run on the same tx as the SET)`,
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
