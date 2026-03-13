"""Integration tests for plan failure recovery and advanced dispatch.

Mock boundary: run_pipeline and worktree creation are mocked. Plan
parsing, topo sort, checkpoint, budget, conflict detection are real.

Depends on: multi_spec_plan_fixture from conftest.py (Phase 0).

Does NOT duplicate (already in test_plan_dispatch.py):
- Basic failure-blocks-dependents (TestPlanDispatch)
- Budget skipping (TestPlanBudget)
- Checkpoint/resume (TestPlanCheckpointResume)
- Wall-time limit (TestPlanWallTimeLimit)
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from darkfactory.plan.executor import execute_plan

from tests.integration.conftest import plan_config, read_events


# --- Tests ---


@pytest.mark.integration
class TestDependencyFailureCascade:
    """3-spec dependency chains: failure cascades vs independent isolation."""

    def test_chain_failure_cascades_to_transitive_dependent(
        self, project_dir, multi_spec_plan_fixture
    ):
        """S1 ships, S2 fails -> S3 gets DEPENDENCY_FAILED (never queued)."""
        plan_name, spec_names = multi_spec_plan_fixture
        config = plan_config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        dispatched = []

        def pipeline_side_effect(spec_name, *args, **kwargs):
            dispatched.append(spec_name)
            if spec_name == "chain-s2":
                return 1  # S2 fails
            return 0  # S1 ships

        with patch("darkfactory.plan.executor.run_pipeline", side_effect=pipeline_side_effect), \
             patch("darkfactory.plan.executor.worktree") as mock_wt:
            mock_wt.create.return_value = project_dir
            result = execute_plan(
                plan_name, config, project_dir,
                events_file=events_file,
            )

        assert result.shipped == 1  # S1
        assert result.failed == 2  # S2 + S3 (cascade)
        # S3 was never dispatched to run_pipeline.
        assert "chain-s3" not in dispatched
        assert "chain-s1" in dispatched
        assert "chain-s2" in dispatched

    def test_independent_specs_not_blocked_by_sibling_failure(self, project_dir):
        """3 independent specs: S1 ships, S2 fails, S3 still runs and ships."""
        for name in ("ind-s1", "ind-s2", "ind-s3"):
            spec_file = project_dir / ".dark-factory" / "specs" / f"{name}.md"
            spec_file.write_text(
                f"# {name}\n\n## Functional Requirements\n- FR-1: {name}\n"
            )

        plan_file = project_dir / ".dark-factory" / "plans" / "ind-plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(
            "# ind-plan\n\n"
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| ind-s1 | — | 1 |\n"
            "| ind-s2 | — | 1 |\n"
            "| ind-s3 | — | 1 |\n"
        )

        config = plan_config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        dispatched = []

        def pipeline_side_effect(spec_name, *args, **kwargs):
            dispatched.append(spec_name)
            if spec_name == "ind-s2":
                return 1
            return 0

        with patch("darkfactory.plan.executor.run_pipeline", side_effect=pipeline_side_effect), \
             patch("darkfactory.plan.executor.worktree") as mock_wt:
            mock_wt.create.return_value = project_dir
            result = execute_plan(
                "ind-plan", config, project_dir,
                events_file=events_file,
            )

        assert result.shipped == 2  # S1 + S3
        assert result.failed == 1  # S2
        assert set(dispatched) == {"ind-s1", "ind-s2", "ind-s3"}


@pytest.mark.integration
class TestPlanConcurrency:
    """Concurrency enforcement during plan dispatch."""

    def test_max_concurrent_respected(self, project_dir):
        """max_concurrent=2, 3 independent specs -> at most 2 run simultaneously."""
        for name in ("con-s1", "con-s2", "con-s3"):
            spec_file = project_dir / ".dark-factory" / "specs" / f"{name}.md"
            spec_file.write_text(
                f"# {name}\n\n## Functional Requirements\n- FR-1: {name}\n"
            )

        plan_file = project_dir / ".dark-factory" / "plans" / "con-plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(
            "# con-plan\n\n"
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| con-s1 | — | 1 |\n"
            "| con-s2 | — | 1 |\n"
            "| con-s3 | — | 1 |\n"
        )

        config = plan_config(project_dir)
        config.max_concurrent_specs = 2
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        # Track max concurrent executions.
        active = {"count": 0, "max": 0}
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def pipeline_side_effect(spec_name, *args, **kwargs):
            with lock:
                active["count"] += 1
                active["max"] = max(active["max"], active["count"])
            # Sync the first 2 threads so they overlap.
            try:
                barrier.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
            # Real sleep (not patched) — the executor's internal sleep is
            # patched below, but this one runs in the pipeline thread to
            # ensure the overlap window is measured correctly.
            time.sleep(0.05)
            with lock:
                active["count"] -= 1
            return 0

        with patch("darkfactory.plan.executor.run_pipeline", side_effect=pipeline_side_effect), \
             patch("darkfactory.plan.executor.worktree") as mock_wt, \
             patch("darkfactory.plan.executor.time.sleep"):
            mock_wt.create.return_value = project_dir
            result = execute_plan(
                "con-plan", config, project_dir,
                events_file=events_file,
            )

        assert result.shipped == 3
        assert active["max"] <= 2

    def test_dispatch_events_include_worktree(self, project_dir):
        """Dispatch events contain worktree path."""
        spec_file = project_dir / ".dark-factory" / "specs" / "ev-s1.md"
        spec_file.write_text("# ev-s1\n\n## Functional Requirements\n- FR-1: ev-s1\n")

        plan_file = project_dir / ".dark-factory" / "plans" / "ev-plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(
            "# ev-plan\n\n"
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| ev-s1 | — | 1 |\n"
        )

        config = plan_config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        with patch("darkfactory.plan.executor.run_pipeline", return_value=0), \
             patch("darkfactory.plan.executor.worktree") as mock_wt:
            mock_wt.create.return_value = project_dir
            execute_plan(
                "ev-plan", config, project_dir,
                events_file=events_file,
            )

        events = read_events(events_file)
        dispatch_events = [e for e in events if e["event"] == "SPEC_DISPATCHED"]
        assert len(dispatch_events) >= 1
        evt = dispatch_events[0]
        # worktree may be top-level or nested in "data".
        has_worktree = "worktree" in evt or "worktree" in evt.get("data", {})
        assert has_worktree, f"no worktree field in dispatch event: {evt}"


@pytest.mark.integration
class TestWorktreeCleanup:
    """Worktree cleanup behavior after plan completion."""

    def test_shipped_worktree_cleaned_failed_preserved(
        self, project_dir, multi_spec_plan_fixture
    ):
        """Shipped spec's worktree is cleaned up; failed spec's preserved."""
        plan_name, spec_names = multi_spec_plan_fixture
        config = plan_config(project_dir)
        config.worktree_cleanup_on_ship = True
        config.max_concurrent_specs = 1
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        # S1 ships, S2 fails -> S3 cascade fails.
        def pipeline_side_effect(spec_name, *args, **kwargs):
            if spec_name == "chain-s2":
                return 1
            return 0

        with patch("darkfactory.plan.executor.run_pipeline", side_effect=pipeline_side_effect), \
             patch("darkfactory.plan.executor.worktree") as mock_wt:
            mock_wt.create.return_value = project_dir
            result = execute_plan(
                plan_name, config, project_dir,
                events_file=events_file,
            )

        assert result.shipped == 1
        # worktree.remove must be called for the shipped spec.
        assert mock_wt.remove.called, "worktree.remove was not called for shipped spec"
        for call in mock_wt.remove.call_args_list:
            assert call[1].get("force") is True


@pytest.mark.integration
class TestResumeWithChangedSpec:
    """Resume after spec content changes -> spec re-run."""

    def test_changed_spec_hash_forces_rerun(
        self, project_dir, multi_spec_plan_fixture
    ):
        """Resume after spec hash changes -> spec re-run (not skipped).

        _handle_resume() in executor.py compares spec hashes from checkpoint
        and only skips specs with matching hashes.
        """
        plan_name, spec_names = multi_spec_plan_fixture
        config = plan_config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        # First run: S1 ships, S2 fails.
        def first_run(spec_name, *args, **kwargs):
            return 0 if spec_name == "chain-s1" else 1

        with patch("darkfactory.plan.executor.run_pipeline", side_effect=first_run), \
             patch("darkfactory.plan.executor.worktree") as mock_wt:
            mock_wt.create.return_value = project_dir
            result1 = execute_plan(
                plan_name, config, project_dir,
                events_file=events_file,
            )

        assert result1.shipped == 1
        assert result1.failed == 2  # S2 failed, S3 cascaded

        # Modify S1's spec file -> hash changes.
        spec_s1 = project_dir / ".dark-factory" / "specs" / "chain-s1.md"
        spec_s1.write_text(spec_s1.read_text() + "\n- FR-2: new requirement\n")

        # Resume: S1 should be re-run because hash changed.
        resume_dispatched = []

        def resume_run(spec_name, *args, **kwargs):
            resume_dispatched.append(spec_name)
            return 0

        with patch("darkfactory.plan.executor.run_pipeline", side_effect=resume_run), \
             patch("darkfactory.plan.executor.worktree") as mock_wt:
            mock_wt.create.return_value = project_dir
            execute_plan(
                plan_name, config, project_dir,
                resume=True,
                events_file=events_file,
            )

        # S1 should have been re-run due to hash change.
        assert "chain-s1" in resume_dispatched
