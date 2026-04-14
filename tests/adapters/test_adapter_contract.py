"""Contract tests applied to any object claiming to be an EcosystemAdapter.

Three halves:

1. **Positive** — ``mock_adapter`` (from conftest) satisfies the
   contract. If this fails, the harness itself is broken.
2. **Negative (value-level)** — for each property the contract enforces
   beyond ABC method-completeness, a deliberately broken adapter
   triggers exactly that failure. Guards against the harness silently
   accepting non-compliant implementations.
3. **ABC-enforcement** — proves the ABC actually refuses to construct
   incomplete subclasses. This is the one thing the S0.2 placeholder
   Protocol *couldn't* enforce; A1 brings the real teeth.

To validate a real adapter, parametrize the positive half over a fixture
list. When A2 ships ``TypescriptNodeAdapter``, add::

    @pytest.fixture(params=[mock_adapter, ts_node_adapter])
    def adapter(request): ...

and the existing tests apply automatically.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from darkfactory.adapters.base import EcosystemAdapter, Finding, GateCommands
from darkfactory.architecture.consistency import ConsistencyRule
from darkfactory.spec.manifest import ArchitectureManifest

from tests.adapters._catalog import REQUIRED_ATTRS, REQUIRED_METHODS


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


def test_mock_adapter_is_ecosystem_adapter(mock_adapter):
    """``isinstance`` against the ABC verifies nominal subclassing.

    The S0.2 placeholder used a ``runtime_checkable`` Protocol, which
    only checked attribute existence at call time. The real ABC
    enforces method-completeness at *construction* time (see
    ``test_incomplete_subclass_cannot_be_instantiated`` below) and
    ``isinstance`` here just confirms the inheritance edge."""
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
# Negative contract (value-level) — each violation must trigger its
# corresponding check. Method-completeness violations are covered by
# the ABC-enforcement section below, not here, because ABC refuses to
# construct such subclasses at all.
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


# ---------------------------------------------------------------------------
# ABC-enforcement — the section the placeholder Protocol couldn't have.
#
# These tests prove that ``darkfactory.adapters.base.EcosystemAdapter``
# refuses to construct incomplete subclasses at instantiation time.
# That refusal is the structural guarantee replacing the S0.2 Protocol's
# attribute-existence check; lose it and an adapter author can ship a
# class that only fails when the missing method is finally called —
# weeks into a pipeline run.
# ---------------------------------------------------------------------------


def test_incomplete_subclass_cannot_be_instantiated():
    """A subclass that omits every abstract method must fail at
    construction with a ``TypeError`` naming the missing methods."""

    class _Incomplete(EcosystemAdapter):
        # Supplies the two abstract properties via class attributes
        # so the failure message focuses on the method omissions.
        name = "incomplete"
        detection_signals = ("x",)
        # Deliberately omits every abstract method.

    with pytest.raises(TypeError, match=r"abstract"):
        _Incomplete()


def test_subclass_missing_one_method_cannot_be_instantiated():
    """A subclass that omits a single abstract method (``precheck``)
    must still fail at construction. Pins that omitting *one* method is
    no different from omitting all of them — there is no partial
    contract."""

    class _MissingPrecheck(EcosystemAdapter):
        name = "missing-precheck"
        detection_signals = ("x",)

        def detect(self, project_dir):
            return False

        # precheck deliberately omitted

        def consistency_rules(self):
            return []

        def architect_addendum(self):
            return "x"

        def quality_gate_dispatch(self, manifest):
            return GateCommands()

        def proposal_rehearsal_typecheck(self, scratch_dir):
            return None

    with pytest.raises(TypeError, match=r"precheck"):
        _MissingPrecheck()


def test_subclass_missing_name_cannot_be_instantiated():
    """A subclass that omits the ``name`` abstract property must also
    fail. Proves that the ``@property + @abstractmethod`` pattern on
    ``name``/``detection_signals`` provides the same enforcement
    guarantee as ``@abstractmethod`` on the methods — without it,
    forgetting to set ``name`` would be an ``AttributeError`` at first
    use, not a construction-time refusal."""

    class _MissingName(EcosystemAdapter):
        # name deliberately omitted
        detection_signals = ("x",)

        def detect(self, project_dir):
            return False

        def precheck(self, spec, project_dir):
            return []

        def consistency_rules(self):
            return []

        def architect_addendum(self):
            return "x"

        def quality_gate_dispatch(self, manifest):
            return GateCommands()

        def proposal_rehearsal_typecheck(self, scratch_dir):
            return None

    with pytest.raises(TypeError, match=r"name"):
        _MissingName()


def test_dataclass_fields_satisfy_abstract_properties(mock_adapter):
    """``_MockAdapter`` is a dataclass that supplies ``name`` and
    ``detection_signals`` as field defaults rather than as ``@property``
    overrides. This pattern (dataclass + ABC, fields shadow abstract
    properties) is the one adapter authors are expected to use; pin it
    so a future Python release or refactor doesn't quietly break it."""
    # Construction succeeded → ABC accepted the field-default override.
    # Verify the values reach instance access cleanly.
    assert isinstance(mock_adapter.name, str)
    assert isinstance(mock_adapter.detection_signals, tuple)


def test_property_override_satisfies_abstract_property():
    """The *second* documented pattern in ``base.py``'s module docstring:
    supplying ``name``/``detection_signals`` as real ``@property``
    overrides (e.g., when the value is computed from a lockfile rather
    than hard-coded). Without this test, only the dataclass-field
    pattern is pinned — and a future ABC refactor that broke real
    properties wouldn't surface here, even though adapter authors are
    explicitly told both patterns are legal."""

    class _PropAdapter(EcosystemAdapter):
        @property
        def name(self) -> str:
            return "prop-adapter"

        @property
        def detection_signals(self) -> tuple[str, ...]:
            return ("derived.lockfile",)

        def detect(self, project_dir):
            return False

        def precheck(self, spec, project_dir):
            return []

        def consistency_rules(self):
            return []

        def architect_addendum(self):
            return "x"

        def quality_gate_dispatch(self, manifest):
            return GateCommands()

        def proposal_rehearsal_typecheck(self, scratch_dir):
            return None

    adapter = _PropAdapter()
    assert isinstance(adapter, EcosystemAdapter)
    assert adapter.name == "prop-adapter"
    assert adapter.detection_signals == ("derived.lockfile",)


# ---------------------------------------------------------------------------
# Catalog-drift guards — when an abstract method/attr is added to the
# ABC, REQUIRED_METHODS / REQUIRED_ATTRS must be updated together with
# new positive + negative tests. These guards fail until the catalogs
# match the ABC's actual abstract surface.
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
        "Update both _catalog.py and the test list together."
    )


def test_required_attrs_catalog_is_complete():
    expected = {"name", "detection_signals"}
    assert set(REQUIRED_ATTRS) == expected


def test_catalog_matches_abc_abstract_surface():
    """The catalogs must enumerate exactly the ABC's abstract members.

    If someone adds a new ``@abstractmethod`` to the ABC and forgets to
    update the catalog, this test fails — that's the whole point of
    keeping a catalog. Without this tripwire, the per-method positive
    test could silently never get written."""
    abstract = set(EcosystemAdapter.__abstractmethods__)
    catalog = set(REQUIRED_METHODS) | set(REQUIRED_ATTRS)
    assert abstract == catalog, (
        f"ABC abstract members {sorted(abstract)} != catalog "
        f"{sorted(catalog)}. Update _catalog.py *and* add per-member "
        f"positive/negative tests."
    )
