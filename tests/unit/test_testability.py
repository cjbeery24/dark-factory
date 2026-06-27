"""Tests for testability analysis — precheck + AnchorManifest subprocess_ac_ids."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.spec.anchors import AnchorManifest
from darkfactory.spec.precheck import (
    check_testability,
    collect_runtime_boundary_ac_ids,
    run_precheck,
)
from darkfactory.spec.spec_model import parse_spec_model


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FULL_SPEC_HEADER = """\
# Test Spec

## Problem Statement
Pain.

## Functional Requirements
### FR-1: Create server
- Processing: validate env, create server
- Side effects: none

## Non-Functional Requirements
- p99 < 200ms

## Constraints
- node 22

## Interface Contract
- POST /api

## Edge Cases
- EC-1: bad input

## Error Handling
- 400 on invalid

"""

_SPEC_FOOTER = """\
## Examples
- example

## Dependencies & Out of Scope
- none

## Scenario Intent
### FR-1: Create server
| # | Intent | Layer |
|---|--------|-------|
| 1 | valid env starts server | happy_path |
"""


def _make_spec(ac_section: str) -> str:
    return _FULL_SPEC_HEADER + ac_section + "\n\n" + _SPEC_FOOTER


class TestRuntimeBoundaryMarker(unittest.TestCase):
    """`vehicle: runtime-boundary` parses into runtime_boundary_ac_ids and,
    UNLIKE subprocess/untestable, does NOT de-scope the anchor."""

    def test_inline_marker_parsed(self) -> None:
        model = parse_spec_model(_make_spec("""\
## Acceptance Criteria
| ID | Criterion | Vehicle |
|----|-----------|---------|
| AC-1 | GIVEN non-VITE_ vars WHEN import.meta.env read THEN absent | runtime-boundary |
"""))
        self.assertEqual(collect_runtime_boundary_ac_ids(model), ["AC-1"])

    def test_not_a_runtime_boundary_when_unmarked(self) -> None:
        model = parse_spec_model(_make_spec("""\
## Acceptance Criteria
| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN valid input WHEN POST /api THEN returns 200 |
"""))
        self.assertEqual(collect_runtime_boundary_ac_ids(model), [])

    def test_prose_mentioning_runtime_boundary_not_matched(self) -> None:
        # A bare mention in prose must not be read as a vehicle declaration.
        model = parse_spec_model(_make_spec("""\
