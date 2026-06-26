"""Tests for the validator differential-classification gate (precheck)."""

from __future__ import annotations

from pathlib import Path

from darkfactory.spec.anchors import AnchorManifest, ScenarioIntent
from darkfactory.spec.precheck import (
    check_differential_intent_blockers,
    check_differential_markers,
    run_precheck,
)
from darkfactory.spec.spec_model import parse_spec_model


def _mf(*intents):
    return AnchorManifest(intents=list(intents))


def _si(layer, mapped, parent="FR-2", index=1):
    return ScenarioIntent(parent=parent, index=index, text="", layer=layer, mapped_anchor=mapped)


# --- check_differential_intent_blockers (the gate predicate) ----------------

def test_unclassified_mapped_rejection_ac_blocks():
    mf = _mf(_si("security", "AC-13"))
    blockers = check_differential_intent_blockers(mf, [], {})
    assert len(blockers) == 1 and "AC-13" in blockers[0]


def test_differential_marker_clears():
    mf = _mf(_si("security", "AC-13"))
    assert check_differential_intent_blockers(mf, ["AC-13"], {}) == []


def test_invariant_marker_clears():
    mf = _mf(_si("security", "AC-15"))
    assert check_differential_intent_blockers(mf, [], {"AC-15": "no secrets in logs"}) == []


def test_unmapped_intent_does_not_block_here():
    # The UNMAPPED side is the orphan gate's job (disjoint).
    mf = _mf(_si("security", None))
    assert check_differential_intent_blockers(mf, [], {}) == []


def test_non_blocking_layer_not_gated():
    mf = _mf(_si("contract", "AC-6"))
    assert check_differential_intent_blockers(mf, [], {}) == []


def test_mixed_slice():
    mf = _mf(
        _si("security", "AC-13"),                 # unclassified → blocks
        _si("error", "AC-14", parent="FR-3"),     # marked differential → clears
        _si("adversarial", "AC-8", parent="CC"),  # marked invariant → clears
    )
    blockers = check_differential_intent_blockers(mf, ["AC-14"], {"AC-8": "log invariant"})
    assert len(blockers) == 1 and "AC-13" in blockers[0]


# --- check_differential_markers (parsing §8 AC-table markers) ----------------

_SPEC = """# S

## 2. Functional Requirements
### FR-2: auth

## 8. Acceptance Criteria
| ID | Criterion | Blocking |
|----|-----------|----------|
| AC-13 | GIVEN whitespace token ... THEN 401. disc:differential | yes |
| AC-15 | GIVEN any failure WHEN logs inspected THEN no secrets. disc:invariant: no valid sibling, content-scanned | yes |
| AC-2 | GIVEN malformed header THEN 401 | yes |

## 9. Scenario Intent
### FR-2: auth
| # | Intent | Layer | ac_id |
|---|--------|-------|-------|
| 1 | whitespace token rejected | security | AC-13 |
| 2 | secrets never logged | security | AC-15 |
| 3 | malformed header rejected | error | AC-2 |
"""


def test_markers_parsed_from_ac_table():
    model = parse_spec_model(_SPEC)
    differential, invariant = check_differential_markers(model)
    assert "AC-13" in differential
    assert invariant.get("AC-15", "").startswith("no valid sibling")
    assert "AC-2" not in differential and "AC-2" not in invariant  # unclassified


def test_invariant_requires_a_reason():
    # A bare "disc:invariant" with no reason must NOT register (mirrors @untestable)
    # so it can't silently de-scope a real attributable rejection.
    spec = _SPEC.replace("disc:invariant: no valid sibling, content-scanned", "disc:invariant")
    model = parse_spec_model(spec)
    differential, invariant = check_differential_markers(model)
    assert "AC-15" not in invariant  # bare marker not honored


def test_run_precheck_gates_then_clears(tmp_path: Path):
    p = tmp_path / "spec.md"
    p.write_text(_SPEC, encoding="utf-8")
    r = run_precheck(p)
    # AC-2 is a mapped error-layer rejection with no disc marker → REVISE.
    assert r.passed is False
    assert r.fatal is False
    assert any("AC-2" in b for b in r.differential_intent_blockers)

    # Classify AC-2 → the differential gate clears (other checks aside).
    p.write_text(_SPEC.replace("GIVEN malformed header THEN 401", "GIVEN malformed header THEN 401. disc:differential"), encoding="utf-8")
    r2 = run_precheck(p)
    assert r2.differential_intent_blockers == []
