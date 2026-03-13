"""Integration tests for progressive canary deployment and rollback.

Mock boundary: run_command, time.sleep, and collect_and_evaluate are mocked.
FileLock, event emission, DeploymentResult construction are real.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from darkfactory.deployment.models import (
    CanaryStage,
    DeploymentStatus,
    MetricThreshold,
    RollbackLayer,
)
from darkfactory.deployment.progressive import DEFAULT_STAGES, run_canary
from darkfactory.deployment.rollback import rollback
from darkfactory.utils.subprocess_runner import RunResult


# --- Helpers ---

def _config(project_dir: Path):
    from darkfactory.config.loader import load_config
    config = load_config(project_dir / ".dark-factory" / "config.toml")
    config.canary_shift_cmd = "/usr/bin/true"
    config.canary_lock_timeout = 5
    config.rollback_timeout = 30
    config.canary_rollback_cmd = ""
    config.flag_disable_cmd = ""
    config.deploy_rollback_cmd = ""
    config.git_base_branch = "main"
    config.git_remote = "origin"
    return config


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


def _completed_proc(returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode)


# --- Canary Tests ---

@pytest.mark.integration
class TestCanaryFullPromotion:
    """Canary rollout succeeds through all stages."""

    def test_full_promotion_default_stages(self, project_dir, sample_spec):
        config = _config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        with patch("darkfactory.deployment.progressive.run_command",
                    return_value=_completed_proc(0)), \
             patch("darkfactory.deployment.progressive.time.sleep"):
            result = run_canary(
                config, sample_spec, "test-run",
                stages=None, thresholds=[],
                events_file=events_file,
                project_dir=str(project_dir),
            )

        assert result.status == DeploymentStatus.PROMOTED
        assert result.stages_completed == len(DEFAULT_STAGES)
        assert result.stages_total == len(DEFAULT_STAGES)

        events = _events(events_file)
        event_types = [e["event"] for e in events]
        assert "CANARY_PROMOTED" in event_types
        assert event_types.count("CANARY_STAGE_START") == len(DEFAULT_STAGES)
        assert event_types.count("CANARY_STAGE_PASSED") == len(DEFAULT_STAGES)

    def test_custom_stages(self, project_dir, sample_spec):
        config = _config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        stages = [
            CanaryStage(percent=1, soak_seconds=0),
            CanaryStage(percent=50, soak_seconds=0),
            CanaryStage(percent=100, soak_seconds=0),
        ]

        with patch("darkfactory.deployment.progressive.run_command",
                    return_value=_completed_proc(0)):
            result = run_canary(
                config, sample_spec, "test-run",
                stages=stages, thresholds=[],
                events_file=events_file,
                project_dir=str(project_dir),
            )

        assert result.status == DeploymentStatus.PROMOTED
        assert result.stages_completed == 3
        assert result.stages_total == 3


@pytest.mark.integration
class TestCanaryFailures:
    """Canary rollback and failure paths."""

    def test_metrics_breach_rolls_back(self, project_dir, sample_spec):
        config = _config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        stages = [
            CanaryStage(percent=1, soak_seconds=0),
            CanaryStage(percent=50, soak_seconds=0),
        ]
        thresholds = [MetricThreshold(name="error_rate", max_value=0.05)]

        with patch("darkfactory.deployment.progressive.run_command",
                    return_value=_completed_proc(0)), \
             patch("darkfactory.deployment.progressive.collect_and_evaluate",
                    side_effect=[
                        ({"error_rate": 0.01}, []),           # Stage 1: pass
                        ({"error_rate": 0.10}, ["error_rate"]),  # Stage 2: breach
                    ]):
            result = run_canary(
                config, sample_spec, "test-run",
                stages=stages, thresholds=thresholds,
                events_file=events_file,
                project_dir=str(project_dir),
            )

        assert result.status == DeploymentStatus.ROLLED_BACK
        assert result.stages_completed == 1
        assert "error_rate" in result.rollback_reason

        events = _events(events_file)
        event_types = [e["event"] for e in events]
        assert "CANARY_STAGE_FAILED" in event_types

    def test_traffic_shift_failure(self, project_dir, sample_spec):
        config = _config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        stages = [CanaryStage(percent=1, soak_seconds=0)]

        with patch("darkfactory.deployment.progressive.run_command",
                    return_value=_completed_proc(1)):
            result = run_canary(
                config, sample_spec, "test-run",
                stages=stages, thresholds=[],
                events_file=events_file,
                project_dir=str(project_dir),
            )

        assert result.status == DeploymentStatus.FAILED
        assert "traffic shift" in result.error

    def test_abort_during_soak(self, project_dir, sample_spec):
        config = _config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"
        stages = [CanaryStage(percent=1, soak_seconds=10)]

        abort_mgr = SimpleNamespace(abort_requested=True)

        with patch("darkfactory.deployment.progressive.run_command",
                    return_value=_completed_proc(0)), \
             patch("darkfactory.deployment.progressive.time.sleep"):
            result = run_canary(
                config, sample_spec, "test-run",
                stages=stages, thresholds=[],
                events_file=events_file,
                project_dir=str(project_dir),
                cleanup_manager=abort_mgr,
            )

        assert result.status == DeploymentStatus.ROLLED_BACK
        assert "aborted during soak" in result.rollback_reason


@pytest.mark.integration
class TestCanaryValidation:
    """Canary stage validation."""

    def test_invalid_stages_raises(self):
        """Non-increasing percents raise ValueError."""
        with pytest.raises(ValueError, match="strictly increasing"):
            run_canary(
                MagicMock(), "test", "run-1",
                stages=[
                    CanaryStage(percent=50, soak_seconds=0),
                    CanaryStage(percent=10, soak_seconds=0),
                ],
                thresholds=[],
                events_file=Path("/dev/null"),
                project_dir="/tmp",
            )


@pytest.mark.integration
class TestCanaryLockTimeout:
    """Canary lock contention."""

    def test_lock_timeout(self, project_dir, sample_spec):
        config = _config(project_dir)
        config.canary_lock_timeout = 0
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        # Pre-acquire the lock using the mkdir-based mechanism.
        lock_path = project_dir / ".dark-factory" / "state" / "canary.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        from darkfactory.state.lock import FileLock
        blocker = FileLock(lock_path, timeout=1)
        blocker.acquire()

        try:
            result = run_canary(
                config, sample_spec, "test-run",
                stages=None, thresholds=[],
                events_file=events_file,
                project_dir=str(project_dir),
            )

            assert result.status == DeploymentStatus.FAILED
            assert "canary lock timeout" in result.error

            events = _events(events_file)
            event_types = [e["event"] for e in events]
            assert "CANARY_LOCK_TIMEOUT" in event_types
        finally:
            blocker.release()


# --- Rollback Tests ---

@pytest.mark.integration
class TestRollbackCascade:
    """Multi-layer rollback cascade."""

    def test_l1_flag_kill_succeeds(self, project_dir, sample_spec):
        config = _config(project_dir)
        config.flag_disable_cmd = "/usr/bin/true"
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        with patch("darkfactory.deployment.rollback.run_command",
                    return_value=_completed_proc(0)):
            result = rollback(
                config, sample_spec, "test-run",
                reason="metric breach",
                start_layer=RollbackLayer.FLAG_KILL,
                project_dir=str(project_dir),
                events_file=events_file,
            )

        assert result == RollbackLayer.FLAG_KILL

        events = _events(events_file)
        event_types = [e["event"] for e in events]
        assert "ROLLBACK_STARTED" in event_types
        assert "ROLLBACK_SUCCESS" in event_types

    def test_l1_fails_l2_succeeds(self, project_dir, sample_spec):
        config = _config(project_dir)
        config.flag_disable_cmd = "/usr/bin/true"
        config.canary_rollback_cmd = "/usr/bin/true"
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        # L1 fails (returncode=1), L2 succeeds (returncode=0)
        with patch("darkfactory.deployment.rollback.run_command",
                    side_effect=[
                        _completed_proc(1),
                        _completed_proc(0),
                    ]):
            result = rollback(
                config, sample_spec, "test-run",
                reason="metric breach",
                start_layer=RollbackLayer.FLAG_KILL,
                project_dir=str(project_dir),
                events_file=events_file,
            )

        assert result == RollbackLayer.TRAFFIC_REVERT

        events = _events(events_file)
        event_types = [e["event"] for e in events]
        assert "ROLLBACK_LAYER_FAILED" in event_types
        assert "ROLLBACK_SUCCESS" in event_types

    def test_all_layers_fail(self, project_dir, sample_spec):
        config = _config(project_dir)
        config.flag_disable_cmd = "/usr/bin/true"
        config.canary_rollback_cmd = "/usr/bin/true"
        config.deploy_rollback_cmd = "/usr/bin/true"
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        with patch("darkfactory.deployment.rollback.run_command",
                    return_value=_completed_proc(1)):
            result = rollback(
                config, sample_spec, "test-run",
                reason="all fail",
                start_layer=RollbackLayer.FLAG_KILL,
                project_dir=str(project_dir),
                events_file=events_file,
                post_merge=False,
            )

        assert result is None

        events = _events(events_file)
        event_types = [e["event"] for e in events]
        assert "ROLLBACK_ALL_FAILED" in event_types

        # Verify all 3 layers were attempted in cascade order
        # (GIT_REVERT skipped because post_merge=False)
        layer_failed = [e for e in events if e["event"] == "ROLLBACK_LAYER_FAILED"]
        assert len(layer_failed) == 3
        assert [e["data"]["layer"] for e in layer_failed] == [
            "flag_kill", "traffic_revert", "container_rollback",
        ]

    def test_git_revert_skipped_without_post_merge(self, project_dir, sample_spec):
        """GIT_REVERT layer skipped when post_merge=False."""
        config = _config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        with patch("darkfactory.deployment.rollback.run") as mock_run:
            result = rollback(
                config, sample_spec, "test-run",
                reason="test",
                start_layer=RollbackLayer.GIT_REVERT,
                project_dir=str(project_dir),
                events_file=events_file,
                post_merge=False,
            )

        assert result is None
        mock_run.assert_not_called()

    def test_git_revert_attempted_with_post_merge(self, project_dir, sample_spec):
        """GIT_REVERT attempted when post_merge=True."""
        config = _config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        git_results = [
            RunResult(exit_code=0, stdout="main"),      # rev-parse HEAD
            RunResult(exit_code=0, stdout="abc123"),     # git log --grep
            RunResult(exit_code=0, stdout=""),           # checkout -b revert branch
            RunResult(exit_code=0, stdout=""),           # git revert
            RunResult(exit_code=0, stdout=""),           # git push
            RunResult(exit_code=0, stdout="PR created"), # gh pr create
            RunResult(exit_code=0, stdout=""),           # git checkout original
        ]

        with patch("darkfactory.deployment.rollback.run",
                    side_effect=git_results) as mock_run:
            result = rollback(
                config, sample_spec, "test-run",
                reason="revert needed",
                start_layer=RollbackLayer.GIT_REVERT,
                project_dir=str(project_dir),
                events_file=events_file,
                post_merge=True,
            )

        assert result == RollbackLayer.GIT_REVERT
        assert mock_run.call_count == 7

        # Verify the git command sequence
        cmds = [call[0][0] for call in mock_run.call_args_list]
        assert cmds[0] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]
        assert cmds[1][:3] == ["git", "log", "--all"]
        assert cmds[2][:3] == ["git", "checkout", "-b"]
        assert cmds[3][:2] == ["git", "revert"]
        assert cmds[4][:2] == ["git", "push"]
        assert cmds[5][0] == "gh"
        assert cmds[6][:2] == ["git", "checkout"]

        events = _events(events_file)
        event_types = [e["event"] for e in events]
        assert "ROLLBACK_SUCCESS" in event_types


# --- Health Check Tests ---

@pytest.mark.integration
class TestHealthChecks:
    """Built-in health check strategies."""

    def test_health_check_http_success(self):
        """HTTP health check returns True on successful curl."""
        from darkfactory.deployment.health import check_http
        from darkfactory.utils.subprocess_runner import RunResult

        with patch("darkfactory.deployment.health.run_subprocess",
                    return_value=RunResult(exit_code=0, stdout="")), \
             patch("darkfactory.deployment.health.time.sleep"):
            result = check_http("http://localhost:8080/health", timeout=5, max_retries=1)

        assert result is True

    def test_health_check_http_failure_timeout(self):
        """HTTP health check returns False when deadline expires."""
        from darkfactory.deployment.health import check_http
        from darkfactory.utils.subprocess_runner import RunResult

        # max_retries=10 so the loop doesn't exit on attempt count —
        # instead it exits because monotonic() passes the deadline.
        with patch("darkfactory.deployment.health.run_subprocess",
                    return_value=RunResult(exit_code=22, stdout="")), \
             patch("darkfactory.deployment.health.time.sleep"), \
             patch("darkfactory.deployment.health.time.monotonic",
                    side_effect=[0.0, 0.0, 0.0, 6.0]):
            result = check_http("http://localhost:8080/health", timeout=5, max_retries=10)

        assert result is False

    def test_health_check_command_success(self):
        """Command health check returns True on exit 0."""
        from darkfactory.deployment.health import check_command
        from darkfactory.utils.subprocess_runner import RunResult

        with patch("darkfactory.deployment.health.run_subprocess",
                    return_value=RunResult(exit_code=0, stdout="ok")):
            result = check_command("/usr/bin/true", "/tmp", timeout=5)

        assert result is True

    def test_health_check_command_failure(self):
        """Command health check returns False on non-zero exit."""
        from darkfactory.deployment.health import check_command
        from darkfactory.utils.subprocess_runner import RunResult

        with patch("darkfactory.deployment.health.run_subprocess",
                    return_value=RunResult(exit_code=1, stdout="")):
            result = check_command("/usr/bin/false", "/tmp", timeout=5)

        assert result is False


# --- Feature Flag Tests ---

@pytest.mark.integration
class TestFeatureFlags:
    """Feature flag lifecycle via user commands."""

    def test_create_flag_calls_command(self, project_dir, sample_spec):
        """create_flag runs flag_create_cmd with correct env vars."""
        from darkfactory.deployment.feature_flags import create_flag
        config = _config(project_dir)
        config.flag_create_cmd = "/usr/bin/true"

        with patch("darkfactory.deployment.feature_flags.run_command",
                    return_value=_completed_proc(0)) as mock_cmd:
            result = create_flag(config, sample_spec, str(project_dir))

        assert result is True
        mock_cmd.assert_called_once()
        env = mock_cmd.call_args.kwargs["env"]
        assert env["FLAG_NAME"] == f"df-{sample_spec}"
        assert env["FLAG_DEFAULT"] == "off"

    def test_create_flag_skipped_when_unconfigured(self, project_dir, sample_spec):
        """create_flag returns True (skip) when no flag_create_cmd."""
        from darkfactory.deployment.feature_flags import create_flag
        config = _config(project_dir)
        config.flag_create_cmd = ""

        result = create_flag(config, sample_spec, str(project_dir))
        assert result is True

    def test_disable_flag_calls_command(self, project_dir, sample_spec):
        """disable_flag runs flag_disable_cmd (kill switch)."""
        from darkfactory.deployment.feature_flags import disable_flag
        config = _config(project_dir)
        config.flag_disable_cmd = "/usr/bin/true"

        with patch("darkfactory.deployment.feature_flags.run_command",
                    return_value=_completed_proc(0)) as mock_cmd:
            result = disable_flag(config, sample_spec, str(project_dir))

        assert result is True
        mock_cmd.assert_called_once()
        env = mock_cmd.call_args[1]["env"]
        assert env["FLAG_NAME"] == f"df-{sample_spec}"
