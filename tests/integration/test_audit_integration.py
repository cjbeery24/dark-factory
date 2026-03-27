"""Integration tests for barrier violation and leakage detection with persistence.

Target: darkfactory/audit/checker.py — check_output_for_barriers(), validate_oracle_feedback()
Mock boundary: Minimal. Real filesystem for audit log and events file.
Why integration: Tests event emission + audit log persistence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from darkfactory.audit.checker import check_output_for_barriers, validate_oracle_feedback


def _read_audit_log(audit_path: Path) -> str:
    """Read audit log contents."""
    if not audit_path.exists():
        return ""
    return audit_path.read_text()


def _read_events(events_file: Path) -> list[dict]:
    """Read JSONL events from file."""
    if not events_file.exists():
        return []
    result = []
    with events_file.open() as f:
        for line in f:
            s = line.strip()
            if s:
                result.append(json.loads(s))
    return result


@pytest.mark.integration
class TestBarrierViolationEvents:
    """Barrier violations emit events and write audit log entries."""

    def test_single_violation_emits_event_and_audit(self, tmp_path: Path):
        """Single violation produces event in events.jsonl and audit.log entry."""
        audit_path = tmp_path / "audit.log"
        events_file = tmp_path / "events.jsonl"

        output = "I read the file at .dark-factory/scenarios/test.md and it was helpful"
        violations = check_output_for_barriers(
            output,
            [".dark-factory/scenarios/"],
            "implementer",
            audit_path,
            events_file,
            spec_name="test-spec",
            run_id="run-001",
        )

        assert len(violations) == 1
        assert ".dark-factory/scenarios/" in violations[0]

        # Audit log written
        log_content = _read_audit_log(audit_path)
        assert "BARRIER VIOLATION" in log_content
        assert "implementer" in log_content

        # Event emitted
        events = _read_events(events_file)
        barrier_events = [e for e in events if e.get("event") == "BARRIER_VIOLATION"]
        assert len(barrier_events) == 1
        assert barrier_events[0]["spec"] == "test-spec"

    def test_multiple_violations_all_logged(self, tmp_path: Path):
        """Multiple restricted paths in output -> all logged."""
        audit_path = tmp_path / "audit.log"
        events_file = tmp_path / "events.jsonl"

        output = (
            "Found .dark-factory/scenarios/test.md and also "
            ".dark-factory/reports/results.json"
        )
        violations = check_output_for_barriers(
            output,
            [".dark-factory/scenarios/", ".dark-factory/reports/"],
            "implementer",
            audit_path,
            events_file,
            spec_name="test-spec",
            run_id="run-002",
        )

        assert len(violations) == 2

        events = _read_events(events_file)
        barrier_events = [e for e in events if e.get("event") == "BARRIER_VIOLATION"]
        assert len(barrier_events) == 2

    def test_clean_output_no_events(self, tmp_path: Path):
        """Clean output produces no violations, no events."""
        audit_path = tmp_path / "audit.log"
        events_file = tmp_path / "events.jsonl"

        output = "I implemented the calculator module as specified in FR-1."
        violations = check_output_for_barriers(
            output,
            [".dark-factory/scenarios/", ".dark-factory/reports/"],
            "implementer",
            audit_path,
            events_file,
            spec_name="test-spec",
            run_id="run-003",
        )

        assert violations == []
        assert not events_file.exists() or _read_events(events_file) == []


@pytest.mark.integration
class TestOracleFeedbackLeakageEvents:
    """Leakage detection in oracle feedback logs to audit."""

    def test_leakage_detected_logged(self, tmp_path: Path):
        """Scenario filename in feedback is detected and logged."""
        audit_path = tmp_path / "audit.log"

        feedback = "The 01-basic-auth.test.ts scenario covers authentication well."
        result = validate_oracle_feedback(feedback, audit_path)

        assert not result.is_compliant
        log_content = _read_audit_log(audit_path)
        assert "SEMANTIC LEAKAGE" in log_content

    def test_mixed_patterns_both_flagged(self, tmp_path: Path):
        """Scenario filename + assertion DSL -> both flagged."""
        audit_path = tmp_path / "audit.log"

        feedback = (
            "The 01-auth-flow.test.ts shows that "
            'describe("auth", () => { test works well.'
        )
        result = validate_oracle_feedback(feedback, audit_path)

        # Should detect scenario filename pattern and test DSL pattern
        assert len(result.violations) >= 2

    def test_clean_feedback_nothing_logged(self, tmp_path: Path):
        """Clean feedback with no leakage patterns -> no audit entries."""
        audit_path = tmp_path / "audit.log"

        feedback = (
            "NO-SHIP\n\n"
            "FR-1 is partially implemented. The add() function works but "
            "multiply() returns incorrect results for negative inputs. "
            "Please fix the sign handling in EC-2."
        )
        result = validate_oracle_feedback(feedback, audit_path)

        assert result.is_compliant
        # Audit log should not have leakage entries
        if audit_path.exists():
            assert "SEMANTIC LEAKAGE" not in _read_audit_log(audit_path)


@pytest.mark.integration
class TestAuditCompositeIntegration:
    """Barrier violations feed into composite anomaly scoring."""

    def test_violations_increase_anomaly_score(self, tmp_path: Path):
        """Barrier violation produces non-zero anomaly data."""
        audit_path = tmp_path / "audit.log"
        events_file = tmp_path / "events.jsonl"

        # Generate a violation
        output = "Reading .dark-factory/scenarios/01-test.md for reference"
        violations = check_output_for_barriers(
            output,
            [".dark-factory/scenarios/"],
            "implementer",
            audit_path,
            events_file,
            spec_name="test-spec",
            run_id="run-004",
        )

        assert len(violations) > 0

        # The violation event should exist with data we can score
        events = _read_events(events_file)
        barrier_events = [e for e in events if e.get("event") == "BARRIER_VIOLATION"]
        assert len(barrier_events) > 0
        # Verify the event has scoring-relevant fields
        assert "role" in barrier_events[0].get("data", barrier_events[0])

    def test_no_violations_clean_state(self, tmp_path: Path):
        """No violations -> no barrier events."""
        audit_path = tmp_path / "audit.log"
        events_file = tmp_path / "events.jsonl"

        output = "Implementation complete. All functional requirements met."
        violations = check_output_for_barriers(
            output,
            [".dark-factory/scenarios/", ".dark-factory/reports/"],
            "implementer",
            audit_path,
            events_file,
            spec_name="test-spec",
            run_id="run-005",
        )

        assert violations == []
        events = _read_events(events_file)
        barrier_events = [e for e in events if e.get("event") == "BARRIER_VIOLATION"]
        assert len(barrier_events) == 0
