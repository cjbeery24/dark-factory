"""Cross-phase integration test — full config overlay precedence chain.

Verifies:
  PipelineConfig defaults
    → config.toml (tuning only)
    → bootstrap delta (from backcompat migration)
    → Architect delta (simulated)
    → auto-detect delta (gap-filling)
    → final resolved config via refresh_config_from_environment

This is the canary test for the config unification plan. If it fails,
the layered resolution is broken.
"""

from __future__ import annotations

import tomli_w
import pytest
from pathlib import Path
from unittest.mock import patch

from darkfactory.architecture.backcompat import ensure_environment_bootstrap
from darkfactory.architecture.deltas import (
    SCHEMA_VERSION,
    hash_view,
    materialize,
)
from darkfactory.architecture.environment import EnvironmentContract
from darkfactory.architecture.runtime import (
    _reset_cache,
    load_environment,
    refresh_config_from_environment,
)
from darkfactory.config.loader import load_config
from darkfactory.models.config import PipelineConfig
from darkfactory.setup.autodetect_delta import write_autodetect_delta


def _deltas_dir(project: Path) -> Path:
    return project / ".dark-factory" / "architecture" / "environment.deltas"


def _env_path(project: Path) -> Path:
    return project / ".dark-factory" / "architecture" / "environment.toml"


def _write_config_toml(project: Path, content: str) -> Path:
    config_path = project / ".dark-factory" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content, encoding="utf-8")
    return config_path


def _write_delta(project: Path, number: int, slug: str, ops: list[dict]) -> Path:
    """Write a delta file and materialize the environment.

    ``number`` must match the actual lex ordering of deltas in the directory
    — callers are responsible for passing sequential values (1, 2, 3, …).
    """
    ddir = _deltas_dir(project)
    ddir.mkdir(parents=True, exist_ok=True)

    # Compute parent hash from current materialized state.
    env_path = _env_path(project)
    _reset_cache()
    if env_path.exists():
        env = load_environment(project)
    else:
        env = EnvironmentContract()
    parent = hash_view(env)

    doc = {
        "schema_version": SCHEMA_VERSION,
        "spec_id": slug,
        "parent_hash": parent,
        "applied_at": "2026-04-10T12:00:00Z",
    }
    if ops:
        doc["ops"] = ops

    filename = f"{number:04d}-{slug}.toml"
    delta_path = ddir / filename
    delta_path.write_text(tomli_w.dumps(doc), encoding="utf-8")
    _reset_cache()
    materialize(ddir, env_path)
    _reset_cache()
    return delta_path


