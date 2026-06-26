"""Tests for the differential (positive-control) pairing gen-time check."""

from __future__ import annotations

from darkfactory.scenarios.differential import (
    MISSING_CONTROL,
    MISSING_REJECTION,
    UNPAIRED,
    check_differential_pairs,
)


def _entry(anchor, leg=None, pair_id=None, test_id="hb:x"):
    e = {"test_id": test_id, "spec_anchor": anchor}
    if leg is not None:
        e["leg"] = leg
    if pair_id is not None:
        e["pair_id"] = pair_id
    return e


def test_complete_pair_is_clean():
    entries = [
        _entry("AC-13", leg="control", pair_id="p1", test_id="hb:c"),
        _entry("AC-13", leg="rejection", pair_id="p1", test_id="hb:r"),
    ]
    assert check_differential_pairs(entries, {"AC-13"}) == []


def test_missing_control_is_caught_run20_shape():
    # The run20 shape: a rejection holdout with no positive control.
    entries = [_entry("AC-13", leg="rejection", pair_id="p1")]
    issues = check_differential_pairs(entries, {"AC-13"})
    assert len(issues) == 1
    assert issues[0].anchor == "AC-13"
    assert issues[0].kind == MISSING_CONTROL


def test_no_differential_holdout_at_all_is_caught():
    # AC declared differential but no leg-tagged entries.
    entries = [_entry("AC-13")]  # ordinary holdout, no leg
    issues = check_differential_pairs(entries, {"AC-13"})
    assert len(issues) == 1
    assert issues[0].kind == MISSING_CONTROL


def test_missing_rejection_is_caught():
    entries = [_entry("AC-13", leg="control", pair_id="p1")]
    issues = check_differential_pairs(entries, {"AC-13"})
    assert [i.kind for i in issues] == [MISSING_REJECTION]


def test_both_legs_but_unpaired_pair_id():
    entries = [
        _entry("AC-13", leg="control", pair_id="p1"),
        _entry("AC-13", leg="rejection", pair_id="p2"),  # different pair_id
    ]
    issues = check_differential_pairs(entries, {"AC-13"})
    assert [i.kind for i in issues] == [UNPAIRED]


def test_non_differential_anchor_is_ignored():
    # AC-15 (no-secrets invariant) is NOT in the differential set → no demand,
    # even though it carries a lone rejection leg.
    entries = [_entry("AC-15", leg="rejection", pair_id="p1")]
    assert check_differential_pairs(entries, set()) == []
    assert check_differential_pairs(entries, {"AC-13"}) != []  # AC-13 IS demanded → flagged


def test_ordinary_holdouts_dont_gate():
    # Plain holdouts with no leg and an anchor not declared differential.
    entries = [_entry("AC-2"), _entry("AC-4")]
    assert check_differential_pairs(entries, set()) == []


def test_fail_soft_on_malformed_entries():
    entries = ["not-a-dict", {"leg": "control"}, {"spec_anchor": "AC-13", "leg": "control", "pair_id": "p"}]
    # Only the well-formed control leg counts → AC-13 missing its rejection.
    issues = check_differential_pairs(entries, {"AC-13"})
    assert [i.kind for i in issues] == [MISSING_REJECTION]


def test_multiple_anchors_mixed():
    entries = [
        _entry("AC-13", leg="control", pair_id="p1"),
        _entry("AC-13", leg="rejection", pair_id="p1"),  # AC-13 complete
        _entry("AC-14", leg="rejection", pair_id="p2"),  # AC-14 missing control
    ]
    issues = check_differential_pairs(entries, {"AC-13", "AC-14"})
    assert len(issues) == 1
    assert issues[0].anchor == "AC-14" and issues[0].kind == MISSING_CONTROL


# --- derive_differential_anchors (the slice − invariants) -----------------

from darkfactory.scenarios.differential import derive_differential_anchors
from darkfactory.spec.anchors import ScenarioIntent


def _si(parent, index, layer, mapped):
    return ScenarioIntent(parent=parent, index=index, text="", layer=layer, mapped_anchor=mapped)


def test_derive_slice_is_security_error_adversarial_mapped():
    intents = [
        _si("FR-2", 3, "security", "AC-13"),   # in slice
        _si("FR-3", 5, "error", "AC-14"),       # in slice
        _si("Cross-Cutting", 2, "adversarial", "AC-8"),  # in slice
        _si("FR-3", 3, "contract", "AC-6"),     # NOT in slice (contract)
        _si("FR-2", 1, "error", None),          # unmapped → ignored
    ]
    assert derive_differential_anchors(intents, set()) == {"AC-13", "AC-14", "AC-8"}


def test_invariant_opt_out_is_subtracted():
    intents = [
        _si("FR-2", 3, "security", "AC-13"),    # differential
        _si("Cross-Cutting", 1, "security", "AC-15"),  # no-secrets invariant
    ]
    # AC-15 declared invariant → excluded; AC-13 remains.
    assert derive_differential_anchors(intents, {"AC-15"}) == {"AC-13"}


def test_empty_when_no_blocking_layer_intents():
    intents = [_si("FR-3", 3, "contract", "AC-6"), _si("FR-1", 4, "edge_cases", "AC-7")]
    assert derive_differential_anchors(intents, set()) == set()
