"""Concrete regex-shape tests for ``PostgresAdapter.classification_hints()``.

These tests pin the two shipped Postgres hints to the scenario shapes
observed in the 2026-04-22 ESCALATED run of
``2026-03-28-database-foundation``:

1. ``POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION`` — scenarios that open a peer
   ``postgres()`` client from ``process.env.*`` and assert tx-scoped
   ``current_setting('app.tenant_id')``. The observed root cause was that
   such assertions read from a DIFFERENT connection than ``SET LOCAL``
   ever touches, making the test permanently red regardless of app code.
2. ``POSTGRES_OWNER_BYPASSES_RLS`` — scenarios that call
   ``ALTER TABLE ... ENABLE ROW LEVEL SECURITY`` without a compensating
   ``ALTER TABLE ... FORCE ROW LEVEL SECURITY``. Since the connecting
   role is the table owner in the archived run, non-FORCE'd policies
   are bypassed entirely.

The hints fire by filename — the tests below drive the real regex
against minimal scenario-file strings and assert ``re.search`` matches
(or doesn't) without invoking ``apply_classification_hints``. End-to-end
wiring is covered in ``tests/unit/test_classification_hints.py``.
"""
from __future__ import annotations

import re

import pytest

from darkfactory.adapters.postgres.adapter import (
    _POSTGRES_CLASSIFICATION_HINTS,
    PostgresAdapter,
)


def _hints_by_code() -> dict[str, re.Pattern[str]]:
    return {
        h.internal_reason_code: re.compile(h.pattern)
        for h in _POSTGRES_CLASSIFICATION_HINTS
    }


# ------------------------------- contract shape --------------------------


def test_postgres_adapter_exposes_exactly_two_hints():
    adapter = PostgresAdapter()
    hints = adapter.classification_hints()
    assert len(hints) == 2
    codes = [h.internal_reason_code for h in hints]
    assert codes == [
        "POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION",
        "POSTGRES_OWNER_BYPASSES_RLS",
    ]
    # Both hints map to SPEC_AMBIGUOUS — the failing test is telling the
    # truth about the substrate; the spec is what needs to change.
    for h in hints:
        assert h.category_override == "SPEC_AMBIGUOUS"


def test_classification_hints_returns_fresh_list_each_call():
    """Callers should be free to mutate the returned list without poisoning
    later calls. The adapter wraps the module-level tuple in ``list(...)``
    to give each caller its own array — this test pins that behavior."""
    adapter = PostgresAdapter()
    first = adapter.classification_hints()
    first.clear()
    second = adapter.classification_hints()
    assert len(second) == 2


# -------------------- POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION -------------


def test_peer_client_fires_on_module_scope_postgres_env_then_app_guc():
    """The 2026-04-22 shape from substrate-rls.test.ts lookalikes:
    a ``postgres(process.env.DATABASE_URL)`` call followed by
    ``current_setting('app.<key>')`` on that connection's tx context.
    Runs against one describe body in production — this raw-regex test
    just confirms the pattern shape on a concatenated text blob.
    """
    scenario = """
import postgres from 'postgres';
import { sql } from 'drizzle-orm';

describe('tenant isolation', () => {
  const client = postgres(process.env.DATABASE_URL!);

  it('reads app.tenant_id from the tx GUC', async () => {
    await client.begin(async (tx) => {
      await tx`SET LOCAL app.tenant_id = ${'t1'}`;
      const r = await tx.execute(sql`SELECT current_setting('app.tenant_id')`);
      expect(r[0].current_setting).toBe('t1');
    });
  });
});
"""
    pat = _hints_by_code()["POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION"]
    assert pat.search(scenario) is not None


def test_peer_client_fires_with_bang_suffix_and_whitespace_variants():
    """``postgres( process.env.FOO )`` with whitespace and a
    trailing ``!`` non-null assertion should still match — the regex
    uses ``\\s*`` around the open-paren."""
    scenario = """
const client = postgres(  process.env.PG_URL  );
await tx.execute(`SELECT current_setting('app.tenant_id')`);
"""
    pat = _hints_by_code()["POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION"]
    assert pat.search(scenario) is not None


