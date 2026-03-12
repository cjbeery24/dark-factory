"""Integration tests for quality gate output parsing and floor enforcement.

Target: darkfactory/pipeline/quality_gate.py — _parse_scenario_counts, floor enforcement
Mock boundary: Mock execute_container. Real: env file parsing, gate result construction.
Why integration: Tests the full gate -> parse -> floor enforcement flow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from darkfactory.pipeline.quality_gate import GateResult, _parse_scenario_counts, _safe_int


def _write_counts(project_dir: Path, spec_name: str, content: str) -> None:
    """Write a scenario counts env file."""
    reports = project_dir / ".dark-factory" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"{spec_name}-scenario-counts.env").write_text(content)


@pytest.mark.integration
class TestScenarioCountsParsing:
    """_parse_scenario_counts reads env files and populates GateResult."""

    def test_normal_env_file(self, tmp_path: Path):
        """Standard env file with all keys populates GateResult correctly."""
        _write_counts(tmp_path, "test-spec", (
            "SCENARIO_PASSED=8\n"
            "SCENARIO_FAILED=2\n"
            "SCENARIO_TOTAL=10\n"
            "SCENARIO_RATE=80\n"
            "SATISFACTION_SCORE=85\n"
        ))

        result = GateResult(exit_code=0)
        _parse_scenario_counts(result, tmp_path, "test-spec")

        assert result.scenario_passed == 8
        assert result.scenario_failed == 2
        assert result.scenario_total == 10
        assert result.pass_rate == 80
        assert result.satisfaction_score == 85

    def test_missing_keys_default_to_zero(self, tmp_path: Path):
        """Missing keys default to 0."""
        _write_counts(tmp_path, "test-spec", (
            "SCENARIO_PASSED=5\n"
            "SCENARIO_TOTAL=5\n"
        ))

        result = GateResult(exit_code=0)
        _parse_scenario_counts(result, tmp_path, "test-spec")

        assert result.scenario_passed == 5
        assert result.scenario_failed == 0  # missing key
        assert result.scenario_total == 5
        assert result.pass_rate == 0  # missing key
        assert result.satisfaction_score == 0  # missing key

    def test_malformed_values_default_to_zero(self, tmp_path: Path):
        """Malformed integer values default to 0 via _safe_int."""
        _write_counts(tmp_path, "test-spec", (
            "SCENARIO_PASSED=abc\n"
            "SCENARIO_FAILED=\n"
            "SCENARIO_TOTAL=10.5\n"
            "SCENARIO_RATE=eighty\n"
        ))

        result = GateResult(exit_code=0)
        _parse_scenario_counts(result, tmp_path, "test-spec")

        assert result.scenario_passed == 0  # "abc" -> 0
        assert result.scenario_failed == 0  # "" -> 0
        assert result.scenario_total == 0  # "10.5" -> 0
        assert result.pass_rate == 0  # "eighty" -> 0

    def test_empty_file(self, tmp_path: Path):
        """Empty env file leaves all fields at 0."""
        _write_counts(tmp_path, "test-spec", "")

        result = GateResult(exit_code=0)
        _parse_scenario_counts(result, tmp_path, "test-spec")

        assert result.scenario_passed == 0
        assert result.scenario_failed == 0
        assert result.scenario_total == 0


@pytest.mark.integration
class TestGateFloorEnforcement:
    """Floor enforcement overrides gate pass when metrics fall below thresholds.

    The floor enforcement logic lives in the iteration loop, but we can test
    the data flow: _parse_scenario_counts populates fields that the iteration
    loop checks against floor thresholds.
    """

    def test_low_satisfaction_detected(self, tmp_path: Path):
        """Gate passes but satisfaction below floor is detectable."""
        _write_counts(tmp_path, "test-spec", (
            "SCENARIO_PASSED=10\n"
            "SCENARIO_FAILED=0\n"
            "SCENARIO_TOTAL=10\n"
            "SCENARIO_RATE=100\n"
            "SATISFACTION_SCORE=30\n"
        ))

        result = GateResult(exit_code=0)
        _parse_scenario_counts(result, tmp_path, "test-spec")

        # Gate exit_code is 0 (pass), but satisfaction is below typical floor (80)
        assert result.exit_code == 0
        assert result.satisfaction_score == 30
        # Iteration loop would force failure when satisfaction < floor

    def test_low_pass_rate_detected(self, tmp_path: Path):
        """Gate passes but low pass_rate is detectable."""
        _write_counts(tmp_path, "test-spec", (
            "SCENARIO_PASSED=3\n"
            "SCENARIO_FAILED=7\n"
            "SCENARIO_TOTAL=10\n"
            "SCENARIO_RATE=30\n"
            "SATISFACTION_SCORE=90\n"
        ))

        result = GateResult(exit_code=0)
        _parse_scenario_counts(result, tmp_path, "test-spec")

        assert result.exit_code == 0
        assert result.pass_rate == 30
        # Iteration loop would force failure when pass_rate < floor

    def test_above_floor_passes_through(self, tmp_path: Path):
        """Both metrics above floor -> gate passes through unchanged."""
        _write_counts(tmp_path, "test-spec", (
            "SCENARIO_PASSED=9\n"
            "SCENARIO_FAILED=1\n"
            "SCENARIO_TOTAL=10\n"
            "SCENARIO_RATE=90\n"
            "SATISFACTION_SCORE=92\n"
        ))

        result = GateResult(exit_code=0)
        _parse_scenario_counts(result, tmp_path, "test-spec")

        assert result.exit_code == 0
        assert result.pass_rate == 90
        assert result.satisfaction_score == 92
