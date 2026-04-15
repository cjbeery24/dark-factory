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

from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
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