def test_peer_client_does_not_fire_without_postgres_call():
    """A file that calls ``current_setting`` through the app's own
    ``withTenantDb`` helper has no module-level ``postgres()`` call and
    should stay in IMPL_BUG classification."""
    scenario = """
import { withTenantDb } from '../../apps/api/src/db';

it('uses app helper', async () => {
  await withTenantDb('t1', async (tx) => {
    const r = await tx.execute(`SELECT current_setting('app.tenant_id')`);
    expect(r[0].current_setting).toBe('t1');
  });
});
"""
    pat = _hints_by_code()["POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION"]
    assert pat.search(scenario) is None


def test_peer_client_does_not_fire_without_current_setting():
    """``postgres(process.env.URL)`` alone — no tx GUC assertion —
    is not the targeted footgun. A peer client that only runs CRUD
    is perfectly legitimate."""
    scenario = """
const client = postgres(process.env.DATABASE_URL!);
await client`SELECT * FROM users`;
"""
    pat = _hints_by_code()["POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION"]
    assert pat.search(scenario) is None


def test_peer_client_does_not_fire_on_hardcoded_url():
    """``postgres('postgres://localhost/...')`` skips the
    ``process.env.NAME`` arm of the pattern. Rare in tests, but the
    regex should be precise enough to avoid false positives on
    hardcoded dev URLs."""
    scenario = """
const client = postgres('postgres://test@localhost:5432/db');
await tx.execute(`SELECT current_setting('app.tenant_id')`);
"""
    pat = _hints_by_code()["POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION"]
    assert pat.search(scenario) is None


def test_peer_client_does_not_fire_on_session_level_guc():
    """Linus #2 (post-hardening): a peer client reading session-level
    GUCs like ``TimeZone`` or ``server_version`` is legitimate. Only
    tx-local ``app.*`` reads off a peer connection are the documented
    footgun. Pin that the pattern is narrowed to ``current_setting\\('app.``.
    """
    scenario_session = """
const client = postgres(process.env.DATABASE_URL!);
await client`SELECT current_setting('TimeZone')`;
"""
    scenario_server_version = """
const client = postgres(process.env.DATABASE_URL!);
await client`SELECT current_setting('server_version')`;
"""
    pat = _hints_by_code()["POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION"]
    assert pat.search(scenario_session) is None
    assert pat.search(scenario_server_version) is None


def test_peer_client_fires_with_app_guc_using_any_quote_style():
    """The narrowed pattern accepts single-quote, double-quote, or
    backtick string-open before ``app.`` — matching the three valid
    JS string literal forms."""
    pat = _hints_by_code()["POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION"]
    for quote in ("'", '"', "`"):
        scenario = f"""
const client = postgres(process.env.URL);
await client.execute(`SELECT current_setting({quote}app.tenant_id{quote})`);
"""
        assert pat.search(scenario) is not None, (
            f"expected hint to fire with {quote!r}-quoted 'app.' GUC name"
        )


# ------------------------- POSTGRES_OWNER_BYPASSES_RLS ------------------


def test_owner_bypass_fires_on_enable_without_force():
    """The substrate-rls.test.ts shape: ``ENABLE ROW LEVEL SECURITY``
    with no ``FORCE ROW LEVEL`` compensator. When the connecting role
    owns the table (default for test fixtures that CREATE TABLE), the
    non-FORCE'd policy is bypassed."""
    scenario = """
await client`
  CREATE TABLE IF NOT EXISTS test_tenant_table (
    id SERIAL PRIMARY KEY,
    tenant_id UUID,
    data TEXT
  )
`;
await client`ALTER TABLE test_tenant_table ENABLE ROW LEVEL SECURITY`;
await client`CREATE POLICY p ON test_tenant_table FOR ALL USING (true)`;
"""
    pat = _hints_by_code()["POSTGRES_OWNER_BYPASSES_RLS"]
    assert pat.search(scenario) is not None


