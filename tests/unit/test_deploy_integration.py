"""E2E deployment integration test — validates full post-ship flow with mocks (Phase 7C)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from darkfactory.deployment.models import DeploymentResult, DeploymentStatus
from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import PipelineStatus, SpecRun


class TestPostShipPipelineIntegration(unittest.TestCase):
    """Test the full post_ship_pipeline flow with mock scripts."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.project = Path(self.tmpdir)
        self.state_dir = self.project / ".dark-factory" / "state"
        self.state_dir.mkdir(parents=True)
        self.state_file = self.state_dir / "test-spec.state"
        self.events_file = self.project / ".dark-factory" / "events.jsonl"

        self.config = PipelineConfig(
            canary_enabled=True,
            canary_rollback_cmd="/bin/true",
            canary_stages=[],
        )
        self.spec_run = SpecRun(spec_name="test-spec")

    @patch("darkfactory.pipeline.orchestrator.run_canary")
    @patch("darkfactory.pipeline.orchestrator.create_flag")
    @patch("darkfactory.pipeline.orchestrator.health_check")
    @patch("darkfactory.pipeline.orchestrator.provision")
    def test_full_flow_success(self, mock_provision: MagicMock,
                                mock_health: MagicMock,
                                mock_flag: MagicMock,
                                mock_canary: MagicMock) -> None:
        from darkfactory.pipeline.orchestrator import post_ship_pipeline

        mock_provision.return_value = MagicMock(
            compose_project="test-proj",
            base_url="http://localhost:8080",
        )
        mock_health.return_value = True
        mock_flag.return_value = True
        mock_canary.return_value = DeploymentResult(
            status=DeploymentStatus.PROMOTED,
            stages_completed=2, stages_total=2,
        )

        self.config.flag_create_cmd = "/bin/true"
        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        self.events_file.touch()

        result = post_ship_pipeline(
            self.spec_run, self.config, self.project,
            state_file=self.state_file,
            events_file=self.events_file,
        )

        self.assertTrue(result)
        mock_provision.assert_called_once()
        mock_health.assert_called_once()
        mock_canary.assert_called_once()

    @patch("darkfactory.pipeline.orchestrator.health_check")
    @patch("darkfactory.pipeline.orchestrator.provision")
    def test_health_check_failure_aborts(self, mock_provision: MagicMock,
                                          mock_health: MagicMock) -> None:
        from darkfactory.pipeline.orchestrator import post_ship_pipeline

        mock_provision.return_value = MagicMock(
            compose_project="test-proj",
            base_url="http://localhost:8080",
        )
        mock_health.return_value = False

        result = post_ship_pipeline(
            self.spec_run, self.config, self.project,
            state_file=self.state_file,
            events_file=self.events_file,
        )

        self.assertFalse(result)
        self.assertIsNotNone(self.spec_run.deployment_result)
        self.assertEqual(self.spec_run.deployment_result.status, DeploymentStatus.FAILED)

    @patch("darkfactory.pipeline.orchestrator.rollback")
    @patch("darkfactory.pipeline.orchestrator.run_canary")
    @patch("darkfactory.pipeline.orchestrator.provision")
    def test_canary_failure_triggers_rollback(self, mock_provision: MagicMock,
                                               mock_canary: MagicMock,
                                               mock_rollback: MagicMock) -> None:
        from darkfactory.pipeline.orchestrator import post_ship_pipeline

        mock_provision.return_value = MagicMock(compose_project="", base_url="")
        mock_canary.return_value = DeploymentResult(
            status=DeploymentStatus.ROLLED_BACK,
            stages_completed=1, stages_total=2,
            rollback_reason="error rate exceeded",
        )
        mock_rollback.return_value = None

        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        self.events_file.touch()

        result = post_ship_pipeline(
            self.spec_run, self.config, self.project,
            state_file=self.state_file,
            events_file=self.events_file,
        )

        self.assertFalse(result)


class TestMonitorIntegration(unittest.TestCase):
    """Test monitor wiring in the orchestrator."""

    def test_monitor_config_fields_exist(self) -> None:
        config = PipelineConfig()
        self.assertEqual(config.rollback_monitor_cmd, "")
        self.assertEqual(config.rollback_window_seconds, 3600)
        self.assertEqual(config.rollback_poll_interval, 60)

    def test_monitor_cmd_in_cmd_fields(self) -> None:
        self.assertIn("rollback_monitor_cmd", PipelineConfig._CMD_FIELDS)


if __name__ == "__main__":
    unittest.main()
