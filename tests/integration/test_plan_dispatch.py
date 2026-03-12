"""Integration tests for plan execution dispatch.

Mock boundary: run_pipeline and worktree creation are mocked. Plan
parsing, topo sort, conflict detection, checkpoint, budget checks
are real.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from darkfactory.plan.executor import PlanResult, execute_plan


# --- Helpers ---

def _config(project_dir: Path):
    from darkfactory.config.loader import load_config
    config = load_config(project_dir / ".dark-factory" / "config.toml")
    config.max_concurrent_specs = 1
    config.plan_token_budget = 10000
    config.plan_max_wall_time_s = 0
    config.worktree_cleanup_on_ship = False
    config.conflict_strategy = "warn"
    return config


def _write_plan(project_dir: Path, table: str, name: str = "test-plan") -> None:
    plan_file = project_dir / ".dark-factory" / "plans" / f"{name}.md"
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.write_text(f"# {name}\n\n{table}\n")


def _write_spec(project_dir: Path, name: str) -> None:
    spec_file = project_dir / ".dark-factory" / "specs" / f"{name}.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(
        f"# {name}\n\n## Functional Requirements\n- FR-1: {name}\n\n"
        f"## Acceptance Criteria\n- AC-1: Works\n"
    )


def _events(events_file: Path) -> list[dict]:
    if not events_file.exists():
        return []
    result = []
    with events_file.open() as f:
        for line in f:
            s = line.strip()
            if s:
                result.append(json.loads(s))
    return result


# --- Tests ---

@pytest.mark.integration
class TestPlanDispatch:
    """Basic plan dispatch with mocked pipeline."""

    def test_single_spec_ships(self, project_dir):
        _write_spec(project_dir, "add-auth")
        _write_plan(project_dir, (
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| add-auth | — | 1 |\n"
        ))
        config = _config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        with patch("darkfactory.plan.executor.run_pipeline", return_value=0), \
             patch("darkfactory.plan.executor.worktree") as mock_wt:
            mock_wt.create.return_value = project_dir
            result = execute_plan(
                "test-plan", config, project_dir,
                events_file=events_file,
            )

        assert result.shipped == 1
        assert result.failed == 0

        events = _events(events_file)
        event_types = [e["event"] for e in events]
        assert "PLAN_START" in event_types
        assert "PLAN_COMPLETE" in event_types

    def test_dependency_order_respected(self, project_dir):
        """add-api depends on add-auth — add-auth must complete before add-api starts."""
        _write_spec(project_dir, "add-auth")
        _write_spec(project_dir, "add-api")
        _write_plan(project_dir, (
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| add-auth | — | 1 |\n"
            "| add-api | add-auth | 2 |\n"
        ))
        config = _config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        dispatch_order = []

        def track_pipeline(spec_name, *args, **kwargs):
            dispatch_order.append(spec_name)
            return 0

        with patch("darkfactory.plan.executor.run_pipeline", side_effect=track_pipeline), \
             patch("darkfactory.plan.executor.worktree") as mock_wt:
            mock_wt.create.return_value = project_dir
            result = execute_plan(
                "test-plan", config, project_dir,
                events_file=events_file,
            )

        assert result.shipped == 2
        assert dispatch_order.index("add-auth") < dispatch_order.index("add-api")

    def test_failure_blocks_dependents(self, project_dir):
        """add-auth fails → add-api (depends on add-auth) is blocked."""
        _write_spec(project_dir, "add-auth")
        _write_spec(project_dir, "add-api")
        _write_plan(project_dir, (
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| add-auth | — | 1 |\n"
            "| add-api | add-auth | 2 |\n"
        ))
        config = _config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        with patch("darkfactory.plan.executor.run_pipeline", return_value=1), \
             patch("darkfactory.plan.executor.worktree") as mock_wt:
            mock_wt.create.return_value = project_dir
            result = execute_plan(
                "test-plan", config, project_dir,
                events_file=events_file,
            )

        # A failed, B blocked → both failed
        assert result.shipped == 0
        assert result.failed == 2


@pytest.mark.integration
class TestPlanBudget:
    """Budget enforcement during plan dispatch."""

    def test_budget_skips_over_budget_specs(self, project_dir):
        _write_spec(project_dir, "add-auth")
        _write_spec(project_dir, "add-api")
        _write_plan(project_dir, (
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| add-auth | — | 1 |\n"
            "| add-api | — | 2 |\n"
        ))
        config = _config(project_dir)
        # Set budget very low — only enough for 1 spec
        config.plan_token_budget = config.agent_max_turns  # exactly 1 spec
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        with patch("darkfactory.plan.executor.run_pipeline", return_value=0), \
             patch("darkfactory.plan.executor.worktree") as mock_wt:
            mock_wt.create.return_value = project_dir
            result = execute_plan(
                "test-plan", config, project_dir,
                events_file=events_file,
            )

        assert result.shipped == 1
        assert result.skipped == 1


@pytest.mark.integration
class TestPlanDryRun:
    """Dry run mode."""

    def test_dry_run_dispatches_nothing(self, project_dir):
        _write_spec(project_dir, "add-auth")
        _write_plan(project_dir, (
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| add-auth | — | 1 |\n"
        ))
        config = _config(project_dir)

        with patch("darkfactory.plan.executor.run_pipeline") as mock_pipeline:
            result = execute_plan(
                "test-plan", config, project_dir, dry_run=True,
            )

        assert result.shipped == 0
        assert result.failed == 0
        mock_pipeline.assert_not_called()


@pytest.mark.integration
class TestPlanMultipleIndependentSpecs:
    """Multiple independent specs dispatched together."""

    def test_all_independent_specs_ship(self, project_dir):
        """Three independent specs dispatched — all ship."""
        for name in ("add-auth", "add-api", "add-ui"):
            _write_spec(project_dir, name)
        _write_plan(project_dir, (
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| add-auth | — | 1 |\n"
            "| add-api | — | 1 |\n"
            "| add-ui | — | 1 |\n"
        ))
        config = _config(project_dir)
        config.max_concurrent_specs = 3
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        dispatched = []

        def track_pipeline(spec_name, *args, **kwargs):
            dispatched.append(spec_name)
            return 0

        with patch("darkfactory.plan.executor.run_pipeline", side_effect=track_pipeline), \
             patch("darkfactory.plan.executor.worktree") as mock_wt:
            mock_wt.create.return_value = project_dir
            result = execute_plan(
                "test-plan", config, project_dir,
                events_file=events_file,
            )

        assert result.shipped == 3
        assert result.failed == 0
        assert set(dispatched) == {"add-auth", "add-api", "add-ui"}


@pytest.mark.integration
class TestPlanCheckpointResume:
    """Checkpoint and resume after partial completion."""

    def test_checkpoint_written_and_resume_skips_completed(self, project_dir):
        """Run 2 specs, first ships. Resume skips first, re-runs second."""
        _write_spec(project_dir, "add-auth")
        _write_spec(project_dir, "add-api")
        _write_plan(project_dir, (
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| add-auth | — | 1 |\n"
            "| add-api | add-auth | 2 |\n"
        ))
        config = _config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        # First run: add-auth ships, add-api fails
        call_count = [0]

        def first_run_pipeline(spec_name, *args, **kwargs):
            call_count[0] += 1
            return 0 if spec_name == "add-auth" else 1

        with patch("darkfactory.plan.executor.run_pipeline", side_effect=first_run_pipeline), \
             patch("darkfactory.plan.executor.worktree") as mock_wt:
            mock_wt.create.return_value = project_dir
            result1 = execute_plan(
                "test-plan", config, project_dir,
                events_file=events_file,
            )

        assert result1.shipped == 1
        assert result1.failed == 1

        # Verify checkpoint was written
        state_dir = project_dir / ".dark-factory" / "state"
        checkpoints = list(state_dir.glob("plan-test-plan-*.jsonl"))
        assert len(checkpoints) > 0

        # Resume: add-auth should be skipped, add-api re-run
        resume_dispatched = []

        def resume_pipeline(spec_name, *args, **kwargs):
            resume_dispatched.append(spec_name)
            return 0

        with patch("darkfactory.plan.executor.run_pipeline", side_effect=resume_pipeline), \
             patch("darkfactory.plan.executor.worktree") as mock_wt:
            mock_wt.create.return_value = project_dir
            result2 = execute_plan(
                "test-plan", config, project_dir,
                resume=True, events_file=events_file,
            )

        # add-auth was completed with "shipped" status → skipped on resume
        assert "add-auth" not in resume_dispatched
        assert "add-api" in resume_dispatched
        assert result2.shipped == 1
        assert result2.failed == 0


@pytest.mark.integration
class TestPlanWallTimeLimit:
    """Wall time limit enforcement."""

    def test_wall_time_limit_aborts_plan(self, project_dir):
        """Mock time.monotonic so the wall-time limit fires deterministically."""
        _write_spec(project_dir, "add-auth")
        _write_spec(project_dir, "add-api")
        _write_plan(project_dir, (
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| add-auth | — | 1 |\n"
            "| add-api | — | 2 |\n"
        ))
        config = _config(project_dir)
        config.plan_max_wall_time_s = 10  # 10 second limit
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        # Mock monotonic: first calls return 0 (plan start),
        # then after first spec dispatches, return 20 (past deadline).
        call_count = [0]
        def mock_monotonic():
            call_count[0] += 1
            # First few calls: plan startup and first spec dispatch
            if call_count[0] <= 3:
                return 0.0
            # After that: past the deadline
            return 20.0

        with patch("darkfactory.plan.executor.run_pipeline", return_value=0), \
             patch("darkfactory.plan.executor.worktree") as mock_wt, \
             patch("darkfactory.plan.executor.time.monotonic", side_effect=mock_monotonic), \
             patch("darkfactory.plan.executor.time.sleep"):
            mock_wt.create.return_value = project_dir
            result = execute_plan(
                "test-plan", config, project_dir,
                events_file=events_file,
            )

        events = _events(events_file)
        event_types = [e["event"] for e in events]
        assert "PLAN_TIMEOUT" in event_types
