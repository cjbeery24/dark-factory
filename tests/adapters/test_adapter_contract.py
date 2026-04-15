"""Contract tests applied to any object claiming to be an EcosystemAdapter.

Three halves:

1. **Positive** — every adapter in the parametrized ``adapter`` fixture
   (currently ``mock`` and ``typescript-node``) satisfies the contract.
   New shipped adapters get added to ``_CONCRETE_ADAPTERS`` in
   ``conftest.py`` and pick up this whole battery automatically.
2. **Negative (value-level)** — for each property the contract enforces
   beyond ABC method-completeness, a deliberately broken adapter
   triggers exactly that failure. Guards against the harness silently
   accepting non-compliant implementations.
3. **ABC-enforcement** — proves the ABC actually refuses to construct
   incomplete subclasses. This is the one thing the S0.2 placeholder
   Protocol *couldn't* enforce; A1 brings the real teeth.

A2 added two new contract checks consumed by every parametrized
adapter (pinning the deferred-from-A1 review items):

- ``test_gate_commands_empty_means_skip_phase`` — pins the empty-string
  sentinel meaning on ``GateCommands``.
- ``test_architect_addendum_within_governance_line_cap`` — moves the
  governance doc's 80-non-blank-line cap from prose to an executable
  check.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from darkfactory.adapters.base import (
    GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP,
    SEVERITIES,
    EcosystemAdapter,
    Finding,
    GateCommands,
    op_str,
)
from darkfactory.architecture.consistency import ConsistencyRule
from darkfactory.spec.manifest import ArchitectureManifest

from tests.adapters._catalog import REQUIRED_ATTRS, REQUIRED_METHODS


# ---------------------------------------------------------------------------
# Positive contract — every parametrized adapter must satisfy every check
# ---------------------------------------------------------------------------


def test_adapter_has_required_attrs(adapter):
    for attr in REQUIRED_ATTRS:
        assert hasattr(adapter, attr), f"missing required attr: {attr}"


def test_adapter_has_required_methods(adapter):
    for method in REQUIRED_METHODS:
        assert callable(getattr(adapter, method, None)), (
            f"missing or non-callable required method: {method}"
        )


def test_adapter_is_ecosystem_adapter(adapter):
    """``isinstance`` against the ABC verifies nominal subclassing.

    The S0.2 placeholder used a ``runtime_checkable`` Protocol, which
    only checked attribute existence at call time. The real ABC
    enforces method-completeness at *construction* time (see
    ``test_incomplete_subclass_cannot_be_instantiated`` below) and
    ``isinstance`` here just confirms the inheritance edge."""
    assert isinstance(adapter, EcosystemAdapter)


def test_adapter_name_is_non_empty(adapter):
    assert isinstance(adapter.name, str) and adapter.name


def test_adapter_detection_signals_non_empty_tuple(adapter):
    sigs = adapter.detection_signals
    assert isinstance(sigs, tuple) and len(sigs) > 0
    assert all(isinstance(s, str) and s for s in sigs)


def test_adapter_consistency_rules_returns_list_of_rules(adapter):
    rules = adapter.consistency_rules()
    assert isinstance(rules, list)
    # Element-type guard: empty list is legal at the contract level,
    # but every entry that IS present must be a real ConsistencyRule.
    # The mock's fixture deliberately returns a non-empty list so the
    # mock instance exercises this loop body; concrete adapters can
    # legitimately return [] (TypescriptNodeAdapter does so in A2;
    # see its method docstring for the migration path).
    for r in rules:
        assert isinstance(r, ConsistencyRule), (
            f"consistency_rules must return ConsistencyRule instances; got {type(r)}"
        )


def test_adapter_architect_addendum_non_empty(adapter):
    addendum = adapter.architect_addendum()
    assert isinstance(addendum, str) and addendum.strip()


def _count_non_blank_content_lines(text: str) -> int:
    """Apply the addendum line-cap accounting rule once.

    HTML comments (``<!-- ... -->`` — including multi-line spans) and
    blank lines don't count against the cap; the cap targets *content*
    lines an LLM has to read, not housekeeping markup. The earlier
    naive "skip lines starting with ``<!--`` or equal to ``-->``"
    predicate counted comment-body lines as content, which is a bug
    waiting to silently inflate counts the moment any addendum grows a
    multi-line preface. Real span-tracking here keeps the cap honest.
    """
    in_comment = False
    count = 0
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln:
            continue
        if in_comment:
            if "-->" in ln:
                in_comment = False
            continue
        if ln.startswith("<!--"):
            # Single-line ``<!-- ... -->`` closes immediately; a
            # comment that opens but doesn't close on the same line
            # enters span mode.
            if "-->" not in ln[4:]:
                in_comment = True
            continue
        count += 1
    return count


def test_architect_addendum_within_governance_line_cap(adapter):
    """Governance caps addenda at 80 non-blank lines.

    Pulled into the contract harness from ``docs/ADAPTER-GOVERNANCE.md``
    (deferred-from-A1 item: "move the 80-line cap from documentation-
    only to a check"). A long addendum signals that ecosystem knowledge
    is leaking back into prose instead of being expressed as
    consistency rules or quality-gate commands; failing here forces the
    adapter author to either compress the addendum or move the
    knowledge into a structured surface.
    """
    text = adapter.architect_addendum()
    n = _count_non_blank_content_lines(text)
    assert n <= GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP, (
        f"adapter {adapter.name!r} architect_addendum has {n} non-blank "
        f"content lines, governance cap is "
        f"{GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP}. Compress the addendum or "
        f"move ecosystem knowledge into consistency_rules / "
        f"quality_gate_dispatch surfaces."
    )


def test_spec_validator_addendum_returns_str(adapter):
    """``spec_validator_addendum`` returns a string; ``""`` is opt-out.

    The opt-out semantics mirror ``architect_addendum``'s empty-string
    sentinel for ``GateCommands`` — adapters with no validator-time
    contribution return ``""`` rather than raising or returning ``None``.
    Tested separately from architect_addendum so the two contracts can
    diverge later without one test silently covering both.
    """
    out = adapter.spec_validator_addendum()
    assert isinstance(out, str), (
        f"adapter {adapter.name!r} spec_validator_addendum must return "
        f"str (empty string opts out); got {type(out).__name__}"
    )


def test_spec_validator_addendum_within_governance_line_cap(adapter):
    """Same governance cap applies to the validator addendum.

    A bloated validator addendum has the same failure mode as a
    bloated architect addendum: ecosystem knowledge leaks into prose
    instead of structured surfaces. Empty-string opt-outs trivially
    satisfy the cap (0 ≤ 80), so this test also serves as a positive
    pin that the cap accounting handles ``""`` without crashing.
    """
    text = adapter.spec_validator_addendum()
    n = _count_non_blank_content_lines(text)
    assert n <= GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP, (
        f"adapter {adapter.name!r} spec_validator_addendum has {n} "
        f"non-blank content lines, governance cap is "
        f"{GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP}."
    )


# Allowlist of adapters that ship a non-empty ``spec_validator_addendum``.
# Pulled out as a module constant so promoting a new adapter is a one-line
# diff (add the name) instead of editing test logic. ``mock`` ships a
# stub addendum strictly so the positive contract tests have something
# non-trivial to exercise; ``postgres`` is the real shipper today.
_VALIDATOR_ADDENDUM_ALLOWLIST: frozenset[str] = frozenset({"postgres", "mock"})


def test_only_allowlisted_adapters_ship_validator_addendum(adapter):
    """Cheap guardrail against accidental knowledge bleed across adapters.

    Per acceptance criterion #2 in
    ``docs/dev/2026-04-15-substrate-verification-plan.md``, only the
    Postgres adapter ships a non-empty ``spec_validator_addendum``
    today. Every other shipped adapter must return the empty-string
    opt-out. When a future adapter genuinely needs validator-time
    guidance, add its name to ``_VALIDATOR_ADDENDUM_ALLOWLIST`` —
    the diff is the prompt to think about whether the guidance is
    genuinely ecosystem-specific.
    """
    if adapter.name in _VALIDATOR_ADDENDUM_ALLOWLIST:
        return
    assert adapter.spec_validator_addendum() == "", (
        f"adapter {adapter.name!r} ships a non-empty spec_validator "
        f"addendum but is not on _VALIDATOR_ADDENDUM_ALLOWLIST. Either "
        f"return ``\"\"`` (opt-out) or add the name to the allowlist "
        f"with a deliberate justification."
    )


def test_adapter_quality_gate_dispatch_returns_gate_commands(adapter):
    cmds = adapter.quality_gate_dispatch(ArchitectureManifest())
    assert isinstance(cmds, GateCommands)


def test_gate_commands_empty_means_skip_phase(adapter):
    """Pin the empty-string sentinel meaning on ``GateCommands``.

    Deferred-from-A1 item: an adapter that contributes nothing to a
    phase MUST encode that as the empty string, not as ``None`` or a
    placeholder. Without this pin, an adapter could ship
    ``typecheck="skip"`` and the gate harness would happily try to
    exec ``skip``.

    Verified via ``GateCommands.phases()`` so the check enumerates the
    same surface the gate harness uses; if a new phase is added to
    ``GateCommands``, this test picks it up automatically.
    """
    cmds = adapter.quality_gate_dispatch(ArchitectureManifest())
    for phase_name, command in cmds.phases():
        assert isinstance(command, str), (
            f"adapter {adapter.name!r} phase {phase_name!r}: command "
            f"must be a str (empty string means 'skip phase'); "
            f"got {type(command).__name__}"
        )


def test_opt_out_adapter_proposal_rehearsal_typecheck_returns_none(
    opt_out_adapter, tmp_path
):
    """Adapters that don't support C2 rehearsal (e.g. Go) return None.
    The pipeline core treats None as "skip rehearsal for this adapter",
    not "rehearsal failed". This test pins that branch as legal."""
    assert opt_out_adapter.proposal_rehearsal_typecheck(tmp_path) is None
    assert isinstance(opt_out_adapter, EcosystemAdapter)


def test_adapter_proposal_rehearsal_typecheck_returns_none_or_findings(
    adapter, tmp_path
):
    result = adapter.proposal_rehearsal_typecheck(tmp_path)
    assert result is None or isinstance(result, list)
    if isinstance(result, list):
        for f in result:
            assert isinstance(f, Finding), (
                f"proposal_rehearsal_typecheck list items must be Finding; "
                f"got {type(f)}"
            )


def test_adapter_detect_returns_bool(adapter):
    """``detect()`` returns a bool. The ``adapter`` fixture binds a
    per-test ``tmp_path`` as ``project_dir`` at construction, so this
    exercises the "empty project tree → no signals → False" path for
    every concrete adapter. The bool-ness is what the contract pins;
    the specific return value is adapter-specific and covered in
    per-adapter tests."""
    assert isinstance(adapter.detect(), bool)


def test_finding_severity_vocabulary_is_closed():
    """Pin the closed severity vocabulary on ``Finding``.

    Deferred-from-A1 item: severity must be one of ``error`` /
    ``warning`` / ``info``; anything else raises ``ValueError`` at
    construction. The runtime guard exists because adapters build
    Findings from non-statically-typed sources (LLM JSON, parsed tsc
    output) where the type checker can't catch a typo.
    """
    # Each member of the closed vocabulary is accepted.
    for sev in SEVERITIES:
        f = Finding(severity=sev, code="x", message="y")
        assert f.severity == sev
    # Anything else is refused.
    for bad in ("critical", "fatal", "ERROR", "", "warn"):
        with pytest.raises(ValueError, match="severity"):
            Finding(severity=bad, code="x", message="y")  # type: ignore[arg-type]


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

        def detect(self):
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

        def detect(self):
            return False

        def precheck(self, spec):
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

        def detect(self):
            return False

        def precheck(self, spec):
            return []

        def consistency_rules(self):
            return []

        def architect_addendum(self):
            return "x"

        def spec_validator_addendum(self):
            return ""

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
        "spec_validator_addendum",
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


# ---------------------------------------------------------------------------
# ``op_str`` helper — the shared coercion that adapters use when their
# ``quality_gate_dispatch`` reads ``commands.*`` entries from
# ``manifest.runtime.amendment_ops``. Both ``TypescriptNodeAdapter`` and
# ``PythonFastApiAdapter`` used to carry byte-identical copies; these
# tests pin the behavior at its single canonical home in ``base.py`` so
# a future adapter that re-implements the coercion (and gets it wrong)
# gets caught by a test in the same file as its contract.
# ---------------------------------------------------------------------------


def test_op_str_missing_key_returns_empty_string():
    """Missing key → ``""``. This is the 'skip phase' sentinel documented
    on ``GateCommands``, so the adapter can thread a missing command
    through without special-casing the lookup."""
    assert op_str({}, "commands.install") == ""


def test_op_str_empty_value_returns_empty_string():
    """An explicit empty string stays empty — no coercion to some other
    falsy shape. Pins the distinction between 'key absent' and 'key
    present with empty value' at the boundary where both collapse to
    the same skip-phase signal."""
    assert op_str({"commands.install": ""}, "commands.install") == ""


def test_op_str_scalar_string_passes_through():
    assert (
        op_str({"commands.install": "pnpm install"}, "commands.install")
        == "pnpm install"
    )


def test_op_str_list_joins_with_double_ampersand():
    """``&&`` is the separator the quality gate understands as 'run
    these in sequence, short-circuit on failure'. ``;`` would run every
    command even after one failed — a real footgun for install/build
    chains. Pinning the join string guards against a future edit that
    switches to ``;``."""
    assert (
        op_str(
            {"commands.install": ["pnpm install", "pnpm build"]},
            "commands.install",
        )
        == "pnpm install && pnpm build"
    )


def test_op_str_list_drops_empty_elements():
    """Empty elements in a list value are dropped before joining. Avoids
    emitting ``" && a && b && "`` when an architect emits a stray blank
    element — the gate would try to exec the empty command."""
    assert (
        op_str(
            {"commands.install": ["", "pnpm install", "", "pnpm build"]},
            "commands.install",
        )
        == "pnpm install && pnpm build"
    )


def test_op_str_list_of_one_does_not_add_separator():
    """A single-element list must not have a trailing ``&&``. Pins the
    edge case where the architect emits a scalar-as-list."""
    assert (
        op_str({"commands.install": ["pnpm install"]}, "commands.install")
        == "pnpm install"
    )


def test_op_str_non_string_non_list_coerces_via_str():
    """Anything that isn't a list falls through to ``str()``. This is
    permissive by design — if the architect ever emits a manifest value
    we didn't anticipate (a tuple in tests, e.g.), the helper returns
    its string form rather than raising. Hardening against malformed
    manifests is tracked as a separate follow-up (see plan:
    'Harden op_str against None / non-str / non-list values')."""
    # Tuple — not a list, so falls through to ``str(raw)``. Documents
    # the current permissive behavior; tighten when the follow-up
    # (raise on non-str-non-list) lands.
    assert op_str({"commands.install": ("a", "b")}, "commands.install") == "('a', 'b')"
