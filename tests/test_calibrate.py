"""Tests for cost calibration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from darkfactory.intelligence.calibrate import (
    run_calibration,
    _measure_invocation,
    _write_factor,
)
from darkfactory.intelligence.cost import estimate_cost
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import RunResult


class TestMeasureInvocation:
    def test_parses_usage_from_json(self) -> None:
        output = json.dumps({
            "result": "hello",
            "usage": {"input_tokens": 500, "output_tokens": 200},
        })
        mock_result = RunResult(exit_code=0, stdout=output)
        with patch("darkfactory.intelligence.calibrate.run_subprocess", return_value=mock_result):
            total = _measure_invocation("test prompt", PipelineConfig())
        assert total == 700

    def test_returns_none_on_failure(self) -> None:
        mock_result = RunResult(exit_code=1, stdout="", stderr="error")
        with patch("darkfactory.intelligence.calibrate.run_subprocess", return_value=mock_result):
            assert _measure_invocation("test", PipelineConfig()) is None

    def test_returns_none_on_bad_json(self) -> None:
        mock_result = RunResult(exit_code=0, stdout="not json")
        with patch("darkfactory.intelligence.calibrate.run_subprocess", return_value=mock_result):
            assert _measure_invocation("test", PipelineConfig()) is None

    def test_returns_none_on_zero_tokens(self) -> None:
        output = json.dumps({"usage": {}})
        mock_result = RunResult(exit_code=0, stdout=output)
        with patch("darkfactory.intelligence.calibrate.run_subprocess", return_value=mock_result):
            assert _measure_invocation("test", PipelineConfig()) is None


class TestWriteFactor:
    def test_creates_new_config(self, tmp_path: Path) -> None:
        _write_factor(tmp_path, 1.32)
        config_path = tmp_path / ".dark-factory" / "config.toml"
        assert config_path.exists()
        content = config_path.read_text()
        assert "cost_correction_factor = 1.32" in content

    def test_updates_existing_factor(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".dark-factory" / "config.toml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text('[cost]\ncost_correction_factor = 1.0\nbudget_usd = 5.0\n')
        _write_factor(tmp_path, 1.45)
        content = config_path.read_text()
        assert "cost_correction_factor = 1.45" in content
        assert "budget_usd = 5.0" in content

    def test_appends_to_existing_cost_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".dark-factory" / "config.toml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text('[cost]\nbudget_usd = 5.0\n')
        _write_factor(tmp_path, 1.2)
        content = config_path.read_text()
        assert "cost_correction_factor = 1.2" in content


class TestRunCalibration:
    def test_computes_factor(self, tmp_path: Path) -> None:
        config = PipelineConfig(
            cost_est_input_tokens_per_turn=4000,
            cost_est_output_tokens_per_turn=2000,
        )

        # Each invocation returns 9000 actual tokens (vs 6000 estimated).
        mock_result = RunResult(
            exit_code=0,
            stdout=json.dumps({"usage": {"input_tokens": 6000, "output_tokens": 3000}}),
        )
        with patch("darkfactory.intelligence.calibrate.run_subprocess", return_value=mock_result):
            factor = run_calibration(config, tmp_path)

        assert factor == 1.5  # 27000 actual / 18000 estimated = 1.5

    def test_handles_all_failures(self, tmp_path: Path) -> None:
        config = PipelineConfig()
        mock_result = RunResult(exit_code=1, stdout="", stderr="fail")
        with patch("darkfactory.intelligence.calibrate.run_subprocess", return_value=mock_result):
            factor = run_calibration(config, tmp_path)
        assert factor == 1.0  # Default when all fail.


class TestCostCorrectionFactor:
    def test_factor_1_no_change(self) -> None:
        config = PipelineConfig(cost_correction_factor=1.0)
        est = estimate_cost(10, config)
        assert est.input_tokens == 40_000  # 10 * 4000 * 1.0
        assert est.output_tokens == 20_000  # 10 * 2000 * 1.0

    def test_factor_increases_estimate(self) -> None:
        config = PipelineConfig(cost_correction_factor=1.5)
        est = estimate_cost(10, config)
        assert est.input_tokens == 60_000  # 10 * 4000 * 1.5
        assert est.output_tokens == 30_000  # 10 * 2000 * 1.5

    def test_factor_applied_to_cost(self) -> None:
        config = PipelineConfig(cost_correction_factor=2.0)
        est_1x = estimate_cost(10, PipelineConfig())
        est_2x = estimate_cost(10, config)
        assert abs(est_2x.estimated_usd - est_1x.estimated_usd * 2) < 0.001
