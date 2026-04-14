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

**Stability:** the shape of this ABC is the public surface that
governance (``docs/ADAPTER-GOVERNANCE.md``) is anchored to. Any change
that adds, removes, or renames an abstract method is a public-surface
change subject to the deprecation cadence in
``docs/RELEASE-CADENCE.md``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from darkfactory.architecture.consistency import ConsistencyRule
from darkfactory.spec.manifest import ArchitectureManifest
from darkfactory.spec.spec_model import SpecModel


@dataclass(frozen=True)
class Finding:
    """A precheck or rehearsal result.

    Flat by design: consumers only need a severity, a stable code for
    grouping, a human-readable message, and an optional file/line
    pointer. Anything richer belongs in a separate diagnostic format,
    not here.
    """

    severity: str  # "error" | "warning" | "info"
    code: str  # short stable identifier (e.g. "missing-tsconfig")
    message: str
    path: str = ""
    line: int = 0


@dataclass(frozen=True)
class GateCommands:
    """Per-phase commands an adapter contributes to the quality gate.

    Each field is a shell command line or empty string. Empty means the
    gate phase is skipped *for this adapter* (e.g., Go has no separate
    typecheck — it's covered by build, so a hypothetical Go adapter
    leaves ``typecheck=""``). The gate harness composes commands across
    all active adapters; this dataclass is the contract for what one
    adapter contributes.
    """

    install: str = ""
    build: str = ""
    lint: str = ""
    typecheck: str = ""
    test: str = ""
    scenario_test: str = ""


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
    def detect(self, project_dir: Path) -> bool:
        """Deterministic predicate: should this adapter activate here?

        Default implementations usually check whether any of
        ``detection_signals`` exists under ``project_dir``; richer
        logic is allowed (e.g., parse a lockfile to confirm dialect).
        """

    @abstractmethod
    def precheck(self, spec: SpecModel, project_dir: Path) -> list[Finding]:
        """Spec-time checks that run before the architect.

        Examples (from the audit): A2-style stub-compile round-trip,
        A3-style library-symbol existence check, A6-style fenced-block
        round-trip. Returning an empty list means the adapter has no
        objection.
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
