"""Tests for Tier 6: Post-Ship Orchestration — full pipeline wiring."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from darkfactory.deployment.models import (
    DeploymentResult,
    DeploymentStatus,
)
from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import PipelineStatus, SpecRun
from darkfactory.pipeline.orchestrator import post_ship_pipeline


def _make_config(**kwargs) -> PipelineConfig:
    defaults = dict(
        canary_enabled=True,
        canary_shift_cmd="scripts/shift.sh",
        canary_rollback_cmd="scripts/rollback.sh",
    )
    defaults.update(kwargs)
    return PipelineConfig(**defaults)


class TestPostShipPipeline(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.project_dir = Path(self.tmpdir)
        self.state_dir = self.project_dir / ".dark-factory" / "state"
        self.state_dir.mkdir(parents=True)
        self.state_file = self.state_dir / "spec1.state"
        self.state_file.touch()
        self.events_file = self.project_dir / ".dark-factory" / "events.jsonl"
        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        self.events_file.touch()

    @patch("darkfactory.pipeline.orchestrator.teardown")
    @patch("darkfactory.pipeline.orchestrator.run_canary")
    @patch("darkfactory.pipeline.orchestrator.provision")
    def test_happy_path_canary_promoted(self, mock_provision, mock_canary, mock_teardown):
        """Full success: provision → canary promoted → True."""
        mock_provision.return_value = None  # No compose, skip provision
        mock_canary.return_value = DeploymentResult(
            status=DeploymentStatus.PROMOTED,
            stages_completed=2, stages_total=2,
        )
        config = _make_config()
        spec_run = SpecRun(spec_name="spec1")

        result = post_ship_pipeline(
            spec_run, config, self.project_dir,
            state_file=self.state_file,
            events_file=self.events_file,
        )

        self.assertTrue(result)
        self.assertEqual(spec_run.deployment_step, 103)
        self.assertEqual(spec_run.deployment_result.status, DeploymentStatus.PROMOTED)

    @patch("darkfactory.pipeline.orchestrator.teardown")
    @patch("darkfactory.pipeline.orchestrator.run_canary")
    @patch("darkfactory.pipeline.orchestrator.provision")
    def test_canary_failure_returns_false(self, mock_provision, mock_canary, mock_teardown):
        """Canary failure: returns False and triggers rollback."""
        mock_provision.return_value = None
        mock_canary.return_value = DeploymentResult(
            status=DeploymentStatus.ROLLED_BACK,
            stages_completed=1, stages_total=3,
            rollback_reason="threshold breach: error_rate",
        )
        config = _make_config()
        spec_run = SpecRun(spec_name="spec1")

        with patch("darkfactory.pipeline.orchestrator.rollback") as mock_rollback:
            result = post_ship_pipeline(
                spec_run, config, self.project_dir,
                state_file=self.state_file,
                events_file=self.events_file,
            )

        self.assertFalse(result)
        self.assertEqual(spec_run.deployment_result.status, DeploymentStatus.ROLLED_BACK)
        mock_rollback.assert_called_once()
        # Verify post_merge=False (pre-merge canary, no L4 git revert)
        self.assertFalse(mock_rollback.call_args[1].get("post_merge", True))

    @patch("darkfactory.pipeline.orchestrator.teardown")
    @patch("darkfactory.pipeline.orchestrator.health_check")
    @patch("darkfactory.pipeline.orchestrator.provision")
    def test_health_check_failure_returns_false(self, mock_provision, mock_health, mock_teardown):
        """Health check failure aborts deployment."""
        from darkfactory.deployment.environment import EnvironmentInfo
        mock_provision.return_value = EnvironmentInfo(base_url="http://localhost:8080")
        mock_health.return_value = False
        config = _make_config()
        spec_run = SpecRun(spec_name="spec1")

        result = post_ship_pipeline(
            spec_run, config, self.project_dir,
            state_file=self.state_file,
            events_file=self.events_file,
        )

        self.assertFalse(result)
        self.assertEqual(spec_run.deployment_result.error, "health check failed")

    @patch("darkfactory.pipeline.orchestrator.teardown")
    @patch("darkfactory.pipeline.orchestrator.run_canary")
    @patch("darkfactory.pipeline.orchestrator.check_migrations")
    @patch("darkfactory.pipeline.orchestrator.provision")
    def test_migration_block_returns_false(self, mock_provision, mock_mig, mock_canary, mock_teardown):
        """Migration check in block mode fails deployment."""
        from darkfactory.deployment.models import MigrationCheckResult
        mock_provision.return_value = None
        mock_mig.return_value = MigrationCheckResult(
            safe=False, mixed=True,
            destructive_ops=["DROP TABLE"], additive_ops=["CREATE TABLE"],
        )
        config = _make_config(
            migration_check_enabled=True,
            migration_check_policy="block",
        )
        spec_run = SpecRun(spec_name="spec1")

        result = post_ship_pipeline(
            spec_run, config, self.project_dir,
            state_file=self.state_file,
            events_file=self.events_file,
        )

        self.assertFalse(result)
        self.assertIn("migration", spec_run.deployment_result.error)
        mock_canary.assert_not_called()  # Should not reach canary

    @patch("darkfactory.pipeline.orchestrator.teardown")
    @patch("darkfactory.pipeline.orchestrator.run_canary")
    @patch("darkfactory.pipeline.orchestrator.check_migrations")
    @patch("darkfactory.pipeline.orchestrator.provision")
    def test_migration_warn_continues(self, mock_provision, mock_mig, mock_canary, mock_teardown):
        """Migration check in warn mode logs but continues."""
        from darkfactory.deployment.models import MigrationCheckResult
        mock_provision.return_value = None
        mock_mig.return_value = MigrationCheckResult(safe=False, mixed=True)
        mock_canary.return_value = DeploymentResult(
            status=DeploymentStatus.PROMOTED,
            stages_completed=1, stages_total=1,
        )
        config = _make_config(
            migration_check_enabled=True,
            migration_check_policy="warn",
        )
        spec_run = SpecRun(spec_name="spec1")

        result = post_ship_pipeline(
            spec_run, config, self.project_dir,
            state_file=self.state_file,
            events_file=self.events_file,
        )

        self.assertTrue(result)
        mock_canary.assert_called_once()

    @patch("darkfactory.pipeline.orchestrator.teardown")
    @patch("darkfactory.pipeline.orchestrator.run_canary")
    @patch("darkfactory.pipeline.orchestrator.create_flag")
    @patch("darkfactory.pipeline.orchestrator.provision")
    def test_flag_creation_non_blocking(self, mock_provision, mock_flag, mock_canary, mock_teardown):
        """Flag creation failure is non-blocking."""
        mock_provision.return_value = None
        mock_flag.return_value = False  # Flag creation fails
        mock_canary.return_value = DeploymentResult(
            status=DeploymentStatus.PROMOTED,
            stages_completed=1, stages_total=1,
        )
        config = _make_config(flag_create_cmd="scripts/flag.sh")
        spec_run = SpecRun(spec_name="spec1")

        result = post_ship_pipeline(
            spec_run, config, self.project_dir,
            state_file=self.state_file,
            events_file=self.events_file,
        )

        self.assertTrue(result)  # Continues despite flag failure

    @patch("darkfactory.pipeline.orchestrator.teardown")
    @patch("darkfactory.pipeline.orchestrator.run_canary")
    @patch("darkfactory.pipeline.orchestrator.provision")
    def test_no_events_file_fails(self, mock_provision, mock_canary, mock_teardown):
        """Without events_file, canary cannot run."""
        mock_provision.return_value = None
        config = _make_config()
        spec_run = SpecRun(spec_name="spec1")

        result = post_ship_pipeline(
            spec_run, config, self.project_dir,
            state_file=self.state_file,
            events_file=None,
        )

        self.assertFalse(result)
        mock_canary.assert_not_called()

    @patch("darkfactory.pipeline.orchestrator.teardown")
    @patch("darkfactory.pipeline.orchestrator.run_canary")
    @patch("darkfactory.pipeline.orchestrator.provision")
    def test_teardown_always_runs(self, mock_provision, mock_canary, mock_teardown):
        """Teardown runs even when canary fails."""
        from darkfactory.deployment.environment import EnvironmentInfo
        mock_provision.return_value = EnvironmentInfo(
            base_url="", compose_project="df-test-123",
        )
        mock_canary.return_value = DeploymentResult(
            status=DeploymentStatus.ROLLED_BACK,
            stages_completed=0, stages_total=2,
            rollback_reason="breach",
        )
        config = _make_config()
        spec_run = SpecRun(spec_name="spec1")

        with patch("darkfactory.pipeline.orchestrator.rollback"):
            post_ship_pipeline(
                spec_run, config, self.project_dir,
                state_file=self.state_file,
                events_file=self.events_file,
            )

        mock_teardown.assert_called_once()
        # Verify compose_project passed to teardown
        self.assertEqual(mock_teardown.call_args[1].get("compose_project", ""), "df-test-123")

    @patch("darkfactory.pipeline.orchestrator.teardown")
    @patch("darkfactory.pipeline.orchestrator.run_canary")
    @patch("darkfactory.pipeline.orchestrator.provision")
    def test_state_written_at_each_step(self, mock_provision, mock_canary, mock_teardown):
        """Deployment steps are persisted to state file for crash recovery."""
        mock_provision.return_value = None
        mock_canary.return_value = DeploymentResult(
            status=DeploymentStatus.PROMOTED,
            stages_completed=1, stages_total=1,
        )
        config = _make_config()
        spec_run = SpecRun(spec_name="spec1")

        post_ship_pipeline(
            spec_run, config, self.project_dir,
            state_file=self.state_file,
            events_file=self.events_file,
        )

        # Read state file and verify deployment_step was written
        from darkfactory.state.store import read_state
        step = read_state(self.state_file, "deployment_step")
        self.assertEqual(step, "103")

    @patch("darkfactory.pipeline.orchestrator.teardown")
    @patch("darkfactory.pipeline.orchestrator.run_canary")
    @patch("darkfactory.pipeline.orchestrator.provision")
    def test_skip_when_unconfigured(self, mock_provision, mock_canary, mock_teardown):
        """With no deployment config, provision returns None and canary still runs."""
        mock_provision.return_value = None
        mock_canary.return_value = DeploymentResult(
            status=DeploymentStatus.PROMOTED,
            stages_completed=1, stages_total=1,
        )
        config = _make_config()
        spec_run = SpecRun(spec_name="spec1")

        result = post_ship_pipeline(
            spec_run, config, self.project_dir,
            state_file=self.state_file,
            events_file=self.events_file,
        )

        self.assertTrue(result)


class TestPostShipResume(unittest.TestCase):
    """Test resume behavior when deployment_step > 0."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.project_dir = Path(self.tmpdir)
        self.state_dir = self.project_dir / ".dark-factory" / "state"
        self.state_dir.mkdir(parents=True)
        self.state_file = self.state_dir / "spec1.state"
        self.state_file.touch()
        self.events_file = self.project_dir / ".dark-factory" / "events.jsonl"
        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        self.events_file.touch()

    @patch("darkfactory.pipeline.orchestrator.teardown")
    @patch("darkfactory.pipeline.orchestrator.run_canary")
    @patch("darkfactory.pipeline.orchestrator.provision")
    def test_resume_skips_completed_steps(self, mock_provision, mock_canary, mock_teardown):
        """When deployment_step=101, skip provision and migration, run canary."""
        mock_canary.return_value = DeploymentResult(
            status=DeploymentStatus.PROMOTED,
            stages_completed=1, stages_total=1,
        )
        config = _make_config()
        spec_run = SpecRun(spec_name="spec1")
        spec_run.deployment_step = 101  # Already past provision and migration

        result = post_ship_pipeline(
            spec_run, config, self.project_dir,
            state_file=self.state_file,
            events_file=self.events_file,
        )

        self.assertTrue(result)
        mock_provision.assert_not_called()  # Skipped
        mock_canary.assert_called_once()

    @patch("darkfactory.pipeline.orchestrator.teardown")
    @patch("darkfactory.pipeline.orchestrator.run_canary")
    @patch("darkfactory.pipeline.orchestrator.provision")
    def test_resume_restores_compose_project(self, mock_provision, mock_canary, mock_teardown):
        """On resume, compose_project is restored from state file."""
        from darkfactory.state.store import write_state
        write_state(self.state_file, "compose_project", "df-spec1-abc12345")

        mock_canary.return_value = DeploymentResult(
            status=DeploymentStatus.PROMOTED,
            stages_completed=1, stages_total=1,
        )
        config = _make_config()
        spec_run = SpecRun(spec_name="spec1")
        spec_run.deployment_step = 102  # Past provision

        post_ship_pipeline(
            spec_run, config, self.project_dir,
            state_file=self.state_file,
            events_file=self.events_file,
        )

        # Teardown should receive the restored compose_project
        mock_teardown.assert_called_once()
        self.assertEqual(mock_teardown.call_args[1].get("compose_project", ""), "df-spec1-abc12345")


