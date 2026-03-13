"""Integration tests for CLI commands.

Tests invoke darkfactory.cli.main(argv=[...]) directly rather than
subprocess to avoid installation dependency. A smoke test verifies
the subprocess entry point works.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from darkfactory.cli import main


@pytest.mark.integration
class TestInit:
    """dark-factory init command."""

    def test_init_creates_project_structure(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # init requires a git repo
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')

        main(["--project-root", str(tmp_path), "init", "--non-interactive"])

        assert (tmp_path / ".dark-factory").is_dir()
        assert (tmp_path / ".dark-factory" / "specs").is_dir()
        assert (tmp_path / ".dark-factory" / "scenarios").is_dir()
        assert (tmp_path / ".dark-factory" / "config.toml").exists()

    def test_init_detects_python_project(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "myapp"\n')
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").touch()

        main(["--project-root", str(tmp_path), "init", "--non-interactive"])

        config_text = (tmp_path / ".dark-factory" / "config.toml").read_text()
        assert 'language = "python"' in config_text

    def test_init_detects_node_project(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
        (tmp_path / "package.json").write_text('{"name": "myapp"}')
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.ts").touch()

        main(["--project-root", str(tmp_path), "init", "--non-interactive"])

        config_text = (tmp_path / ".dark-factory" / "config.toml").read_text()
        assert 'language = "node"' in config_text

    def test_init_idempotent(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')

        main(["--project-root", str(tmp_path), "init", "--non-interactive"])
        main(["--project-root", str(tmp_path), "init", "--non-interactive"])

        assert (tmp_path / ".dark-factory" / "config.toml").exists()


@pytest.mark.integration
class TestDashboard:
    """dark-factory dashboard command."""

    def test_dashboard_no_events(self, project_dir: Path, capsys):
        main(["--project-root", str(project_dir), "dashboard"])
        captured = capsys.readouterr()
        assert "No events" in captured.out

    def test_dashboard_with_events(self, project_dir: Path, populated_events, capsys):
        main(["--project-root", str(project_dir), "dashboard"])
        captured = capsys.readouterr()
        # Dashboard should show shipped count > 0 and recent runs
        assert "Shipped:     1" in captured.out
        assert "Recent Runs" in captured.out


@pytest.mark.integration
class TestEventsRotate:
    """dark-factory events rotate command."""

    def test_events_rotate_no_rotation_needed(self, project_dir: Path, populated_events, capsys):
        # Fixture events file is tiny (< default 10MB max_size), so no rotation needed
        main(["--project-root", str(project_dir), "events", "rotate"])
        captured = capsys.readouterr()
        assert "No rotation needed." in captured.out


@pytest.mark.integration
class TestMigrateConfig:
    """dark-factory migrate-config command."""

    def test_migrate_config(self, project_dir: Path, capsys):
        bash_config = project_dir / ".dark-factory" / "config.sh"
        bash_config.write_text('LANGUAGE="go"\nAGENT_MAX_TURNS=15\n')
        toml_output = project_dir / ".dark-factory" / "migrated.toml"

        main([
            "--project-root", str(project_dir),
            "migrate-config",
            "--config-path", ".dark-factory/config.sh",
            "--output", ".dark-factory/migrated.toml",
        ])

        assert toml_output.exists()
        content = toml_output.read_text()
        assert 'language = "go"' in content
        assert "max_turns = 15" in content


@pytest.mark.integration
class TestConfirm:
    """dark-factory confirm command."""

    def test_confirm_no_hold_file(self, project_dir: Path, sample_spec):
        with pytest.raises(SystemExit) as exc_info:
            main(["--project-root", str(project_dir), "confirm", sample_spec])
        assert exc_info.value.code == 1


@pytest.mark.integration
class TestMetrics:
    """dark-factory metrics command."""

    def test_metrics_no_file(self, project_dir: Path, capsys):
        main(["--project-root", str(project_dir), "metrics"])
        captured = capsys.readouterr()
        assert "No metrics" in captured.out


@pytest.mark.integration
class TestExitCodes:
    """CLI exit code correctness."""

    def test_no_command_exits_1(self):
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 1

    def test_help_flag(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    def test_init_help(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["init", "--help"])
        assert exc_info.value.code == 0


@pytest.mark.integration
class TestGlobalFlags:
    """Global CLI flags work correctly."""

    def test_dry_run_accepted(self, project_dir: Path, sample_spec):
        # --dry-run should be parsed without error (actual effect tested in pipeline tests)
        with pytest.raises(SystemExit):
            main(["--project-root", str(project_dir), "--dry-run", "validate", sample_spec])

    def test_debug_accepted(self, project_dir: Path, capsys):
        main(["--project-root", str(project_dir), "--debug", "dashboard"])

    def test_ci_accepted(self, project_dir: Path, capsys):
        main(["--project-root", str(project_dir), "--ci", "dashboard"])


@pytest.mark.integration
class TestValidateCommand:
    """dark-factory validate command."""

    def test_validate_exits_1_missing_spec(self, project_dir: Path):
        """Validate a spec that doesn't exist → exit 1."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--project-root", str(project_dir), "validate", "nonexistent"])
        assert exc_info.value.code == 1

    def test_validate_invokes_pipeline(self, project_dir: Path, sample_spec):
        """Validate invokes run_pipeline on valid spec."""
        mock_precheck = MagicMock()
        mock_precheck.passed = True
        mock_precheck.ambiguities = []
        with patch("darkfactory.spec.precheck.run_precheck", return_value=mock_precheck), \
             patch("darkfactory.pipeline.orchestrator.run_pipeline", return_value=0) as mock_rp:
            with pytest.raises(SystemExit) as exc_info:
                main(["--project-root", str(project_dir), "validate", sample_spec])
        assert exc_info.value.code == 0
        mock_rp.assert_called_once()
        assert mock_rp.call_args[0][0] == sample_spec  # spec name


