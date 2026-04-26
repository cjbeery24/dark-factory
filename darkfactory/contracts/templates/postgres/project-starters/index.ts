// PROJECT STARTER — re-exports the convention helpers so scenarios
// can import from the staging root:
//
//   import { assertAppSetsTxGuc } from
//     '../_contracts/postgres/project/index.js';
//
// Note the trailing `.js` on the import — the staging tree is emitted
// as `.ts` source but TypeScript's `moduleResolution: "node16"` /
// `"nodenext"` requires the `.js` suffix on relative imports.
//
// The framework helpers (assertUnsetGucIsNullish, assertTxGucRoundTrips,
// assertRlsEnabled) live ONE LEVEL UP at
// `../_contracts/postgres/index.js` — separate path keeps the
// framework/project namespace boundary visible at every import site.

export { assertAppSetsTxGuc } from './app_tx_guc.js';
export {
  assertTenantSeesOwnRows,
  assertTenantCannotSeeOtherRows,
} from './isolation.js';