@pytest.mark.integration
class TestFullOverlayChain:
    """End-to-end: defaults → config.toml → bootstrap → architect → auto-detect."""

    def _setup_legacy_project(self, tmp_path: Path) -> Path:
        """Create a project with a legacy config.toml containing operational fields.

        Uses non-default values for language and source_dirs so the bootstrap
        delta actually migrates them (default values are skipped).
        """
        project = tmp_path / "project"
        project.mkdir()
        (project / "lib").mkdir()

        # Legacy config.toml: has both tuning AND operational fields.
        _write_config_toml(project, """\
[core]
language = "python"
source_dirs = ["lib"]

[gates]
timeout = 999
min_ship_pass_rate = 95
build_cmd = "pip install -e ."
test_cmd = "pytest"
lint_cmd = "ruff check ."

[autonomy]
max_iterations = 5
""")
        return project

    def test_defaults_before_any_overlay(self) -> None:
        """PipelineConfig() defaults are the baseline."""
        config = PipelineConfig()
        assert config.build_cmd == ""
        assert config.test_cmd == ""
        assert config.language == "node"
        assert config.source_dirs == ["src"]
        assert config.gate_timeout == 300
        assert config.get_provenance("build_cmd") == "default"

    def test_config_toml_sets_tuning_only(self, tmp_path: Path) -> None:
        """After migration, config.toml should only set tuning fields."""
        project = self._setup_legacy_project(tmp_path)
        config_path = project / ".dark-factory" / "config.toml"
        config = load_config(config_path)

        # Tuning fields come from config.toml.
        assert config.gate_timeout == 999
        assert config.min_ship_pass_rate == 95
        assert config.max_iterations == 5

    def test_bootstrap_delta_captures_operational_fields(self, tmp_path: Path) -> None:
        """ensure_environment_bootstrap migrates operational fields to delta."""
        project = self._setup_legacy_project(tmp_path)
        config_path = project / ".dark-factory" / "config.toml"
        config = load_config(config_path)

        synthesized = ensure_environment_bootstrap(config, project)
        assert synthesized is True

        # Bootstrap delta should exist.
        ddir = _deltas_dir(project)
        deltas = sorted(ddir.glob("*.toml"))
        assert len(deltas) >= 1
        assert "bootstrap" in deltas[0].name

        # Environment.toml should have the operational values.
        _reset_cache()
        env = load_environment(project)
        assert env.commands.build == "pip install -e ."
        assert env.commands.test == "pytest"
        assert env.commands.lint == "ruff check ."
        assert env.runtime.language == "python"
        assert env.layout.source_dirs == ["lib"]

    def test_architect_delta_overrides_bootstrap(self, tmp_path: Path) -> None:
        """An Architect delta overrides values set by the bootstrap."""
        project = self._setup_legacy_project(tmp_path)
        config_path = project / ".dark-factory" / "config.toml"
        config = load_config(config_path)

        assert ensure_environment_bootstrap(config, project) is True

        # Simulate Architect amending the build command.
        _write_delta(project, 1, "arch-spec", [
            {"op": "set", "path": "commands.build", "value": "python -m build"},
            {"op": "set", "path": "commands.setup", "value": "alembic upgrade head"},
        ])

        _reset_cache()
        env = load_environment(project)
        assert env.commands.build == "python -m build"
        assert env.commands.setup == "alembic upgrade head"
        # Bootstrap values unchanged for non-overridden fields.
        assert env.commands.test == "pytest"
        assert env.commands.lint == "ruff check ."

    def test_autodetect_fills_gaps_only(self, tmp_path: Path) -> None:
        """Auto-detect delta fills empty fields but does not override set ones."""
        project = self._setup_legacy_project(tmp_path)
        config_path = project / ".dark-factory" / "config.toml"
        config = load_config(config_path)

        assert ensure_environment_bootstrap(config, project) is True

        # After bootstrap: build, test, lint are set. typecheck is empty.
        _reset_cache()
        env = load_environment(project)
        refresh_config_from_environment(config, env)

        # Mock detection to find a typecheck command.
        from darkfactory.setup.detect import DetectionResult
        mock_result = DetectionResult(
            language="python",
            build_cmd="pip install -e .",
            test_cmd="pytest",
            lint_cmd="ruff check .",
            typecheck_cmd="mypy lib",  # NEW — not in bootstrap
        )
        with patch("darkfactory.setup.autodetect_delta.detect_project", return_value=mock_result):
            filled = write_autodetect_delta(project, config)

        # Only typecheck should be filled (others already set).
        filled_fields = [f for f, _ in filled]
        assert "typecheck_cmd" in filled_fields
        assert "build_cmd" not in filled_fields
        assert "test_cmd" not in filled_fields
        assert "lint_cmd" not in filled_fields

        _reset_cache()
        env = load_environment(project)
        assert env.commands.typecheck == "mypy lib"
        # Existing values untouched.
        assert env.commands.build == "pip install -e ."

    def test_full_chain_provenance(self, tmp_path: Path) -> None:
        """After the full chain, provenance tracks which layer set each field."""
        project = self._setup_legacy_project(tmp_path)
        config_path = project / ".dark-factory" / "config.toml"
        config = load_config(config_path)

        # Step 1: Bootstrap migration.
        assert ensure_environment_bootstrap(config, project) is True

        # Step 2: Architect override.
        _write_delta(project, 1, "arch-spec", [
            {"op": "set", "path": "commands.build", "value": "python -m build"},
        ])

        # Step 3: Refresh config from environment.
        _reset_cache()
        env = load_environment(project)
        refresh_config_from_environment(config, env)

        # Operational fields should have environment.toml provenance.
        assert config.get_provenance("build_cmd") == "environment.toml"
        assert config.get_provenance("test_cmd") == "environment.toml"
        # The actual values should reflect the latest delta.
        assert config.build_cmd == "python -m build"  # Architect override
        assert config.test_cmd == "pytest"  # From bootstrap

        # Tuning fields stay at config.toml provenance.
        assert config.get_provenance("gate_timeout") == "config.toml:[gates]"
        assert config.gate_timeout == 999

    def test_config_toml_stripped_after_migration(self, tmp_path: Path) -> None:
        """After bootstrap, operational fields are removed from config.toml."""
        project = self._setup_legacy_project(tmp_path)
        config_path = project / ".dark-factory" / "config.toml"
        config = load_config(config_path)

        assert ensure_environment_bootstrap(config, project) is True

        # Re-read config.toml — operational fields should be gone.
        import tomllib
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
        gates = raw.get("gates", {})
        assert "build_cmd" not in gates
        assert "test_cmd" not in gates
        assert "lint_cmd" not in gates
        # Tuning fields preserved.
        assert gates.get("timeout") == 999
        assert gates.get("min_ship_pass_rate") == 95

    def test_operational_values_not_from_config_toml(self, tmp_path: Path) -> None:
        """After full chain, operational config comes from env, not config.toml."""
        project = self._setup_legacy_project(tmp_path)
        config_path = project / ".dark-factory" / "config.toml"

        # Load config (gets operational fields from legacy config.toml).
        config = load_config(config_path)
        assert config.build_cmd == "pip install -e ."

        # Run migration — strips operational fields.
        assert ensure_environment_bootstrap(config, project) is True

        # Reload config from stripped config.toml.
        config2 = load_config(config_path)
        # Operational fields should now be empty (not in config.toml anymore).
        assert config2.build_cmd == ""
        assert config2.test_cmd == ""

        # They come back only after env overlay.
        _reset_cache()
        env = load_environment(project)
        refresh_config_from_environment(config2, env)
        assert config2.build_cmd == "pip install -e ."
        assert config2.test_cmd == "pytest"

    def test_complete_chain_all_layers(self, tmp_path: Path) -> None:
        """Full canary: exercises every layer on one project in sequence.

        defaults → config.toml → bootstrap → architect → auto-detect → refresh
        """
        project = self._setup_legacy_project(tmp_path)
        config_path = project / ".dark-factory" / "config.toml"

        # Layer 1: PipelineConfig defaults.
        defaults = PipelineConfig()
        assert defaults.build_cmd == ""
        assert defaults.typecheck_cmd == ""

        # Layer 2: config.toml (tuning + legacy operational).
        config = load_config(config_path)
        assert config.gate_timeout == 999  # tuning
        assert config.build_cmd == "pip install -e ."  # legacy operational

        # Layer 3: Bootstrap delta (migrates operational fields).
        assert ensure_environment_bootstrap(config, project) is True
        _reset_cache()
        env = load_environment(project)
        assert env.commands.build == "pip install -e ."
        assert env.commands.test == "pytest"
        assert env.runtime.language == "python"

        # Layer 4: Architect delta (overrides build, adds setup).
        _write_delta(project, 1, "arch-spec", [
            {"op": "set", "path": "commands.build", "value": "python -m build"},
            {"op": "set", "path": "commands.setup", "value": "alembic upgrade head"},
        ])
        _reset_cache()
        env = load_environment(project)
        assert env.commands.build == "python -m build"
        assert env.commands.setup == "alembic upgrade head"
        assert env.commands.test == "pytest"  # untouched

        # Refresh after architect delta.
        refresh_config_from_environment(config, env)
        assert config.build_cmd == "python -m build"
        assert config.setup_cmd == "alembic upgrade head"
        assert config.test_cmd == "pytest"

        # Layer 5: Auto-detect delta (fills typecheck gap).
        from darkfactory.setup.detect import DetectionResult
        mock_result = DetectionResult(
            language="python",
            build_cmd="pip install -e .",
            test_cmd="pytest",
            lint_cmd="ruff check .",
            typecheck_cmd="mypy lib",
        )
        with patch("darkfactory.setup.autodetect_delta.detect_project", return_value=mock_result):
            filled = write_autodetect_delta(project, config)

        filled_fields = [f for f, _ in filled]
        assert "typecheck_cmd" in filled_fields
        assert "build_cmd" not in filled_fields  # already set by architect

        # Final refresh.
        _reset_cache()
        env = load_environment(project)
        refresh_config_from_environment(config, env)

        # Verify final values — each from the correct layer.
        assert config.build_cmd == "python -m build"  # architect
        assert config.test_cmd == "pytest"  # bootstrap
        assert config.lint_cmd == "ruff check ."  # bootstrap
        assert config.typecheck_cmd == "mypy lib"  # auto-detect
        assert config.setup_cmd == "alembic upgrade head"  # architect
        assert config.language == "python"  # bootstrap

        # Verify provenance — all operational fields from env.
        assert config.get_provenance("build_cmd") == "environment.toml"
        assert config.get_provenance("test_cmd") == "environment.toml"
        assert config.get_provenance("typecheck_cmd") == "environment.toml"
        assert config.get_provenance("setup_cmd") == "environment.toml"
        assert config.get_provenance("language") == "environment.toml"

        # Tuning stays at config.toml provenance.
        assert config.get_provenance("gate_timeout") == "config.toml:[gates]"
        assert config.gate_timeout == 999
        assert config.min_ship_pass_rate == 95
        assert config.max_iterations == 5
