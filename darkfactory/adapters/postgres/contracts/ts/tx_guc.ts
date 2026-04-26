import type { Sql } from 'postgres';

class RollbackSentinel extends Error {
  constructor() {
    super('rollback');
    this.name = 'RollbackSentinel';
  }
}

/**
 * DEPRECATION NOTICE — ROADMAP.md Phase 3 B9a.
 *
 * `assertAppSetsTxGuc` (this function — NOT the sibling
 * `assertTxGucRoundTrips` below) is a CONVENTION helper. It assumes
 * the app exposes a `withTenantDb`-style callback shape that issues
 * `SET LOCAL` from inside its own code path. Projects with different
 * tenant-scoping patterns (e.g., JWT-claim-based or SECURITY DEFINER)
 * have nothing to put in the callback. v(N) (current): the framework
 * ships this helper alongside the universal `assertTxGucRoundTrips`
 * for the deprecation window. v(N+1): this function is moved to
 * `templates/contracts/postgres/project-starters/app_tx_guc.ts`
 * exclusively; `assertTxGucRoundTrips` stays here.
 *
 * Maintainers: do not add NEW project-convention helpers to this
 * file. Add them to the starter dir from day one.
 *
 * ---
 *
 * Assert that the application's code path correctly issues a SET LOCAL
 * for the named GUC, by running the app callback inside a tx and
 * reading the GUC back on the same tx before rolling back.
 *
 * Why this shape (callback must invoke a named app symbol):
 *
 *   `SET LOCAL` is transaction-scoped, so any assertion on
 *   `current_setting('app.*')` only carries signal when performed on
 *   the same tx the `SET LOCAL` ran on. The old `assertTxGucEquals`
 *   exposed the tx as a callback argument, which technically lets the
 *   scenario author write raw SQL to set the GUC directly — a
 *   foot-gun that sidesteps the app entirely and assures the test
 *   never observes an app-side regression.
 *
 *   The new shape takes a callback that invokes the app. The scenario
 *   cannot observe the tx directly; the app path is the only way the
 *   GUC gets set. If the app calls `withTenantDb(tenant, async () => ...)`
 *   and that wrapper issues `SET LOCAL`, the callback body looks like
 *   `appCallback: () => withTenantDb(tenant, () => {})` — the tx is
 *   internal to the adapter, and tests that refuse to touch the app
 *   fail loudly at callback level.
 *
 *   Scenarios that need to seed SQL directly (not through an app
 *   symbol) should use `assertTxGucRoundTrips` below, which is the
 *   explicit "I'm testing the GUC plumbing, not the app" primitive.
 *
 * Wire surface note: `current_setting(name, true)` returns NULL at the
 * SQL layer when the GUC is unset; postgres.js surfaces that as the
 * empty string `''`. Strict equality is used — callers asserting
 * "unset" should use `assertUnsetGucIsNullish` instead.
 *
 * The transaction rolls back unconditionally via a sentinel error so
 * the GUC mutation does not leak across scenarios.
 */
export async function assertAppSetsTxGuc(
  client: Sql,
  gucName: string,
  expected: string,
  appCallback: () => Promise<void>,
): Promise<void> {
  // Reject ``expected === ''`` outright. The postgres.js wire surface
  // for an unset GUC is the empty string, so a scenario asserting
  // ``expected=''`` would pass trivially even if the app never
  // issued SET LOCAL — exactly the wrong-signal failure mode this
  // helper was built to prevent. Authors asserting "unset" should
  // use ``assertUnsetGucIsNullish`` (accepts both ``null`` and ``''``
  // without ambiguity). Authors asserting "cleared via RESET LOCAL
  // inside this tx" should pass a distinct sentinel value.
  if (expected === '') {
    throw new Error(
      `assertAppSetsTxGuc: expected='' is ambiguous — '' is also the ` +
        `postgres.js wire form of an unset GUC, so this assertion ` +
        `would pass on an app that never issued SET LOCAL. Use ` +
        `assertUnsetGucIsNullish for "unset", or pass a non-empty ` +
        `sentinel value.`,
    );
  }
  await client.begin(async (tx) => {
    // The app callback must, via its own code path, cause a
    // `set_config(…, …, true)` (i.e. SET LOCAL semantics) on the tx
    // currently open on this connection. The typical wiring is
    // `withTenantDb(tenant, async () => {})` — the wrapper pins the
    // GUC before yielding to the inner callback, and the inner
    // callback can be empty because we only care about the GUC
    // side-effect here.
    //
    // NB: if the app opens its own nested tx (`client.begin(...)`
    // inside the callback), its SET LOCAL pins on that tx, not ours,
    // and the assertion below observes an unset GUC — a real signal
    // that the app's transaction wiring diverges from the
    // "run-in-caller-tx" contract.
    await appCallback();

    const rows = await tx`SELECT current_setting(${gucName}, true) AS v`;
    const row = rows[0] as { v: string | null } | undefined;
    if (row === undefined) {
      throw new Error(
        `assertAppSetsTxGuc: current_setting(${gucName}, true) returned ` +
          `no rows (driver or connection error)`,
      );
    }
    const value = row.v;
    if (value !== expected) {
      throw new Error(
        `assertAppSetsTxGuc: current_setting(${gucName}, true) expected ` +
          `${JSON.stringify(expected)}, got ${JSON.stringify(value)} ` +
          `(reminder: SET LOCAL is tx-local — the app's SET must run on ` +
          `this tx, not on a nested tx it opens itself)`,
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
 * Refuses `value === ''` for the same ambiguity reason as
 * `assertAppSetsTxGuc` — an empty-string assertion cannot be
 * distinguished from "GUC is unset" over the postgres.js wire.
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