## Acceptance Criteria
| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN a runtime-boundary value WHEN read THEN returns 200 |
"""))
        self.assertEqual(collect_runtime_boundary_ac_ids(model), [])


# ---------------------------------------------------------------------------
# check_testability() tests
# ---------------------------------------------------------------------------


class TestCheckTestability(unittest.TestCase):
    """Test the check_testability function directly."""

    def test_ac_with_process_exit_no_vehicle_emits_finding(self) -> None:
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN PORT='abc' WHEN server starts THEN it calls process.exit(1) |
""")
        model = parse_spec_model(spec)
        findings, subprocess_ids, _untestable = check_testability(model)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].criterion_id, "AC-1")
        self.assertIn("process.exit", findings[0].matched_pattern)
        self.assertFalse(findings[0].has_vehicle_annotation)
        self.assertEqual(subprocess_ids, [])

    def test_ac_with_process_exit_and_vehicle_subprocess_no_finding(self) -> None:
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion | Vehicle |
|----|-----------|---------|
| AC-1 | GIVEN PORT='abc' WHEN server starts THEN it calls process.exit(1) | subprocess |
""")
        model = parse_spec_model(spec)
        findings, subprocess_ids, _untestable = check_testability(model)
        self.assertEqual(len(findings), 0)
        self.assertEqual(subprocess_ids, ["AC-1"])

    def test_ac_without_side_effect_no_finding(self) -> None:
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN valid input WHEN POST /api THEN returns 200 |
""")
        model = parse_spec_model(spec)
        findings, subprocess_ids, _untestable = check_testability(model)
        self.assertEqual(len(findings), 0)
        self.assertEqual(subprocess_ids, [])

    def test_ac_with_sys_exit_emits_finding(self) -> None:
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN bad config WHEN app starts THEN calls sys.exit(1) |
""")
        model = parse_spec_model(spec)
        findings, subprocess_ids, _untestable = check_testability(model)
        self.assertEqual(len(findings), 1)
        self.assertIn("sys.exit", findings[0].matched_pattern)

    def test_ac_with_sigterm_emits_finding(self) -> None:
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN running server WHEN SIGTERM is sent THEN graceful shutdown |
""")
        model = parse_spec_model(spec)
        findings, subprocess_ids, _untestable = check_testability(model)
        self.assertEqual(len(findings), 1)
        self.assertIn("SIGTERM", findings[0].matched_pattern)

    def test_multiple_acs_mixed_findings(self) -> None:
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion | Vehicle |
|----|-----------|---------|
| AC-1 | GIVEN valid env WHEN server starts THEN listens on port | |
| AC-2 | GIVEN bad PORT WHEN server starts THEN calls process.exit(1) | |
| AC-3 | GIVEN bad NODE_ENV WHEN server starts THEN calls process.exit(1) | subprocess |
""")
        model = parse_spec_model(spec)
        findings, subprocess_ids, _untestable = check_testability(model)
        # AC-1: no pattern → no finding
        # AC-2: process.exit + no vehicle → finding
        # AC-3: process.exit + vehicle:subprocess → no finding, but in subprocess_ids
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].criterion_id, "AC-2")
        self.assertEqual(subprocess_ids, ["AC-3"])

    def test_vehicle_column_case_insensitive(self) -> None:
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion | Vehicle |
|----|-----------|---------|
| AC-1 | GIVEN bad config WHEN app starts THEN calls process.exit(1) | SUBPROCESS |
""")
        model = parse_spec_model(spec)
        findings, subprocess_ids, _untestable = check_testability(model)
        self.assertEqual(len(findings), 0)
        self.assertEqual(subprocess_ids, ["AC-1"])

    def test_os_exit_pattern(self) -> None:
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN bad config WHEN app starts THEN calls os.Exit(1) |
""")
        model = parse_spec_model(spec)
        findings, subprocess_ids, _untestable = check_testability(model)
        self.assertEqual(len(findings), 1)
        self.assertIn("os.Exit", findings[0].matched_pattern)

    def test_cluster_fork_pattern(self) -> None:
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN master mode WHEN app starts THEN calls cluster.fork for each CPU |
""")
        model = parse_spec_model(spec)
        findings, subprocess_ids, _untestable = check_testability(model)
        self.assertEqual(len(findings), 1)
        self.assertIn("cluster.fork", findings[0].matched_pattern)

    def test_no_acceptance_criteria_section(self) -> None:
        spec = """\
# Test Spec
## Problem Statement
Pain.
## Functional Requirements
### FR-1: foo
stuff
"""
        model = parse_spec_model(spec)
        findings, subprocess_ids, untestable = check_testability(model)
        self.assertEqual(findings, [])
        self.assertEqual(subprocess_ids, [])
        self.assertEqual(untestable, {})


class TestCheckTestabilityCompileTime(unittest.TestCase):
    """Piece 1: compile-time/type-level ACs → advisory suggesting @untestable.

    Generic type-system vocabulary, not tuned to any spec. Treadmill-tested:
    each token's mere presence is the signal.
    """

    def test_typecheck_ac_emits_compile_time_finding(self) -> None:
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN the module WHEN typechecked THEN the signature matches the contract |
""")
        model = parse_spec_model(spec)
        findings, _sub, _unt = check_testability(model)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].criterion_id, "AC-1")
        self.assertIn("typecheck", findings[0].matched_pattern.lower())
        self.assertIn("@untestable", findings[0].message)

    def test_declaration_merge_ac_emits_finding(self) -> None:
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN the plugin WHEN a handler uses request.withDb THEN the declaration merge is in effect |
""")
        model = parse_spec_model(spec)
        findings, _sub, _unt = check_testability(model)
        self.assertEqual(len(findings), 1)

    def test_type_error_is_NOT_flagged(self) -> None:
        # "type error" is deliberately excluded — it can name an observable HTTP
        # error response, so flagging it would mis-fire on a testable AC.
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN bad body WHEN POST /api THEN returns 400 with a type error message |
""")
        model = parse_spec_model(spec)
        findings, _sub, _unt = check_testability(model)
        self.assertEqual(findings, [])


class TestCheckTestabilityNestedAndNumbered(unittest.TestCase):
    """The AC tables live in numbered headings + nested ### FR-N sub-tables +
    a sibling 'Non-Negotiable Acceptance Criteria' (the canonical template
    shape). _ac_tables must gather all of them, or the advisory silently
    no-ops and author markers never strip from ac_ids.
    """

    def test_numbered_heading_and_nested_subtables_are_scanned(self) -> None:
        spec = _make_spec("""\
