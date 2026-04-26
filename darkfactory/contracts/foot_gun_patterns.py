"""Shared foot-gun-pattern library — single source of truth for the
regex shapes that ``scenario_lint`` blocks at the gate, that
``analyze_trial.KNOWN_BUGS`` counts post-hoc, and that
``retire_after_fixtures.Fixture`` pins as the canonical violation per
universal helper. ROADMAP.md Phase 4 B5.

Each :class:`FootGunPattern` carries exactly ONE compiled regex,
labeled with the dialect it targets. A foot-gun concept that has
distinct manifestations in multiple dialects (TS / SQL / Python) gets
multiple records, one per (concept, dialect) pair — same ``name``,
different ``dialect``. This shape ("discriminator over per-record
multi-dialect") was chosen on neo's review:

* Different dialects' fix-hints are not interchangeable copy. The TS
  scenario "do not write ``tx`SET LOCAL …${val}``" has nothing in
  common with the eventual SQL-side rule about parameter binding.
* It mirrors :class:`darkfactory.adapters.postgres.retire_after_fixtures.Fixture`'s
  shape — one record per concrete pattern with the regex inline.
* Filtering by dialect at the consumer level is a one-line list-comp.

**Naming**: ``name`` is kebab-case to match §4a ``forbidden_test_shapes``
on the spec side and the rule names ``scenario_lint`` already emits
(``parameterized-set-local``, ``peer-client-guc-read``, etc.). The
join key is the ``name``; consumers that want to address a specific
(concept, dialect) pair use ``(name, dialect)`` together.

**Library home**: this module lives inside the ``darkfactory.contracts``
package (not ``scripts/lib/`` as the ROADMAP literally said). Putting
it under ``scripts/lib/`` would re-introduce the wheel-packaging bug
Linus flagged for the contracts templates in the Phase-4-v0 review —
``scripts/`` is outside the ``darkfactory`` package and would not ship
in non-editable installs. Cross-import from ``scripts/lib/scenario_lint.py``
works because dark-factory is installed in every dev/agent environment.

**Authorization model** (AUTHORIZATION-MODEL.md):

Trigger #1 (audience escalation): NO. Library content is regex/prose
metadata consumed by host-side Python (scenario_lint, analyze_trial)
and the postgres-adapter test fixtures. No string crosses into a
barrier-restricted reader's view; the patterns and fix_hints are
already inline in scenario_lint today and reach the same audiences
post-refactor.

Trigger #2 (changed-surfaces declaration): NO. ``barriers.py``
untouched, ``quality-gate.sh`` report rows untouched, ``scenario_lint``
emitter behavior unchanged (still emits the same `LintIssue`
records), no new ``.dark-factory/`` artifact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


Dialect = Literal["ts", "sql", "py"]


@dataclass(frozen=True)
class FootGunPattern:
    """One compiled regex with metadata, keyed by (name, dialect).

    Frozen so consumers cannot mutate ``pattern`` at runtime — the
    library's whole purpose is to be a single source of truth, and a
    consumer that swaps the regex would defeat that.
    """

    name: str
    dialect: Dialect
    pattern: re.Pattern[str]
    reason: str
    fix_hint: str


# ---------------------------------------------------------------------
# TS patterns — postgres-adapter foot-guns the framework catches today.
# Future SQL/Python patterns add records below this section.
# ---------------------------------------------------------------------


# ``db-migrate-in-scenario``: child_process / subprocess calls that
# invoke a known migration tool. Two shapes alternated in one regex:
#   (a) shell form — ``subprocess.run("alembic upgrade head")``,
#       ``execSync("pnpm db:migrate")``, optionally prefixed by
#       ``cd <dir> && ``.
#   (b) Python list form — ``subprocess.run(["alembic", "upgrade",
#       ...])``.
# Both shapes are the SAME RULE with the same fix_hint; alternated
# inline because per-shape addressing has no current consumer
# (Linus B5 review). Ship as ONE record, ONE compiled regex —
# scenario_lint's ``finditer`` reports every match site regardless
# of which alternation branch fired.
#
# Migrations are substrate-setup; running them from inside a blind
# scenario produces ENOENT/cwd-not-found noise the blind-test
# harness should never see.
_DB_MIGRATE_RE = re.compile(
    r"""(?x)
    (?:
        # Shell form: subprocess call → optional ``cd`` → migration
        # tool invocation as a string arg.
        (?:
            execSync      |       # node child_process.execSync("...")
            spawnSync     |       # node child_process.spawnSync
            exec          |       # child_process.exec (callback form)
            subprocess\.run  |    # python subprocess.run("...", shell=True)
            subprocess\.check_(?:output|call)  |
            subprocess\.Popen     # python subprocess.Popen(...)
        )
        [^;\n]{0,200}?            # command args (bounded, no newline)
        (?:
            (?:cd\s+\S+\s*&&\s*)?  # optional leading cd
            (?:
                pnpm\s+db:migrate                |
                npm\s+run\s+db:migrate           |
                yarn\s+db:migrate                |
                (?:npx\s+|pnpm\s+exec\s+)?drizzle-kit\s+(?:push|migrate)  |
                (?:python\s+-m\s+)?alembic\s+upgrade\b                     |
                rake\s+db:migrate                |
                (?:python\s+manage\.py|django-admin)\s+migrate\b           |
                prisma\s+migrate\s+(?:dev|deploy)                          |
                goose\s+up                       |
                flyway\s+migrate
            )
        )
    |
        # Python list form: subprocess.run(["alembic", ...]).
        subprocess\.(?:run|Popen|check_(?:output|call))
        \s*\(\s*\[\s*
        ['"](?:
            alembic |
            drizzle-kit |
            goose |
            flyway |
            prisma
        )['"]
    )
    """,
)


# (deleted: separate _DB_MIGRATE_LIST_RE was collapsed into the
# unified _DB_MIGRATE_RE alternation below — Linus B5 review.)


# ``parameterized-set-local``: ``tx`SET LOCAL <name> = '${value}'`` ``
# tagged-template. postgres.js converts ``${value}`` to bind ``$1``;
# Postgres rejects "could not determine data type of parameter $1".
# Bindable form is ``tx`SELECT set_config(${name}, ${value}, true)`` ``.
#
# Bounded regex: ``tx`` → backtick → non-backtick body (lazy) → ``SET``
# → ``LOCAL`` → non-backtick body (lazy) → ``${``. The bounded
# ``[^`]`` segments prevent the lazy quantifier from roaming across
# unrelated tagged templates elsewhere in the file.
#
# This regex is also imported by
# ``darkfactory.adapters.postgres.retire_after_fixtures`` as the
# fixture's foot-gun pattern (B5 de-duped the prior byte-for-byte
# duplicate; the cross-module test now pins object identity).
_PARAMETERIZED_SET_LOCAL_RE = re.compile(
    r"""(?x)
    \btx\s*`                # tagged-template call on ``tx``
    [^`]*?                  # SQL body before SET LOCAL (lazy, no backtick)
    \bSET\s+LOCAL\b         # the foot-gun statement
    [^`]*?                  # SQL body between SET LOCAL and placeholder
    \$\{                    # postgres.js bind-parameter placeholder
    """,
    re.IGNORECASE,
)


# ``peer-client-guc-read``: a second postgres client
# (``postgres(process.env.…)``) followed by a ``current_setting('app.*')``
# read on it. ``SET LOCAL`` is tx-scoped to the app's connection, so a
# peer ``current_setting('app.*')`` always returns ``''`` regardless of
# implementation correctness — the assertion is a wrong-scope oracle.
#
# Mirrored by classification-hint regex at
# ``darkfactory/adapters/postgres/adapter.py``; if one fires for a
# scenario, the other should too. ``[\s\S]*?`` allows the two anchors
# to sit on separate lines because lint runs against the full file
# body.
_PEER_CLIENT_GUC_READ_RE = re.compile(
    r"postgres\s*\(\s*process\.env\."
    r"[A-Z_][A-Z0-9_]*"
    r"[\s\S]*?current_setting\s*\(\s*['\"`]app\."
)


# ``owner-bypass-missing-force``: ``ENABLE ROW LEVEL SECURITY``
# without a corresponding ``FORCE`` in the same file. Table owners
# bypass policies unless ``FORCE ROW LEVEL SECURITY`` is set. The
# absence-check (no FORCE in the same file) is performed at the
# consumer level (``analyze_trial.grep_known_bugs``); this regex
# fires on the ENABLE; absence-of-FORCE is a separate test.
_OWNER_BYPASS_MISSING_FORCE_RE = re.compile(
    r"ENABLE\s+ROW\s+LEVEL\s+SECURITY"
)


# ``strict-null-empty-guc-check``: scenarios that compare
# ``current_setting('app.*')`` strictly to ``null`` or ``''`` via
# ``===``, ``!==``, or ``toBe()``. The postgres.js wire surface
# returns ``''`` for an unset GUC at the SQL layer's NULL — both
# concrete forms are ambiguous. Use the framework's
# ``assertUnsetGucIsNullish`` helper.
#
# This is the foot-gun the framework's ``assertUnsetGucIsNullish``
# helper exists to prevent (B9b foundation Fixture). Same shape as
# ``retire_after_fixtures._UNSET_GUC_TOBE_NULL_RE`` but more general
# (covers ``=== null``, ``!==`` variants, not just ``.toBeNull()``).
_STRICT_NULL_EMPTY_GUC_CHECK_RE = re.compile(
    r"current_setting\s*\(\s*['\"`]app\.[a-z_]+['\"`][^)]*\)"
    r"[\s\S]{0,200}?(===|!==|toBe\()\s*(null|''|\"\")"
)


# ---------------------------------------------------------------------
# Fix-hint copy. Multi-line strings live up here so the PATTERNS tuple
# below stays readable.
# ---------------------------------------------------------------------


_FIX_HINT_DB_MIGRATE = (
    "Remove the migration invocation from this scenario. Migrations "
    "are substrate setup and must run before the gate, not from "
    "inside a blind scenario. If a scenario requires the DB to be "
    "migrated, add the migration to the substrate manifest (see "
    ".dark-factory/architecture/substrate-manifests/<spec>.toml) so "
    "the pipeline runs it once during Substrate Verification."
)


_FIX_HINT_PARAMETERIZED_SET_LOCAL = (
    "Replace ``tx`SET LOCAL app.x = '${val}'`` with "
    "``tx`SELECT set_config('app.x', ${val}, true)`` —  SET LOCAL "
    "does not accept bind parameters, but set_config does and has "
    "identical semantics (transaction-local when the third argument "
    "is ``true``). If you are asserting that the app correctly "
    "writes the GUC, use ``assertAppSetsTxGuc`` from the postgres "
    "adapter; if you are round-tripping a known value, use "
    "``assertTxGucRoundTrips``."
)


_FIX_HINT_PEER_CLIENT_GUC = (
    "Peer-connection current_setting('app.*') always returns '' — "
    "SET LOCAL is transaction-scoped to the app's own transaction, "
    "so a second client can never observe it. Move the assertion "
    "onto the same tx as the SET (use ``client.begin(async tx => …)`` "
    "and read the GUC via ``tx``), or use ``assertAppSetsTxGuc`` "
    "from the postgres adapter, which opens the tx, invokes the app "
    "callback, and reads back the GUC within the same tx."
)


_FIX_HINT_OWNER_BYPASS_MISSING_FORCE = (
    "``ALTER TABLE ... ENABLE ROW LEVEL SECURITY`` is not enough — "
    "table owners (and superusers) bypass policies unless ``FORCE "
    "ROW LEVEL SECURITY`` is also set. Add ``ALTER TABLE <t> FORCE "
    "ROW LEVEL SECURITY`` in the same migration. The framework's "
    "``assertRlsEnabled`` helper checks for both."
)


_FIX_HINT_STRICT_NULL_EMPTY_GUC_CHECK = (
    "``current_setting('app.x', true)`` returns NULL at the SQL "
    "layer when the GUC is unset, but postgres.js surfaces NULL as "
    "the empty string ``''``. A strict ``=== null`` or "
    "``.toBe(null)`` assertion fails against a correct "
    "implementation; a strict ``=== ''`` passes trivially even when "
    "the app never set the GUC. Use ``assertUnsetGucIsNullish`` "
    "from the postgres adapter, which accepts both wire forms."
)


# ---------------------------------------------------------------------
# Pattern registry. Stable order: alphabetical by (name, dialect) so
# test reports and consumer-side iteration are deterministic.
# ---------------------------------------------------------------------


PATTERNS: tuple[FootGunPattern, ...] = (
    FootGunPattern(
        name="db-migrate-in-scenario",
        dialect="ts",
        pattern=_DB_MIGRATE_RE,
        reason=(
            "Scenarios that invoke db-migration tools (pnpm "
            "db:migrate, drizzle-kit push, alembic upgrade, ...) via "
            "subprocess — both shell-string and Python list-form "
            "shapes. Migrations are substrate setup, not scenario "
            "concerns."
        ),
        fix_hint=_FIX_HINT_DB_MIGRATE,
    ),
    FootGunPattern(
        name="owner-bypass-missing-force",
        dialect="sql",
        pattern=_OWNER_BYPASS_MISSING_FORCE_RE,
        reason=(
            "``ENABLE ROW LEVEL SECURITY`` without a same-file "
            "``FORCE`` is a silent failure mode — table owners "
            "bypass policies."
        ),
        fix_hint=_FIX_HINT_OWNER_BYPASS_MISSING_FORCE,
    ),
    FootGunPattern(
        name="parameterized-set-local",
        dialect="ts",
        pattern=_PARAMETERIZED_SET_LOCAL_RE,
        reason=(
            "Tagged-template ``tx`SET LOCAL <name> = '${value}'`` "
            "binds ``${value}`` as ``$1``; Postgres rejects "
            "parameterized values at the SET LOCAL position. The "
            "bindable form is ``set_config(name, value, true)``."
        ),
        fix_hint=_FIX_HINT_PARAMETERIZED_SET_LOCAL,
    ),
    FootGunPattern(
        name="peer-client-guc-read",
        dialect="ts",
        pattern=_PEER_CLIENT_GUC_READ_RE,
        reason=(
            "A second ``postgres(process.env.…)`` client reading "
            "``current_setting('app.*')`` always returns ``''`` "
            "because ``SET LOCAL`` is tx-scoped to the app's own "
            "connection — the assertion carries zero RLS signal."
        ),
        fix_hint=_FIX_HINT_PEER_CLIENT_GUC,
    ),
    FootGunPattern(
        name="strict-null-empty-guc-check",
        dialect="ts",
        pattern=_STRICT_NULL_EMPTY_GUC_CHECK_RE,
        reason=(
            "Strict ``=== null`` / ``.toBe(null)`` / ``=== ''`` "
            "assertions against ``current_setting('app.*')`` — the "
            "postgres.js wire surface makes both forms ambiguous "
            "with the unset GUC."
        ),
        fix_hint=_FIX_HINT_STRICT_NULL_EMPTY_GUC_CHECK,
    ),
)


# Lookup-by-(name, dialect) index. Two patterns can share a ``name``
# (e.g., ``db-migrate-in-scenario`` ships both ``ts`` and ``py``
# variants), so the dict key is the (name, dialect) tuple. Consumers
# that want all patterns with a given name use ``patterns_by_name()``
# below.
BY_NAME_AND_DIALECT: dict[tuple[str, Dialect], FootGunPattern] = {
    (p.name, p.dialect): p for p in PATTERNS
}


def patterns_by_name(name: str) -> tuple[FootGunPattern, ...]:
    """All patterns sharing a given ``name`` across dialects.

    Useful for spec-side §4a authors who want to check all
    manifestations of a foot-gun by its canonical name without
    enumerating dialects.
    """
    return tuple(p for p in PATTERNS if p.name == name)


def ts_patterns() -> tuple[FootGunPattern, ...]:
    """All TypeScript-targeted patterns. Convenience for consumers
    that lint TS scenario files (``scenario_lint``)."""
    return tuple(p for p in PATTERNS if p.dialect == "ts")
