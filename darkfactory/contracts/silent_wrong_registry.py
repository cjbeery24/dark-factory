"""Registry of silent-wrong-helper guard assertions for
``dark-factory contracts verify <adapter>`` (Phase 4 Contracts CLI v1).

Per neo's v1 design verdict (option D, this session): each silent-
wrong helper is paired with a small set of source-text invariants
that prove its safety guard is in place. The verifier walks the
project's helper files and applies these invariants. A developer
who edits a helper to remove its guard ships a silently-broken
helper that the next pipeline run would accept; v1 catches that
helper-source-regression-at-edit-time hole.

Two registries:

* :data:`GUARDS` — per-helper "must contain" patterns. Each helper
  has 0 or more :class:`HelperGuard` entries; the verifier checks
  the file declaring the helper for each pattern. Patterns are raw
  regex (not B5 library references) because the guards are helper-
  internal implementation details, not foot-gun shapes.

* :data:`FOOT_GUN_BANS` — per-adapter "must NOT contain" pattern
  references into the B5 library. Applied across ALL helper-file
  source text in the adapter's contracts dir. Single-source-of-truth
  preserved: the verifier looks up the actual regex via
  :data:`darkfactory.contracts.foot_gun_patterns.BY_NAME_AND_DIALECT`
  at runtime.

**Helper presence is not enforced.** A project that removes a
silent-wrong helper isn't penalized — the verifier checks helpers
that ARE present, not whether the registry's full list is. This
matches the v0 init story (developers customize project-side
helpers, including removal).

**Loud-wrong helpers have no entries here.** A helper that crashes
visibly when misused doesn't need a verifier — the test author hits
the crash on their first run. v1 covers silent-wrong helpers
exclusively.

**Verifier scoping rules** (Linus v1 review #1, #3 — pinned here so
the contract is data, not English):

* Helper-file discovery uses :data:`HELPER_DECL_RE` to find the file
  declaring ``export async function <helper_name>``. Helpers that
  lack this exact declaration shape (e.g., named expressions, arrow
  functions) are treated as not-present and skipped.

* :class:`HelperGuard` patterns are scanned against the WHOLE FILE
  source containing the matching helper export, not the function
  body. When a single file declares multiple helpers (e.g.,
  ``isolation.ts`` ships both ``assertTenantSeesOwnRows`` and
  ``assertTenantCannotSeeOtherRows``), a guard match anywhere in
  the file satisfies BOTH helpers' guards if their patterns
  coincide. v1 accepts this scope coarseness for the postgres
  starter set; per-function-body scanning is a v2 concern. Each
  guard regex SHOULD be anchored to a syntactic feature (e.g.,
  ``if\\s*\\(``, ``await\\s+``) so a stray comment containing the
  guard text doesn't false-pass.

* :class:`HelperFootGunBan` patterns are scanned PER FILE (not
  across the concatenated adapter dir). Some B5 patterns (notably
  ``peer-client-guc-read``) use ``[\\s\\S]*?`` cross-line spans;
  in a multi-function helper file, that span can falsely fire on
  two unrelated function bodies. Accepted false-positive risk for
  v1; if it bites in practice, refine to per-export-block scan in
  v2.

**Authorization model** (AUTHORIZATION-MODEL.md):

Trigger #1 (audience escalation): NO. Registry is host-side data
consumed by the verifier CLI. No string crosses any agent boundary.

Trigger #2 (changed-surfaces declaration): NO. ``barriers.py``,
``quality-gate.sh``, ``scenario_lint.py`` untouched. No new
``.dark-factory/`` artifact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


Dialect = Literal["ts", "sql", "py"]


# Helper-file-discovery contract (Linus v1 review #1): the verifier
# locates a helper by searching the adapter's contracts dir for files
# whose source contains a matching ``export async function <name>``
# declaration. Lifted to a module constant so the verifier and tests
# both consume the SAME shape — encoding the rule in code rather than
# leaving it in prose.
#
# ``{name}`` is a substitution sentinel; callers do
# ``HELPER_DECL_RE.format(name=re.escape(helper_name))`` to build the
# concrete regex. The substitution + escape is tested.
HELPER_DECL_RE: str = r"export\s+async\s+function\s+{name}\b"


@dataclass(frozen=True)
class HelperGuard:
    """A regex the helper's source MUST match.

    Frozen so the registry can't be mutated at runtime — the
    verifier consults it as data, not as configuration.

    ``helper_name`` is the camelCase TS export name (e.g.,
    ``assertAppSetsTxGuc``). The verifier locates the file declaring
    this export, then runs ``pattern`` against that file's source.
    """

    helper_name: str
    pattern: re.Pattern[str]
    reason: str


@dataclass(frozen=True)
class HelperFootGunBan:
    """A B5 foot-gun pattern that MUST NOT appear in any helper-file
    source within the adapter's contracts dir.

    References the B5 library by ``(name, dialect)`` rather than
    duplicating the regex inline — single-source-of-truth, the
    verifier looks up the compiled pattern at runtime.

    ``reason`` is a human-readable explanation surfaced when the ban
    fires. Keep it short; the file name + foot-gun name are surfaced
    separately by the verifier.
    """

    b5_name: str
    b5_dialect: Dialect
    reason: str


# ---------------------------------------------------------------------
# Postgres adapter — three project-side silent-wrong helpers.
# ---------------------------------------------------------------------


# ``expected === ''`` guard: rejects callers passing the empty string
# as the asserted value, because the postgres.js wire surface returns
# ``''`` for an unset GUC. Without this guard, ``expected=''`` passes
# trivially even if the app never issued SET LOCAL — exactly the
# wrong-signal failure the helper exists to prevent.
#
# Anchored on ``if (...)`` so a stray comment containing
# ``expected === ''`` doesn't false-pass (Linus v1 review #2).
# Tolerant of either quote style.
_APP_TX_GUC_EXPECTED_EMPTY_GUARD = re.compile(
    r"if\s*\(\s*expected\s*===\s*(?:''|\"\")\s*\)\s*\{"
)


# ``await client.begin(async (`` wrapper: every helper that depends
# on tx-locality MUST run inside ``client.begin``. A peer-client read
# (separate connection) would always see ``''`` regardless of impl
# correctness — the wrong-scope oracle the B5 ``peer-client-guc-read``
# foot-gun catches at scenario time.
#
# Anchored on ``await `` prefix so a stray comment or string literal
# containing ``client.begin(async (`` doesn't false-pass (Linus v1
# review #2). The actual call site form is always preceded by
# ``await``.
_CLIENT_BEGIN_ASYNC = re.compile(
    r"\bawait\s+client\.begin\s*\(\s*async\s*\("
)


# ``reason`` strings are formatted as "headline\n\nbody" — the CLI
# handler splits on ``\n\n`` to surface the headline as a one-line
# violation prefix and the body as an indented detail block (Linus
# v1 review). Author the headline as a single sentence; the body
# explains the corrective action.


GUARDS: dict[str, list[HelperGuard]] = {
    "postgres": [
        HelperGuard(
            helper_name="assertAppSetsTxGuc",
            pattern=_APP_TX_GUC_EXPECTED_EMPTY_GUARD,
            reason=(
                "assertAppSetsTxGuc must guard `expected === ''` "
                "— the postgres.js wire surface returns the empty "
                "string for an unset GUC, so accepting `expected=''` "
                "lets a broken app pass trivially.\n\n"
                "The starter ships this guard at the top of the "
                "function body; if you removed it, restore it or use "
                "`assertUnsetGucIsNullish` for unset-GUC assertions."
            ),
        ),
        HelperGuard(
            helper_name="assertTenantSeesOwnRows",
            pattern=_CLIENT_BEGIN_ASYNC,
            reason=(
                "assertTenantSeesOwnRows must run inside "
                "`client.begin(async (...))`.\n\n"
                "`SET LOCAL` is tx-scoped, so a peer-connection read "
                "returns '' regardless of impl correctness. The "
                "starter ships the wrap; if you replaced it with a "
                "peer client, restore the same-tx shape."
            ),
        ),
        HelperGuard(
            helper_name="assertTenantCannotSeeOtherRows",
            pattern=_CLIENT_BEGIN_ASYNC,
            reason=(
                "assertTenantCannotSeeOtherRows must run inside "
                "`client.begin(async (...))`.\n\n"
                "Same reason as assertTenantSeesOwnRows: SET LOCAL is "
                "tx-scoped; a peer client can't observe it."
            ),
        ),
    ],
}


FOOT_GUN_BANS: dict[str, list[HelperFootGunBan]] = {
    "postgres": [
        HelperFootGunBan(
            b5_name="parameterized-set-local",
            b5_dialect="ts",
            reason=(
                "Helper source contains `tx`SET LOCAL <name> = "
                "'${val}'`` — Postgres rejects parameterized GUC "
                "values.\n\n"
                "The server raises 'could not determine data type of "
                "parameter $1' for parameterized values at the SET "
                "LOCAL position. Use `set_config(name, value, true)` "
                "instead — that form IS bindable and has identical "
                "transaction-local semantics."
            ),
        ),
        HelperFootGunBan(
            b5_name="peer-client-guc-read",
            b5_dialect="ts",
            reason=(
                "Helper source opens a second postgres() client and "
                "reads current_setting('app.*') on it.\n\n"
                "SET LOCAL is tx-scoped to the app's own "
                "transaction — a peer always sees ''. Stay inside "
                "client.begin(...) for both write and read so the "
                "GUC observation happens on the SAME tx that issued "
                "the SET LOCAL."
            ),
        ),
    ],
}


def adapters() -> list[str]:
    """Adapters with silent-wrong-helper guards registered. Returned
    sorted for deterministic CLI output."""
    keys = set(GUARDS.keys()) | set(FOOT_GUN_BANS.keys())
    return sorted(keys)