## 8. Acceptance Criteria

### FR-1: connection
| ID | Criterion | Blocking |
|----|-----------|----------|
| AC-1 | GIVEN the module WHEN typechecked THEN the signature matches | no |

### FR-2: tenant
| ID | Criterion | Blocking |
|----|-----------|----------|
| AC-2 | GIVEN valid input WHEN POST /api THEN returns 200 | no |
""")
        model = parse_spec_model(spec)
        findings, _sub, _unt = check_testability(model)
        # AC-1 (typecheck, nested under a numbered heading) must be found.
        self.assertEqual([f.criterion_id for f in findings], ["AC-1"])

    def test_sibling_non_negotiable_section_is_part_of_ac_block(self) -> None:
        spec = _make_spec("""\
## 8. Acceptance Criteria

### FR-1: connection
| ID | Criterion | Blocking |
|----|-----------|----------|
| AC-1 | GIVEN valid input WHEN POST /api THEN returns 200 | no |

## 8a. Non-Negotiable Acceptance Criteria

| ID | Criterion | Blocking |
|----|-----------|----------|
| AC-2 | GIVEN the plugin WHEN a handler runs THEN the declaration merge is in effect | yes |
""")
        model = parse_spec_model(spec)
        findings, _sub, _unt = check_testability(model)
        # AC-2 lives under the sibling 8a section — it must still be scanned.
        self.assertEqual([f.criterion_id for f in findings], ["AC-2"])

    def test_ec_markers_in_edge_cases_and_error_handling_are_parsed(self) -> None:
        # EC anchors live in Edge Cases / Error Handling sections and are scored
        # by the floor (ec_ids) — their author vehicle markers must be parsed.
        spec = _make_spec("""\
## 8. Acceptance Criteria
| ID | Criterion | Blocking |
|----|-----------|----------|
| AC-1 | GIVEN valid input WHEN POST /api THEN returns 200 | no |

## 6. Edge Cases
| ID | Condition | Expected |
|----|-----------|----------|
| EC-1 | DB unreachable | health degraded vehicle: subprocess |

## 7. Error Handling
| ID | Condition | Expected |
|----|-----------|----------|
| EC-2 | pool exhausted | 503 @untestable: pool saturation not exposed |
""")
        model = parse_spec_model(spec)
        _findings, subprocess_ids, untestable = check_testability(model)
        self.assertEqual(subprocess_ids, ["EC-1"])
        self.assertIn("EC-2", untestable)
        self.assertIn("pool", untestable["EC-2"])

    def test_non_ac_sibling_closes_the_block(self) -> None:
        # A row in a non-AC sibling section must NOT be scanned as an AC.
        spec = _make_spec("""\
## 8. Acceptance Criteria

### FR-1: connection
| ID | Criterion | Blocking |
|----|-----------|----------|
| AC-1 | GIVEN valid input WHEN POST /api THEN returns 200 | no |

## 8b. Forbidden Patterns

