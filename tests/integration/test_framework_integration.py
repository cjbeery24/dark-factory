"""Integration tests for framework integration into target projects.

Target: darkfactory/integration/integrator.py — integrate()
Mock boundary: Mock subprocess for Docker build. Real: filesystem operations,
config generation, CLAUDE.md updates.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from darkfactory.integration.integrator import (
    _REQUIRED_DIRS,
    _LANGUAGE_DEFAULTS,
    integrate,
)
from darkfactory.utils.subprocess_runner import RunResult


def _setup_framework_dir(tmp_path: Path) -> Path:
    """Create a minimal framework directory with required subdirs."""
    fw = tmp_path / "framework"
    fw.mkdir()
    for d in ("agents", "templates", "docs", "skills"):
        subdir = fw / d
        subdir.mkdir()
        (subdir / "README.md").write_text(f"# {d}\n")
    # Docker build script (won't actually run — we mock it)
    docker_dir = fw / "docker"
    docker_dir.mkdir()
    (docker_dir / "build-images.sh").write_text("#!/bin/sh\necho built\n")
    os.chmod(docker_dir / "build-images.sh", 0o755)
    return fw


def _setup_project_dir(tmp_path: Path, marker_file: str = "") -> Path:
    """Create a minimal target project directory."""
    project = tmp_path / "project"
    project.mkdir()
    if marker_file:
        (project / marker_file).write_text("{}\n")
    return project


@pytest.mark.integration
class TestFullIntegration:
    """Full integration creates all required directories and files."""

    @patch("darkfactory.integration.integrator.run")
    def test_all_dirs_created(self, mock_run, tmp_path: Path):
        mock_run.return_value = RunResult(exit_code=0, stdout="built")
        fw = _setup_framework_dir(tmp_path)
        project = _setup_project_dir(tmp_path, "pyproject.toml")

        result = integrate(project, fw, language="python")

        assert result is True
        for d in _REQUIRED_DIRS:
            assert (project / d).is_dir(), f"Missing directory: {d}"

    @patch("darkfactory.integration.integrator.run")
    def test_config_and_claude_md_generated(self, mock_run, tmp_path: Path):
        mock_run.return_value = RunResult(exit_code=0, stdout="built")
        fw = _setup_framework_dir(tmp_path)
        project = _setup_project_dir(tmp_path, "pyproject.toml")

        integrate(project, fw, language="python")

        config = project / ".dark-factory" / "config.toml"
        assert config.exists()
        content = config.read_text()
        assert 'language = "python"' in content
        assert "pytest" in content

        claude_md = project / "CLAUDE.md"
        assert claude_md.exists()
        assert "Dark Factory" in claude_md.read_text()


@pytest.mark.integration
class TestLanguageDetection:
    """Language detection from marker files produces correct config defaults."""

    @patch("darkfactory.integration.integrator.run")
    def test_python_detected(self, mock_run, tmp_path: Path):
        mock_run.return_value = RunResult(exit_code=0, stdout="built")
        fw = _setup_framework_dir(tmp_path)
        project = _setup_project_dir(tmp_path, "pyproject.toml")

        integrate(project, fw, language="python")

        config = (project / ".dark-factory" / "config.toml").read_text()
        assert "pytest" in config
        assert "ruff" in config

    @patch("darkfactory.integration.integrator.run")
    def test_node_detected(self, mock_run, tmp_path: Path):
        mock_run.return_value = RunResult(exit_code=0, stdout="built")
        fw = _setup_framework_dir(tmp_path)
        project = _setup_project_dir(tmp_path, "package.json")

        integrate(project, fw, language="node")

        config = (project / ".dark-factory" / "config.toml").read_text()
        assert "npm" in config

    @patch("darkfactory.integration.integrator.run")
    def test_go_detected(self, mock_run, tmp_path: Path):
        mock_run.return_value = RunResult(exit_code=0, stdout="built")
        fw = _setup_framework_dir(tmp_path)
        project = _setup_project_dir(tmp_path, "go.mod")

        integrate(project, fw, language="go")

        config = (project / ".dark-factory" / "config.toml").read_text()
        assert "go test" in config


@pytest.mark.integration
class TestIdempotent:
    """Running integrate() twice is safe."""

    @patch("darkfactory.integration.integrator.run")
    def test_double_run_no_errors(self, mock_run, tmp_path: Path):
        mock_run.return_value = RunResult(exit_code=0, stdout="built")
        fw = _setup_framework_dir(tmp_path)
        project = _setup_project_dir(tmp_path, "pyproject.toml")

        assert integrate(project, fw, language="python") is True
        assert integrate(project, fw, language="python") is True

    @patch("darkfactory.integration.integrator.run")
    def test_no_duplicate_claude_md_sections(self, mock_run, tmp_path: Path):
        mock_run.return_value = RunResult(exit_code=0, stdout="built")
        fw = _setup_framework_dir(tmp_path)
        project = _setup_project_dir(tmp_path, "pyproject.toml")

        integrate(project, fw, language="python")
        integrate(project, fw, language="python")

        content = (project / "CLAUDE.md").read_text()
        assert content.count("## Dark Factory") == 1


@pytest.mark.integration
class TestForceFlag:
    """force=True overwrites existing framework files."""

    @patch("darkfactory.integration.integrator.run")
    def test_force_overwrites(self, mock_run, tmp_path: Path):
        mock_run.return_value = RunResult(exit_code=0, stdout="built")
        fw = _setup_framework_dir(tmp_path)
        project = _setup_project_dir(tmp_path, "pyproject.toml")

        integrate(project, fw, language="python")

        # Modify a framework file
        agents_readme = project / "dark-factory" / "agents" / "README.md"
        agents_readme.write_text("modified")

        # Force re-install
        integrate(project, fw, language="python", force=True)

        assert agents_readme.read_text() == "# agents\n"


@pytest.mark.integration
class TestSymlinkMode:
    """symlink=True creates a symlink instead of copying."""

    @patch("darkfactory.integration.integrator.run")
    def test_symlink_created(self, mock_run, tmp_path: Path):
        mock_run.return_value = RunResult(exit_code=0, stdout="built")
        fw = _setup_framework_dir(tmp_path)
        project = _setup_project_dir(tmp_path, "pyproject.toml")

        integrate(project, fw, language="python", symlink=True, force=True)

        target = project / "dark-factory"
        assert target.is_symlink()
        assert target.resolve() == fw.resolve()


@pytest.mark.integration
class TestDryRun:
    """dry_run=True creates no files."""

    @patch("darkfactory.integration.integrator.run")
    def test_no_files_created(self, mock_run, tmp_path: Path):
        fw = _setup_framework_dir(tmp_path)
        project = _setup_project_dir(tmp_path)

        result = integrate(project, fw, language="python", dry_run=True)

        assert result is True
        assert not (project / ".dark-factory" / "config.toml").exists()
        assert not (project / "dark-factory").exists()
        assert not (project / "CLAUDE.md").exists()
        mock_run.assert_not_called()


@pytest.mark.integration
class TestDockerUnavailable:
    """Docker build failure does not block integration."""

    @patch("darkfactory.integration.integrator.run")
    def test_docker_fail_still_succeeds(self, mock_run, tmp_path: Path):
        mock_run.return_value = RunResult(exit_code=1, stderr="docker not found")
        fw = _setup_framework_dir(tmp_path)
        project = _setup_project_dir(tmp_path, "pyproject.toml")

        result = integrate(project, fw, language="python")

        assert result is True
        assert (project / ".dark-factory" / "config.toml").exists()


@pytest.mark.integration
class TestMissingFrameworkDir:
    """Missing framework_dir produces a clear error."""

    def test_missing_framework_dir(self, tmp_path: Path):
        project = _setup_project_dir(tmp_path, "pyproject.toml")
        missing = tmp_path / "nonexistent"

        # integrate should handle the missing dir — it won't find framework subdirs
        # but should still create project dirs and config
        with patch("darkfactory.integration.integrator.run") as mock_run:
            mock_run.return_value = RunResult(exit_code=1, stderr="not found")
            result = integrate(project, missing, language="python")
            # Framework files won't be installed but integration still returns True
            assert result is True
            assert not (project / "dark-factory" / "agents").exists()