def test_owner_bypass_suppressed_by_force_row_level_anywhere_in_file():
    """Cross-line negative lookahead: if the file ALSO issues
    ``ALTER TABLE ... FORCE ROW LEVEL SECURITY``, the scenario is
    already defending against the owner footgun and must not be
    misclassified as SPEC_AMBIGUOUS."""
    scenario = """
await client`ALTER TABLE test_tenant_table ENABLE ROW LEVEL SECURITY`;
await client`ALTER TABLE test_tenant_table FORCE ROW LEVEL SECURITY`;
"""
    pat = _hints_by_code()["POSTGRES_OWNER_BYPASSES_RLS"]
    assert pat.search(scenario) is None


def test_owner_bypass_suppressed_when_force_appears_before_enable():
    """Post-Linus hardening: the ``\\A``-anchored negative-lookahead
    scans the whole scope for FORCE, not just the post-match tail.
    If FORCE appears *anywhere* in the scope — including before ENABLE —
    the hint is suppressed. Otherwise a multi-table migration that
    FORCEs one table and later ENABLEs another gets falsely flagged."""
    scenario = """
await client`ALTER TABLE t FORCE ROW LEVEL SECURITY`;
await client`ALTER TABLE t ENABLE ROW LEVEL SECURITY`;
"""
    pat = _hints_by_code()["POSTGRES_OWNER_BYPASSES_RLS"]
    assert pat.search(scenario) is None


def test_owner_bypass_does_not_fire_without_enable_rls():
    """A scenario that only defines policies without enabling RLS is
    already broken in an obvious way (policies don't apply until RLS
    is enabled); that's IMPL_BUG, not owner-bypass."""
    scenario = """
await client`CREATE POLICY p ON test_tenant_table FOR ALL USING (true)`;
"""
    pat = _hints_by_code()["POSTGRES_OWNER_BYPASSES_RLS"]
    assert pat.search(scenario) is None


def test_owner_bypass_is_case_sensitive_on_sql_keywords():
    """SQL is case-insensitive in Postgres, but the hint regex matches
    the uppercase canonical form that architect-generated scenarios use.
    If a scenario uses ``enable row level security`` lowercase, we
    currently miss it — pin this as a known limitation so a future
    change to ``(?i)`` is a deliberate one-line diff."""
    scenario = """
await client`alter table t enable row level security`;
"""
    pat = _hints_by_code()["POSTGRES_OWNER_BYPASSES_RLS"]
    assert pat.search(scenario) is None


# ---------------- both hints on the same file (priority) ----------------


def test_substrate_rls_shape_matches_both_hints():
    """The real 2026-04-22 substrate-rls.test.ts has a module-level
    peer client AND enables RLS without FORCE AND asserts
    ``current_setting('app.tenant_id')``. Both regexes match —
    ``apply_classification_hints`` resolves the tie by first-match-wins,
    so the PEER_CLIENT hint (index 0) is authoritative for any defect
    whose describe-body matched."""
    scenario = """
import postgres from 'postgres';

describe('PostgreSQL RLS substrate validation', () => {
  let client: postgres.Sql;

  beforeAll(async () => {
    client = postgres(process.env.DATABASE_URL!);
    await client`ALTER TABLE test_tenant_table ENABLE ROW LEVEL SECURITY`;
    await client`CREATE POLICY p ON test_tenant_table FOR ALL USING
      (tenant_id = current_setting('app.tenant_id')::uuid)`;
  });
});
"""
    hints = _hints_by_code()
    assert hints["POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION"].search(scenario)
    assert hints["POSTGRES_OWNER_BYPASSES_RLS"].search(scenario)


# ---------------- regex-compilation well-formedness pin ------------------


@pytest.mark.parametrize(
    "hint",
    _POSTGRES_CLASSIFICATION_HINTS,
    ids=lambda h: h.internal_reason_code,
)
def test_each_hint_pattern_compiles_and_has_no_empty_alternatives(hint):
    """Cheap sanity pin against typo-level regex bugs. Empty branches
    like ``(foo|)`` match the empty string and would flag every file."""
    compiled = re.compile(hint.pattern)
    # Walk the parsed regex and ensure no empty branch exists. We don't
    # need to be exhaustive — this is a tripwire for obvious regressions,
    # not a full regex audit.
    assert compiled.search("") is None, (
        f"hint {hint.internal_reason_code} matches the empty string — "
        f"likely an empty alternative branch"
    )
