"""Tests for mechanical stable-failure SHIP override (Phase 1)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import TempDirTestCase, make_project
from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import IterationState, SpecRun
from darkfactory.pipeline.iteration import should_ship_with_gaps
from darkfactory.pipeline.orchestrator import _write_defect_registry
from darkfactory.errors import ConfigError


def _make_config(**overrides) -> PipelineConfig:
    defaults = dict(
        model_id="claude-sonnet-4-20250514",
        source_dirs=["src"],
        max_iterations=3,
        min_ship_pass_rate=80,
        stable_failure_ship_override=True,
        stable_failure_min_ratio=0.8,
        stable_failure_min_iterations=2,
    )
    defaults.update(overrides)
    return PipelineConfig(**defaults)


def _make_spec_run(
    iteration: int = 2,
    stable_failure_ratio: float = 1.0,
    stable_failure_names: set[str] | None = None,
    ship_with_gaps: bool = False,
) -> SpecRun:
    sr = SpecRun(spec_name="test-spec")
    sr.iteration = iteration
    sr.stable_failure_ratio = stable_failure_ratio
    sr.stable_failure_names = stable_failure_names or {"test_a", "test_b"}
    sr.ship_with_gaps = ship_with_gaps
    return sr


class TestMechanicalShipOverrideConditions(unittest.TestCase):
    """Verify should_ship_with_gaps() — the actual function used in the iteration loop."""

    def test_override_fires_all_conditions_met(self) -> None:
        config = _make_config()
        spec_run = _make_spec_run(iteration=2, stable_failure_ratio=1.0)
        self.assertTrue(should_ship_with_gaps(config, spec_run, gate_exit_code=0, pass_rate=90))

    def test_override_blocked_by_low_pass_rate(self) -> None:
        config = _make_config(min_ship_pass_rate=80)
        spec_run = _make_spec_run(iteration=2, stable_failure_ratio=1.0)
        self.assertFalse(should_ship_with_gaps(config, spec_run, gate_exit_code=0, pass_rate=50))

    def test_override_blocked_by_failed_gates(self) -> None:
        config = _make_config()
        spec_run = _make_spec_run(iteration=2, stable_failure_ratio=1.0)
        self.assertFalse(should_ship_with_gaps(config, spec_run, gate_exit_code=1, pass_rate=90))

    def test_override_blocked_when_disabled(self) -> None:
        config = _make_config(stable_failure_ship_override=False)
        spec_run = _make_spec_run(iteration=2, stable_failure_ratio=1.0)
        self.assertFalse(should_ship_with_gaps(config, spec_run, gate_exit_code=0, pass_rate=90))

    def test_override_blocked_by_low_iteration(self) -> None:
        config = _make_config(stable_failure_min_iterations=3)
        spec_run = _make_spec_run(iteration=2, stable_failure_ratio=1.0)
        self.assertFalse(should_ship_with_gaps(config, spec_run, gate_exit_code=0, pass_rate=90))

    def test_override_blocked_by_low_stable_ratio(self) -> None:
        config = _make_config(stable_failure_min_ratio=0.8)
        spec_run = _make_spec_run(iteration=2, stable_failure_ratio=0.5)
        self.assertFalse(should_ship_with_gaps(config, spec_run, gate_exit_code=0, pass_rate=90))


class TestDefectRegistry(TempDirTestCase):
    """Test defect registry markdown generation."""

    def test_writes_registry_file(self) -> None:
        project_dir = make_project(self.tmp_dir)
        spec_run = _make_spec_run(
            iteration=2,
            stable_failure_names={"auth.test.ts > login fails", "api.test.ts > timeout"},
            ship_with_gaps=True,
        )
        # Add an iteration state so current_iteration works.
        iter_state = IterationState(
            number=2,
            gate_exit_code=0,
            pass_rate=87,
            scenario_pass_count=20,
            scenario_total=23,
        )
        spec_run.iterations.append(iter_state)

        _write_defect_registry(spec_run, project_dir)

        report_path = project_dir / ".dark-factory" / "reports" / "test-spec-defect-registry.md"
        self.assertTrue(report_path.exists())
        content = report_path.read_text()
        self.assertIn("MECHANICAL_OVERRIDE", content)
        self.assertIn("87%", content)
        self.assertIn("auth.test.ts > login fails", content)
        self.assertIn("api.test.ts > timeout", content)
        self.assertIn("20/23", content)


class TestConfigValidation(unittest.TestCase):
    """Test config validation for stable-failure fields."""

    def test_min_ratio_out_of_range(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig(stable_failure_min_ratio=1.5)

    def test_min_ratio_negative(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig(stable_failure_min_ratio=-0.1)

    def test_min_iterations_zero(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig(stable_failure_min_iterations=0)

    def test_valid_config(self) -> None:
        config = PipelineConfig(
            stable_failure_min_ratio=0.5,
            stable_failure_min_iterations=3,
        )
        self.assertEqual(config.stable_failure_min_ratio, 0.5)
        self.assertEqual(config.stable_failure_min_iterations, 3)


if __name__ == "__main__":
    unittest.main()
