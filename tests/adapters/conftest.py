"""Fixtures for the adapter contract harness.

A1 swap: these fixtures used to duck-type against a runtime-checkable
Protocol because the real ABC didn't exist yet (S0.2 placeholder).
With A1 shipping ``darkfactory.adapters.base.EcosystemAdapter``, the
mock and opt-out adapters now subclass the ABC directly. ABC
machinery enforces method completeness at instantiation; the harness's
positive/negative tests cover everything else (return types, value
constraints, catalog drift).

Provides:
- ``mock_adapter`` — a minimal, *valid* ``EcosystemAdapter``. Baseline
  noise check: if the contract test fails on this, the harness is
  broken, not the adapter.
- ``opt_out_adapter`` — like mock, but ``proposal_rehearsal_typecheck``
  returns ``None`` to exercise the opt-out branch (Go-style ecosystems
  with no separate typecheck pass).
- ``broken_adapter_factory`` — produces adapters with one specific
  *value-level* contract violation each (bad return type, empty name,
  etc.). Method-completeness violations are covered separately by the
  ABC-enforcement tests in ``test_adapter_contract.py`` since ABC
  refuses to construct such subclasses at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from darkfactory.adapters.base import EcosystemAdapter, Finding, GateCommands
from darkfactory.architecture.consistency import ConsistencyRule
from darkfactory.spec.manifest import ArchitectureManifest
from darkfactory.spec.spec_model import SpecModel


# ---------------------------------------------------------------------------
# Sample data — non-empty so the contract tests' element-type assertions
# actually exercise. Returning ``[]``/``None`` from the mock would let a
# malformed contract slip through silently.
# ---------------------------------------------------------------------------


_SAMPLE_RULE = ConsistencyRule(
    name="mock-rule",
    env_paths=("commands.setup",),
    spec_patterns=(re.compile(r"mock", re.IGNORECASE),),
    forbidden_env_patterns=(re.compile(r"forbidden", re.IGNORECASE),),
    description="Mock rule used by the contract harness fixture.",
)

_SAMPLE_FINDING = Finding(
    severity="info",
    code="mock-finding",
    message="Mock rehearsal finding from the contract harness fixture.",
)


# ---------------------------------------------------------------------------
# Minimal valid adapter — passes every contract check
# ---------------------------------------------------------------------------


@dataclass
class _MockAdapter(EcosystemAdapter):
    """Smallest object that satisfies the ``EcosystemAdapter`` ABC.

    Used as the baseline. If the contract test fails on this fixture,
    the harness itself is broken — fix the harness, not the fixture.

    Note on the dataclass + ABC pattern: dataclass field defaults
    (``name: str = "mock"``) shadow the abstract property descriptors
    on ``EcosystemAdapter`` at class-definition time, so this
    instantiates without ceremony. See ``base.py``'s module docstring
    for the rationale.
    """

    name: str = "mock"
    detection_signals: tuple[str, ...] = ("mock.lockfile",)
    # Bound at construction by fixtures that exercise detect/precheck.
    # None by default so tests of ecosystem-agnostic surfaces (name,
    # addendum, consistency rules) can instantiate without ceremony.
    # ``detect`` raises if this is None — mirrors the concrete-adapter
    # policy so the mock tests the same code path shape.
    project_dir: Path | None = None

    def detect(self) -> bool:
        if self.project_dir is None:
            raise RuntimeError(
                "_MockAdapter.detect() requires project_dir to be bound "
                "at construction (same policy as concrete adapters)."
            )
        return (self.project_dir / "mock.lockfile").exists()

    def precheck(self, spec: SpecModel) -> list[Finding]:
        return []

    def consistency_rules(self) -> list[ConsistencyRule]:
        # Returning a non-empty list ensures the positive contract test
        # actually verifies the *element type*, not just the outer list.
        return [_SAMPLE_RULE]

    def architect_addendum(self) -> str:
        return "# Mock adapter addendum (contract harness fixture)"

    def spec_validator_addendum(self) -> str:
        # Non-empty so the positive contract test exercises the
        # "addendum present and within governance cap" branch. The
        # empty-string opt-out is covered by ``_OptOutAdapter`` below.
        return "# Mock spec validator addendum (contract harness fixture)"

    def scenario_architect_addendum(self) -> str:
        # Same rationale as ``spec_validator_addendum``: non-empty here
        # so positive contract tests exercise the present-and-capped
        # branch; empty-string opt-out covered by ``_OptOutAdapter``.
        return "# Mock scenario architect addendum (contract harness fixture)"

    def quality_gate_dispatch(self, manifest: ArchitectureManifest) -> GateCommands:
        return GateCommands(
            install="echo mock install",
            build="echo mock build",
            test="echo mock test",
        )

    def proposal_rehearsal_typecheck(
        self, scratch_dir: Path
    ) -> list[Finding] | None:
        # Non-None so the positive test exercises the list-of-Finding
        # branch. ``None`` is also legal per contract — covered by the
        # ``opt_out_adapter`` fixture below.
        return [_SAMPLE_FINDING]


@pytest.fixture
def mock_adapter() -> EcosystemAdapter:
    return _MockAdapter()


# ---------------------------------------------------------------------------
# Broken-adapter factory — used by negative contract tests
#
# Each broken class subclasses ``_MockAdapter`` so the ABC's
# method-completeness check is satisfied; the violations here are all
# *value-level* (wrong return type, empty required string, etc.) — the
# kind of mistake the ABC alone can't catch and the harness must.
#
# Method-omission violations are covered separately by
# ``test_incomplete_subclass_cannot_be_instantiated`` in
# ``test_adapter_contract.py`` — that test exercises the ABC directly
# and doesn't go through this factory.
# ---------------------------------------------------------------------------


def _make_broken(violation: str) -> object:
    """Return a mock-adapter clone with one specific contract violation.

    Each ``violation`` corresponds to a property the contract harness
    checks. Adding a check? Add a violation here so the negative test
    proves the check actually fails.
    """
    if violation == "missing_name":
        return _NoName()
    if violation == "empty_detection_signals":
        return _EmptySignals()
    if violation == "consistency_returns_non_list":
        return _ConsistencyNonList()
    if violation == "consistency_returns_invalid_rule":
        return _ConsistencyInvalidRule()
    if violation == "empty_architect_addendum":
        return _EmptyAddendum()
    if violation == "gate_dispatch_returns_wrong_type":
        return _GateWrongType()

    raise ValueError(f"Unknown violation: {violation}")


@dataclass
class _NoName(_MockAdapter):
    name: str = ""  # violates: name must be non-empty


@dataclass
class _EmptySignals(_MockAdapter):
    detection_signals: tuple[str, ...] = ()  # violates: must be non-empty


class _ConsistencyNonList(_MockAdapter):
    def consistency_rules(self):  # type: ignore[override]
        return "not a list"  # violates: must return list


class _ConsistencyInvalidRule(_MockAdapter):
    def consistency_rules(self):  # type: ignore[override]
        return ["not a ConsistencyRule"]  # violates: items must be ConsistencyRule


class _EmptyAddendum(_MockAdapter):
    def architect_addendum(self) -> str:
        return ""  # violates: must be non-empty when adapter is active


class _GateWrongType(_MockAdapter):
    def quality_gate_dispatch(self, manifest):  # type: ignore[override]
        return {"install": "echo"}  # violates: must return GateCommands


@pytest.fixture
def broken_adapter_factory():
    return _make_broken


# ---------------------------------------------------------------------------
# Opt-out adapter — proves ``proposal_rehearsal_typecheck`` returning
# ``None`` is legal at the contract level. Without this fixture the
# ``None`` branch is undocumented behavior; with it, an adapter author
# can grep for "opt_out" to see the example.
# ---------------------------------------------------------------------------


@dataclass
class _OptOutAdapter(_MockAdapter):
    """Adapter that opts out of C2 proposal rehearsal — Go, for example,
    has no separate typecheck pass — and of the spec_validator addendum,
    so the empty-string opt-out branch is exercised."""

    name: str = "mock-opt-out"

    def proposal_rehearsal_typecheck(self, scratch_dir: Path) -> list[Finding] | None:
        return None

    def spec_validator_addendum(self) -> str:
        # Empty-string opt-out: pinned as legal by the contract harness
        # so non-Postgres adapters can stay silent at validator time.
        return ""

    def scenario_architect_addendum(self) -> str:
        # Empty-string opt-out: same precedent as the validator addendum
        # — non-Postgres adapters stay silent at scenario-generation time.
        return ""


@pytest.fixture
def opt_out_adapter() -> EcosystemAdapter:
    return _OptOutAdapter()


# ---------------------------------------------------------------------------
# Real-adapter fixtures — the contract harness runs every positive
# property check against both the mock and every shipped concrete
# adapter via the parametrized ``adapter`` fixture below. New adapters
# add one entry to ``_CONCRETE_ADAPTERS`` and immediately pick up the
# full positive contract battery; no per-adapter test re-authoring.
# ---------------------------------------------------------------------------


def _make_typescript_adapter(project_dir: Path) -> EcosystemAdapter:
    # Local import keeps the dependency on the typescript adapter
    # contained to the factory; if a future split-wheel layout makes
    # the adapter package optional, only this factory needs to handle
    # the missing dependency. (Collection itself is not at risk —
    # ``TypescriptNodeAdapter`` has only pure-Python in-tree imports;
    # there's no node/tsc dependency at import time.)
    from darkfactory.adapters.typescript import TypescriptNodeAdapter
    return TypescriptNodeAdapter(project_dir=project_dir)


def _make_postgres_adapter(project_dir: Path) -> EcosystemAdapter:
    # Same import-isolation reasoning as the typescript factory. A3's
    # PostgresAdapter imports pglast lazily (only when lint runs), so
    # no Postgres dependency is needed at fixture-collection time.
    from darkfactory.adapters.postgres import PostgresAdapter
    return PostgresAdapter(project_dir=project_dir)


def _make_python_fastapi_adapter(project_dir: Path) -> EcosystemAdapter:
    # Sprint 6 #2 stub adapter. Pure-Python in-tree imports only; no
    # runtime Python-tooling dependency at fixture-collection time. The
    # adapter itself does nothing expensive — its value is exactly that
    # the contract harness already pins it as ABC-conformant.
    from darkfactory.adapters.python_fastapi import PythonFastApiAdapter
    return PythonFastApiAdapter(project_dir=project_dir)


# Factories take a ``project_dir`` argument so the ``adapter`` fixture
# below can bind each adapter to a per-test ``tmp_path``. The binding
# mirrors production (``load_active_adapters`` always passes
# ``project_dir`` at construction) and is required now that ``detect``
# reads ``self.project_dir`` instead of taking it as a method arg —
# see ``darkfactory/adapters/base.py::EcosystemAdapter.detect``.
#
# ``id`` keys propagate into pytest test names: collected as
# ``test_X[mock]``, ``test_X[typescript-node]`` etc., so a single
# failing adapter is visible without scrolling per-test parametrize ids.
_CONCRETE_ADAPTERS: tuple[tuple[str, Callable[[Path], EcosystemAdapter]], ...] = (
    ("mock", lambda pd: _MockAdapter(project_dir=pd)),
    ("typescript-node", _make_typescript_adapter),
    ("postgres", _make_postgres_adapter),
    ("python-fastapi", _make_python_fastapi_adapter),
)


@pytest.fixture(params=[entry for entry in _CONCRETE_ADAPTERS], ids=[e[0] for e in _CONCRETE_ADAPTERS])
def adapter(request, tmp_path: Path) -> EcosystemAdapter:
    """Yields each shipped concrete adapter in turn, bound to ``tmp_path``.

    Positive contract tests use this fixture so adding a new adapter
    extends the battery automatically. Negative tests stay tied to the
    ``broken_adapter_factory`` because they exercise a deliberate
    contract violation that isn't ecosystem-specific.

    The per-test ``tmp_path`` is bound at construction so ``detect``
    and ``precheck`` can run without raising — the adapters now read
    ``self.project_dir`` (see the ABC's ``detect`` docstring for the
    single-source-of-truth rationale).
    """
    _id, factory = request.param
    return factory(tmp_path)