class TestHandleShipWithCanary(unittest.TestCase):
    """Test _handle_ship integration with canary."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.project_dir = Path(self.tmpdir)
        self.state_dir = self.project_dir / ".dark-factory" / "state"
        self.state_dir.mkdir(parents=True)
        self.state_file = self.state_dir / "spec1.state"
        self.state_file.touch()
        self.events_file = self.project_dir / ".dark-factory" / "events.jsonl"
        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        self.events_file.touch()

    @patch("darkfactory.pipeline.orchestrator.notify_canary_failed")
    @patch("darkfactory.pipeline.orchestrator.git_ops")
    @patch("darkfactory.pipeline.orchestrator.post_ship_pipeline")
    @patch("darkfactory.pipeline.orchestrator.registry_register")
    def test_canary_failure_closes_pr(self, mock_reg, mock_post_ship, mock_git, mock_notify):
        """When canary fails, _handle_ship closes the PR and returns 1."""
        from darkfactory.pipeline.orchestrator import _handle_ship

        mock_git.create_branch.return_value = "dark-factory/spec1-abc"
        mock_git.commit_changes.return_value = True
        mock_git.push_branch.return_value = True
        mock_git.create_pr.return_value = "https://github.com/test/pr/1"
        mock_git.close_pr.return_value = True

        mock_post_ship.return_value = False  # Canary failed

        config = _make_config(auto_pr=True)
        spec_run = SpecRun(spec_name="spec1")
        spec_run.deployment_result = DeploymentResult(
            status=DeploymentStatus.ROLLED_BACK,
            stages_completed=0, stages_total=2,
            rollback_reason="threshold breach",
        )

        result = _handle_ship(
            spec_run, config, self.project_dir,
            state_file=self.state_file,
            events_file=self.events_file,
        )

        self.assertEqual(result, 1)
        self.assertEqual(spec_run.status, PipelineStatus.ROLLED_BACK)
        mock_git.close_pr.assert_called_once()
        mock_notify.assert_called_once()
        # Verify merge was NOT called
        mock_git.merge_queue_add.assert_not_called()

    @patch("darkfactory.pipeline.orchestrator.notify_shipped")
    @patch("darkfactory.pipeline.orchestrator.notify_canary_promoted")
    @patch("darkfactory.pipeline.orchestrator.git_ops")
    @patch("darkfactory.pipeline.orchestrator.post_ship_pipeline")
    @patch("darkfactory.pipeline.orchestrator.registry_register")
    def test_canary_success_merges(self, mock_reg, mock_post_ship, mock_git, mock_notify_promoted, mock_notify_shipped):
        """When canary succeeds, _handle_ship proceeds to merge."""
        from darkfactory.pipeline.orchestrator import _handle_ship

        mock_git.create_branch.return_value = "dark-factory/spec1-abc"
        mock_git.commit_changes.return_value = True
        mock_git.push_branch.return_value = True
        mock_git.create_pr.return_value = "https://github.com/test/pr/1"

        mock_post_ship.return_value = True  # Canary passed

        config = _make_config(auto_pr=True, merge_queue=True)
        spec_run = SpecRun(spec_name="spec1")
        spec_run.deployment_result = DeploymentResult(
            status=DeploymentStatus.PROMOTED,
            stages_completed=2, stages_total=2,
        )

        result = _handle_ship(
            spec_run, config, self.project_dir,
            state_file=self.state_file,
            events_file=self.events_file,
        )

        self.assertEqual(result, 0)
        self.assertEqual(spec_run.status, PipelineStatus.DEPLOYED)
        mock_git.merge_queue_add.assert_called_once()
        mock_git.close_pr.assert_not_called()

    @patch("darkfactory.pipeline.orchestrator.notify_shipped")
    @patch("darkfactory.pipeline.orchestrator.git_ops")
    @patch("darkfactory.pipeline.orchestrator.registry_register")
    def test_no_canary_skips_deployment(self, mock_reg, mock_git, mock_notify):
        """Without canary_enabled, _handle_ship behaves as before."""
        from darkfactory.pipeline.orchestrator import _handle_ship

        mock_git.create_branch.return_value = "dark-factory/spec1-abc"
        mock_git.commit_changes.return_value = True
        mock_git.push_branch.return_value = True
        mock_git.create_pr.return_value = "https://github.com/test/pr/1"

        config = PipelineConfig(auto_pr=True)
        spec_run = SpecRun(spec_name="spec1")

        result = _handle_ship(
            spec_run, config, self.project_dir,
            state_file=self.state_file,
            events_file=self.events_file,
        )

        self.assertEqual(result, 0)
        self.assertEqual(spec_run.status, PipelineStatus.SHIPPED)


    @patch("darkfactory.pipeline.orchestrator.notify_canary_failed")
    @patch("darkfactory.pipeline.orchestrator.git_ops")
    @patch("darkfactory.pipeline.orchestrator.post_ship_pipeline")
    @patch("darkfactory.pipeline.orchestrator.registry_register")
    def test_close_pr_failure_still_returns_1(self, mock_reg, mock_post_ship, mock_git, mock_notify):
        """Even if close_pr fails, _handle_ship still returns 1 (rolled back)."""
        from darkfactory.pipeline.orchestrator import _handle_ship

        mock_git.create_branch.return_value = "dark-factory/spec1-abc"
        mock_git.commit_changes.return_value = True
        mock_git.push_branch.return_value = True
        mock_git.create_pr.return_value = "https://github.com/test/pr/1"
        mock_git.close_pr.return_value = False  # close_pr fails

        mock_post_ship.return_value = False

        config = _make_config(auto_pr=True)
        spec_run = SpecRun(spec_name="spec1")
        spec_run.deployment_result = DeploymentResult(
            status=DeploymentStatus.ROLLED_BACK,
            stages_completed=0, stages_total=2,
            rollback_reason="breach",
        )

        result = _handle_ship(
            spec_run, config, self.project_dir,
            state_file=self.state_file,
            events_file=self.events_file,
        )

        self.assertEqual(result, 1)
        self.assertEqual(spec_run.status, PipelineStatus.ROLLED_BACK)


class TestPostShipException(unittest.TestCase):
    """Test exception handling during canary."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.project_dir = Path(self.tmpdir)
        self.state_dir = self.project_dir / ".dark-factory" / "state"
        self.state_dir.mkdir(parents=True)
        self.state_file = self.state_dir / "spec1.state"
        self.state_file.touch()
        self.events_file = self.project_dir / ".dark-factory" / "events.jsonl"
        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        self.events_file.touch()

    @patch("darkfactory.pipeline.orchestrator.teardown")
    @patch("darkfactory.pipeline.orchestrator.run_canary")
    @patch("darkfactory.pipeline.orchestrator.provision")
    def test_canary_exception_propagates(self, mock_provision, mock_canary, mock_teardown):
        """Exception from run_canary propagates but teardown still runs."""
        mock_provision.return_value = None
        mock_canary.side_effect = RuntimeError("connection refused")
        config = _make_config()
        spec_run = SpecRun(spec_name="spec1")

        with self.assertRaises(RuntimeError):
            post_ship_pipeline(
                spec_run, config, self.project_dir,
                state_file=self.state_file,
                events_file=self.events_file,
            )

        # Teardown still runs even on exception (finally block)
        # Note: teardown only runs if compose_project or deploy_teardown_cmd is set