| ID | Criterion | Blocking |
|----|-----------|----------|
| AC-2 | GIVEN code WHEN typechecked THEN it fails | no |
""")
        model = parse_spec_model(spec)
        findings, _sub, _unt = check_testability(model)
        # AC-2 is under a non-AC sibling → not scanned.
        self.assertEqual(findings, [])


class TestCheckTestabilityUntestableDeclaration(unittest.TestCase):
    """Piece 3: author @untestable declaration (position-independent marker)."""

    def test_at_untestable_marker_captures_reason_and_suppresses_finding(self) -> None:
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN the module WHEN typechecked THEN the signature matches @untestable: compile-time-enforced (tsc) |
""")
        model = parse_spec_model(spec)
        findings, _sub, untestable = check_testability(model)
        # Author declared a vehicle → no advisory, anchor recorded with reason.
        self.assertEqual(findings, [])
        self.assertIn("AC-1", untestable)
        self.assertIn("tsc", untestable["AC-1"])

    def test_inline_vehicle_subprocess_marker_position_independent(self) -> None:
        # No dedicated Vehicle column (3rd col is Blocking) — inline marker works.
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion | Blocking |
|----|-----------|----------|
| AC-1 | GIVEN bad PORT WHEN server starts THEN process.exit(1) vehicle: subprocess | no |
""")
        model = parse_spec_model(spec)
        findings, subprocess_ids, _unt = check_testability(model)
        self.assertEqual(findings, [])
        self.assertEqual(subprocess_ids, ["AC-1"])

    def test_prose_mentioning_untestable_without_colon_is_not_a_declaration(self) -> None:
        # Codex BLOCK: descriptive prose containing "@untestable" (no colon /
        # key:value shape) must NOT de-scope a real anchor from the floor.
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN the admin UI WHEN rendering docs THEN it must not display the literal marker @untestable to users |
""")
        model = parse_spec_model(spec)
        _findings, _sub, untestable = check_testability(model)
        self.assertEqual(untestable, {})

    def test_prose_mentioning_subprocess_in_third_column_is_not_a_vehicle(self) -> None:
        # Codex BLOCK: a 3rd column carrying prose that mentions "subprocess"
        # (here a negated statement) must NOT be read as a vehicle declaration.
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion | Expected |
|----|-----------|----------|
| AC-1 | GIVEN startup policy WHEN it boots THEN it must NOT spawn a subprocess and returns 200 |
""")
        model = parse_spec_model(spec)
        _findings, subprocess_ids, _unt = check_testability(model)
        self.assertEqual(subprocess_ids, [])

    def test_untestable_requires_colon_and_reason(self) -> None:
        # The structured form with a colon + reason IS a declaration.
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN the module WHEN typechecked THEN signature matches @untestable: tsc-enforced |
""")
        model = parse_spec_model(spec)
        _findings, _sub, untestable = check_testability(model)
        self.assertEqual(untestable, {"AC-1": "tsc-enforced"})

    def test_blocking_column_does_not_false_trigger_subprocess(self) -> None:
        # A 3rd "Blocking" column with values no/yes must NOT be read as a vehicle.
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion | Blocking |
|----|-----------|----------|
| AC-1 | GIVEN valid input WHEN POST /api THEN returns 200 | yes |
""")
        model = parse_spec_model(spec)
        findings, subprocess_ids, untestable = check_testability(model)
        self.assertEqual(subprocess_ids, [])
        self.assertEqual(untestable, {})


# ---------------------------------------------------------------------------
# AnchorManifest.subprocess_ac_ids round-trip
# ---------------------------------------------------------------------------


class TestAnchorManifestSubprocessField(unittest.TestCase):
    """Test subprocess_ac_ids field on AnchorManifest."""

    def test_round_trip_with_subprocess_ac_ids(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "manifest.json"
            m = AnchorManifest(
                fr_ids=["FR-1"],
                ac_ids=["AC-1", "AC-2"],
                ec_ids=["EC-1"],
                subprocess_ac_ids=["AC-2"],
            )
            m.to_json_path(p)
            loaded = AnchorManifest.from_json_path(p)
            assert loaded is not None
            self.assertEqual(loaded.subprocess_ac_ids, ["AC-2"])
            self.assertEqual(loaded.fr_ids, ["FR-1"])
            self.assertEqual(loaded.ac_ids, ["AC-1", "AC-2"])

    def test_round_trip_with_runtime_boundary_ac_ids(self) -> None:
        # Regression guard (Codex): from_json_path uses an explicit kwarg list, so
        # a new field must be added there or it's silently dropped on reload — which
        # makes the floor exemption a no-op (the floor loads the manifest from disk).
        with TemporaryDirectory() as td:
            p = Path(td) / "manifest.json"
            m = AnchorManifest(
                fr_ids=["FR-1"],
                ac_ids=["AC-1", "AC-2"],
                runtime_boundary_ac_ids=["AC-2"],
            )
            m.to_json_path(p)
            loaded = AnchorManifest.from_json_path(p)
            assert loaded is not None
            self.assertEqual(loaded.runtime_boundary_ac_ids, ["AC-2"])
            # NOT excluded from ac_ids (unlike subprocess/untestable).
            self.assertEqual(loaded.ac_ids, ["AC-1", "AC-2"])

    def test_backcompat_missing_runtime_boundary_ac_ids(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "manifest.json"
            p.write_text(json.dumps({"fr_ids": [], "ac_ids": ["AC-1"], "ec_ids": []}),
                         encoding="utf-8")
            loaded = AnchorManifest.from_json_path(p)
            assert loaded is not None
            self.assertEqual(loaded.runtime_boundary_ac_ids, [])

    def test_backcompat_missing_subprocess_ac_ids(self) -> None:
        """Manifests written before this field exists deserialize with empty list."""
        with TemporaryDirectory() as td:
            p = Path(td) / "manifest.json"
            data = {
                "fr_ids": ["FR-1"],
                "ac_ids": ["AC-1"],
                "ec_ids": [],
                "declared_intents": {},
            }
            p.write_text(json.dumps(data), encoding="utf-8")
            loaded = AnchorManifest.from_json_path(p)
            assert loaded is not None
            self.assertEqual(loaded.subprocess_ac_ids, [])

    def test_default_empty(self) -> None:
        m = AnchorManifest()
        self.assertEqual(m.subprocess_ac_ids, [])
        self.assertEqual(m.untestable_ac_ids, {})

    def test_round_trip_with_untestable_ac_ids(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "manifest.json"
            m = AnchorManifest(
                ac_ids=["AC-1", "AC-2"],
                untestable_ac_ids={"AC-2": "compile-time-enforced (tsc)"},
            )
            m.to_json_path(p)
            loaded = AnchorManifest.from_json_path(p)
            assert loaded is not None
            self.assertEqual(
                loaded.untestable_ac_ids, {"AC-2": "compile-time-enforced (tsc)"}
            )

    def test_backcompat_missing_untestable_ac_ids(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "manifest.json"
            data = {"fr_ids": [], "ac_ids": ["AC-1"], "ec_ids": []}
            p.write_text(json.dumps(data), encoding="utf-8")
            loaded = AnchorManifest.from_json_path(p)
            assert loaded is not None
            self.assertEqual(loaded.untestable_ac_ids, {})


# ---------------------------------------------------------------------------
# Integration: run_precheck surfaces testability findings
# ---------------------------------------------------------------------------


class TestPrecheckTestabilityIntegration(unittest.TestCase):
    """Verify run_precheck includes testability findings in result."""

    def test_precheck_reports_testability_advisory(self) -> None:
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN PORT='abc' WHEN server starts THEN calls process.exit(1) |
""")
        with TemporaryDirectory() as td:
            p = Path(td) / "spec.md"
            p.write_text(spec, encoding="utf-8")
            result = run_precheck(p)
            # Testability is advisory — should not affect passed status
            # (other issues like missing scenario intents may affect it)
            self.assertEqual(len(result.testability), 1)
            self.assertEqual(result.testability[0].criterion_id, "AC-1")
            self.assertIn("Testability advisories", result.summary)
            # Piece 2: the validate-time summary renders the SAME vehicle menu the
            # floor abort shows — so the author sees it in seconds, not an hour in.
            self.assertIn("vehicle: subprocess", result.summary)
            self.assertIn("@untestable", result.summary)

    def test_precheck_summary_menu_matches_floor_abort_menu(self) -> None:
        # The validate-time menu must be the IDENTICAL object the floor renders.
        from darkfactory.scenarios.coverage import _FLOOR_VEHICLE_MENU
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN PORT='abc' WHEN server starts THEN calls process.exit(1) |
""")
        with TemporaryDirectory() as td:
            p = Path(td) / "spec.md"
            p.write_text(spec, encoding="utf-8")
            result = run_precheck(p)
            for menu_line in _FLOOR_VEHICLE_MENU:
                self.assertIn(menu_line, result.summary)

    def test_precheck_merges_subprocess_ac_ids_into_manifest(self) -> None:
        spec = _make_spec("""\
## Acceptance Criteria
| ID | Criterion | Vehicle |
|----|-----------|---------|
| AC-1 | GIVEN valid env WHEN server starts THEN listens on port | |
| AC-2 | GIVEN bad PORT WHEN server starts THEN calls process.exit(1) | subprocess |
""")
        with TemporaryDirectory() as td:
            p = Path(td) / "spec.md"
            p.write_text(spec, encoding="utf-8")
            result = run_precheck(p)
            assert result.anchor_manifest is not None
            self.assertIn("AC-2", result.anchor_manifest.subprocess_ac_ids)
            # No testability finding for AC-2 since it has vehicle annotation
            findings_for_ac2 = [
                f for f in result.testability if f.criterion_id == "AC-2"
            ]
            self.assertEqual(len(findings_for_ac2), 0)


if __name__ == "__main__":
    unittest.main()
