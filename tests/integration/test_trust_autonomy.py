"""Integration tests for trust scoring and autonomy decisions.

Tests exercise trust calculation with real event files and
autonomy level mapping.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from darkfactory.intelligence.trust import (
    calculate_trust,
    predict_iterations,
    trust_to_autonomy,
)
from darkfactory.models.config import PipelineConfig


def _write_events(events_file: Path, events: list[dict]) -> None:
    events_file.parent.mkdir(parents=True, exist_ok=True)
    with events_file.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _shipped_event(spec: str, iterations: int = 1) -> dict:
    return {
        "event": "SPEC_SHIPPED",
        "spec": spec,
        "ts": "2026-03-11T12:00:00Z",
        "data": {"iterations": iterations},
    }


def _failed_event(spec: str) -> dict:
    return {
        "event": "SPEC_FAILED",
        "spec": spec,
        "ts": "2026-03-11T12:00:00Z",
    }


@pytest.mark.integration
class TestTrustToAutonomy:
    """Trust score to autonomy level mapping."""

    def test_low_trust(self):
        assert trust_to_autonomy(0) == 1
        assert trust_to_autonomy(29) == 1

    def test_medium_trust(self):
        assert trust_to_autonomy(30) == 2
        assert trust_to_autonomy(59) == 2

    def test_high_trust(self):
        assert trust_to_autonomy(60) == 3
        assert trust_to_autonomy(100) == 3


@pytest.mark.integration
class TestTrustCalculation:
    """Trust score calculation with real event files."""

    def test_cold_start(self, project_dir: Path):
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.touch()

        config = PipelineConfig(trust_min_history=5)
        score = calculate_trust(events_file, config)
        assert score.total == 50
        assert score.cold_start is True

    def test_insufficient_history(self, project_dir: Path):
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        # Only 3 events but min_history=5
        _write_events(events_file, [
            _shipped_event("a"),
            _shipped_event("b"),
            _shipped_event("c"),
        ])
        config = PipelineConfig(trust_min_history=5)
        score = calculate_trust(events_file, config)
        assert score.total == 50
        assert score.cold_start is True

    def test_all_first_pass_high_trust(self, project_dir: Path):
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        # 5 specs all shipped on iteration 1
        _write_events(events_file, [
            _shipped_event(f"spec-{i}", iterations=1) for i in range(5)
        ])
        config = PipelineConfig(trust_min_history=5)
        score = calculate_trust(events_file, config)
        assert not score.cold_start
        # All first-pass, avg iter=1, no regressions, 5 specs familiarity
        # first_pass_rate=100, avg_iter=100, regression=100, complexity=100, familiarity=50
        # total = 100*30//100 + 100*20//100 + 100*20//100 + 100*15//100 + 50*15//100
        #       = 30 + 20 + 20 + 15 + 7 = 92
        assert score.total == 92

    def test_mixed_results_lower_trust(self, project_dir: Path):
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        _write_events(events_file, [
            _shipped_event("a", iterations=1),
            _shipped_event("b", iterations=3),
            _shipped_event("c", iterations=2),
            _failed_event("d"),
            _shipped_event("e", iterations=1),
        ])
        config = PipelineConfig(trust_min_history=5)
        score = calculate_trust(events_file, config)
        assert not score.cold_start
        # 4 shipped, 1 failed, total_runs=5
        # first_pass: 2/4 → (2*100)//4 = 50
        # avg_iter: [1,3,2,1] avg=1.75 → round=2 → (5-2)*25 = 75
        # regression: no events → 100
        # complexity: 0 files, 0 deps → 100
        # familiarity: min(4,10)*10 = 40
        # total = 50*30//100 + 75*20//100 + 100*20//100 + 100*15//100 + 40*15//100
        #       = 15 + 15 + 20 + 15 + 6 = 71
        assert score.total == 71

    def test_complexity_reduces_trust(self, project_dir: Path):
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        _write_events(events_file, [
            _shipped_event(f"spec-{i}", iterations=1) for i in range(5)
        ])
        config = PipelineConfig(trust_min_history=5)

        # Complex spec (many target files and deps)
        score = calculate_trust(events_file, config,
                                spec_target_files=5, spec_deps=3)
        # complexity = max(0, 100 - (5*10 + 3*20)) = max(0, 100-110) = 0
        assert score.dimensions.spec_complexity == 0
        # Simple spec
        score_simple = calculate_trust(events_file, config,
                                       spec_target_files=0, spec_deps=0)
        assert score_simple.total > score.total


@pytest.mark.integration
class TestPredictIterations:
    """Iteration prediction from historical data."""

    def test_insufficient_history_returns_max(self, project_dir: Path):
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.touch()
        config = PipelineConfig(max_iterations=3, trust_min_history=5)
        assert predict_iterations(events_file, config) == 3

    def test_predicts_from_history(self, project_dir: Path):
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        _write_events(events_file, [
            _shipped_event(f"spec-{i}", iterations=2) for i in range(5)
        ])
        config = PipelineConfig(max_iterations=5, trust_min_history=5)
        predicted = predict_iterations(events_file, config)
        assert predicted == 2

    def test_clamps_to_max_iterations(self, project_dir: Path):
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        _write_events(events_file, [
            _shipped_event(f"spec-{i}", iterations=5) for i in range(5)
        ])
        config = PipelineConfig(max_iterations=3, trust_min_history=5)
        predicted = predict_iterations(events_file, config)
        assert predicted <= 3

    def test_rounds_up(self, project_dir: Path):
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        _write_events(events_file, [
            _shipped_event("a", iterations=1),
            _shipped_event("b", iterations=2),
            _shipped_event("c", iterations=1),
            _shipped_event("d", iterations=2),
            _shipped_event("e", iterations=2),
        ])
        config = PipelineConfig(max_iterations=5, trust_min_history=5)
        predicted = predict_iterations(events_file, config)
        # avg = 8/5 = 1.6, int(1.6 + 0.5) = int(2.1) = 2
        assert predicted == 2
