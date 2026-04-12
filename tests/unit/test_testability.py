"""Tests for testability analysis — precheck + AnchorManifest subprocess_ac_ids."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.spec.anchors import AnchorManifest
from darkfactory.spec.precheck import (
    check_testability,
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
        findings, subprocess_ids = check_testability(model)
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
        findings, subprocess_ids = check_testability(model)
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
        findings, subprocess_ids = check_testability(model)
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
        findings, subprocess_ids = check_testability(model)
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
        findings, subprocess_ids = check_testability(model)
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
        findings, subprocess_ids = check_testability(model)
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
        findings, subprocess_ids = check_testability(model)
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
        findings, subprocess_ids = check_testability(model)
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
        findings, subprocess_ids = check_testability(model)
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
        findings, subprocess_ids = check_testability(model)
        self.assertEqual(findings, [])
        self.assertEqual(subprocess_ids, [])


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