@pytest.mark.integration
class TestDeployCommand:
    """dark-factory deploy command."""

    def test_deploy_exits_1_no_canary(self, project_dir: Path, sample_spec):
        """Deploy with canary_enabled=False → exit 1."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--project-root", str(project_dir), "deploy", sample_spec])
        assert exc_info.value.code == 1

    def test_deploy_invokes_post_ship_pipeline(self, project_dir: Path, sample_spec):
        """Deploy with canary_enabled=True invokes post_ship_pipeline."""
        # Enable canary in config TOML
        config_path = project_dir / ".dark-factory" / "config.toml"
        config_text = config_path.read_text()
        config_path.write_text(
            config_text + "\n[canary]\nenabled = true\nrollback_cmd = \"/usr/bin/true\"\n"
        )

        with patch("darkfactory.pipeline.orchestrator.post_ship_pipeline", return_value=True) as mock_psp:
            with pytest.raises(SystemExit) as exc_info:
                main(["--project-root", str(project_dir), "deploy", sample_spec])
        assert exc_info.value.code == 0
        mock_psp.assert_called_once()
        assert mock_psp.call_args[0][0].spec_name == sample_spec


@pytest.mark.integration
class TestScaleTestCommand:
    """dark-factory scale-test command."""

    def test_scale_test_invokes_runner(self, project_dir: Path, capsys):
        """scale-test invokes run_scale_validation and reports results."""
        from darkfactory.scenarios.scale import ScaleResult
        mock_result = ScaleResult(
            total=5, passed=5, failed=0,
            wall_seconds=10.0, latencies=[1.0, 2.0, 3.0, 4.0, 5.0],
        )
        with patch("darkfactory.scenarios.scale.run_scale_validation", return_value=mock_result):
            with pytest.raises(SystemExit) as exc_info:
                main(["--project-root", str(project_dir), "scale-test"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "5/5 passed" in captured.out
        assert "0 failed" in captured.out


@pytest.mark.integration
class TestIntegrateCommand:
    """dark-factory integrate command."""

    def test_integrate_invokes_integrator(self, project_dir: Path, tmp_path: Path):
        """integrate calls the integrator with target path."""
        target = tmp_path / "target-project"
        target.mkdir()
        with patch("darkfactory.integration.integrator.integrate", return_value=True) as mock_int:
            with pytest.raises(SystemExit) as exc_info:
                main(["--project-root", str(project_dir), "integrate", str(target)])
        assert exc_info.value.code == 0
        mock_int.assert_called_once()
        assert mock_int.call_args[0][0] == target.resolve()  # target path


@pytest.mark.integration
class TestCalibrateCostCommand:
    """dark-factory calibrate-cost command."""

    def test_calibrate_cost_invokes_calibration(self, project_dir: Path, capsys):
        """calibrate-cost calls run_calibration and prints factor."""
        with patch("darkfactory.intelligence.calibrate.run_calibration", return_value=1.25):
            main(["--project-root", str(project_dir), "calibrate-cost"])
        captured = capsys.readouterr()
        assert "Correction factor: 1.25" in captured.out


@pytest.mark.integration
class TestSetupValidateCommand:
    """dark-factory setup validate command."""

    def test_setup_validate_reports_status(self, project_dir: Path, capsys):
        """setup validate prints check results."""
        from darkfactory.setup.validator import ValidationReport, CheckResult
        mock_report = ValidationReport()
        mock_report.checks = [
            CheckResult(status="pass", message="Git available"),
            CheckResult(status="fail", message="Docker not found"),
        ]
        with patch("darkfactory.setup.validator.validate_setup", return_value=mock_report):
            with pytest.raises(SystemExit) as exc_info:
                main(["--project-root", str(project_dir), "setup", "validate"])
        # Report has failures → exit 1
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Git available" in captured.out
        assert "Docker not found" in captured.out


@pytest.mark.integration
class TestRegressionCommand:
    """dark-factory regression command."""

    def test_regression_invokes_runner(self, project_dir: Path, capsys):
        """regression calls run_regression and prints results."""
        with patch("darkfactory.scenarios.regression.run_regression", return_value=(8, 2, 10)):
            with pytest.raises(SystemExit) as exc_info:
                main(["--project-root", str(project_dir), "regression"])
        assert exc_info.value.code == 1  # 2 failed
        captured = capsys.readouterr()
        assert "8/10 passed" in captured.out
        assert "2 failed" in captured.out


@pytest.mark.integration
class TestRunCommand:
    """dark-factory run command."""

    def test_run_invokes_pipeline(self, project_dir: Path, sample_spec):
        """run invokes run_pipeline with spec name."""
        with patch("darkfactory.pipeline.orchestrator.run_pipeline", return_value=0) as mock_rp:
            with pytest.raises(SystemExit) as exc_info:
                main(["--project-root", str(project_dir), "run", sample_spec])
        assert exc_info.value.code == 0
        mock_rp.assert_called_once()
        assert mock_rp.call_args[0][0] == sample_spec

    def test_run_dry_run(self, project_dir: Path, sample_spec):
        """run --dry-run passes dry_run=True to run_pipeline."""
        with patch("darkfactory.pipeline.orchestrator.run_pipeline", return_value=0) as mock_rp:
            with pytest.raises(SystemExit) as exc_info:
                main(["--project-root", str(project_dir), "--dry-run", "run", sample_spec])
        assert exc_info.value.code == 0
        assert mock_rp.call_args.kwargs["dry_run"] is True


@pytest.mark.integration
class TestGateCommand:
    """dark-factory gate command."""

    def test_gate_invokes_quality_gate(self, project_dir: Path, sample_spec, capsys):
        """gate invokes run_quality_gate and prints result."""
        from darkfactory.pipeline.quality_gate import GateResult
        mock_result = GateResult(
            exit_code=0, output="", scenario_passed=10, scenario_failed=0,
            scenario_total=10, pass_rate=100, satisfaction_score=90,
        )
        with patch("darkfactory.pipeline.quality_gate.run_quality_gate", return_value=mock_result) as mock_gate:
            with pytest.raises(SystemExit) as exc_info:
                main(["--project-root", str(project_dir), "gate", sample_spec])
        assert exc_info.value.code == 0
        mock_gate.assert_called_once()
        assert mock_gate.call_args[0][0] == sample_spec
        captured = capsys.readouterr()
        assert "pass_rate=100%" in captured.out


@pytest.mark.integration
class TestScenariosCommand:
    """dark-factory scenarios command."""

    def test_scenarios_invokes_runner(self, project_dir: Path, sample_spec):
        """scenarios invokes run_scenarios."""
        from darkfactory.utils.subprocess_runner import RunResult
        mock_result = RunResult(exit_code=0, stdout="5 passed", stderr="")
        with patch("darkfactory.scenarios.runner.run_scenarios", return_value=mock_result) as mock_scen:
            with pytest.raises(SystemExit) as exc_info:
                main(["--project-root", str(project_dir), "scenarios", sample_spec])
        assert exc_info.value.code == 0
        mock_scen.assert_called_once()
        assert mock_scen.call_args[0][0] == sample_spec


@pytest.mark.integration
class TestNewSpecCommand:
    """dark-factory new-spec command."""

    def test_new_spec_invokes_generator(self, project_dir: Path, capsys):
        """new-spec generates a draft spec file."""
        with patch("darkfactory.spec.generator.generate_spec",
                    return_value=project_dir / ".dark-factory" / "specs" / "add-auth.md") as mock_gen:
            main(["--project-root", str(project_dir), "new-spec", "Add user authentication"])
        mock_gen.assert_called_once()
        assert mock_gen.call_args[0][0] == "Add user authentication"
        captured = capsys.readouterr()
        assert "add-auth.md" in captured.out


@pytest.mark.integration
class TestUnknownCommand:
    """Unknown command produces error exit."""

    def test_unknown_command_exits_nonzero(self):
        """Unrecognized command → argparse exits with code 2."""
        with pytest.raises(SystemExit) as exc_info:
            main(["totally-bogus-command"])
        assert exc_info.value.code == 2


@pytest.mark.integration
class TestSubprocessSmoke:
    """Verify the entry point works via subprocess."""

    def test_help_via_subprocess(self):
        result = subprocess.run(
            [sys.executable, "-m", "darkfactory", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "dark-factory" in result.stdout.lower() or "Dark Factory" in result.stdout
