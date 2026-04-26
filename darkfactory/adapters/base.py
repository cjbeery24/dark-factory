"""``EcosystemAdapter`` ABC — the contract every per-ecosystem adapter
must satisfy.

**Purpose:** keep the pipeline core ecosystem-blind. The core orchestrator
calls into the active adapters by name; the adapters do the work that
requires knowledge of TypeScript, Postgres, pnpm, etc. The core never
imports ``tsc``, ``pglast``, or ``node_modules``. See
``docs/audits/2026-04-14-ecosystem-leakage.md`` for what the audit found
when this discipline was *not* enforced and ``docs/plans/2026-04-14-pipeline-modularity-and-remediation.md``
for the plan that motivated this ABC.

**Why an ABC, not a Protocol:** ABC machinery refuses to instantiate
subclasses that omit any abstract method, catching contract violations
*at construction time* with the offending method name in the
``TypeError``. Protocol's runtime-checkable form only verifies
attribute existence and only when ``isinstance`` is called, which means
a malformed adapter could silently no-op for an entire pipeline run
before its missing method was exercised. The S0.2 harness used
``@runtime_checkable`` Protocol *only* because the ABC didn't exist
yet; this module is its replacement.

**Why abstract properties for ``name`` and ``detection_signals``:**
plain class-attr annotations (``name: str``) are not enforced by ABC
machinery — a subclass that omits the assignment still instantiates and
fails later with ``AttributeError``. ``@property + @abstractmethod``
gives the same enforcement guarantee as the methods. Dataclass field
assignments (``name: str = "typescript-node"``) shadow the abstract
property at class-definition time, so the standard pattern of writing
adapters as dataclasses works without ceremony — see ``_MockAdapter``
in ``tests/adapters/conftest.py`` for the canonical example.

**Footgun on the dataclass-field pattern:** the field MUST have a
default value. A bare annotation like ``name: str`` (no default)
defines an ``__init__`` parameter but does NOT shadow the abstract
property descriptor on the ABC, so ``MyAdapter.__abstractmethods__``
still contains ``"name"`` and instantiation raises
``TypeError: Can't instantiate abstract class MyAdapter without an
implementation for abstract method 'name'`` even though the user did
write a field. Always supply a default
(``name: str = "my-ecosystem"``); use ``@property`` overrides if the
value must be computed at instance-init time.

**Stability:** the shape of this ABC is the public surface that
governance (``docs/ADAPTER-GOVERNANCE.md``) is anchored to. Any change
that adds, removes, or renames an abstract method is a public-surface
change subject to the deprecation cadence in
``docs/RELEASE-CADENCE.md``.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Literal, get_args

from darkfactory.architecture.consistency import ConsistencyRule
from darkfactory.spec.manifest import ArchitectureManifest
from darkfactory.spec.spec_model import SpecModel


# The closed severity vocabulary. Exposed at module scope so the
# contract harness, downstream consumers, and test fixtures can
# enumerate it from one place rather than restating the literals.
Severity = Literal["error", "warning", "info"]
SEVERITIES: tuple[str, ...] = get_args(Severity)


# Governance cap on architect-prompt addenda — see
# ``docs/ADAPTER-GOVERNANCE.md``. Lives here so the governance doc, the
# contract harness, and any future auditing tool all reference one
# literal rather than restating ``80`` in three places. The contract
# harness's ``test_architect_addendum_within_governance_line_cap``
# imports this constant; the governance doc cites it by qualified name.
GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP: int = 80


@dataclass(frozen=True)
class Finding:
    """A precheck or rehearsal result.

    Flat by design: consumers only need a severity, a stable code for
    grouping, a human-readable message, and an optional file/line
    pointer. Anything richer belongs in a separate diagnostic format,
    not here.

    ``severity`` is a closed vocabulary (``error`` / ``warning`` /
    ``info``). The ``Literal`` annotation gives static checkers a
    payload, and ``__post_init__`` enforces it at runtime so adapters
    that build Findings from non-statically-typed sources (LLM JSON,
    parsed tsc output, etc.) can't silently introduce a fourth bucket.
    Reviewers and dashboards can then assume severity ∈ ``SEVERITIES``
    without a fallback case.
    """

    severity: Severity
    code: str  # short stable identifier (e.g. "missing-tsconfig")
    message: str
    path: str = ""
    line: int = 0

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(
                f"Finding.severity must be one of {SEVERITIES}; "
                f"got {self.severity!r}"
            )


@dataclass(frozen=True)
class GateCommands:
    """Per-phase commands an adapter contributes to the quality gate.

    **Sentinel:** an empty string for any phase means "this adapter
    skips this phase." The gate harness composes commands across all
    active adapters; an adapter that leaves ``typecheck=""`` simply
    contributes nothing to the typecheck phase. ``None`` is *not* a
    legal value — use the empty string. This is enforced by the field
    type and pinned by ``test_gate_commands_empty_means_skip`` in the
    contract harness so adapter authors can grep for the sentinel
    semantics.

    The ``phases()`` helper yields ``(phase_name, command)`` tuples in
    fixed order; the harness uses it to enumerate phases without
    hard-coding field names everywhere.
    """

    install: str = ""
    build: str = ""
    lint: str = ""
    typecheck: str = ""
    test: str = ""
    scenario_test: str = ""

    def phases(self) -> tuple[tuple[str, str], ...]:
        """Return ``((phase_name, command), ...)`` for every phase.

        Used by the gate harness and by the contract test that pins
        the empty-string sentinel — both want a single source of truth
        for "what counts as a phase." Field iteration via
        ``dataclasses.fields`` keeps this in sync with the dataclass
        definition automatically.
        """
        return tuple((f.name, getattr(self, f.name)) for f in fields(self))


_addendum_cache: dict[Path, str] = {}


def load_packaged_addendum(adapter_name: str, path: Path) -> str:
    """Read a packaged addendum file with a process-wide cache.

    Adapters used to each carry their own ``_load_addendum`` /
    ``_addendum_cache`` pair (one cache per addendum kind per adapter).
    With three adapters and now two addenda each, that's six module-
    global caches, all implementing the same "lazy-read packaged
    markdown, fail loud on packaging error" pattern. Hoist once.

    Single ``dict[Path, str]`` cache because the path is the natural
    key — two adapters can never share a path. The ``adapter_name``
    parameter is purely for the error message: a packaging bug surfaces
    as ``... addendum missing for postgres at .../foo.md`` instead of
    a bare path the reader has to guess about.

    ``OSError`` (the missing-file family) is propagated unchanged. The
    orchestrator catches ``OSError`` narrowly at the addendum-injection
    site and continues without that adapter's contribution; wrapping
    here as ``RuntimeError`` would defeat that catch and turn a
    recoverable I/O failure into a hard pipeline abort.
    """
    cached = _addendum_cache.get(path)
    if cached is not None:
        return cached
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(
            f"{adapter_name} addendum missing or unreadable at {path}: {exc}"
        ) from exc
    _addendum_cache[path] = text
    return text


def op_str(ops: dict[str, object], key: str) -> str:
    """Read a ``commands.*`` entry from ``amendment_ops`` as a string.

    Adapters that implement ``quality_gate_dispatch`` by reading
    ``manifest.runtime.amendment_ops`` (the standard pattern — see
    ``TypescriptNodeAdapter`` and ``PythonFastApiAdapter``) must share
    a single interpretation of the value shape:

    * Missing key or empty value → ``""`` (the gate's "skip phase"
      sentinel documented on ``GateCommands``).
    * String → the string itself.
    * List of strings → joined with ``" && "``, empty elements dropped.

    The list case exists so an architect that emits a compound command
    as ``["pnpm install", "pnpm build"]`` doesn't silently drop
    everything after the first element (which is what ``str(list)``
    would do). Every shipped adapter was re-implementing this
    conversion identically; moving it here removes the copy and gives
    a future adapter author one place to grep for.

    This is a seam between the ABC and its implementations, not part
    of the ABC's abstract surface: no adapter is required to use it
    (``PostgresAdapter`` returns an empty ``GateCommands()`` and never
    touches ``amendment_ops``), but adapters that do should use this
    helper rather than re-coding the coercion.
    """
    raw = ops.get(key, "")
    if isinstance(raw, list):
        return " && ".join(str(x) for x in raw if x)
    return str(raw) if raw else ""


# Closed vocabulary a ``ClassificationHint.category_override`` may
# produce. Duplicated (not imported from ``pipeline.iteration``) because
# ``pipeline/iteration.py`` imports from this module at load time, and
# pulling ``_VALID_CLASSIFICATIONS`` back the other way would make the
# cycle. Kept in sync by the contract test
# ``test_hint_category_vocab_matches_classifier`` which asserts this set
# equals ``pipeline.iteration._VALID_CLASSIFICATIONS`` at runtime.
HINT_CATEGORY_VOCAB: frozenset[str] = frozenset({
    "IMPL_BUG",
    "SPEC_AMBIGUOUS",
    "SCENARIO_CRASH",
    "ENV_ISSUE",
    "BRITTLE",
    "OUT_OF_SCOPE",
    "UNKNOWN",
})


# UPPER_SNAKE_CASE, 3-64 chars, first char a letter. Adapter-owned
# reason codes live in ``defects.json`` (implementer-masked) — the shape
# is pinned so dashboards / post-mortems can rely on grep-ability and
# so adapter authors can't smuggle free-text prose past the barrier as
# a "code."
_INTERNAL_REASON_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


@dataclass(frozen=True)
class ClassificationHint:
    """Adapter-emitted pattern that reclassifies a defect post-classifier.

    The defect-classifier agent labels every failing scenario as
    ``IMPL_BUG`` by default when it cannot structurally tell a test-
    topology bug from an implementation bug. Peer-connection assertions
    on tx-local GUCs, owner-role BYPASSRLS, and other infrastructure-
    induced failures look identical to "implementation missing" from
    the classifier's vantage.

    Adapters contribute structural patterns they CAN recognize. The
    pipeline runs adapter hints after the classifier has emitted a
    validated ``defects.json``; for each defect whose scenario file
    matches the pattern, ``category`` is overridden and
    ``internal_reason_code`` is stamped onto the record.

    **Barrier discipline (load-bearing — see Task 40):**

    * ``pattern`` is applied only to scenario file contents, which are
      implementer-writable by construction — matching does not cross
      a barrier.
    * ``category_override`` is spec-anchor vocabulary; the implementer
      already receives the category in iteration feedback, so this
      rewrites a label the implementer was going to see anyway.
    * ``internal_reason_code`` is written ONLY to ``defects.json``,
      which lives under ``.dark-factory/reports/`` — a path masked as
      empty tmpfs in the Implementer container. Never propagate this
      code into ``<spec>-feedback-iterN.md`` or any review artifact
      that the Implementer container can read. The iteration renderer
      (``pipeline/iteration.py::_format_defects_inline``) pulls
      ``category``, ``cited_anchor_ids``, ``test_name``, and
      ``rationale`` by name — ``internal_reason_code`` is deliberately
      NOT in that list.

    **Scope:** reclassification only. Hints cannot inject rationale,
    pass rates, or oracle guidance — those surfaces would leak adapter
    vocabulary across the barrier. If a future failure mode needs richer
    telemetry it belongs in ``events.jsonl``, not in the implementer-
    visible feedback path.
    """

    pattern: str
    category_override: str
    internal_reason_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.pattern, str) or not self.pattern:
            raise ValueError(
                "ClassificationHint.pattern must be a non-empty string"
            )
        try:
            re.compile(self.pattern)
        except re.error as exc:
            raise ValueError(
                f"ClassificationHint.pattern must be a valid regex: {exc}"
            ) from exc
        if self.category_override not in HINT_CATEGORY_VOCAB:
            raise ValueError(
                f"ClassificationHint.category_override must be one of "
                f"{sorted(HINT_CATEGORY_VOCAB)}; "
                f"got {self.category_override!r}"
            )
        if not isinstance(self.internal_reason_code, str) or not \
                _INTERNAL_REASON_CODE_RE.match(self.internal_reason_code):
            raise ValueError(
                f"ClassificationHint.internal_reason_code must match "
                f"{_INTERNAL_REASON_CODE_RE.pattern}; "
                f"got {self.internal_reason_code!r}"
            )


@dataclass(frozen=True)
class ImportInfo:
    """G2: A single import extracted from a test/scenario file.

    ``specifier`` is the module string (e.g. ``"@budget-brain/shared"``).
    ``symbols`` is the list of named imports (e.g. ``["DrizzleTx", "db"]``).
    ``file_path`` is the scenario file containing the import.
    ``line`` is the 1-based line number.
    """

    specifier: str
    symbols: list[str] = field(default_factory=list)
    file_path: str = ""
    line: int = 0


class EcosystemAdapter(ABC):
    """Per-ecosystem behavior surface.

    See module docstring for design rationale. Per-method docs below
    only describe the contract; rationale for the *shape* of each
    method (why this exists, what failure mode it prevents) is in the
    plan at ``docs/plans/2026-04-14-pipeline-modularity-and-remediation.md``.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable kebab-case registry key (e.g. ``"typescript-node"``).

        Subclasses typically supply this as a dataclass field default,
        which shadows this abstract property at class-definition time.
        """

    @property
    @abstractmethod
    def detection_signals(self) -> tuple[str, ...]:
        """Filenames/markers that indicate this adapter applies.

        Used by the registry (A5) to compute the active adapter set.
        Must be a non-empty tuple of non-empty strings; the contract
        harness enforces this.
        """

    @abstractmethod
    def detect(self) -> bool:
        """Deterministic predicate: should this adapter activate here?

        Default implementations usually check whether any of
        ``detection_signals`` exists under ``self.project_dir``; richer
        logic is allowed (e.g., parse a lockfile to confirm dialect).

        **Source of truth:** subclasses MUST read ``self.project_dir``
        (declared by every concrete adapter as a dataclass field). An
        earlier version of this ABC took ``project_dir`` as a method
        argument *in addition to* storing it on ``self``; the dual-
        state created an opportunity for the two to disagree. The
        registry (``load_active_adapters``) already binds
        ``project_dir`` at construction time — downstream dispatch
        reads the bound value, never re-passes it. A subclass whose
        ``self.project_dir`` is ``None`` should raise, not silently
        return ``False``, so the "no project context" case is obvious
        at the call site instead of hiding as a false negative.
        """

    @abstractmethod
    def precheck(self, spec: SpecModel) -> list[Finding]:
        """Spec-time checks that run before the architect.

        Examples (from the audit): A2-style stub-compile round-trip,
        A3-style library-symbol existence check, A6-style fenced-block
        round-trip. Returning an empty list means the adapter has no
        objection.

        Reads ``self.project_dir`` (see ``detect`` above for the
        single-source-of-truth rationale). The spec is passed in
        because it varies per pipeline run; the project root is bound
        once at construction because it doesn't.
        """

    @abstractmethod
    def consistency_rules(self) -> list[ConsistencyRule]:
        """Ecosystem-baseline rules contributed to A1's consistency check.

        Project-hints can add more rules on top; the active rule set
        is the union across active adapters plus project-hints.
        """

    @abstractmethod
    def architect_addendum(self) -> str:
        """Prompt fragment injected into the architect prompt when active.

        Covers ecosystem specifics the core architect prompt
        deliberately omits. Governance caps this at 80 non-blank lines
        (``docs/ADAPTER-GOVERNANCE.md``); longer fragments signal that
        ecosystem knowledge is leaking back into prose instead of
        being expressed as consistency rules or quality-gate commands.
        """

    @abstractmethod
    def spec_validator_addendum(self) -> str:
        """Prompt fragment injected into the spec validator prompt.

        Symmetric with ``architect_addendum`` — the validator stays
        ecosystem-blind in core and adapters contribute ecosystem-
        specific completeness rules through this hook. Same governance
        cap (``GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP``) and same
        opt-out sentinel: return ``""`` (no fragment is appended; the
        contract harness pins the empty-string-is-opt-out semantics
        for adapters that have no validator-time guidance).

        Reason this exists: substrate-thin implementations slipped past
        validator + reviewer + oracle on 2026-04-15 because the spec
        text shipped behavioral assertions ("RLS isolates tenants")
        without a structured contract the gate could verify. The
        Postgres adapter uses this hook to demand a
        ``## Substrate Primitives`` fenced TOML block whose schema is
        defined by ``substrate-rules.toml``. See
        ``docs/dev/2026-04-15-substrate-verification-plan.md`` (Piece 2).
        """

    @abstractmethod
    def scenario_architect_addendum(self) -> str:
        """Ecosystem-specific guidance injected into the Test Engineer
        prompt.

        Symmetric with ``architect_addendum`` and
        ``spec_validator_addendum``: same governance cap
        (``GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP``) and same opt-out
        sentinel (``""`` means no fragment is appended; pinned by the
        contract harness). Adapters with no scenario-time guidance
        return ``""``.

        See concrete adapter overrides for ecosystem-specific rationale;
        the ABC contract is just "string in, string out, empty-string is
        opt-out."
        """

    @abstractmethod
    def quality_gate_dispatch(
        self, manifest: ArchitectureManifest
    ) -> GateCommands:
        """Per-phase shell commands the quality gate runs.

        The gate composes commands across all active adapters. Empty
        strings opt this adapter out of a phase.
        """

    @abstractmethod
    def proposal_rehearsal_typecheck(
        self, scratch_dir: Path
    ) -> list[Finding] | None:
        """Optional C2-rehearsal hook.

        Returns ``None`` when the ecosystem doesn't support C2
        rehearsal (e.g., Go has no separate typecheck pass). The
        orchestrator treats ``None`` as "skip rehearsal for this
        adapter", not "rehearsal failed" — the distinction matters for
        the audit log.
        """

    def contracts_dir(self) -> Path | None:
        """Absolute path to a directory of library-wire-contract helpers
        the orchestrator stages under
        ``.dark-factory/scenarios/<spec>/_contracts/<self.name>/`` before
        the scenario-architect runs.

        Returning ``None`` (the default) means "this adapter has no
        contracts to stage" — the orchestrator skips staging for this
        adapter. Concrete additive extension point: existing adapters
        inherit the default without code changes, so adding it is NOT a
        public-surface change subject to the deprecation cadence.

        The staged helpers encode ecosystem-specific wire-level truths
        that would otherwise leak into every scenario as hand-rolled
        predicates (2026-04-16 database-foundation escalation). The
        test-engineer imports them via relative path; the implementer
        remains barrier-masked from the scenarios directory.
        """
        return None

    def project_contracts_dir(self, project_dir: Path) -> Path | None:
        """Absolute path to a directory of *project-side* helpers the
        orchestrator stages under
        ``.dark-factory/scenarios/<spec>/_contracts/<self.name>/project/``
        before the scenario-architect runs.

        ROADMAP.md Phase 3 B9d. Default implementation returns
        ``project_dir/.dark-factory/contracts/<self.name>/`` if that
        directory exists, else ``None``. Adapters may override to point
        at a different convention (e.g., ``contracts/postgres/`` at the
        repo root for projects that prefer top-level placement).

        Path-namespacing — project helpers land under a ``project/``
        subdirectory inside the adapter's contracts root, alongside but
        physically separated from framework helpers. Collisions between
        framework and project helpers are structurally impossible
        because they live at non-overlapping paths; a framework adapter
        that ships a child named ``project`` is an authoring error and
        is rejected at staging time.

        Why a separate hook from ``contracts_dir()``: framework helpers
        are universal-wire knowledge that ships with the adapter
        package; project helpers encode project-specific conventions
        (column names, GUC namespaces, etc.) that change on the
        project's release cadence, not the framework's. Decoupling the
        two lets convention helpers move out of the framework
        (Phase 3 B9a) without breaking the adapter ABC.
        """
        candidate = (
            project_dir / ".dark-factory" / "contracts" / self.name
        )
        return candidate if candidate.is_dir() else None

    def spec_triggers(self, spec_text: str) -> bool:
        """True if this adapter must activate for a spec whose text is
        ``spec_text``, regardless of whether the project-scan inferred
        it from the repo tree.

        The ABC default is ``False`` — an adapter with no substrate
        taxonomy pays no attention to spec content. Adapters that DO
        have a taxonomy (today: postgres via ``substrate-rules.toml``)
        override this to return ``True`` when any of their trigger
        patterns match.

        Concrete additive extension point: existing adapters inherit
        the ``False`` default without code changes, so adding this hook
        is NOT a public-surface change subject to the deprecation
        cadence. (Same mechanism as ``contracts_dir``.)

        **Why this exists (the G1 backstop):** the 2026-04-16
        database-foundation run shipped a spec demanding RLS + tenant
        isolation against a project whose ``active_adapters`` list
        omitted postgres. The entire wire-contracts regime (Steps 1-5
        of the 2026-04-16 plan) was silently dark because nothing
        called postgres-adapter code. The orchestrator unions
        ``spec_triggers`` hits into ``active_adapters`` before the
        scenario-architect runs, so a spec that names postgres
        concepts activates postgres regardless of project-level
        detection. The paired post-stage assertion (see Move 5 in the
        2026-04-16 remediation) turns a silent failure into a loud
        one if the activation did not carry through to contract
        staging.

        ``spec_text`` is the full ``.md`` spec body (frontmatter +
        prose + fenced blocks). Subclasses typically delegate to a
        module-level rule-matcher that precompiles the patterns —
        e.g. ``PostgresAdapter.spec_triggers`` returns
        ``bool(triggered_rules(spec_text))``.
        """
        return False

    @abstractmethod
    def verify_substrate(
        self,
        sidecar_url: str,
        manifest_path: Path,
        spec_text: str,
    ) -> list[Finding] | None:
        """Substrate-layer verification phase, called by the gate
        between ``setup`` and ``self_tests``.

        Three return states the gate distinguishes:

        - ``None`` — legitimate skip. The adapter has no substrate
          contract for this spec and there is nothing to verify. The
          gate logs SKIP and proceeds.
        - ``[]`` — verification ran successfully and produced no
          findings. The gate logs PASS.
        - ``[Finding(...), ...]`` — verification ran and produced one
          or more findings; ``severity="error"`` items fail the gate.

        **Distinguishability contract (the load-bearing reason this
        method takes ``spec_text``):** a missing manifest is NOT
        automatically a ``None``. An adapter whose taxonomy fires
        against the spec text but whose architect failed to emit the
        manifest MUST return an ``error`` Finding (e.g.
        ``substrate-manifest-missing``) — that is the exact silent-
        failure mode the substrate-verification work exists to prevent
        (see ``docs/dev/2026-04-15-substrate-verification-plan.md``,
        Piece 5). Adapters with no substrate taxonomy at all (today:
        ``typescript-node``, ``python-fastapi``) return ``None``
        unconditionally — the spec_text argument is unused.

        ``sidecar_url`` is a connection string the gate passes in;
        adapters that don't need a database connection ignore it.
        """

    def parse_test_imports(self, file_path: Path) -> list[ImportInfo]:
        """G2: Extract import/require statements from a test file.

        Returns a list of ``ImportInfo`` describing each import in the
        file. The default implementation returns ``[]`` (no-op) — adapters
        override this to implement language-specific import parsing.

        Concrete method with default no-op: adding this does NOT require
        existing adapters to change (per ADAPTER-GOVERNANCE.md).
        """
        return []

    def detect_workspaces(self, project_dir: Path) -> list[str]:
        """M1: Discover workspace package directories.

        Returns a list of workspace-relative directory paths (e.g.
        ``["apps/api", "packages/shared"]``). The default returns ``[]``
        (no workspace detection) — adapters override for their ecosystem's
        workspace conventions (pnpm, npm, yarn, Go modules, etc.).

        Concrete method with default no-op: adding this does NOT require
        existing adapters to change (per ADAPTER-GOVERNANCE.md).
        """
        return []

    def classification_hints(self) -> list[ClassificationHint]:
        """Barrier-safe patterns the pipeline applies post-classifier to
        downgrade ``IMPL_BUG`` labels when the failure's root cause is
        structurally visible in the scenario file.

        Default ``[]`` — most adapters have no scenario-level footgun to
        recognize. Concrete adapters override when they have a known
        class of test-topology bug that the defect-classifier agent
        cannot distinguish from an implementation bug (e.g. Postgres
        peer-client assertions on transaction-local GUCs; owner-role
        RLS assertions that bypass due to missing ``FORCE ROW LEVEL``).

        Concrete method with default no-op: adding this does NOT require
        existing adapters to change (per ADAPTER-GOVERNANCE.md).

        See ``ClassificationHint`` for the barrier contract — in
        particular, the ``internal_reason_code`` is implementer-masked
        and MUST NOT appear in iteration feedback. The pipeline applies
        hints by matching pattern against scenario-file contents, then
        mutating ``defects.json``; no hint data crosses the Implementer
        barrier beyond the category relabeling (which the classifier
        already emits in the implementer-visible surface anyway).
        """
        return []

    def validate_command_scope(
        self,
        command: str,
        source_dirs: list[str],
    ) -> list[Finding]:
        """M1: Check that a command string targets a source_dir-consistent directory.

        Parses the command for ecosystem-specific scoping patterns
        (``cd <dir>``, ``--filter <pkg>``, positional directory arguments)
        and verifies each target is consistent with ``source_dirs``.

        Returns a list of ``Finding`` objects for each scoping violation.
        The default returns ``[]`` (skip check) — adapters override for
        their package manager's CLI syntax.

        Concrete method with default no-op: adding this does NOT require
        existing adapters to change (per ADAPTER-GOVERNANCE.md).
        """
        return []
