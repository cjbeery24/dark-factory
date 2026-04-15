"""``PostgresAdapter`` — concrete ``EcosystemAdapter`` for projects with
a Postgres database.

Scope, as of A3:

- **Detection** runs against the project filesystem. Signals: any ``.sql``
  file anywhere in the tree (excluding common vendored directories);
  a ``docker-compose.yml`` that declares a ``postgres``/``postgresql``
  service; a ``postgres://`` or ``postgresql://`` connection string in
  any tracked config file. Multiple adapters can be active
  simultaneously — a TS+Postgres project activates both Postgres and
  ``typescript-node``.
- **Precheck** delegates to ``lint_sql_fences`` (moved from
  ``darkfactory/spec/sql_lint.py``) and maps ``SqlLintFinding`` into
  the ABC's unified ``Finding`` shape. Severity is ``"warning"`` —
  sql_lint is advisory (ground truth used to deflate validator
  hallucinations); until C1's remediation flow wires it into a
  blocking gate, nothing downstream should treat these as fail-precheck
  errors.
- **Consistency rules, gate commands, proposal rehearsal:** Postgres
  does not contribute a build/test toolchain — those belong to the
  language adapter (TypeScript+Node, Python, Go…). So
  ``quality_gate_dispatch`` returns an empty ``GateCommands`` and
  ``proposal_rehearsal_typecheck`` returns ``None`` (documented opt-
  out; there is no separate PG typecheck pass distinct from the
  dialect parse at precheck).

**A5 preview:** the registry will instantiate ``PostgresAdapter`` once
per project run with ``project_dir`` set, then iterate
``precheck(spec, project_dir)`` over all active adapters. A3 keeps
``darkfactory/spec/precheck.py``'s direct call to the re-exported
``lint_sql_fences`` intact via the BC shim at
``darkfactory/spec/sql_lint.py``; A5 swaps that call for dispatch
through the registry.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.adapters.base import (
    EcosystemAdapter,
    Finding,
    GateCommands,
    load_packaged_addendum,
)
from darkfactory.adapters.postgres.sql_lint import (
    SqlLintFinding,
    lint_sql_fences,
)
from darkfactory.adapters.postgres.substrate_rules import (
    SubstrateRule,
    load_substrate_rules,
)
from darkfactory.architecture.consistency import ConsistencyRule
from darkfactory.spec.manifest import ArchitectureManifest
from darkfactory.spec.spec_model import SpecModel

log = logging.getLogger(__name__)

# Cap on how many files we scan when searching for connection-string
# signals. Detection must be cheap; a pathological repo (large monorepo
# with thousands of config files) shouldn't turn `detect()` into a
# multi-second operation. 200 files covers real projects comfortably —
# connection strings live at the top of the tree or in obvious config
# files, never in transitive generated output.
_CONNECTION_STRING_SCAN_LIMIT = 200

# Directories we skip when walking for .sql / connection-string signals.
# These are the universal vendored/build output names; adapters that
# need a tighter exclude list can extend this with no governance risk
# since detect() is a pure predicate.
_SKIP_DIRS = frozenset({
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".next",
    ".dark-factory",
})

_ADDENDUM_PATH = Path(__file__).with_name("architect_addendum.md")
_VALIDATOR_ADDENDUM_PATH = Path(__file__).with_name("spec_validator_addendum.md")


def _render_substrate_taxonomy(rules: list[SubstrateRule]) -> str:
    """Render the substrate-rules taxonomy as a self-contained markdown
    table for inline injection into the validator prompt.

    The prompt must carry the taxonomy by value, not by reference, so
    the validator agent has everything it needs without consulting any
    external file. Composing here in code — with the canonical
    ``load_substrate_rules`` loader as the single source — keeps the
    TOML authoritative while the prompt stays self-contained.
    """
    if not rules:
        return "_(no substrate rules registered)_"
    lines = [
        "| Trigger | Match patterns (regex, case-insensitive) | Required primitives |",
        "|---------|------------------------------------------|----------------------|",
    ]
    for r in rules:
        patterns = " · ".join(f"`{p.pattern}`" for p in r.match_patterns)
        primitives = ", ".join(f"`{p}`" for p in r.required_primitives) or "_(none)_"
        lines.append(f"| `{r.trigger}` | {patterns} | {primitives} |")
    return "\n".join(lines)


@dataclass
class PostgresAdapter(EcosystemAdapter):
    """The Postgres ecosystem adapter.

    Construction:

        adapter = PostgresAdapter()                 # contract-test instantiation
        adapter = PostgresAdapter(project_dir=...)  # production instantiation

    ``project_dir`` is optional for construction because contract
    tests that exercise ecosystem-agnostic surfaces (name, addendum,
    consistency rules) don't need a project tree. ``detect`` reads
    ``self.project_dir`` and raises when it's ``None``; the registry
    (``load_active_adapters``) always binds it for production.
    """

    # Dataclass field defaults shadow the abstract ``name`` /
    # ``detection_signals`` properties on the ABC at class-definition
    # time — the canonical pattern documented in
    # ``darkfactory/adapters/base.py``.
    name: str = "postgres"
    # Detection signals are advisory for the registry's display/logs.
    # The actual predicate lives in ``detect()`` because Postgres has
    # no single universal lockfile — multiple heterogeneous signals
    # collapse to one boolean. The strings here are the common cases
    # so "why is this adapter active?" logs read cleanly.
    detection_signals: tuple[str, ...] = (
        "*.sql",
        "docker-compose.yml",
        "postgres://",
    )
    project_dir: Path | None = None
    # Mirrors TypescriptNodeAdapter — A5's registry will inject
    # project-hints-derived rules through this field without
    # subclassing.
    extra_consistency_rules: list[ConsistencyRule] = field(default_factory=list)

    # ---- ABC: detection -------------------------------------------------

    def detect(self) -> bool:
        """True iff any Postgres dialect signal is present in ``self.project_dir``.

        Signals, checked in a single walk:
        1. Any ``.sql`` file anywhere in the tree (outside skip dirs).
        2. ``docker-compose.yml`` / ``docker-compose.yaml`` mentioning
           ``postgres`` or ``postgresql`` as a service name.
        3. ``postgres://`` or ``postgresql://`` in any config-shaped
           file (suffix/name allowlist), capped at
           ``_CONNECTION_STRING_SCAN_LIMIT`` reads to keep detection
           bounded on pathological trees.

        The walk uses ``os.walk`` with in-place ``dirnames`` mutation to
        prune ``_SKIP_DIRS`` before descending — crucial for monorepos
        with large ``node_modules`` trees, since ``Path.rglob`` would
        otherwise yield every entry and only then let us discard.

        Returns ``False`` if ``self.project_dir`` does not exist or is
        not a directory — the registry will never attempt to dispatch
        into a non-existent project, but defensive coding here means a
        mis-wired test won't raise an opaque ``OSError``. Raises if
        ``self.project_dir`` was not bound at construction (see the
        ABC's ``detect`` docstring for the single-source-of-truth
        rationale).
        """
        if self.project_dir is None:
            raise RuntimeError(
                "PostgresAdapter.detect() requires project_dir to be "
                "bound at construction. The registry binds it via "
                "load_active_adapters(..., project_dir=...); tests that "
                "exercise detect() must construct the adapter with an "
                "explicit project_dir."
            )
        if not self.project_dir.is_dir():
            return False
        return _walk_for_signals(self.project_dir)

    # ---- ABC: precheck --------------------------------------------------

    def precheck(self, spec: SpecModel) -> list[Finding]:
        """Run SQL fenced-block parser; return one Finding per bad fence.

        Does not currently consult the project tree —
        ``lint_sql_fences`` operates entirely on the parsed spec.
        Postgres-specific project-tree checks (e.g. validating that
        ``pg_dump`` is installed) would read ``self.project_dir`` when
        added; no signature change needed.
        """
        out: list[Finding] = []
        for sf in lint_sql_fences(spec):
            out.append(_sql_finding_to_finding(sf))
        return out

    # ---- ABC: consistency rules ----------------------------------------

    def consistency_rules(self) -> list[ConsistencyRule]:
        """Adapter-baseline consistency rules.

        A3 returns only rules supplied via ``extra_consistency_rules``.
        Like A2, adding ecosystem-baseline rules here is Sprint 3 work
        (B5: A1 reads from project-hints) — doing it during A3 would
        couple the move to a behavior change the equivalence harness
        can't yet certify.
        """
        return list(self.extra_consistency_rules)

    # ---- ABC: architect prompt addendum --------------------------------

    def architect_addendum(self) -> str:
        """Return the markdown addendum injected into the architect prompt.

        A4 migrated the DATABASE SCHEMA SETUP section out of
        ``agents/architect.md`` into ``architect_addendum.md`` next to
        this module. The loader path did not change; only the file
        contents did.
        """
        return load_packaged_addendum(self.name, _ADDENDUM_PATH)

    # ---- ABC: spec validator prompt addendum ---------------------------

    def spec_validator_addendum(self) -> str:
        """Return the validator addendum: prose template + inline taxonomy.

        The prose template (``spec_validator_addendum.md``) tells the
        validator HOW to evaluate substrate-primitive completeness; the
        taxonomy (rendered from ``substrate-rules.toml`` via
        ``load_substrate_rules``) tells it WHAT to look for. They are
        composed here — not statically baked into the markdown — because
        the TOML is the source of truth and the prompt must carry the
        taxonomy by value (the validator's container has no access to
        the Python package on disk).
        """
        template = load_packaged_addendum(self.name, _VALIDATOR_ADDENDUM_PATH)
        taxonomy = _render_substrate_taxonomy(load_substrate_rules())
        return f"{template.rstrip()}\n\n{taxonomy}\n"

    # ---- ABC: quality-gate dispatch ------------------------------------

    def quality_gate_dispatch(
        self, manifest: ArchitectureManifest
    ) -> GateCommands:
        """Postgres contributes no build/test commands.

        Build toolchain and test runner belong to the language adapter
        (TypeScript+Node, Python, etc.). Returning empty strings for
        every phase is the documented opt-out; the gate harness will
        compose commands only from adapters that claim a phase.
        """
        return GateCommands()

    # ---- ABC: proposal-rehearsal typecheck -----------------------------

    def proposal_rehearsal_typecheck(
        self, scratch_dir: Path
    ) -> list[Finding] | None:
        """Postgres has no separate C2 typecheck rehearsal.

        The dialect parse is already paid at precheck time (via
        ``lint_sql_fences``); running it again against proposals
        produces no new signal. Returns ``None`` — the documented
        opt-out the orchestrator treats as "skip rehearsal for this
        adapter", distinct from "rehearsal ran and found nothing".
        """
        return None


# ---------------------------------------------------------------------------
# Detection helpers — module-level functions so they are trivially
# testable. The walk-driven entry point (``_walk_for_signals``)
# traverses the project tree exactly once, classifying each filename
# into zero or more of the three signal probes. Per-file content
# probes (``_compose_mentions_postgres``,
# ``_file_has_connection_string``) stay independent so they remain
# unit-testable.
#
# All OSErrors are caught and treated as "signal absent" because
# detection must never raise into the registry's adapter-activation
# loop.
# ---------------------------------------------------------------------------


def _walk_for_signals(project_dir: Path) -> bool:
    """Walk ``project_dir`` once, short-circuit on the first signal.

    Uses ``os.walk`` with in-place ``dirnames`` mutation to prune
    ``_SKIP_DIRS`` before descending — ``Path.rglob`` does not offer
    that, so it would walk every entry under a 200k-file
    ``node_modules`` before the per-entry skip filter could discard
    them. The in-place mutation is the documented ``os.walk`` pattern
    for this.

    Order of probes per file (from cheapest to most expensive):
    1. filename ``*.sql`` → instant hit.
    2. filename matches ``docker-compose.yml[/.yaml]`` → read + substring.
    3. filename matches ``_CONFIG_SUFFIXES`` / ``_CONFIG_NAMES`` →
       read + substring; counted against ``_CONNECTION_STRING_SCAN_LIMIT``.

    The cap on connection-string *reads* (not walk entries) is a
    real safety net: after pruning skip dirs, the cap prevents a
    pathological tree of checked-in config files from turning
    detection into a multi-second scan.
    """
    connection_reads = 0
    try:
        for dirpath, dirnames, filenames in os.walk(project_dir):
            # Prune in place — this is the only mechanism that actually
            # keeps os.walk from descending. ``os.walk`` honors the
            # mutation *before* recursing.
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

            for fn in filenames:
                # Cheap check first: .sql filename is a decisive signal.
                if fn.lower().endswith(".sql"):
                    return True

                # Docker compose: read the file and substring-check.
                if fn in _COMPOSE_FILENAMES:
                    compose = Path(dirpath) / fn
                    if _compose_mentions_postgres(compose):
                        return True

                # Connection-string probe: only peek at config-shaped
                # files, and stop entirely after the cap to keep the
                # walk bounded.
                if connection_reads >= _CONNECTION_STRING_SCAN_LIMIT:
                    continue
                if not _is_config_filename(fn):
                    continue
                candidate = Path(dirpath) / fn
                connection_reads += 1
                if _file_has_connection_string(candidate):
                    return True
    except OSError as exc:
        log.debug("postgres: detection walk failed: %s", exc)
    return False


def _is_config_filename(fn: str) -> bool:
    """True iff ``fn`` is a filename we'll peek at for connection
    strings. Narrow allowlist to avoid reading binaries/logs."""
    suffix = Path(fn).suffix.lower()
    return suffix in _CONFIG_SUFFIXES or fn in _CONFIG_NAMES


def _compose_mentions_postgres(compose: Path) -> bool:
    """True iff the compose file contains ``postgres`` as a substring.

    Deliberately loose — a deeper YAML parse would mis-trigger on
    projects with older-compose schema variants (``postgres:15`` image
    string vs ``services.postgres.image`` key). The substring check
    catches both shapes without a YAML dependency and without
    false-negating on a minor schema difference.

    False negatives are acceptable here: if a project uses Postgres
    but hides it behind an alias like ``services.db.image:
    postgres:15``, the substring still matches. If it's hidden behind
    an environment variable or a base image that doesn't name
    Postgres, detection falls through to the connection-string probe.
    """
    try:
        text = compose.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.debug("postgres: compose read failed (%s): %s", compose, exc)
        return False
    lowered = text.lower()
    return "postgres" in lowered or "postgresql" in lowered


def _file_has_connection_string(path: Path) -> bool:
    """True iff ``path`` contains a ``postgres://`` or ``postgresql://``
    literal. Read errors are swallowed — detection must never raise."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "postgres://" in text or "postgresql://" in text


