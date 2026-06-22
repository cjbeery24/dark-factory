"""Tests for the validator-time substrate-deferral gate's DETERMINISTIC parts
(precondition extraction, prompt construction, output parsing). The single
SEMANTIC step (outcome-vs-mechanism) is an LLM call measured separately
(16/16 EXACT, sonnet-4-6 + opus-4-8) — not unit-tested here.
"""

import json

from darkfactory.spec.deferral_check import (
    build_deferral_check_prompt,
    build_elicitation_prompt,
    deferral_report_path,
    extract_deferral_check_inputs,
    extract_elicitation_inputs,
    parse_deferral_check_output,
    parse_elicitation_output,
    write_deferral_consistency_report,
    _deferred_primitive_names,
    _default_verify_primitive_types,
)

SUBSTRATE = """\
## Substrate Primitives

```toml
[[guc]]
name = "app.tenant_id"

[[tenant_isolation]]
tenant_table = "tenants"
verifiable = false

[[rls_policy]]
name = "f1c_baseline_no_policies"
verifiable = false
```

## 13. Free Variables
"""

SPEC = (
    SUBSTRATE
    + """
## 8. Acceptance Criteria

| ID | Criterion | Blocking |
|----|-----------|:-:|
| AC-13 | GIVEN tenantId undefined WHEN withDb THEN throws TENANT_REQUIRED | yes |
| AC-14 | GIVEN rows for tenants A and B WHEN A queries THEN only A's rows | yes |
| AC-15 | GIVEN rows for A and B WHEN A queries THEN only A's rows (non-blocking) | no |
| AC-16 | GIVEN the signature WHEN typechecked THEN it matches | @untestable: tsc |
"""
)


# --- precondition extraction ----------------------------------------------

def test_deferred_primitives_detected_by_boolean_identity():
    names = _deferred_primitive_names(SUBSTRATE)
    # tenant_isolation + rls_policy are verifiable=false; guc is not.
    assert any("tenant_isolation" in n for n in names)
    assert any("rls_policy" in n for n in names)
    assert not any("guc" in n for n in names)
    assert len(names) == 2


def test_verifiable_true_and_absent_and_string_not_deferred():
    block = """\
```toml
[[a]]
name = "x"
verifiable = true

[[b]]
name = "y"

[[c]]
name = "z"
verifiable = "false"
```
"""
    assert _deferred_primitive_names(block) == []


def test_extract_returns_inputs_when_precondition_met():
    di = extract_deferral_check_inputs(SPEC, {"AC-13", "AC-14"})
    assert di is not None
    assert len(di.deferred_primitive_names) == 2
    # WIDE candidate set: all non-@untestable AC rows (incl. the non-blocking
    # AC-15); the §8a-annotated AC-16 (@untestable) is excluded.
    assert di.candidate_anchors == ["AC-13", "AC-14", "AC-15"]
    assert di.blocking_anchors == ["AC-13", "AC-14"]
    assert "AC-15" in di.anchor_text and "AC-16" not in di.anchor_text


def test_annotated_anchors_excluded_from_candidates():
    """@untestable / vehicle:subprocess rows are already out of scenario
    generation → never candidates (can't be an unsatisfiable gap)."""
    di = extract_deferral_check_inputs(SPEC, {"AC-13"})
    assert "AC-16" not in di.candidate_anchors


def test_extract_returns_inputs_with_no_blocking_anchors():
    """WIDE-set: the precondition is a verifiable=false primitive + ≥1
    CANDIDATE (not necessarily Blocking) — so a spec with only non-blocking
    deferred-outcome ACs (the EC-12 class) still triggers the gate."""
    di = extract_deferral_check_inputs(SPEC, set())
    assert di is not None
    assert di.candidate_anchors == ["AC-13", "AC-14", "AC-15"]
    assert di.blocking_anchors == []


def test_extract_none_when_no_deferred_primitive():
    spec = SPEC.replace("verifiable = false", "verifiable = true")
    assert extract_deferral_check_inputs(spec, {"AC-13"}) is None


def test_extract_none_when_no_candidates():
    """No AC/EC rows at all → nothing to judge → skip."""
    spec = SUBSTRATE + "\n## 8. Acceptance Criteria\n\n(none yet)\n"
    assert extract_deferral_check_inputs(spec, set()) is None


def test_extract_none_when_no_substrate_section():
    spec = "## 8. Acceptance Criteria\n| AC-1 | x | yes |\n"
    assert extract_deferral_check_inputs(spec, {"AC-1"}) is None


