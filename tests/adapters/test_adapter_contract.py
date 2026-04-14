"""Contract tests applied to any object claiming to be an EcosystemAdapter.

Two halves:

1. **Positive** — ``mock_adapter`` (from conftest) satisfies the contract.
   If this fails, the harness itself is broken.
2. **Negative** — for each property the contract enforces, a deliberately
   broken adapter triggers exactly that failure. This is what guards
   against the harness silently accepting non-compliant implementations.

To validate a real adapter, parametrize the positive half over a fixture
list. When A2 ships ``TypescriptNodeAdapter``, add::

    @pytest.fixture(params=[mock_adapter, ts_node_adapter])
    def adapter(request): ...

and the existing tests apply automatically.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from darkfactory.architecture.consistency import ConsistencyRule
from darkfactory.spec.manifest import ArchitectureManifest

from tests.adapters._protocol import (
    REQUIRED_ATTRS,
    REQUIRED_METHODS,
    EcosystemAdapter,
    Finding,
    GateCommands,
)


# ---------------------------------------------------------------------------
# Positive contract — mock_adapter must satisfy every check
# ---------------------------------------------------------------------------


def test_mock_adapter_has_required_attrs(mock_adapter):
    for attr in REQUIRED_ATTRS:
        assert hasattr(mock_adapter, attr), f"missing required attr: {attr}"


def test_mock_adapter_has_required_methods(mock_adapter):
    for method in REQUIRED_METHODS:
        assert callable(getattr(mock_adapter, method, None)), (
            f"missing or non-callable required method: {method}"
        )


def test_mock_adapter_satisfies_protocol(mock_adapter):
    """``isinstance`` on a ``runtime_checkable`` Protocol verifies that
    the named attributes/methods *exist* (and are callable for methods).
    It does NOT verify signatures or return types — those are covered by
    the per-method positive tests below. Kept as a separate one-liner so
    a protocol-level miss produces a focused pytest failure."""
    assert isinstance(mock_adapter, EcosystemAdapter)


def test_mock_adapter_name_is_non_empty(mock_adapter):
    assert isinstance(mock_adapter.name, str) and mock_adapter.name


def test_mock_adapter_detection_signals_non_empty_tuple(mock_adapter):
    sigs = mock_adapter.detection_signals
    assert isinstance(sigs, tuple) and len(sigs) > 0
    assert all(isinstance(s, str) and s for s in sigs)


def test_mock_adapter_consistency_rules_returns_list_of_rules(mock_adapter):
    rules = mock_adapter.consistency_rules()
    assert isinstance(rules, list)
    # Mock returns at least one rule so the element-type check below
    # actually executes — empty-list returns are legal but make the
    # assertion vacuous.
    assert len(rules) >= 1, (
        "mock_adapter must return at least one rule so the element-type "
        "check exercises; an empty list passes vacuously"
    )
    for r in rules:
        assert isinstance(r, ConsistencyRule), (
            f"consistency_rules must return ConsistencyRule instances; got {type(r)}"
        )


def test_mock_adapter_architect_addendum_non_empty(mock_adapter):
    addendum = mock_adapter.architect_addendum()
    assert isinstance(addendum, str) and addendum.strip()


def test_mock_adapter_quality_gate_dispatch_returns_gate_commands(mock_adapter):
    cmds = mock_adapter.quality_gate_dispatch(ArchitectureManifest())
    assert isinstance(cmds, GateCommands)


def test_opt_out_adapter_proposal_rehearsal_typecheck_returns_none(
    opt_out_adapter, tmp_path
):
    """Adapters that don't support C2 rehearsal (e.g. Go) return None.
    The pipeline core treats None as "skip rehearsal for this adapter",
    not "rehearsal failed". This test pins that branch as legal."""
    assert opt_out_adapter.proposal_rehearsal_typecheck(tmp_path) is None
    assert isinstance(opt_out_adapter, EcosystemAdapter)


def test_mock_adapter_proposal_rehearsal_typecheck_returns_none_or_findings(
    mock_adapter, tmp_path
):
    result = mock_adapter.proposal_rehearsal_typecheck(tmp_path)
    assert result is None or isinstance(result, list)
    if isinstance(result, list):
        # Element-type guard, parallel to consistency_rules above. Mock
        # returns a non-empty list so this branch executes.
        assert len(result) >= 1, (
            "mock_adapter returned an empty list; can't verify Finding "
            "element type vacuously"
        )
        for f in result:
            assert isinstance(f, Finding), (
                f"proposal_rehearsal_typecheck list items must be Finding; "
                f"got {type(f)}"
            )


def test_mock_adapter_detect_returns_bool(mock_adapter, tmp_path):
    assert isinstance(mock_adapter.detect(tmp_path), bool)


# ---------------------------------------------------------------------------
# Negative contract — each violation must trigger its corresponding check
# ---------------------------------------------------------------------------


def test_negative_missing_name(broken_adapter_factory):
    adapter = broken_adapter_factory("missing_name")
    assert not (isinstance(adapter.name, str) and adapter.name)


def test_negative_empty_detection_signals(broken_adapter_factory):
    adapter = broken_adapter_factory("empty_detection_signals")
    sigs = adapter.detection_signals
    # The check the contract enforces — len > 0 — fails here.
    assert isinstance(sigs, tuple) and len(sigs) == 0


def test_negative_consistency_returns_non_list(broken_adapter_factory):
    adapter = broken_adapter_factory("consistency_returns_non_list")
    assert not isinstance(adapter.consistency_rules(), list)


def test_negative_consistency_returns_invalid_rule(broken_adapter_factory):
    adapter = broken_adapter_factory("consistency_returns_invalid_rule")
    rules = adapter.consistency_rules()
    assert isinstance(rules, list) and not all(
        isinstance(r, ConsistencyRule) for r in rules
    )


def test_negative_empty_architect_addendum(broken_adapter_factory):
    adapter = broken_adapter_factory("empty_architect_addendum")
    assert not adapter.architect_addendum().strip()


def test_negative_gate_dispatch_returns_wrong_type(broken_adapter_factory):
    adapter = broken_adapter_factory("gate_dispatch_returns_wrong_type")
    assert not isinstance(
        adapter.quality_gate_dispatch(ArchitectureManifest()), GateCommands
    )


def test_negative_missing_precheck_method_fails_protocol(broken_adapter_factory):
    adapter = broken_adapter_factory("missing_precheck")
    assert not isinstance(adapter, EcosystemAdapter)


# ---------------------------------------------------------------------------
# Property check the contract test catalog enforces, asserted as a list.
# Adding a new contract check? Add to REQUIRED_METHODS / REQUIRED_ATTRS in
# _protocol.py AND add a positive + negative test above. This sanity check
# ensures the catalog and tests stay in sync with each other.
# ---------------------------------------------------------------------------


def test_required_methods_catalog_is_complete():
    expected = {
        "detect",
        "precheck",
        "consistency_rules",
        "architect_addendum",
        "quality_gate_dispatch",
        "proposal_rehearsal_typecheck",
    }
    assert set(REQUIRED_METHODS) == expected, (
        "REQUIRED_METHODS drifted from the contract test catalog. "
        "Update both _protocol.py and the test list together."
    )


def test_required_attrs_catalog_is_complete():
    expected = {"name", "detection_signals"}
    assert set(REQUIRED_ATTRS) == expected
