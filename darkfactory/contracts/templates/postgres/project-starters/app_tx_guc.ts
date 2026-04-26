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
 * conventions below to match your project's actual GUC names and tx
 * wiring.
 *
 * Why this is a starter, not a framework helper: the SHAPE of the
 * assertion ("the app issues SET LOCAL via a wrapper") is universal,
 * but the GUC NAME (`app.tenant_id` vs `app.org_id` vs whatever your
 * project chose) and the wrapper SHAPE (whether you have a
 * `withTenantDb(tenant, fn)` wrapper or a different convention) are
 * project-specific. ROADMAP.md Phase 3 B9a moves these out of the
 * framework over a 2-minor-version deprecation window so changing
 * `app.tenant_id` → `app.org_id` does not require a framework release.
 *
 * v(N) (current): this starter ships under
 * `templates/contracts/postgres/project-starters/`. The framework also
 * ships a live copy under `darkfactory/adapters/postgres/contracts/ts/tx_guc.ts`
 * for the deprecation window. v(N+1): framework deletion; the starter
 * is the only source.
 *
 * Assert that the application's code path correctly issues a SET LOCAL
 * for the named GUC, by running the app callback inside a tx and
 * reading the GUC back on the same tx before rolling back.
 *
 * Why this shape (callback must invoke a named app symbol):
 *
 *   `SET LOCAL` is transaction-scoped, so any assertion on
 *   `current_setting('app.*')` only carries signal when performed on
 *   the same tx the `SET LOCAL` ran on. Exposing the tx as a callback
 *   argument would let the scenario author write raw SQL to set the
 *   GUC directly — a foot-gun that sidesteps the app entirely and
 *   ensures the test never observes an app-side regression.
 *
 *   The shape below takes a callback that invokes the app. The
 *   scenario cannot observe the tx directly; the app path is the only
 *   way the GUC gets set. If the app calls
 *   `withTenantDb(tenant, async () => ...)` and that wrapper issues
 *   `SET LOCAL`, the callback body looks like
 *   `appCallback: () => withTenantDb(tenant, () => {})` — the tx is
 *   internal to the adapter, and tests that refuse to touch the app
 *   fail loudly at callback level.
 *
 *   Scenarios that need to seed SQL directly (not through an app
 *   symbol) should use `assertTxGucRoundTrips` from
 *   `_contracts/postgres/tx_guc.ts` (the framework helper), which is
 *   the explicit "I'm testing the GUC plumbing, not the app" primitive.
 *
 * Wire surface note: `current_setting(name, true)` returns NULL at the
 * SQL layer when the GUC is unset; postgres.js surfaces that as the
 * empty string `''`. Strict equality is used — callers asserting
 * "unset" should use `assertUnsetGucIsNullish` (framework helper)
 * instead.
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
  // use ``assertUnsetGucIsNullish`` (framework — accepts both ``null``
  // and ``''`` without ambiguity). Authors asserting "cleared via
  // RESET LOCAL inside this tx" should pass a distinct sentinel value.
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