# File suffixes we'll peek at when probing for connection strings.
# Keeping this narrow prevents wasted I/O on large binary artifacts.
_CONFIG_SUFFIXES = frozenset({
    ".env",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".ts",
    ".js",
    ".py",
    ".go",
    ".rs",
    ".ini",
    ".cfg",
    ".conf",
})

# Files we'll peek at by exact name even when they have no suffix.
_CONFIG_NAMES = frozenset({
    ".env",
    ".env.local",
    ".env.example",
    "Dockerfile",
})

# docker-compose filenames we'll substring-check for Postgres mentions.
_COMPOSE_FILENAMES = frozenset({"docker-compose.yml", "docker-compose.yaml"})


# ---------------------------------------------------------------------------
# Finding adapter — translate SqlLintFinding into the ABC's unified
# ``Finding`` shape.
# ---------------------------------------------------------------------------


def _sql_finding_to_finding(sf: SqlLintFinding) -> Finding:
    """Translate a ``SqlLintFinding`` into a generic ``Finding``.

    Severity is ``"warning"``: sql_lint is advisory. It produces
    ground truth the validator can use to deflate its own "invalid
    SQL" hallucinations, but precheck does not hard-fail on these.
    Promoting severity is an adapter API change, not this method's
    concern.

    The section_path is serialized into ``path`` as a slash-joined
    breadcrumb so generic consumers can display it without knowing
    about SqlLintFinding's tuple shape.
    """
    section = "/".join(sf.section_path) if sf.section_path else ""
    return Finding(
        severity="warning",
        code="pg-sql-parse-error",
        message=f"{sf.error} — snippet: {sf.snippet}",
        path=section,
        line=sf.line_number,
    )
