"""Canonical-violation fixtures for the postgres adapter's universal
wire-contract helpers. ROADMAP.md Phase 3 B9b — foundation commit.

Each :class:`Fixture` declares: the helper-name it's the canonical
violation for, the model version the violation was last verified
against, the wrong-code shape (a TS snippet representing the foot-gun
the helper exists to catch), an optional regex pattern that
distinguishes the wrong-code from a correct version, and a positive-
control correct counter-sample.

The auto-eval runner (B9b follow-up, not shipped in the foundation
commit) consumes these fixtures: on a model bump, it runs a vanilla
prompt with the helper absent and checks the model's output against
the fixture's distinguishers across N rounds. If N/N rounds miss the
foot-gun pattern AND import the helper-named primitive (for
helper-import-tracked fixtures), the runner auto-opens a removal PR.

Why these fixtures live in a top-level Python module rather than in
``contracts/ts/`` next to the helper sources: the fixtures are
harness-only inputs. No agent container reads them at runtime; they
are not staged into ``_contracts/`` for the Test Engineer. Keeping
them in Python avoids polluting the TS-tool-traversed
``contracts/ts/`` tree and lets the test suite consume the fixtures
without an importlib-from-non-package dance.

Authorization-model triggers stay inert: this module lives in the
framework package, not under ``.dark-factory/`` or any barrier-
restricted directory. No ``barriers.py`` /
``scripts/quality-gate.sh`` report row / ``scripts/lib/scenario_lint.py``
/ ``.dark-factory/`` artifact is touched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Fixture:
    """One canonical-violation fixture per universal helper.

    ``foot_gun_pattern`` is ``None`` for helpers whose foot-gun is the
    *absence* of a check rather than the presence of a wrong call —
    pattern-matching for absence at the regex level produces fragile
    expressions. The test suite handles ``None`` by relying on
    helper-import discipline alone (wrong-code does not import the
    helper; correct-code does).

    ``correct_code`` is the positive control. Without it, an
    over-greedy regex in ``foot_gun_pattern`` could fire on both wrong
    and correct snippets and the test would still pass against
    ``wrong_code`` only — silent regression. Pinning both sides closes
    that gap.
    """

    helper_name: str
    retire_after: str
    wrong_code: str
    correct_code: str
    foot_gun_pattern: re.Pattern[str] | None = None


# ``assertUnsetGucIsNullish`` foot-gun: scenarios that assert
# ``.toBeNull()`` (or ``=== null``) directly on
# ``current_setting('app.x', true)`` results. The postgres.js wire
# surface returns the empty string ``''`` for an unset GUC, not
# ``null``, so the assertion always fails against a correct
# implementation (2026-04-16 database-foundation escalation).
_UNSET_GUC_TOBE_NULL_RE = re.compile(
    r"current_setting\s*\([^)]*\)[\s\S]{0,400}?\.toBeNull\s*\(\s*\)",
    re.IGNORECASE,
)

_UNSET_GUC_FIXTURE = Fixture(
    helper_name="assertUnsetGucIsNullish",
    retire_after="claude-opus-4-7",
    wrong_code='''\
import { describe, it, expect } from 'vitest';

describe('tenant context', () => {
  it('starts unset before the app issues SET LOCAL', async () => {
    const rows = await client`SELECT current_setting('app.tenant_id', true) AS v`;
    expect(rows[0]?.v).toBeNull();
  });
});
''',
    correct_code='''\
import { describe, it } from 'vitest';
import { assertUnsetGucIsNullish } from '../_contracts/postgres';

describe('tenant context', () => {
  it('starts unset before the app issues SET LOCAL', async () => {
    await assertUnsetGucIsNullish(client, 'app.tenant_id');
  });
});
''',
    foot_gun_pattern=_UNSET_GUC_TOBE_NULL_RE,
)


# ``assertTxGucRoundTrips`` foot-gun: ``tx`SET LOCAL app.x = '${val}'``
# tagged-template literals. Postgres rejects parameterized GUC values
# at the SET LOCAL position with "could not determine data type of
# parameter $1"; the bindable form is
# ``SELECT set_config(name, value, true)``.
#
# B5 (2026-04-25): pattern imported from the shared library at
# ``darkfactory.contracts.foot_gun_patterns``. The cross-module
# byte-equality test that the foundation commit installed (``Test
# PatternMatchesScenarioLint``) became object-identity now that
# scenario_lint and this fixture both reference the SAME compiled
# pattern instance — the prior B9b foundation review's "no import-
# time coupling" decision is reversed: the library IS the import
# layer the foundation review didn't have available. The test was
# updated to match.
from darkfactory.contracts.foot_gun_patterns import (
    BY_NAME_AND_DIALECT as _FOOT_GUN_PATTERNS_BY_NAME_AND_DIALECT,
)

_PARAMETERIZED_SET_LOCAL_RE = _FOOT_GUN_PATTERNS_BY_NAME_AND_DIALECT[
    ("parameterized-set-local", "ts")
].pattern

_TX_GUC_FIXTURE = Fixture(
    helper_name="assertTxGucRoundTrips",
    retire_after="claude-opus-4-7",
    wrong_code='''\
import { describe, it, expect } from 'vitest';

describe('tenant context', () => {
  it('round-trips a tenant id via SET LOCAL', async () => {
    const tenantId = 'acme';
    await client.begin(async (tx) => {
      await tx`SET LOCAL app.tenant_id = '${tenantId}'`;
      const rows = await tx`SELECT current_setting('app.tenant_id', true) AS v`;
      expect(rows[0]?.v).toBe(tenantId);
    });
  });
});
''',
    correct_code='''\
import { describe, it } from 'vitest';
import { assertTxGucRoundTrips } from '../_contracts/postgres';

describe('tenant context', () => {
  it('round-trips a tenant id', async () => {
    await assertTxGucRoundTrips(client, 'app.tenant_id', 'acme');
  });
});
''',
    foot_gun_pattern=_PARAMETERIZED_SET_LOCAL_RE,
)


# ``assertRlsEnabled`` foot-gun: scenarios that "test" RLS by
# observing application-layer row counts on the protected table
# without ever asserting that the RLS subsystem itself is enabled
# (``pg_tables.rowsecurity = true`` AND ``pg_policies`` count >= 1).
# A stub implementation that returns the right rows but has no RLS
# enforcement passes the wrong-code's row-count assertion trivially
# (2026-04-15 substrate-thin baseline escalation).
#
# Honest design limit: the foot-gun is the *absence* of pg_tables /
# pg_policies references in an RLS-context test, which a single regex
# cannot robustly express (negative-lookahead across an arbitrary
# describe-block body produces fragile patterns). For the foundation
# commit, ``foot_gun_pattern = None`` and the test relies on
# helper-import discipline alone — wrong-code does not import
# ``assertRlsEnabled``; correct-code does. The auto-eval runner
# (B9b follow-up) can still drive the canonical comparison: if a
# vanilla-prompted model writes scenarios that import
# ``assertRlsEnabled`` 0 / N times AND the same scenarios contain
# ``pg_tables`` / ``pg_policies`` references in N / N rounds, the
# helper is retire-able. The fixture ships the wrong/correct code as
# canonical reference data for that comparison.
_RLS_FIXTURE = Fixture(
    helper_name="assertRlsEnabled",
    retire_after="claude-opus-4-7",
    wrong_code='''\
import { describe, it, expect } from 'vitest';

describe('RLS isolation on users table', () => {
  it('prevents cross-tenant reads', async () => {
    // Foot-gun: row counts as proof of RLS. A stub implementation
    // that returns the right rows without enabling RLS passes this
    // assertion trivially.
    const rows = await client`SELECT * FROM users WHERE tenant_id = 'a'`;
    expect(rows.length).toBeGreaterThan(0);
  });
});
''',
    correct_code='''\
import { describe, it } from 'vitest';
import { assertRlsEnabled } from '../_contracts/postgres';

describe('RLS isolation on users table', () => {
  it('has RLS enabled with at least one policy attached', async () => {
    await assertRlsEnabled(client, 'public.users');
  });
});
''',
    foot_gun_pattern=None,
)


# Stable ordering by helper name keeps test reports reviewer-stable
# across Python's dict-insertion-order behavior shifts and across
# additions of new helpers in the future.
FIXTURES: tuple[Fixture, ...] = (
    _RLS_FIXTURE,
    _TX_GUC_FIXTURE,
    _UNSET_GUC_FIXTURE,
)


# Lookup-by-helper-name index, derived from ``FIXTURES``. The auto-
# eval runner (``retire_after_runner.py``) needs O(1) lookup at every
# round; ship the index from this module rather than letting the
# runner derive its own inline ``{f.helper_name: f for f in FIXTURES}``
# (Linus B9b 3b review). Parallels
# ``retire_after_trigger_prompts.BY_HELPER_NAME``. The ``tuple`` is
# the source of truth; ordering and membership flow from there into
# the dict.
BY_HELPER_NAME: dict[str, Fixture] = {f.helper_name: f for f in FIXTURES}