def test_annotation_excluded_in_4column_ac_table(tmp_path):
    """Linus C1: a 4-column AC table puts the @untestable annotation in a
    TRAILING column, not cells[3]. The whole-row scan must still exclude it."""
    spec = SUBSTRATE + """
## 8. Acceptance Criteria

| ID | Criterion | Sprint | Blocking |
|----|-----------|--------|:-:|
| AC-40 | GIVEN the signature WHEN typechecked THEN it matches | S3 | @untestable: tsc |
| AC-41 | GIVEN rows for A,B WHEN A queries THEN only A's rows | S3 | yes |
"""
    di = extract_deferral_check_inputs(spec, {"AC-41"})
    assert "AC-40" not in di.candidate_anchors  # annotation in trailing col
    assert "AC-41" in di.candidate_anchors


def test_inline_annotated_ec_excluded(tmp_path):
    """Linus C2: EC tables have no annotation column — annotations live INLINE
    in the prose. The whole-row scan must exclude an inline vehicle:subprocess
    EC, and must NOT mis-exclude a plain isolation EC."""
    spec = SUBSTRATE + """
## Edge Cases

| ID | Condition | Expected Behavior |
|----|-----------|-------------------|
| EC-20 | migrate run twice | no-op on 2nd call (vehicle: subprocess — re-invocation) |
| EC-21 | concurrent A,B requests | each sees only its own tenant's rows |
"""
    di = extract_deferral_check_inputs(spec, set())
    assert "EC-20" not in di.candidate_anchors  # inline vehicle:subprocess
    assert "EC-21" in di.candidate_anchors


def test_criterion_prose_marker_does_not_exclude(tmp_path):
    """Linus follow-up: the regex matches annotation FORMS, not bare nouns — an
    isolation AC whose CRITERION prose mentions 'subprocess' must stay a
    candidate (else the gate silently no-ops on a real deferred-outcome anchor)."""
    spec = SUBSTRATE + """
## 8. Acceptance Criteria

| ID | Criterion | Blocking |
|----|-----------|:-:|
| AC-50 | GIVEN a subprocess seeds rows for A and B WHEN B queries THEN B sees only B's rows | yes |
"""
    di = extract_deferral_check_inputs(spec, {"AC-50"})
    assert di is not None
    assert "AC-50" in di.candidate_anchors


# --- prompt construction --------------------------------------------------

def test_prompt_contains_primitives_candidates_and_discriminators():
    di = extract_deferral_check_inputs(SPEC, {"AC-13", "AC-14"})
    prompt = build_deferral_check_prompt(di)
    assert "verifiable = false" in prompt
    # All candidates present (incl. non-blocking AC-15); annotated AC-16 absent.
    assert "AC-13" in prompt and "AC-15" in prompt and "AC-16" not in prompt
    # Both discriminators that gave FP=0 must be in the prompt.
    assert "OUTCOME" in prompt and "MECHANISM" in prompt
    assert "different capability" in prompt.lower()
    assert "JSON array" in prompt


# --- output parsing -------------------------------------------------------

def test_parse_clean_json_array():
    assert parse_deferral_check_output('["AC-14","AC-16","EC-5"]') == {
        "AC-14", "AC-16", "EC-5"
    }


def test_parse_tolerates_surrounding_prose():
    out = 'Here is my answer:\n["AC-14", "AC-18"]\nDone.'
    assert parse_deferral_check_output(out) == {"AC-14", "AC-18"}


def test_parse_empty_array():
    assert parse_deferral_check_output("[]") == set()


def test_parse_lowercase_normalized():
    assert parse_deferral_check_output('["ac-14"]') == {"AC-14"}


def test_parse_ignores_noncanonical_and_nonstring():
    assert parse_deferral_check_output('["AC-14", 14, "not-an-anchor"]') == {"AC-14"}


def test_parse_no_array_yields_empty():
    assert parse_deferral_check_output("no findings here") == set()


# --- shared report writer (consumed by /df-rescope-deferred) --------------

def test_write_and_locate_report(tmp_path):
    reviews = tmp_path / "reviews"
    findings = [
        {"kind": "BLOCKING_OVER_DEFERRED", "primitive": "tenant_isolation",
         "anchor": "AC-14", "message": "drop from Non-Negotiable"},
        {"kind": "LIVE_SCENARIO_OVER_DEFERRED", "primitive": "tenant_isolation",
         "anchor": "AC-14", "message": "mark @untestable"},
    ]
    path = write_deferral_consistency_report(reviews, "my-spec", "run123", findings)
    assert path == deferral_report_path(reviews, "my-spec")
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["spec"] == "my-spec" and data["run_id"] == "run123"
    assert {f["anchor"] for f in data["findings"]} == {"AC-14"}
    assert {f["kind"] for f in data["findings"]} == {
        "BLOCKING_OVER_DEFERRED", "LIVE_SCENARIO_OVER_DEFERRED"
    }


