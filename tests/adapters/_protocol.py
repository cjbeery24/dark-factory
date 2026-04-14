"""Placeholder ``EcosystemAdapter`` protocol — S0.2 deliverable.

**Lifecycle:** This module exists to give S0.2's contract harness something
concrete to test against *before* Sprint 1's A1 lands the real ABC at
``darkfactory/adapters/base.py``. When A1 ships:

1. Replace this protocol with ``from darkfactory.adapters.base import EcosystemAdapter``.
2. Delete this file.
3. The contract tests in ``test_adapter_contract.py`` keep working unchanged
   because they import the protocol via this module's name.

The shape mirrors the A1 sketch in the plan (lines 126-151). If A1 lands a
different shape, the contract tests are the canary — update them in
lockstep with the ABC.

Why a Protocol and not an ABC here: ABC is enforced at instantiation,
which means every test fixture that fails the contract would crash at
construction with an unhelpful ``TypeError`` instead of producing a
named pytest failure. Protocol + explicit hasattr/isinstance checks let
the contract test report exactly which method is missing or malformed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from darkfactory.architecture.consistency import ConsistencyRule
from darkfactory.spec.manifest import ArchitectureManifest


@dataclass(frozen=True)
class Finding:
    """A precheck or rehearsal result. Flat because consumers only need
    severity + message + an optional file/line pointer."""

    severity: str  # "error" | "warning" | "info"
    code: str  # short stable identifier for grouping (e.g. "missing-tsconfig")
    message: str
    path: str = ""
    line: int = 0


@dataclass(frozen=True)
class GateCommands:
    """Commands an adapter contributes to the quality gate.

    Each field is a shell command line or empty string. Empty means the
    gate phase is skipped for this adapter (e.g., Go has no separate
    typecheck — covered by build).

    The gate harness composes commands across active adapters; this
    dataclass is the contract for what one adapter contributes.
    """

    install: str = ""
    build: str = ""
    lint: str = ""
    typecheck: str = ""
    test: str = ""
    scenario_test: str = ""


# Stand-in for the spec object passed to ``precheck``. Deliberately
# empty: the live ``darkfactory.spec.spec_model.SpecModel`` does *not*
# expose ``.text`` or ``.name`` attributes — its surface is methods like
# ``find_section`` and ``wire_contracts`` plus a ``sections`` list. A1
# is responsible for declaring the precise spec shape it consumes;
# until then the harness treats the spec arg as opaque so the contract
# test doesn't bake in attributes that don't exist on real ``SpecModel``.
class SpecLike(Protocol):
    pass


@runtime_checkable
class EcosystemAdapter(Protocol):
    """Per-ecosystem behavior surface.

    Replaces direct framework knowledge of ``tsc``, ``pnpm``, ``drizzle``,
    etc. The pipeline core dispatches by name; adapters do the work.

    Method-by-method rationale (deferred to A1's docstrings; here only as
    enough doc to make the contract test's failure messages make sense):

    - ``name``: registry key. Stable string.
    - ``detection_signals``: filenames/markers that indicate this adapter
      applies. Used by the registry to compute ``active_adapters``.
    - ``detect``: deterministic predicate for "should this adapter be
      active for this project_dir?" Usually checks for one of
      ``detection_signals`` to exist. May implement richer logic.
    - ``precheck``: spec-time checks that run before architect (e.g.,
      A2/A3/A6-style stub round-trip, library symbol checks).
    - ``consistency_rules``: ecosystem-baseline rules contributed to A1's
      consistency check. Project-hints can add more on top.
    - ``architect_addendum``: prompt fragment injected into the architect
      prompt when this adapter is active. The text covers ecosystem
      specifics the core prompt deliberately omits.
    - ``quality_gate_dispatch``: per-phase commands (install/build/lint/
      typecheck/test/scenario_test) the gate runs.
    - ``proposal_rehearsal_typecheck``: optional C2-rehearsal hook. Returns
      ``None`` for ecosystems that don't support C2 (no skip-gate in
      orchestrator core; the adapter declares "I don't do this").
    """

    name: str
    detection_signals: tuple[str, ...]

    def detect(self, project_dir: Path) -> bool: ...

    def precheck(self, spec: SpecLike, project_dir: Path) -> list[Finding]: ...

    def consistency_rules(self) -> list[ConsistencyRule]: ...

    def architect_addendum(self) -> str: ...

    def quality_gate_dispatch(self, manifest: ArchitectureManifest) -> GateCommands: ...

    def proposal_rehearsal_typecheck(
        self, scratch_dir: Path
    ) -> list[Finding] | None: ...


# Names the contract test iterates. Keep in sync with the protocol.
REQUIRED_METHODS: tuple[str, ...] = (
    "detect",
    "precheck",
    "consistency_rules",
    "architect_addendum",
    "quality_gate_dispatch",
    "proposal_rehearsal_typecheck",
)

REQUIRED_ATTRS: tuple[str, ...] = ("name", "detection_signals")
