"""Tests for project initialization."""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from darkfactory.setup.init import (
    _create_directories,
    _write_bootstrap_delta,
    _write_config,
    init_project,
)
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
    def test_writes_language_only(self, tmp_path: Path) -> None:
        """Phase 3: config.toml has language only, no operational fields."""
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
        # Operational fields are NOT in config.toml — they go to bootstrap delta.
        assert "source_dirs" not in content
        assert "test_dir" not in content
        assert "test_cmd" not in content
        assert "lint_cmd" not in content
        assert "[gates]" not in content

    def test_config_toml_has_no_gates_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".dark-factory" / "config.toml"
        config_path.parent.mkdir(parents=True)
        detection = DetectionResult(
            language="node",
            build_cmd="pnpm run build",
            test_cmd="pnpm test",
        )
        _write_config(config_path, detection)

        content = config_path.read_text()
        assert "[gates]" not in content
        assert "build_cmd" not in content


class TestWriteBootstrapDelta:
    def test_creates_bootstrap_delta(self, tmp_path: Path) -> None:
        detection = DetectionResult(
            language="python",
            package_manager="pip",
            source_dirs=["src/myapp"],
            test_dir="tests",
            build_cmd="pip install -e . -q",
            lint_cmd="ruff check .",
            test_cmd="pytest",
        )
        _write_bootstrap_delta(tmp_path, detection)

        deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
        assert deltas_dir.is_dir()
        delta_files = list(deltas_dir.glob("*.toml"))
        assert len(delta_files) == 1
        assert "bootstrap-from-detection" in delta_files[0].name

    def test_bootstrap_delta_contains_detected_commands(self, tmp_path: Path) -> None:
        detection = DetectionResult(
            language="node",
            package_manager="pnpm",
            build_cmd="pnpm run build",
            lint_cmd="pnpm -r lint",
            test_cmd="pnpm test",
            setup_cmd="pnpm exec prisma db push",
            server_cmd="pnpm run dev",
        )
        _write_bootstrap_delta(tmp_path, detection)

        deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
        delta_path = list(deltas_dir.glob("*.toml"))[0]
        raw = tomllib.loads(delta_path.read_text())
        ops = {op["path"]: op["value"] for op in raw["ops"]}

        assert ops["runtime.language"] == "node"
        assert ops["runtime.package_manager"] == "pnpm"
        assert ops["commands.build"] == "pnpm run build"
        assert ops["commands.lint"] == "pnpm -r lint"
        assert ops["commands.test"] == "pnpm test"
        assert ops["commands.setup"] == "pnpm exec prisma db push"
        assert ops["commands.server_start"] == "pnpm run dev"

    def test_bootstrap_delta_materializes_environment_toml(self, tmp_path: Path) -> None:
        detection = DetectionResult(
            language="python",
            build_cmd="pip install -e . -q",
        )
        _write_bootstrap_delta(tmp_path, detection)

        env_path = tmp_path / ".dark-factory" / "architecture" / "environment.toml"
        assert env_path.exists()
        raw = tomllib.loads(env_path.read_text())
        assert raw["runtime"]["language"] == "python"
        assert raw["commands"]["build"] == "pip install -e . -q"

    def test_noop_when_no_detection(self, tmp_path: Path) -> None:
        detection = DetectionResult(language="unknown")
        _write_bootstrap_delta(tmp_path, detection)

        deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
        # No delta files should be written when language is unknown.
        if deltas_dir.exists():
            assert list(deltas_dir.glob("*.toml")) == []

    def test_bootstrap_delta_includes_source_dirs(self, tmp_path: Path) -> None:
        detection = DetectionResult(
            language="python",
            source_dirs=["src/myapp", "src/lib"],
            test_dir="tests",
        )
        _write_bootstrap_delta(tmp_path, detection)

        deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
        delta_path = list(deltas_dir.glob("*.toml"))[0]
        raw = tomllib.loads(delta_path.read_text())
        ops = {op["path"]: op["value"] for op in raw["ops"]}

        assert ops["layout.source_dirs"] == ["src/myapp", "src/lib"]
        assert ops["layout.test_dirs"] == ["tests"]


    def test_reinit_skips_bootstrap_when_deltas_exist(self, tmp_path: Path) -> None:
        """Re-running init must not corrupt existing delta chain."""
        detection = DetectionResult(
            language="python",
            build_cmd="pip install -e . -q",
        )
        _write_bootstrap_delta(tmp_path, detection)

        deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
        count_after_first = len(list(deltas_dir.glob("*.toml")))

        # Second call with different detection — should be skipped.
        detection2 = DetectionResult(
            language="node",
            build_cmd="pnpm run build",
        )
        _write_bootstrap_delta(tmp_path, detection2)

        # No new delta file.
        assert len(list(deltas_dir.glob("*.toml"))) == count_after_first

    def test_rollback_on_materialize_failure(self, tmp_path: Path) -> None:
        detection = DetectionResult(language="python", build_cmd="make")
        with patch(
            "darkfactory.architecture.deltas.materialize",
            side_effect=RuntimeError("disk full"),
        ):
            with pytest.raises(RuntimeError, match="disk full"):
                _write_bootstrap_delta(tmp_path, detection)

        deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
        if deltas_dir.exists():
            assert list(deltas_dir.glob("*.toml")) == []


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
        # Bootstrap delta was created.
        assert (tmp_path / ".dark-factory" / "architecture" / "environment.toml").exists()

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

    def test_new_project_config_toml_has_no_operational_fields(self, tmp_path: Path) -> None:
        """Phase 3: config.toml must not contain [gates], source_dirs, test_dir."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'app'\n")
        (tmp_path / "src" / "app").mkdir(parents=True)
        (tmp_path / "src" / "app" / "__init__.py").write_text("")
        (tmp_path / "tests").mkdir()

        with patch("darkfactory.setup.init.check_prerequisites", return_value=[]):
            init_project(tmp_path, non_interactive=True)

        config = (tmp_path / ".dark-factory" / "config.toml").read_text()
        assert "[gates]" not in config
        assert "source_dirs" not in config
        assert "test_dir" not in config
        assert "build_cmd" not in config
