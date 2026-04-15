<!--
A3 ships this file as a minimal initial surface so the registry has
something to inject. Real Postgres architect guidance (schema setup,
migration mode, connection-string conventions) lives in
`agents/architect.md` under DATABASE SCHEMA SETUP and migrates here
under A4, which ships with the equivalence-harness validation the
plan requires. Removing A4's content from `architect.md` before that
harness can prove no manifest regression would skip A4's acceptance
criterion — so until A4 lands, the real guidance is still at the
architect prompt, not here.

Content below is intentionally minimal. Do not expand it without
A4's equivalence harness; adding content here silently extends a
prompt surface that no harness validates.

Governance cap: 80 non-blank lines. Enforced by the contract harness
(`test_architect_addendum_within_governance_line_cap`).
-->

## Postgres addendum

This adapter is active because the project has Postgres dialect
signals (SQL files, a `postgres://`/`postgresql://` connection
string, or `postgres`/`postgresql` mentioned in a docker-compose
service). Real architect guidance for this adapter migrates here in
A4; for A3 the content is intentionally minimal.
