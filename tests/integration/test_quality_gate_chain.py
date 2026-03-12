"""Integration tests for the quality gate chain.

Mock boundary: execute_container is mocked. All other modules
(Docker args construction, scenario counts parsing, mutation gate
logic) are real.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, call

import pytest

from darkfactory.pipeline.quality_gate import GateResult, run_quality_gate
from darkfactory.utils.subprocess_runner import RunResult

from tests.integration.conftest import _SequenceMock


# --- Helpers ---

def _config(project_dir: Path):
    from darkfactory.config.loader import load_config
    config = load_config(project_dir / ".dark-factory" / "config.toml")
    config.mutation_cmd = ""
    return config


def _write_counts(project_dir: Path, spec_name: str, **counts):
    """Write a scenario-counts.env file with the given values."""
    counts_file = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_name}-scenario-counts.env"
    )
    counts_file.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in counts.items()]
    counts_file.write_text("\n".join(lines) + "\n")


def _container_with_counts(
    project_dir: Path, spec_name: str, exit_code: int = 0,
    passed: int = 10, failed: int = 0, total: int = 10,
    rate: int = 100, satisfaction: int = 90,
):
    """Return a mock side_effect that writes counts and returns RunResult."""
    def side_effect(*args, **kwargs):
        _write_counts(
            project_dir, spec_name,
            SCENARIO_PASSED=str(passed),
            SCENARIO_FAILED=str(failed),
            SCENARIO_TOTAL=str(total),
            SCENARIO_RATE=str(rate),
            SATISFACTION_SCORE=str(satisfaction),
        )
        return RunResult(exit_code=exit_code, stdout="gate output", stderr="")
    return side_effect


@pytest.mark.integration
class TestQualityGatePass:
    """Full gate pass with scenario counts."""

    def test_full_gate_pass(self, project_dir, sample_spec):
        config = _config(project_dir)
        side_effect = _container_with_counts(
            project_dir, sample_spec,
            exit_code=0, passed=10, failed=0, total=10, rate=100, satisfaction=90,
        )

        with patch("darkfactory.pipeline.quality_gate.execute_container",
                    side_effect=side_effect) as mock_exec:
            result = run_quality_gate(sample_spec, config, "test-run", project_dir)

        assert result.exit_code == 0
        assert result.scenario_passed == 10
        assert result.scenario_failed == 0
        assert result.scenario_total == 10
        assert result.pass_rate == 100
        assert result.satisfaction_score == 90
        mock_exec.assert_called_once()


@pytest.mark.integration
class TestQualityGateFailures:
    """Build and lint failures."""

    def test_build_failure_exit_2(self, project_dir, sample_spec):
        config = _config(project_dir)

        with patch("darkfactory.pipeline.quality_gate.execute_container",
                    return_value=RunResult(exit_code=2, stdout="build error")):
            result = run_quality_gate(sample_spec, config, "test-run", project_dir)

        assert result.exit_code == 2
        # No counts file written → all zeros
        assert result.scenario_passed == 0
        assert result.scenario_total == 0

    def test_lint_failure_exit_1(self, project_dir, sample_spec):
        config = _config(project_dir)
        side_effect = _container_with_counts(
            project_dir, sample_spec,
            exit_code=1, passed=8, failed=2, total=10, rate=80, satisfaction=60,
        )

        with patch("darkfactory.pipeline.quality_gate.execute_container",
                    side_effect=side_effect):
            result = run_quality_gate(sample_spec, config, "test-run", project_dir)

        assert result.exit_code == 1
        assert result.scenario_passed == 8
        assert result.scenario_failed == 2
        assert result.pass_rate == 80


@pytest.mark.integration
class TestScenarioCountsParsing:
    """Scenario counts parsed from env file."""

    def test_counts_parsed_correctly(self, project_dir, sample_spec):
        config = _config(project_dir)
        _write_counts(
            project_dir, sample_spec,
            SCENARIO_PASSED="7", SCENARIO_FAILED="3", SCENARIO_TOTAL="10",
            SCENARIO_RATE="70", SATISFACTION_SCORE="65",
        )

        with patch("darkfactory.pipeline.quality_gate.execute_container",
                    return_value=RunResult(exit_code=0, stdout="")):
            result = run_quality_gate(sample_spec, config, "test-run", project_dir)

        assert result.scenario_passed == 7
        assert result.scenario_failed == 3
        assert result.scenario_total == 10
        assert result.pass_rate == 70
        assert result.satisfaction_score == 65

    def test_missing_counts_file_defaults_to_zero(self, project_dir, sample_spec):
        config = _config(project_dir)
        # No counts file written

        with patch("darkfactory.pipeline.quality_gate.execute_container",
                    return_value=RunResult(exit_code=0, stdout="")):
            result = run_quality_gate(sample_spec, config, "test-run", project_dir)

        assert result.scenario_passed == 0
        assert result.scenario_total == 0
        assert result.pass_rate == 0

    def test_zero_scenarios_handled(self, project_dir, sample_spec):
        config = _config(project_dir)
        _write_counts(
            project_dir, sample_spec,
            SCENARIO_PASSED="0", SCENARIO_FAILED="0", SCENARIO_TOTAL="0",
            SCENARIO_RATE="0", SATISFACTION_SCORE="0",
        )

        with patch("darkfactory.pipeline.quality_gate.execute_container",
                    return_value=RunResult(exit_code=0, stdout="")):
            result = run_quality_gate(sample_spec, config, "test-run", project_dir)

        assert result.scenario_total == 0
        assert result.pass_rate == 0

    def test_malformed_counts_default_to_zero(self, project_dir, sample_spec):
        """Non-integer values in counts file default to 0."""
        config = _config(project_dir)
        counts_file = (
            project_dir / ".dark-factory" / "reports"
            / f"{sample_spec}-scenario-counts.env"
        )
        counts_file.parent.mkdir(parents=True, exist_ok=True)
        counts_file.write_text("SCENARIO_PASSED=abc\nSCENARIO_TOTAL=\n")

        with patch("darkfactory.pipeline.quality_gate.execute_container",
                    return_value=RunResult(exit_code=0, stdout="")):
            result = run_quality_gate(sample_spec, config, "test-run", project_dir)

        assert result.scenario_passed == 0
        assert result.scenario_total == 0


@pytest.mark.integration
class TestMutationGate:
    """Mutation testing gate."""

    def test_mutation_runs_when_configured(self, project_dir, sample_spec):
        config = _config(project_dir)
        config.mutation_cmd = "mutmut run"

        gate_result = RunResult(exit_code=0, stdout="")
        mutation_result = RunResult(exit_code=0, stdout="Mutation score: 85%")

        seq = _SequenceMock([gate_result, mutation_result])
        # Write counts so gate parse succeeds
        _write_counts(
            project_dir, sample_spec,
            SCENARIO_PASSED="10", SCENARIO_FAILED="0", SCENARIO_TOTAL="10",
            SCENARIO_RATE="100", SATISFACTION_SCORE="90",
        )

        with patch("darkfactory.pipeline.quality_gate.execute_container", new=seq):
            result = run_quality_gate(sample_spec, config, "test-run", project_dir)

        assert result.exit_code == 0
        assert result.mutation_score == 85
        assert seq.call_count == 2

    def test_mutation_skipped_when_unconfigured(self, project_dir, sample_spec):
        config = _config(project_dir)
        # mutation_cmd is empty

        with patch("darkfactory.pipeline.quality_gate.execute_container",
                    return_value=RunResult(exit_code=0, stdout="")) as mock_exec:
            result = run_quality_gate(sample_spec, config, "test-run", project_dir)

        assert result.mutation_score == 0
        mock_exec.assert_called_once()  # Only gate container, no mutation

    def test_mutation_score_below_threshold(self, project_dir, sample_spec):
        config = _config(project_dir)
        config.mutation_cmd = "mutmut run"
        config.mutation_min_score = 80

        gate_result = RunResult(exit_code=0, stdout="")
        mutation_result = RunResult(exit_code=0, stdout="Mutation score: 60%")

        seq = _SequenceMock([gate_result, mutation_result])
        _write_counts(
            project_dir, sample_spec,
            SCENARIO_PASSED="10", SCENARIO_FAILED="0", SCENARIO_TOTAL="10",
            SCENARIO_RATE="100", SATISFACTION_SCORE="90",
        )

        with patch("darkfactory.pipeline.quality_gate.execute_container", new=seq):
            result = run_quality_gate(sample_spec, config, "test-run", project_dir)

        assert result.exit_code == 1  # Failed due to mutation score
        assert result.mutation_score == 60

    def test_mutation_skipped_on_gate_failure(self, project_dir, sample_spec):
        """Mutation gate only runs when base gate passes (exit_code=0)."""
        config = _config(project_dir)
        config.mutation_cmd = "mutmut run"

        with patch("darkfactory.pipeline.quality_gate.execute_container",
                    return_value=RunResult(exit_code=1, stdout="test failures")) as mock_exec:
            result = run_quality_gate(sample_spec, config, "test-run", project_dir)

        assert result.exit_code == 1
        mock_exec.assert_called_once()  # No mutation container


@pytest.mark.integration
class TestQualityGateDryRun:
    """Dry run mode."""

    def test_dry_run_passes_flag(self, project_dir, sample_spec):
        config = _config(project_dir)

        with patch("darkfactory.pipeline.quality_gate.execute_container",
                    return_value=RunResult(exit_code=0, stdout="")) as mock_exec:
            result = run_quality_gate(
                sample_spec, config, "test-run", project_dir, dry_run=True,
            )

        # Verify dry_run=True was passed to execute_container
        _, kwargs = mock_exec.call_args
        assert kwargs.get("dry_run") is True


@pytest.mark.integration
class TestQualityGateDockerArgs:
    """Docker args correctly constructed."""

    def test_container_args_include_spec_name(self, project_dir, sample_spec):
        config = _config(project_dir)

        with patch("darkfactory.pipeline.quality_gate.execute_container",
                    return_value=RunResult(exit_code=0, stdout="")) as mock_exec:
            run_quality_gate(sample_spec, config, "test-run", project_dir)

        args, kwargs = mock_exec.call_args
        # args[2] is the cmd list
        cmd = args[2]
        assert sample_spec in cmd

    def test_container_image_matches_language(self, project_dir, sample_spec):
        config = _config(project_dir)

        with patch("darkfactory.pipeline.quality_gate.execute_container",
                    return_value=RunResult(exit_code=0, stdout="")) as mock_exec:
            run_quality_gate(sample_spec, config, "test-run", project_dir)

        args, kwargs = mock_exec.call_args
        # args[1] is the image name
        image = args[1]
        assert image == f"dark-factory-{config.language}"


@pytest.mark.integration
class TestQualityGateTimeout:
    """Gate timeout produces exit code 124."""

    def test_gate_timeout_exit_124(self, project_dir, sample_spec):
        config = _config(project_dir)

        with patch("darkfactory.pipeline.quality_gate.execute_container",
                    return_value=RunResult(exit_code=124, stdout="", timed_out=True)):
            result = run_quality_gate(sample_spec, config, "test-run", project_dir)

        assert result.exit_code == 124
        assert result.scenario_passed == 0
        assert result.scenario_total == 0


@pytest.mark.integration
class TestScenarioCountsFileWritten:
    """Gate run writes scenario counts file as a side effect."""

    def test_counts_file_exists_after_gate(self, project_dir, sample_spec):
        config = _config(project_dir)
        side_effect = _container_with_counts(
            project_dir, sample_spec,
            exit_code=0, passed=10, failed=0, total=10, rate=100, satisfaction=90,
        )

        with patch("darkfactory.pipeline.quality_gate.execute_container",
                    side_effect=side_effect):
            run_quality_gate(sample_spec, config, "test-run", project_dir)

        counts_file = (
            project_dir / ".dark-factory" / "reports"
            / f"{sample_spec}-scenario-counts.env"
        )
        assert counts_file.exists()
        content = counts_file.read_text()
        assert "SCENARIO_PASSED=10" in content
        assert "SCENARIO_TOTAL=10" in content
