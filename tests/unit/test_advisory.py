"""Phase 0 exit criterion: precheck advisory JSON is byte-identical and
deterministic across writes for the same PreCheckResult content.

The orchestrator (`darkfactory/pipeline/orchestrator.py`) and the CLI
`validate`/`precheck` commands (`darkfactory/cli.py`) must both write the
advisory through `darkfactory.spec.advisory.write_precheck_advisory`. This
test pins the determinism contract those call sites depend on.
"""

from __future__ import annotations

from pathlib import Path

from darkfactory.spec.advisory import (
    build_advisory_payload,
    precheck_advisory_path,
    write_precheck_advisory,
)
from darkfactory.spec.precheck import (
    AmbiguityFinding,
    ContractCheckResult,
    CriteriaFinding,
    PlaceholderFinding,
    PreCheckResult,
    ScenarioIntentResult,
    SectionResult,
    SectionStatus,
    ShapeClaimFinding,
    SpecStructureResult,
)


def _make_result(passed: bool = True, fatal: bool = False) -> PreCheckResult:
    return PreCheckResult(
        passed=passed,
        structure=SpecStructureResult(
            sections=[
                SectionResult("Problem Statement", SectionStatus.PRESENT, 1),
                SectionResult("Functional Requirements", SectionStatus.PRESENT, 5),
            ],
            score=100,
            passed=True,
        ),
        fr_count=2,
        summary="ok",
        fatal=fatal,
    )


def test_advisory_path_under_precheck_dir(tmp_path: Path) -> None:
    p = precheck_advisory_path(tmp_path, "my-spec")
    assert p == tmp_path / ".dark-factory" / "precheck" / "my-spec.json"


def test_advisory_write_is_byte_identical(tmp_path: Path) -> None:
    """Two writes of the same PreCheckResult must produce identical bytes.

    This is the Phase 0 invariant: orchestrator and CLI write the same file
    via the same code path, so consecutive writes must be reproducible.
    """
    result = _make_result()

    p1 = write_precheck_advisory(tmp_path, "spec-a", result)
    bytes1 = p1.read_bytes()

    p2 = write_precheck_advisory(tmp_path, "spec-a", result)
    bytes2 = p2.read_bytes()

    assert bytes1 == bytes2
    assert bytes1.endswith(b"\n")


def test_advisory_payload_keys_sorted(tmp_path: Path) -> None:
    """JSON output must use sorted keys (deterministic ordering)."""
    result = _make_result()
    p = write_precheck_advisory(tmp_path, "spec-b", result)
    text = p.read_text()
    # First top-level key after the opening brace must be alphabetically first.
    # Current keys include: ambiguities, anchor_intent_issues, contracts,
    # criteria_issues, fatal, fr_count, placeholders, scenario_intents,
    # shape_claims, structure
    first_key_line = next(
        line for line in text.splitlines() if line.strip().startswith('"')
    )
    assert '"ambiguities"' in first_key_line


def _make_populated_result() -> PreCheckResult:
    """A result with one of every finding type, exercising every payload branch.

    Pins the byte-identity contract for non-trivial payloads. If a finding
    dataclass grows a non-deterministic field (set, unsorted dict, Path),
    this test will catch it.
    """
    return PreCheckResult(
        passed=False,
        structure=SpecStructureResult(
            sections=[
                SectionResult("Problem Statement", SectionStatus.PRESENT, 1),
                SectionResult("Functional Requirements", SectionStatus.MISSING, None),
            ],
            score=70,
            passed=False,
        ),
        placeholders=[
            PlaceholderFinding(line_number=12, matched_text="TBD", pattern=r"\bTBD\b"),
        ],
        ambiguities=[
            AmbiguityFinding(line_number=20, word="appropriate", context="use appropriate value"),
        ],
        criteria_issues=[
            CriteriaFinding(
                criterion_id="AC-3",
                line_number=45,
                missing_keywords=["given", "when"],
                kind="MALFORMED_GWT",
                message="missing GWT structure",
            ),
        ],
        shape_claims=[
            ShapeClaimFinding(
                section="Interface Contract",
                line_number=60,
                matched_token="UserSchema",
                context="returns UserSchema",
                section_path=("Interface Contract", "Endpoints"),
                available_wire_contracts=["GET /users"],
                would_break_table=False,
            ),
        ],
        anchor_intent_issues=["FR-2 has no anchor"],
        fr_count=4,
        scenario_intents=ScenarioIntentResult(
            present=True,
            fr_intent_counts={"FR-2": 1, "FR-1": 2},
            issues=["FR-1 over-specified"],
        ),
        contracts=ContractCheckResult(
            present=True, count=3, invariant_count=1, errors=["bad shape"],
        ),
        summary="advisory findings present",
        fatal=False,
    )


def test_advisory_byte_identical_with_populated_findings(tmp_path: Path) -> None:
    """Byte-identity must hold for non-trivial payloads, not just empty ones."""
    result = _make_populated_result()
    p1 = write_precheck_advisory(tmp_path, "spec-pop", result)
    bytes1 = p1.read_bytes()
    p2 = write_precheck_advisory(tmp_path, "spec-pop", result)
    bytes2 = p2.read_bytes()
    assert bytes1 == bytes2


def test_advisory_payload_serializes_all_finding_types(tmp_path: Path) -> None:
    """Every finding category must round-trip through json.dumps without TypeError."""
    import json as _json
    payload = build_advisory_payload(_make_populated_result())
    text = _json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True)
    # Sanity: every finding category appears in the output.
    assert '"placeholders"' in text
    assert '"ambiguities"' in text
    assert '"criteria_issues"' in text
    assert '"shape_claims"' in text
    assert '"anchor_intent_issues"' in text
    assert '"scenario_intents"' in text
    assert '"contracts"' in text


def test_advisory_path_is_pure(tmp_path: Path) -> None:
    """precheck_advisory_path must NOT create the directory (post-Linus fix)."""
    p = precheck_advisory_path(tmp_path, "no-dir-yet")
    assert p == tmp_path / ".dark-factory" / "precheck" / "no-dir-yet.json"
    assert not p.parent.exists()


def test_advisory_payload_records_fatal_flag(tmp_path: Path) -> None:
    fatal_result = _make_result(passed=False, fatal=True)
    payload = build_advisory_payload(fatal_result)
    assert payload["fatal"] is True

    ok_payload = build_advisory_payload(_make_result())
    assert ok_payload["fatal"] is False