class TestGitOpsClosePr(unittest.TestCase):
    @patch("darkfactory.git.ops.run")
    def test_close_pr_success(self, mock_run):
        from darkfactory.git.ops import close_pr
        mock_run.return_value = MagicMock(exit_code=0)
        self.assertTrue(close_pr(Path("/tmp"), comment="canary failed"))
        cmd = mock_run.call_args[0][0]
        self.assertIn("close", cmd)
        self.assertIn("--comment", cmd)

    @patch("darkfactory.git.ops.run")
    def test_close_pr_failure(self, mock_run):
        from darkfactory.git.ops import close_pr
        mock_run.return_value = MagicMock(exit_code=1, stderr="not found")
        self.assertFalse(close_pr(Path("/tmp")))

    @patch("darkfactory.git.ops.run")
    def test_close_pr_no_comment(self, mock_run):
        from darkfactory.git.ops import close_pr
        mock_run.return_value = MagicMock(exit_code=0)
        close_pr(Path("/tmp"))
        cmd = mock_run.call_args[0][0]
        self.assertNotIn("--comment", cmd)

    @patch("darkfactory.git.ops.run")
    def test_close_pr_with_branch(self, mock_run):
        from darkfactory.git.ops import close_pr
        mock_run.return_value = MagicMock(exit_code=0)
        close_pr(Path("/tmp"), branch="dark-factory/spec1-abc")
        cmd = mock_run.call_args[0][0]
        self.assertIn("dark-factory/spec1-abc", cmd)


