"""Integration tests for CLI command gaps.

Tests CLI dispatch to underlying functions. Pipeline-triggering
commands mock their underlying function at the definition site.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from darkfactory.cli import main


@pytest.mark.integration
class TestNewSpec:
    """dark-factory new-spec command."""

    def test_new_spec_creates_file(self, project_dir: Path, capsys):
        # Copy templates dir so generate_spec can find them
        framework_templates = Path(__file__).parents[2] / "templates"
        dest_templates = project_dir / "templates"
        if framework_templates.exists():
            shutil.copytree(framework_templates, dest_templates)

        main([
            "--project-root", str(project_dir),
            "new-spec", "Add user authentication",
        ])
        captured = capsys.readouterr()
        assert "Draft written:" in captured.out

        specs_dir = project_dir / ".dark-factory" / "specs"
        spec_files = list(specs_dir.glob("*.md"))
        assert len(spec_files) >= 1


@pytest.mark.integration
class TestRunCommand:
    """dark-factory run command."""

    def test_run_invokes_pipeline(self, project_dir: Path, sample_spec):
        with patch("darkfactory.pipeline.orchestrator.run_pipeline", return_value=0) as mock_pipeline:
            with pytest.raises(SystemExit) as exc_info:
                main(["--project-root", str(project_dir), "run", sample_spec])
            assert exc_info.value.code == 0
            mock_pipeline.assert_called_once()
            args, kwargs = mock_pipeline.call_args
            assert args[0] == sample_spec

    def test_run_dry_run(self, project_dir: Path, sample_spec):
        with patch("darkfactory.pipeline.orchestrator.run_pipeline", return_value=0) as mock_pipeline:
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "--project-root", str(project_dir),
                    "--dry-run", "run", sample_spec,
                ])
            assert exc_info.value.code == 0
            _, kwargs = mock_pipeline.call_args
            assert kwargs.get("dry_run") is True


@pytest.mark.integration
class TestValidateCommand:
    """dark-factory validate command."""

    def test_validate_invokes_pipeline(self, project_dir: Path, sample_spec):
        from tests.helpers import make_passing_precheck_result
        mock_precheck = make_passing_precheck_result()
        with patch("darkfactory.spec.precheck.run_precheck", return_value=mock_precheck), \
             patch("darkfactory.pipeline.orchestrator.run_pipeline", return_value=0) as mock_pipeline:
            with pytest.raises(SystemExit) as exc_info:
                main(["--project-root", str(project_dir), "validate", sample_spec])
            assert exc_info.value.code == 0
            mock_pipeline.assert_called_once()


@pytest.mark.integration
class TestGateCommand:
    """dark-factory gate command."""

    def test_gate_invokes_quality_gate(self, project_dir: Path, sample_spec, capsys):
        from darkfactory.pipeline.quality_gate import GateResult
        mock_result = GateResult(exit_code=0, pass_rate=100, satisfaction_score=90)

        with patch("darkfactory.pipeline.quality_gate.run_quality_gate", return_value=mock_result):
            with pytest.raises(SystemExit) as exc_info:
                main(["--project-root", str(project_dir), "gate", sample_spec])
            assert exc_info.value.code == 0


@pytest.mark.integration
class TestScenariosCommand:
    """dark-factory scenarios command."""

    def test_scenarios_invokes_runner(self, project_dir: Path, sample_spec):
        mock_result = MagicMock()
        mock_result.exit_code = 0

        with patch("darkfactory.scenarios.runner.run_scenarios", return_value=mock_result):
            with pytest.raises(SystemExit) as exc_info:
                main(["--project-root", str(project_dir), "scenarios", sample_spec])
            assert exc_info.value.code == 0


@pytest.mark.integration
class TestUnknownCommand:
    """Unknown command produces error."""

    def test_unknown_command_error(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["nonexistent-command"])
        assert exc_info.value.code == 2
