"""Integration tests for cost estimation, budget enforcement, and calibration.

No Docker needed — tests exercise the cost module with real config
and events file I/O. Calibration mocks subprocess to avoid real Claude calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from unittest.mock import patch

from darkfactory.errors import BudgetExceeded
from darkfactory.intelligence.calibrate import run_calibration
from darkfactory.intelligence.cost import check_budget, estimate_cost
from darkfactory.models.config import PipelineConfig
from darkfactory.models.events import EventType
from darkfactory.utils.subprocess_runner import RunResult


def _read_events(events_file: Path) -> list[dict]:
    if not events_file.exists():
        return []
    result = []
    with events_file.open() as f:
        for line in f:
            s = line.strip()
            if s:
                result.append(json.loads(s))
    return result


@pytest.mark.integration
class TestCostEstimation:
    """Cost estimation with known inputs."""

    def test_exact_formula(self):
        config = PipelineConfig(
            cost_per_input_token=0.000015,
            cost_per_output_token=0.000075,
            cost_est_input_tokens_per_turn=4000,
            cost_est_output_tokens_per_turn=2000,
            cost_correction_factor=1.0,
        )
        est = estimate_cost(10, config)
        # 10 turns * 4000 = 40000 input tokens
        # 10 turns * 2000 = 20000 output tokens
        assert est.input_tokens == 40000
        assert est.output_tokens == 20000
        # 40000 * 0.000015 + 20000 * 0.000075 = 0.6 + 1.5 = 2.1
        assert abs(est.estimated_usd - 2.1) < 0.0001

    def test_correction_factor(self):
        config = PipelineConfig(
            cost_per_input_token=0.000015,
            cost_per_output_token=0.000075,
            cost_est_input_tokens_per_turn=4000,
            cost_est_output_tokens_per_turn=2000,
            cost_correction_factor=1.5,
        )
        est = estimate_cost(10, config)
        # tokens scaled by 1.5
        assert est.input_tokens == 60000
        assert est.output_tokens == 30000

    def test_zero_turns(self):
        config = PipelineConfig()
        est = estimate_cost(0, config)
        assert est.estimated_usd == 0.0
        assert est.input_tokens == 0
        assert est.output_tokens == 0


@pytest.mark.integration
class TestBudgetEnforcement:
    """Budget checking with real events file."""

    def test_no_budget_returns_empty_status(self, project_dir: Path):
        config = PipelineConfig(cost_budget_usd=0.0)
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        status = check_budget(100, config, events_file, "test", "run1")
        assert not status.exceeded

    def test_within_budget(self, project_dir: Path):
        config = PipelineConfig(cost_budget_usd=100.0)
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        status = check_budget(10, config, events_file, "test", "run1")
        assert not status.exceeded
        assert status.spent_usd > 0
        assert status.remaining_usd > 0

    def test_exceeds_budget_raises(self, project_dir: Path):
        # Set a tiny budget that will be exceeded
        config = PipelineConfig(cost_budget_usd=0.001)
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        with pytest.raises(BudgetExceeded, match="budget exceeded"):
            check_budget(100, config, events_file, "test", "run1")

    def test_alert_thresholds(self, project_dir: Path):
        config = PipelineConfig(
            cost_budget_usd=10.0,
            cost_alert_thresholds=[50, 80, 100],
        )
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        alerted: set[int] = set()

        # With enough turns to hit ~50% but not 100%
        # 10 turns ≈ $2.10, budget=$10 → 21%
        status = check_budget(10, config, events_file, "test", "run1", alerted=alerted)
        assert not status.exceeded

        # 30 turns ≈ $6.30, budget=$10 → 63%
        status = check_budget(30, config, events_file, "test", "run1", alerted=alerted)
        assert 50 in alerted
        assert not status.exceeded

    @patch("darkfactory.intelligence.cost.notify_budget_alert")
    @patch("darkfactory.intelligence.cost.emit_budget_alert")
    def test_alerted_set_prevents_duplicate(self, mock_emit, mock_notify, project_dir: Path):
        config = PipelineConfig(
            cost_budget_usd=10.0,
            cost_alert_thresholds=[50, 80, 100],
        )
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        alerted: set[int] = {50}  # Pre-mark 50 as already alerted

        # 30 turns ≈ 63% — would trigger 50% alert, but already alerted
        check_budget(30, config, events_file, "test", "run1", alerted=alerted)
        # Neither emit nor notify should be called for the 50% threshold
        mock_emit.assert_not_called()
        mock_notify.assert_not_called()

    def test_higher_turns_cost_more(self, project_dir: Path):
        config = PipelineConfig(cost_budget_usd=100.0)
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        # check_budget is stateless — each call estimates from total_turns
        s1 = check_budget(10, config, events_file, "test", "run1")
        s2 = check_budget(20, config, events_file, "test", "run1")
        assert s2.spent_usd > s1.spent_usd
        assert s2.percent_used > s1.percent_used

    def test_cost_events_emitted_on_alert(self, project_dir: Path):
        """Budget alert writes BUDGET_ALERT event to events file."""
        config = PipelineConfig(
            cost_budget_usd=10.0,
            cost_alert_thresholds=[50, 80, 100],
        )
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        alerted: set[int] = set()

        # 30 turns ≈ $6.30, budget=$10 → 63% → triggers 50% alert
        with patch("darkfactory.intelligence.cost.notify_budget_alert"):
            check_budget(30, config, events_file, "test-spec", "run-1", alerted=alerted)

        events = _read_events(events_file)
        event_types = [e["event"] for e in events]
        assert EventType.BUDGET_ALERT.value in event_types
        alert_event = next(e for e in events if e["event"] == EventType.BUDGET_ALERT.value)
        assert alert_event["spec"] == "test-spec"
        assert alert_event["run_id"] == "run-1"


@pytest.mark.integration
class TestCalibration:
    """Calibration with mocked Claude invocations."""

    def test_calibration_computes_factor(self, tmp_path: Path):
        config = PipelineConfig(
            cost_est_input_tokens_per_turn=4000,
            cost_est_output_tokens_per_turn=2000,
        )
        # Each estimated invocation = 4000 + 2000 = 6000 tokens.
        # Mock actual: 9000 tokens each (6000 in, 3000 out) → factor = 27000/18000 = 1.5
        mock_output = json.dumps({
            "usage": {"input_tokens": 6000, "output_tokens": 3000},
            "result": "ok",
        })

        with patch("darkfactory.intelligence.calibrate.run_subprocess",
                    return_value=RunResult(exit_code=0, stdout=mock_output)):
            factor = run_calibration(config, tmp_path)

        assert factor == 1.5

        # Verify factor written to config.toml
        config_path = tmp_path / ".dark-factory" / "config.toml"
        assert config_path.exists()
        content = config_path.read_text()
        assert "cost_correction_factor = 1.5" in content

    def test_calibration_overwrites_existing_factor(self, tmp_path: Path):
        """Calibration replaces a pre-existing cost_correction_factor in config.toml."""
        config = PipelineConfig(
            cost_est_input_tokens_per_turn=4000,
            cost_est_output_tokens_per_turn=2000,
        )
        # Pre-populate config with old factor
        config_path = tmp_path / ".dark-factory" / "config.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "[core]\nlanguage = \"python\"\n\n[cost]\ncost_correction_factor = 1.0\n"
        )

        mock_output = json.dumps({
            "usage": {"input_tokens": 8000, "output_tokens": 4000},
            "result": "ok",
        })
        # 12000 actual / 6000 estimated = 2.0 factor per invocation
        # 3 prompts × 12000 = 36000 actual, 3 × 6000 = 18000 estimated → factor = 2.0

        with patch("darkfactory.intelligence.calibrate.run_subprocess",
                    return_value=RunResult(exit_code=0, stdout=mock_output)):
            factor = run_calibration(config, tmp_path)

        assert factor == 2.0
        content = config_path.read_text()
        assert "cost_correction_factor = 2.0" in content
        assert "cost_correction_factor = 1.0" not in content

    def test_calibration_all_failures_returns_default(self, tmp_path: Path):
        config = PipelineConfig()

        with patch("darkfactory.intelligence.calibrate.run_subprocess",
                    return_value=RunResult(exit_code=1, stdout="")):
            factor = run_calibration(config, tmp_path)

        assert factor == 1.0


@pytest.mark.integration
class TestIterCostEvent:
    """ITER_COST event emitted from iteration loop with correct data."""

    def test_iter_cost_event_fields(self, project_dir: Path):
        """Verify ITER_COST event contains iteration, turns, and estimated_usd."""
        from darkfactory.events.types import emit_iter_cost

        events_file = project_dir / ".dark-factory" / "events.jsonl"
        emit_iter_cost(
            events_file, spec="test-spec", run_id="run-1",
            iteration=2, turns=15, estimated_usd=3.15,
        )

        events = _read_events(events_file)
        assert len(events) == 1
        event = events[0]
        assert event["event"] == EventType.ITER_COST.value
        assert event["spec"] == "test-spec"
        assert event["iter"] == 2
        assert event["data"]["turns"] == 15
        assert abs(event["data"]["estimated_usd"] - 3.15) < 0.01
