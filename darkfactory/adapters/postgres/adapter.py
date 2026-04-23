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
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.adapters.base import (
    ClassificationHint,
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
    triggered_rules,
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
_SCENARIO_ADDENDUM_PATH = Path(__file__).with_name("scenario_architect_addendum.md")


# Module-level ClassificationHints for two test-topology footguns the
# defect-classifier cannot distinguish from IMPL_BUG without reading
# scenario file contents (Task 40). Rationale sources:
#   - Pattern 1: 2026-04-22 run report §Root causes #3. A peer
#     connection asserting on the ``app.*`` tx-local GUC namespace.
#     ``SET LOCAL`` is transaction-scoped; the peer connection's own
#     transaction context reads the GUC as unset regardless of impl.
#   - Pattern 2: 2026-04-22 run report §Root causes #4. Owner-role
#     RLS assertion. Table owners bypass RLS unless ``FORCE ROW LEVEL
#     SECURITY`` is set somewhere in the test; the assertion returns
#     all rows regardless of policy correctness.
#
# Regex design (post-Linus hardening):
#   - Pattern 1 requires ``current_setting('app.`` literally. A
#     peer-client test that reads session GUCs like ``TimeZone`` or
#     ``server_version`` is legitimate and must not be flagged —
#     only tx-local app.* reads off a peer connection are footguns.
#   - Pattern 2 uses ``\A(?![\s\S]*FORCE\s+ROW\s+LEVEL)[\s\S]*ENABLE..``
#     so that *any* FORCE statement anywhere in the scan scope
#     suppresses the hint. The previous post-match-tail lookahead
#     produced false positives when FORCE appeared before ENABLE
#     in the same scope.
#   - Both hints run per-describe-body now (see
#     ``pipeline.classification_hints._extract_describe_bodies``), so
#     "scan scope" means "the matched describe block's body text."
_POSTGRES_CLASSIFICATION_HINTS: tuple[ClassificationHint, ...] = (
    ClassificationHint(
        pattern=(
            r"postgres\s*\(\s*process\.env\."
            r"[A-Z_][A-Z0-9_]*"
            r"[\s\S]*?current_setting\s*\(\s*['\"`]app\."
        ),
        category_override="SPEC_AMBIGUOUS",
        internal_reason_code="POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION",
    ),
    ClassificationHint(
        pattern=(
            r"\A(?![\s\S]*FORCE\s+ROW\s+LEVEL)"
            r"[\s\S]*ENABLE\s+ROW\s+LEVEL\s+SECURITY"
        ),
        category_override="SPEC_AMBIGUOUS",
        internal_reason_code="POSTGRES_OWNER_BYPASSES_RLS",
    ),
)


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

    Substrate verification (see docs/dev/2026-04-15-substrate-verification-plan.md)
    extends this adapter's surface within the existing `core` tier — it is not a tier promotion.

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
        # Move 1 (wire-contracts gap G1, 2026-04-16): check the
        # declarative environment contract before walking the tree.
        # ``_walk_for_signals`` skips ``.dark-factory/`` by design (to
        # avoid re-scanning runtime artifacts), which leaves specs that
        # declare postgres only via ``[services.postgres]`` in
        # ``.dark-factory/architecture/environment.toml`` invisible to
        # detection — the exact shape budget-brain-v2 was in when the
        # 2026-04-16 success-measure run failed to activate this adapter.
        # Check the cheap, declarative signal first; short-circuit OR
        # with the walk for the original filesystem-evidence signals.
        return (
            _detect_from_environment_contract(self.project_dir)
            or _walk_for_signals(self.project_dir)
        )

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

    # ---- ABC: scenario architect prompt addendum ----------------------

    def scenario_architect_addendum(self) -> str:
        """Return the scenario-architect addendum: substrate-layer testing.

        Static prose — unlike the validator addendum, the substrate-rule
        taxonomy is not injected here. The Test Engineer reads the
        substrate manifest directly and emits scenarios per manifest
        entry; the addendum's job is to enforce the "raw client, not
        ORM" discipline plus the positive/negative pair contract for
        tenant-isolation rules.

        Reason this exists: 2026-04-15 substrate failure shipped because
        Test Engineer scenarios routed every assertion through the
        application's ORM/DI layer, never touching the database — a stub
        implementation that returns the right shape passes them all.
        Substrate scenarios bypass the ORM and execute assertions as raw
        SQL against the sidecar, so a stub-shaped migration fails loudly.

        Note: the orchestrator guards consumption of this addendum on the
        substrate manifest existing for the current spec
        (``.dark-factory/architecture/substrate-manifests/<spec>.toml``),
        so the prose can assume the manifest is present. See
        ``docs/dev/2026-04-15-substrate-verification-plan.md`` (Piece 4).
        """
        return load_packaged_addendum(self.name, _SCENARIO_ADDENDUM_PATH)

    # ---- classification hints (adapter-emitted post-classifier) -------

    def classification_hints(self) -> list[ClassificationHint]:
        """Two structural test-topology patterns the defect-classifier
        cannot distinguish from ``IMPL_BUG`` without reading scenario
        contents — see Task 40 / 2026-04-22 run report §Root causes 3-4.

        Returns a fresh list so callers cannot mutate the module-level
        tuple. The tuple itself is immutable but ``list(tuple)`` is the
        cheapest defensive copy that satisfies the ABC signature.
        """
        return list(_POSTGRES_CLASSIFICATION_HINTS)

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

    # ---- ABC: wire-contract helper staging ----------------------------

    def contracts_dir(self) -> Path | None:
        """Postgres wire-contract helpers (assertUnsetGucIsNullish,
        assertAppSetsTxGuc, assertTxGucRoundTrips, assertRlsEnabled,
        assertTenantSeesOwnRows, assertTenantCannotSeeOtherRows) live
        adjacent to this module under ``contracts/ts/``. The
        orchestrator copies that directory into
        ``.dark-factory/scenarios/<spec>/_contracts/postgres/`` so the
        test-engineer can import the helpers via relative path
        (``./_contracts/postgres``) when authoring blind scenarios.
        """
        return Path(__file__).parent / "contracts" / "ts"

    # ---- ABC: per-spec activation hook (G1 backstop) ------------------

    def spec_triggers(self, spec_text: str) -> bool:
        """True if any substrate rule from ``substrate-rules.toml`` fires
        against ``spec_text``. Same predicate ``verify_substrate`` uses
        to decide whether a missing manifest is an error (not a skip),
        hoisted out so the orchestrator can activate this adapter for a
        spec *before* the architect runs — closing the gap where the
        project-scan didn't list postgres but the spec obviously needs
        it (2026-04-16 database-foundation failure)."""
        return bool(triggered_rules(spec_text))

    # ---- ABC: substrate verification ----------------------------------

    def verify_substrate(
        self,
        sidecar_url: str,
        manifest_path: Path,
        spec_text: str,
    ) -> list[Finding] | None:
        """Run the substrate manifest's checks against the live sidecar.

        Three return states (mirrors ABC contract):

        - ``None`` — no substrate rules fired against ``spec_text`` and
          no manifest exists. Legitimate skip; the gate logs SKIP.
        - ``[]`` — manifest existed and every check passed.
        - ``[Finding(...), ...]`` — manifest had findings, OR rules
          fired but the manifest is absent (the
          ``substrate-manifest-missing`` distinguishability case from
          Piece 5 of the substrate verification plan).

        ``[[isolation_pair]]`` entries in the manifest are scenario-
        layer (Piece 4 generates positive/negative scenarios that exec
        through the application stack); they are not gate-phase checks
        and are intentionally skipped here.
        """
        if not manifest_path.exists():
            if triggered_rules(spec_text):
                return [Finding(
                    severity="error",
                    code="substrate-manifest-missing",
                    message=(
                        "spec triggers substrate rules but architect "
                        "produced no substrate manifest at "
                        f"{manifest_path}; re-run architecture or fix "
                        "the spec's `## Substrate Primitives` block"
                    ),
                    path=str(manifest_path),
                )]
            return None

        # Parse the manifest BEFORE importing psycopg — a malformed
        # manifest is a deterministic local failure that should surface
        # under its own code, not get masked behind "psycopg missing"
        # in environments without the driver. (Tests run without
        # psycopg installed; production gate containers always carry
        # it.)
        try:
            manifest = tomllib.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, tomllib.TOMLDecodeError) as exc:
            return [Finding(
                severity="error",
                code="substrate-manifest-unreadable",
                message=(
                    f"failed to parse substrate manifest at "
                    f"{manifest_path}: {exc}"
                ),
                path=str(manifest_path),
            )]

        # If the manifest has nothing to verify (no [[guc]], no
        # [[rls_table]]), there's no need to import psycopg or open a
        # connection — return an empty findings list. This is the
        # ``[[isolation_pair]]``-only case (those entries are
        # scenario-layer, not gate-layer).
        if not manifest.get("guc") and not manifest.get("rls_table"):
            return []

        try:
            import psycopg  # type: ignore[import-not-found]
        except ImportError as exc:
            return [Finding(
                severity="error",
                code="substrate-psycopg-missing",
                message=(
                    "psycopg is required to run verify_substrate but is "
                    f"not installed in the gate environment: {exc}"
                ),
                path=str(manifest_path),
            )]

        findings: list[Finding] = []
        # Catch psycopg.Error here — the package's documented base
        # class for every connection / protocol failure. We narrow
        # rather than ``except Exception`` so a real bug in our helper
        # code (TypeError, KeyError) propagates and surfaces as a test
        # failure instead of being silently re-skinned as a sidecar
        # error and lost to the gate report.
        try:
            with psycopg.connect(sidecar_url) as conn:
                findings.extend(
                    _check_gucs(conn, manifest.get("guc", []), manifest_path)
                )
                findings.extend(
                    _check_rls_tables(
                        conn, manifest.get("rls_table", []), manifest_path
                    )
                )
        except psycopg.Error as exc:
            return [Finding(
                severity="error",
                code="substrate-sidecar-unreachable",
                message=(
                    f"could not run substrate checks against sidecar "
                    f"{sidecar_url!r}: {exc}"
                ),
                path=str(manifest_path),
            )]
        return findings


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


