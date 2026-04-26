// Universal wire-contract helpers shipped by the framework. Project-
// convention helpers (assertAppSetsTxGuc, assertTenantSeesOwnRows,
// assertTenantCannotSeeOtherRows) moved to project staging in B9a
// v(N+1) and are bootstrapped via `dark-factory contracts init
// postgres`; the orchestrator stages them under
// `_contracts/postgres/project/` so scenarios import from
// `'./_contracts/postgres/project'` rather than this index.
export { assertUnsetGucIsNullish } from './guc.js';
export { assertTxGucRoundTrips } from './tx_guc.js';
export { assertRlsEnabled } from './rls.js';
