"""Tests for project initialization."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from darkfactory.setup.init import _create_directories, _write_config, init_project
from darkfactory.setup.detect import DetectionResult


class TestCreateDirectories:
    def test_creates_all_dirs(self, tmp_path: Path) -> None:
        _create_directories(tmp_path)
        expected = [
            ".dark-factory",
            ".dark-factory/specs",
            ".dark-factory/scenarios",
            ".dark-factory/reviews",
            ".dark-factory/reports",
            ".dark-factory/audit",
            ".dark-factory/state",
            ".dark-factory/plans",
        ]
        for d in expected:
            assert (tmp_path / d).is_dir(), f"{d} not created"

    def test_idempotent(self, tmp_path: Path) -> None:
        _create_directories(tmp_path)
        _create_directories(tmp_path)  # Should not raise.


class TestWriteConfig:
    def test_writes_basic_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".dark-factory" / "config.toml"
        config_path.parent.mkdir(parents=True)
        detection = DetectionResult(
            language="python",
            source_dirs=["src/myapp"],
            test_dir="tests",
            test_cmd="pytest",
            lint_cmd="ruff check .",
        )
        _write_config(config_path, detection)

        content = config_path.read_text()
        assert 'language = "python"' in content
        assert 'source_dirs = ["src/myapp"]' in content
        assert 'test_dir = "tests"' in content
        assert 'test_cmd = "pytest"' in content
        assert 'lint_cmd = "ruff check ."' in content

    def test_skips_empty_commands(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".dark-factory" / "config.toml"
        config_path.parent.mkdir(parents=True)
        detection = DetectionResult(language="go", source_dirs=["."], test_dir=".")
        _write_config(config_path, detection)

        content = config_path.read_text()
        assert "[gates]" not in content  # No commands detected.


class TestInitProject:
    def test_non_interactive_python(self, tmp_path: Path) -> None:
        # Create a minimal Python project.
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'myapp'\n")
        (tmp_path / "src" / "myapp").mkdir(parents=True)
        (tmp_path / "src" / "myapp" / "__init__.py").write_text("")
        (tmp_path / "tests").mkdir()

        with patch("darkfactory.setup.init.check_prerequisites", return_value=[]):
            result = init_project(tmp_path, non_interactive=True)

        assert result.success
        assert result.language == "python"
        assert (tmp_path / ".dark-factory" / "config.toml").exists()
        assert (tmp_path / ".dark-factory" / "specs").is_dir()

    def test_non_interactive_with_language_override(self, tmp_path: Path) -> None:
        # Empty project — force language.
        (tmp_path / "pyproject.toml").write_text("")
        (tmp_path / "app.py").write_text("")

        with patch("darkfactory.setup.init.check_prerequisites", return_value=[]):
            result = init_project(tmp_path, non_interactive=True, language="python")

        assert result.success
        assert result.language == "python"

    def test_fails_on_prereq_errors(self, tmp_path: Path) -> None:
        with patch("darkfactory.setup.init.check_prerequisites",
                    return_value=["Docker not installed"]):
            result = init_project(tmp_path, non_interactive=True, language="python")

        assert not result.success
        assert "Docker not installed" in result.errors

    def test_fails_on_unknown_language(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Hello")

        with patch("darkfactory.setup.init.check_prerequisites", return_value=[]):
            result = init_project(tmp_path, non_interactive=True)

        assert not result.success
        assert any("Could not detect" in e for e in result.errors)