# --- ELICITATION (upstream: surface the need for verifiable=false) ---------

ELICIT_SPEC = SUBSTRATE + """
## 3. Functional Requirements

- FR-1: the app.tenant_id GUC is set per transaction via set_config.

## Dependencies & Out of Scope

- Entity schemas (tenant tables) — defined by F-2 onward.
- Concrete row-filtering policies — declared per-table by F-2+.
"""


def test_default_verify_types_excludes_verifiable_false():
    # SUBSTRATE: guc default-verify; tenant_isolation + rls_policy verifiable=false.
    assert _default_verify_primitive_types(SUBSTRATE) == ["guc"]


def test_default_verify_types_when_stripped():
    stripped = SUBSTRATE.replace("verifiable = false", "")
    assert set(_default_verify_primitive_types(stripped)) == {
        "guc", "tenant_isolation", "rls_policy"
    }


def test_extract_elicitation_inputs_candidates_and_sections():
    ei = extract_elicitation_inputs(ELICIT_SPEC)
    assert ei is not None
    assert ei.candidate_primitives == ["guc"]
    assert "Functional Requirements" in ei.functional_requirements
    assert "F-2" in ei.out_of_scope


def test_extract_elicitation_none_when_all_verifiable_false():
    spec = ELICIT_SPEC.replace(
        '[[guc]]\nname = "app.tenant_id"',
        '[[guc]]\nname = "app.tenant_id"\nverifiable = false',
    )
    assert extract_elicitation_inputs(spec) is None


def test_extract_elicitation_none_when_no_substrate():
    assert extract_elicitation_inputs("## 3. Functional Requirements\n- FR-1\n") is None


def test_elicitation_prompt_contains_candidates_and_scope_framing():
    prompt = build_elicitation_prompt(extract_elicitation_inputs(ELICIT_SPEC))
    assert "guc" in prompt
    assert "OUT OF SCOPE" in prompt and "FUNCTIONAL REQUIREMENTS" in prompt
    assert "DEFERRED" in prompt and "gate-verified" in prompt


def test_parse_elicitation_filters_to_candidates():
    out = '["tenant_isolation", "not_a_primitive"]'
    assert parse_elicitation_output(out, ["tenant_isolation", "rls_policy"]) == {
        "tenant_isolation"
    }


def test_parse_elicitation_empty_and_prose():
    assert parse_elicitation_output("none: []", ["guc"]) == set()
    assert parse_elicitation_output("no array here", ["guc"]) == set()


def test_fr_extraction_stops_at_non_functional_requirements():
    """Linus C1: the FR pattern must NOT bleed into '## N. Non-Functional
    Requirements' (the feature-spec template's very next heading)."""
    spec = SUBSTRATE.replace("verifiable = false", "") + """
## 2. Functional Requirements

- FR-1: the GUC is set per transaction.

## 3. Non-Functional Requirements

- NFR-1: p99 latency under 50ms; OpenTelemetry observability on every span.
"""
    ei = extract_elicitation_inputs(spec)
    assert "FR-1" in ei.functional_requirements
    assert "NFR-1" not in ei.functional_requirements
    assert "OpenTelemetry" not in ei.functional_requirements  # NFR leak guard


def test_fr_extraction_matches_unnumbered_heading():
    spec = SUBSTRATE.replace("verifiable = false", "") + """
## Functional Requirements

- FR-1: the GUC is set.
"""
    ei = extract_elicitation_inputs(spec)
    assert "FR-1" in ei.functional_requirements


def test_out_of_scope_merges_dependencies_and_scope_sections():
    """Q1: Dependencies + a later Out of Scope are both deferral context and are
    captured together (load-bearing on the same regex-merge quirk)."""
    spec = SUBSTRATE.replace("verifiable = false", "") + """
## Dependencies

- depends on F-1b env validation.

## Out of Scope

- Entity schemas — F-2 onward.
"""
    ei = extract_elicitation_inputs(spec)
    assert "F-1b" in ei.out_of_scope and "F-2 onward" in ei.out_of_scope