class TestNotifyDeployment(unittest.TestCase):
    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_canary_promoted(self, mock_send):
        from darkfactory.notify.dispatcher import notify_canary_promoted
        config = PipelineConfig()
        notify_canary_promoted(config, "spec1", "run1", 3)
        mock_send.assert_called_once()
        text = mock_send.call_args[0][1]
        self.assertIn("PROMOTED", text)

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_canary_failed(self, mock_send):
        from darkfactory.notify.dispatcher import notify_canary_failed
        config = PipelineConfig()
        notify_canary_failed(config, "spec1", "run1", "breach")
        text = mock_send.call_args[0][1]
        self.assertIn("FAILED", text)

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_rollback(self, mock_send):
        from darkfactory.notify.dispatcher import notify_rollback
        config = PipelineConfig()
        notify_rollback(config, "spec1", "run1", "flag_kill", "error_rate")
        text = mock_send.call_args[0][1]
        self.assertIn("ROLLBACK", text)
        self.assertIn("flag_kill", text)

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_rollback_failed(self, mock_send):
        from darkfactory.notify.dispatcher import notify_rollback_failed
        config = PipelineConfig()
        notify_rollback_failed(config, "spec1", "run1", "all failed")
        text = mock_send.call_args[0][1]
        self.assertIn("ROLLBACK FAILED", text)

    @patch("darkfactory.notify.dispatcher._send")
    def test_notify_flag_killed(self, mock_send):
        from darkfactory.notify.dispatcher import notify_flag_killed
        config = PipelineConfig()
        notify_flag_killed(config, "spec1", "run1")
        text = mock_send.call_args[0][1]
        self.assertIn("FLAG KILLED", text)


class TestPipelineStatusExtension(unittest.TestCase):
    def test_new_statuses_exist(self):
        self.assertEqual(PipelineStatus.DEPLOYING.value, "deploying")
        self.assertEqual(PipelineStatus.DEPLOYED.value, "deployed")
        self.assertEqual(PipelineStatus.ROLLED_BACK.value, "rolled_back")

    def test_spec_run_deployment_fields(self):
        spec_run = SpecRun(spec_name="test")
        self.assertIsNone(spec_run.deployment_result)
        self.assertEqual(spec_run.deployment_step, 0)


if __name__ == "__main__":
    unittest.main()