def _detect_from_environment_contract(project_dir: Path) -> bool:
    """Declarative signal: ``.dark-factory/architecture/environment.toml``
    declares a postgres substrate service.

    The filesystem walk skips ``.dark-factory/`` (it's runtime state,
    not project state), so specs that haven't shipped yet — where the
    architect-produced env contract is the only evidence postgres is
    in play — are invisible to the walk. This check reads one known
    file and looks for three signatures, any of which is decisive:

    1. ``[services.<name>]`` where ``<name>`` contains ``postgres``.
    2. Any service block with an ``image`` field containing ``postgres``.
    3. Any string value anywhere in the file containing a
       ``postgres://`` or ``postgresql://`` URL (catches
       ``DATABASE_URL`` in ``inject_env``, env-var defaults, etc.).

    Returns ``False`` — never raises — when the file is missing,
    unreadable, or malformed. An invalid env.toml is a real problem,
    but it's not this function's job to surface it; the architect and
    orchestrator layers already validate the contract at their own
    seams. Raising from ``detect()`` would break the registry's
    activation loop.
    """
    env_path = project_dir / ".dark-factory" / "architecture" / "environment.toml"
    # ``is_file`` inside the try block: on some filesystems (broken
    # symlinks with parent-permissions quirks, NFS/SMB edge cases)
    # ``is_file`` itself can raise ``OSError``. Catching here keeps
    # the documented "never raises" invariant tight against the
    # registry's activation loop.
    try:
        if not env_path.is_file():
            return False
        env = tomllib.loads(env_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
        return False
    services = env.get("services")
    if isinstance(services, dict):
        for svc_name, svc in services.items():
            if "postgres" in str(svc_name).lower():
                return True
            if isinstance(svc, dict):
                image = svc.get("image")
                if isinstance(image, str) and "postgres" in image.lower():
                    return True
    # Deep-scan any string value for a postgres connection URL — covers
    # DATABASE_URL in inject_env and similar. Bounded by the file size
    # (environment.toml is operator-authored, always small).
    return _any_pg_url(env)


def _any_pg_url(obj: object) -> bool:
    """Recursively walk parsed TOML looking for a ``postgres://`` or
    ``postgresql://`` substring in any string leaf. Non-string leaves
    (bool/int/float/None) short-circuit to ``False`` — only strings
    can carry a URL."""
    if isinstance(obj, str):
        low = obj.lower()
        return "postgres://" in low or "postgresql://" in low
    if isinstance(obj, dict):
        return any(_any_pg_url(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_any_pg_url(v) for v in obj)
    return False


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
# Substrate verification helpers — module-level so each check is
# trivially unit-testable with a mock connection. Keeping these flat
# (one function per primitive type) means a future primitive (extension,
# trigger, materialized view) is added by appending one helper and one
# dispatch line in ``verify_substrate``, not by extending a polymorphic
# class hierarchy.
# ---------------------------------------------------------------------------


def _scalar(conn, sql: str) -> tuple[bool, object | None]:
    """Run ``sql`` and return ``(row_present, value)``.

    ``row_present`` is ``False`` iff the query returned zero rows —
    distinguishable from "row returned with NULL/empty value" because
    those two cases mean different things in substrate verification:
    "no row" usually means a missing target object (table doesn't exist,
    GUC isn't registered), while a returned NULL/empty value is a real
    Postgres-side answer the rule may want to interpret in its own way.

    Why this matters: an earlier shape returned ``None`` for both
    cases, which collapsed "table missing" with "rowsecurity is FALSE"
    and gave the wrong implementer-facing diagnosis (Linus review of
    Piece 5).

    Uses ``cursor()`` + ``execute`` rather than psycopg's connection-
    level ``execute`` so a mock connection in tests only needs to
    provide ``cursor()``. NOTE: do not "simplify" to ``conn.execute``
    — ``_FakeConn`` deliberately mirrors only the cursor surface; a
    helper that bypasses it would silently lose unit-test coverage.
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    if row is None:
        return (False, None)
    return (True, row[0])


def _check_gucs(
    conn, gucs: list, manifest_path: Path
) -> list[Finding]:
    """For each ``[[guc]]`` entry, run its ``registration_check``.

    Failure mode that motivated this: a stub migration with zero
    executable SQL passed AC inspection because the AC asserts the
    GUC's *behavior* through the app stack; the GUC itself was never
    registered. ``current_setting('app.x', true)`` returns ``NULL`` for
    an unregistered GUC and the registered value (possibly empty
    string) otherwise. We treat ``NULL`` (no value, or no row) as the
    decisive "unregistered" signal; an empty-string value is *not*
    treated as unregistered — that is a legitimate registered-with-
    empty-default state (``ALTER DATABASE x SET app.tenant_id = ''``).
    A rule that needs to assert "registered AND non-empty default"
    must emit a separate ``default_check`` against ``[[guc]].default``;
    that is a manifest-author contract, not a helper-side conflation.
    """
    findings: list[Finding] = []
    for guc in gucs:
        name = guc.get("name", "<unnamed>")
        check = guc.get("registration_check") or ""
        if not check:
            findings.append(Finding(
                severity="error",
                code="substrate-guc-no-check",
                message=(
                    f"GUC entry {name!r} has no registration_check; "
                    "manifest is malformed"
                ),
                path=str(manifest_path),
            ))
            continue
        try:
            row_present, value = _scalar(conn, check)
        except Exception as exc:
            findings.append(Finding(
                severity="error",
                code="substrate-guc-check-failed",
                message=(
                    f"GUC {name!r} registration_check raised: {exc}"
                ),
                path=str(manifest_path),
            ))
            continue
        # ``current_setting('x', true)`` always returns one row; the
        # value is NULL when the GUC is unregistered. The "no row"
        # branch (``not row_present``) is here for defensiveness against
        # a check SQL the manifest author wrote that returns zero rows
        # — semantically identical to "unregistered" from this helper's
        # perspective.
        if not row_present or value is None:
            findings.append(Finding(
                severity="error",
                code="substrate-guc-unregistered",
                message=(
                    f"GUC {name!r} is not registered after setup; "
                    f"check `{check}` returned NULL"
                ),
                path=str(manifest_path),
            ))
    return findings


def _check_rls_tables(
    conn, rls_tables: list, manifest_path: Path
) -> list[Finding]:
    """For each ``[[rls_table]]`` entry, verify the table exists, RLS
    is enabled, and at least one policy is attached. When the entry
    carries ``force = true`` (Task 43), additionally verify
    ``pg_class.relforcerowsecurity`` is true.

    Six distinct failure modes the helper distinguishes:

    - ``substrate-rls-table-missing`` — ``pg_tables`` has no row for
      this name; the migration probably never ran ``CREATE TABLE``.
    - ``substrate-rls-disabled`` — table exists, ``rowsecurity = false``;
      migration is missing ``ALTER TABLE ... ENABLE ROW LEVEL SECURITY``.
    - ``substrate-rls-no-policies`` — table exists, RLS enabled, but
      ``pg_policies`` row count is 0; migration is missing
      ``CREATE POLICY``.
    - ``substrate-rls-verifiable-not-bool`` — manifest ``verifiable``
      key is present but not a TOML boolean; fix target is the
      spec/validator.
    - ``substrate-rls-force-not-bool`` — manifest ``force`` key is
      present but not a TOML boolean (e.g. ``force = "yes"``); the
      fix target is the spec/validator, not the migration.
    - ``substrate-rls-not-forced`` — manifest declared ``force = true``
      but ``pg_class.relforcerowsecurity`` is false; migration is
      missing ``ALTER TABLE ... FORCE ROW LEVEL SECURITY``. Without
      FORCE, the table's owner (including whatever role holds
      ``CREATE`` on the schema — typically the migration role) bypasses
      every RLS policy on the table, which silently breaks tenant
      isolation when an owner-role assertion appears in a test.

    Entries with ``verifiable = false`` are silently skipped — the
    spec author has declared the gate cannot reach the table (external
    seed, view over unmigrated bases, etc.). Omission defaults to
    verify for backward compatibility.
    """
    findings: list[Finding] = []
    for rls in rls_tables:
        name = rls.get("name", "<unnamed>")
        # Task 41: ``verifiable = false`` opts an entry out of gate
        # verification because the spec author knows the table can't
        # be populated/migrated in the gate container (e.g., seeded
        # from an external system, or a view whose base tables aren't
        # in the manifest). Default-on for backward compatibility —
        # legacy manifests without the field keep verifying — and
        # gated on TOML boolean identity (same lesson as ``force``
        # from Task 43: truthy coercion silently routes a string
        # sentinel through the wrong branch).
        verifiable_raw = rls.get("verifiable")
        if verifiable_raw is False:
            continue
        if verifiable_raw is not None and verifiable_raw is not True:
            findings.append(Finding(
                severity="error",
                code="substrate-rls-verifiable-not-bool",
                message=(
                    f"rls_table {name!r} has verifiable={verifiable_raw!r} "
                    f"(type: {type(verifiable_raw).__name__}); must be a "
                    "TOML boolean (`verifiable = true` or `verifiable = "
                    "false`)"
                ),
                path=str(manifest_path),
            ))
            continue
        row_check = rls.get("rowsecurity_check") or ""
        policy_check = rls.get("policy_count_check") or ""
        if not row_check or not policy_check:
            findings.append(Finding(
                severity="error",
                code="substrate-rls-no-check",
                message=(
                    f"rls_table entry {name!r} missing rowsecurity_check "
                    "or policy_count_check; manifest is malformed"
                ),
                path=str(manifest_path),
            ))
            continue
        try:
            row_present, row_enabled = _scalar(conn, row_check)
        except Exception as exc:
            findings.append(Finding(
                severity="error",
                code="substrate-rls-check-failed",
                message=(
                    f"rls_table {name!r} rowsecurity_check raised: {exc}"
                ),
                path=str(manifest_path),
            ))
            continue
        if not row_present:
            # Zero rows from pg_tables means the table doesn't exist.
            # An empty migration that never ran ``CREATE TABLE`` lands
            # here — and the implementer needs to be told the table
            # is missing, not that they should "enable RLS."
            findings.append(Finding(
                severity="error",
                code="substrate-rls-table-missing",
                message=(
                    f"table {name!r} does not exist in pg_tables; the "
                    "migration probably never ran CREATE TABLE"
                ),
                path=str(manifest_path),
            ))
            continue
        if not row_enabled:
            findings.append(Finding(
                severity="error",
                code="substrate-rls-disabled",
                message=(
                    f"row-level security is not enabled on table "
                    f"{name!r}; pg_tables.rowsecurity = {row_enabled!r}"
                ),
                path=str(manifest_path),
            ))
            continue
        try:
            _present, policy_count = _scalar(conn, policy_check)
        except Exception as exc:
            findings.append(Finding(
                severity="error",
                code="substrate-rls-check-failed",
                message=(
                    f"rls_table {name!r} policy_count_check raised: {exc}"
                ),
                path=str(manifest_path),
            ))
            continue
        # ``count(*)`` always returns exactly one row with an integer
        # ≥ 0 — so policy_count is never None and never negative. Plain
        # ``< 1`` covers the "zero policies" case. (Edge case the
        # implementer-facing diagnosis can't disambiguate: if a manifest
        # author wrote a malformed ``policy_count_check`` that returned
        # zero rows or NULL, this branch would emit ``-no-policies`` —
        # technically misleading, but in practice such queries raise
        # and hit ``-check-failed`` first; callers should ensure the
        # check uses ``count(*)`` against pg_policies, not arbitrary SQL.)
        if policy_count is None or int(policy_count) < 1:
            findings.append(Finding(
                severity="error",
                code="substrate-rls-no-policies",
                message=(
                    f"table {name!r} has RLS enabled but no policies "
                    "attached; pg_policies row count = "
                    f"{policy_count!r}"
                ),
                path=str(manifest_path),
            ))
            continue
        # Force check (Task 43). Backward compatible: specs that didn't
        # opt into ``force = true`` and architects that predate this
        # field emit no ``force`` key, so the legacy shape skips
        # force verification silently.
        #
        # Gate on TOML boolean identity, not truthiness. ``force =
        # "yes"`` parses to the string "yes" — truthy under
        # ``if rls.get("force"):`` — and would silently opt into a
        # check the author never wrote, surfacing as a mis-targeted
        # ``substrate-rls-not-forced`` finding aimed at the migration
        # author when the real defect is a spec/validator typo. The
        # explicit ``is True`` / ``is False`` / ``is None`` triple
        # maps cleanly to TOML's type system: only the literal
        # booleans opt in or out; everything else is a manifest bug.
        force_raw = rls.get("force")
        if force_raw is None or force_raw is False:
            continue
        if force_raw is not True:
            findings.append(Finding(
                severity="error",
                code="substrate-rls-force-not-bool",
                message=(
                    f"rls_table {name!r} has force={force_raw!r} "
                    f"(type: {type(force_raw).__name__}); must be a "
                    "TOML boolean (`force = true` or `force = false`)"
                ),
                path=str(manifest_path),
            ))
            continue
        force_check = rls.get("force_check") or ""
        if not force_check:
            # Diagnostic wording — the adapter does not construct SQL
            # here; it tells the architect what to emit next time.
            # The suggested template schema-qualifies with
            # ``relnamespace = 'public'::regnamespace`` because
            # ``pg_class.relname`` is ambiguous across schemas: a
            # same-named table in a non-`public` schema would trip
            # the check against the wrong row. Non-`public` schemas
            # are a Task 43 follow-up (needs a ``schema`` field on
            # ``[[rls_table]]`` and plumb-through to the architect
            # template).
            findings.append(Finding(
                severity="error",
                code="substrate-rls-no-force-check",
                message=(
                    f"rls_table entry {name!r} declared force=true but "
                    "omitted force_check; architect should emit "
                    "`SELECT relforcerowsecurity FROM pg_class WHERE "
                    f"relname = '{name}' AND relnamespace = "
                    "'public'::regnamespace`"
                ),
                path=str(manifest_path),
            ))
            continue
        try:
            force_present, force_enabled = _scalar(conn, force_check)
        except Exception as exc:
            findings.append(Finding(
                severity="error",
                code="substrate-rls-check-failed",
                message=(
                    f"rls_table {name!r} force_check raised: {exc}"
                ),
                path=str(manifest_path),
            ))
            continue
        if not force_present or not force_enabled:
            findings.append(Finding(
                severity="error",
                code="substrate-rls-not-forced",
                message=(
                    f"table {name!r} declared force=true but "
                    "pg_class.relforcerowsecurity is "
                    f"{force_enabled!r}; migration is missing "
                    "`ALTER TABLE ... FORCE ROW LEVEL SECURITY` "
                    "(without which owner-role connections bypass "
                    "every policy)"
                ),
                path=str(manifest_path),
            ))
    return findings


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
