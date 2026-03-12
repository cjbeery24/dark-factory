"""Integration tests for non-trivial project structures.

Phase 3 of integration test expansion — validates the end-to-end path
from config loading through barrier configuration and scenario detection
for multi-source-dir project layouts.

All projects are built at runtime in tmp_path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from darkfactory.config.loader import load_config
from darkfactory.docker.barriers import configure_barriers
from darkfactory.models.agent import AgentRole
from darkfactory.scenarios.detection import has_scenario_files


def _write_config(project: Path, source_dirs: list[str], **extra: str) -> Path:
    """Write a config.toml and return its path."""
    dirs_toml = "[" + ", ".join(f'"{d}"' for d in source_dirs) + "]"
    lang = extra.get("language", "python")
    test_dir = extra.get("test_dir", "tests")
    config_path = project / ".dark-factory" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        f'[core]\nlanguage = "{lang}"\nsource_dirs = {dirs_toml}\n'
        f'test_dir = "{test_dir}"\ntest_cmd = "python3 -m pytest"\n'
        f"\n[agents]\nmax_turns = 5\ntimeout = 60\n"
        f"\n[autonomy]\nlevel = 3\nmax_iterations = 3\n"
        f"\n[gates]\ntimeout = 30\n"
    )
    return config_path


def _write_scenarios(project: Path, spec_name: str, count: int = 3) -> Path:
    """Write numbered scenario files and return the scenario dir."""
    scenario_dir = project / ".dark-factory" / "scenarios" / spec_name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, count + 1):
        (scenario_dir / f"{i:02d}-contract_test_{spec_name}.py").write_text(
            f"def test_{spec_name}_{i}():\n    assert True\n"
        )
    return scenario_dir


@pytest.mark.integration
class TestMultiSourceDirProject:
    """End-to-end: TOML with multiple source_dirs drives barrier masks
    and scenario detection in a single integration path."""

    @pytest.fixture
    def project(self, tmp_path: Path):
        source_dirs = ["src/python", "src/js"]
        for d in source_dirs:
            (tmp_path / d).mkdir(parents=True)
            (tmp_path / d / "mod.py").write_text("x = 1\n")
        config_path = _write_config(tmp_path, source_dirs)
        scenario_dir = _write_scenarios(tmp_path, "add-validation")
        cfg = load_config(config_path)
        return tmp_path, cfg, scenario_dir

    def test_config_to_barrier_end_to_end(self, project):
        """Config source_dirs flow through to barrier masks for every role."""
        _, cfg, scenario_dir = project
        assert cfg.source_dirs == ["src/python", "src/js"]

        barriers = configure_barriers(cfg.source_dirs)

        # Roles that mask source dirs must mask ALL of them.
        for role in (AgentRole.SCENARIO_ARCHITECT, AgentRole.EVALUATOR):
            masked = barriers[role].masked_dirs
            for d in cfg.source_dirs:
                assert d in masked, f"{role.value} missing mask for {d}"

        # Implementer must NOT mask source dirs.
        impl_masked = barriers[AgentRole.IMPLEMENTER].masked_dirs
        for d in cfg.source_dirs:
            assert d not in impl_masked, f"Implementer should not mask {d}"

        # Scenario detection works for the spec created alongside the project.
        assert has_scenario_files(scenario_dir)

