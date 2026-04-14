"""E2 — calibration dataset row schema, validation, and append.

Motivated by the 2026-04-14 database-foundation post-mortem. See
``docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md``
(Sprint 1, E2).
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from darkfactory.calibration.post_mortem import (
    OUTCOMES,
    ROOT_CAUSE_CODES,
    SCHEMA_VERSION,
    VALIDATOR_DIVERGENCE,
    VALIDATOR_VERDICTS,
    PostMortemRow,
    append_row,
    default_dataset_path,
    read_rows,
)


# ---------------------------------------------------------------------------
# Schema sanity
# ---------------------------------------------------------------------------


def test_vocab_includes_plan_acceptance_codes():
    """Plan E2 acceptance row uses these three codes — they must be in vocab."""
    for code in ("broken_scenarios", "architect_spec_drift", "slow_implementer"):
        assert code in ROOT_CAUSE_CODES, (
            f"plan acceptance row references {code!r}; "
            f"it must be in the canonical vocabulary"
        )


def test_outcomes_are_lowercase_strings():
    """JSONL is JSON; enums would force a serializer. Strings only."""
    for o in OUTCOMES:
        assert o.islower()


def test_validator_verdicts_are_uppercase_strings():
    for v in VALIDATOR_VERDICTS:
        assert v.isupper()


def test_validator_divergence_values():
    assert VALIDATOR_DIVERGENCE == frozenset({"LOW", "MEDIUM", "HIGH"})


# ---------------------------------------------------------------------------
# Required-field validation — every guardrail must fire
# ---------------------------------------------------------------------------


def _baseline_kwargs(**overrides) -> dict:
    base = dict(
        spec_id="2026-03-28-database-foundation",
        run_id="034c318ff2eb4ef3",
        date="2026-04-14",
        target_project="budget-brain-v2",
        outcome="escalated",
        iterations=3,
        validator_verdict="PASS",
        validator_score=89,
        validator_divergence="LOW",
        validator_blockers=("missing_migration_journal",),
        stuck_set_categories=("library_internals", "rls_invariant_violation"),
        root_cause_codes=("broken_scenarios", "architect_spec_drift", "slow_implementer"),
        dark_factory_head="a276820",
        notes="see docs/pipeline-run-reports/2026-04-14-04-45-...md",
    )
    base.update(overrides)
    return base


def test_baseline_row_constructs_clean():
    row = PostMortemRow(**_baseline_kwargs())
    assert row.schema_version == SCHEMA_VERSION
    assert row.outcome == "escalated"


def test_blank_spec_id_rejected():
    with pytest.raises(ValueError, match="spec_id"):
        PostMortemRow(**_baseline_kwargs(spec_id=""))


def test_blank_run_id_rejected():
    with pytest.raises(ValueError, match="run_id"):
        PostMortemRow(**_baseline_kwargs(run_id="   "))


def test_blank_target_project_rejected():
    with pytest.raises(ValueError, match="target_project"):
        PostMortemRow(**_baseline_kwargs(target_project=""))


def test_malformed_date_rejected():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        PostMortemRow(**_baseline_kwargs(date="14 April 2026"))


def test_unknown_outcome_rejected():
    with pytest.raises(ValueError, match="outcome must be one of"):
        PostMortemRow(**_baseline_kwargs(outcome="maybe"))


def test_unknown_validator_verdict_rejected():
    with pytest.raises(ValueError, match="validator_verdict"):
        PostMortemRow(**_baseline_kwargs(validator_verdict="SOFTPASS"))


def test_unknown_validator_divergence_rejected():
    with pytest.raises(ValueError, match="validator_divergence"):
        PostMortemRow(**_baseline_kwargs(validator_divergence="EXTREME"))


def test_validator_divergence_allows_none():
    row = PostMortemRow(**_baseline_kwargs(validator_divergence=None))
    assert row.validator_divergence is None


def test_negative_iterations_rejected():
    with pytest.raises(ValueError, match="iterations"):
        PostMortemRow(**_baseline_kwargs(iterations=-1))


def test_score_out_of_range_rejected():
    with pytest.raises(ValueError, match="validator_score"):
        PostMortemRow(**_baseline_kwargs(validator_score=120))


def test_score_allows_none():
    row = PostMortemRow(**_baseline_kwargs(validator_score=None))
    assert row.validator_score is None


def test_unknown_root_cause_code_rejected():
    """A free-form code defeats the whole calibration premise."""
    with pytest.raises(ValueError, match="unknown root_cause_codes"):
        PostMortemRow(
            **_baseline_kwargs(
                root_cause_codes=("broken_scenarios", "vibes"),
            )
        )


def test_root_cause_codes_must_be_snake_case():
    """Belt-and-braces: even if a typo somehow hit the vocab, shape-check it."""
    # Inject a malformed code into the vocab via monkeypatch is too fragile;
    # instead, ensure all canonical codes pass the shape rule.
    import re
    pattern = re.compile(r"^[a-z][a-z0-9_]*$")
    for code in ROOT_CAUSE_CODES:
        assert pattern.match(code), (
            f"vocabulary code {code!r} violates snake_case shape rule"
        )


def test_notes_must_be_one_line():
    with pytest.raises(ValueError, match="one-liner"):
        PostMortemRow(**_baseline_kwargs(notes="line one\nline two"))


def test_row_is_frozen():
    row = PostMortemRow(**_baseline_kwargs())
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.outcome = "shipped"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


def test_to_dict_starts_with_schema_version():
    """schema_version must lead so a reader can short-circuit on mismatch."""
    row = PostMortemRow(**_baseline_kwargs())
    d = row.to_dict()
    assert next(iter(d)) == "schema_version"


def test_to_dict_serializes_to_json():
    row = PostMortemRow(**_baseline_kwargs())
    payload = json.dumps(row.to_dict())
    parsed = json.loads(payload)
    # tuples are lists in JSON
    assert parsed["root_cause_codes"] == [
        "broken_scenarios", "architect_spec_drift", "slow_implementer",
    ]
    assert parsed["validator_blockers"] == ["missing_migration_journal"]


def test_round_trip_via_jsonl(tmp_path):
    path = tmp_path / "post-mortem-dataset.jsonl"
    original = PostMortemRow(**_baseline_kwargs())
    append_row(original, path=path)

    rows = read_rows(path)
    assert len(rows) == 1
    assert rows[0] == original  # frozen dataclass equality


def test_append_creates_parent_dir(tmp_path):
    path = tmp_path / "deep" / "nested" / "post-mortem-dataset.jsonl"
    row = PostMortemRow(**_baseline_kwargs())
    append_row(row, path=path)
    assert path.exists()


def test_append_two_rows_yields_two_lines(tmp_path):
    path = tmp_path / "post-mortem-dataset.jsonl"
    append_row(PostMortemRow(**_baseline_kwargs()), path=path)
    append_row(
        PostMortemRow(
            **_baseline_kwargs(
                run_id="other-run",
                outcome="shipped",
                stuck_set_categories=(),
                root_cause_codes=(),
            )
        ),
        path=path,
    )
    rows = read_rows(path)
    assert len(rows) == 2
    assert rows[0].outcome == "escalated"
    assert rows[1].outcome == "shipped"


def test_read_rejects_unsupported_schema_version(tmp_path):
    path = tmp_path / "post-mortem-dataset.jsonl"
    payload = {"schema_version": 999, "spec_id": "x"}
    path.write_text(json.dumps(payload) + "\n")
    with pytest.raises(ValueError, match="unsupported schema_version"):
        read_rows(path)


def test_read_rejects_missing_required_field_with_source_context(tmp_path):
    """A hand-edited row missing a required key must surface file:line context."""
    path = tmp_path / "post-mortem-dataset.jsonl"
    # Valid schema_version, but missing run_id.
    payload = {
        "schema_version": 1,
        "spec_id": "x",
        "date": "2026-04-14",
        "target_project": "y",
        "outcome": "shipped",
        "iterations": 1,
        "validator_verdict": "PASS",
    }
    path.write_text(json.dumps(payload) + "\n")
    with pytest.raises(ValueError, match=r":1: missing required field 'run_id'"):
        read_rows(path)


def test_read_rejects_invalid_json(tmp_path):
    path = tmp_path / "post-mortem-dataset.jsonl"
    path.write_text("{not json\n")
    with pytest.raises(ValueError, match="invalid JSON"):
        read_rows(path)


def test_read_skips_blank_lines(tmp_path):
    path = tmp_path / "post-mortem-dataset.jsonl"
    row = PostMortemRow(**_baseline_kwargs())
    append_row(row, path=path)
    # inject blank lines
    with path.open("a") as f:
        f.write("\n\n   \n")
    append_row(row, path=path)
    rows = read_rows(path)
    assert len(rows) == 2


def test_read_missing_file_returns_empty(tmp_path):
    assert read_rows(tmp_path / "missing.jsonl") == []


# ---------------------------------------------------------------------------
# Plan E2 acceptance — the row this whole sprint exists to capture
# ---------------------------------------------------------------------------


def test_plan_acceptance_row_constructs_and_serializes():
    """Plan E2:
    > This run (database-foundation) appends a row capturing
    > verdict=PASS, score=89, outcome=escalated,
    > root_causes=[broken_scenarios, architect_spec_drift, slow_implementer]
    """
    row = PostMortemRow(
        spec_id="2026-03-28-database-foundation",
        run_id="034c318ff2eb4ef3",
        date="2026-04-14",
        target_project="budget-brain-v2",
        outcome="escalated",
        iterations=3,
        validator_verdict="PASS",
        validator_score=89,
        root_cause_codes=(
            "broken_scenarios",
            "architect_spec_drift",
            "slow_implementer",
        ),
    )
    d = row.to_dict()
    assert d["validator_verdict"] == "PASS"
    assert d["validator_score"] == 89
    assert d["outcome"] == "escalated"
    assert d["root_cause_codes"] == [
        "broken_scenarios", "architect_spec_drift", "slow_implementer",
    ]


# ---------------------------------------------------------------------------
# default_dataset_path
# ---------------------------------------------------------------------------


def test_default_dataset_path_under_docs(tmp_path):
    p = default_dataset_path(tmp_path)
    assert p == tmp_path / "docs" / "post-mortem-dataset.jsonl"
