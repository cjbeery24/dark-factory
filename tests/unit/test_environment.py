"""Tests for darkfactory.deployment.environment — ephemeral environment lifecycle."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from darkfactory.deployment.environment import (
    _sanitize_compose_name,
    health_check,
    provision,
    teardown,
)
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import RunResult


class TestSanitizeComposeName(unittest.TestCase):
    def test_lowercases_and_strips_special(self):
        self.assertEqual(_sanitize_compose_name("My Spec!"), "my-spec-")

    def test_truncates_long_names(self):
        result = _sanitize_compose_name("a" * 100)
        self.assertLessEqual(len(result), 48)


class TestProvision(unittest.TestCase):
    """Test environment provisioning."""

    def test_skip_when_no_deploy_config(self):
        config = PipelineConfig()
        result = provision(config, "/tmp/proj", "spec1", "abc123")
        self.assertIsNone(result)

    @patch("darkfactory.deployment.environment.run")
    def test_provision_compose(self, mock_run):
        mock_run.return_value = RunResult(exit_code=0)
        config = PipelineConfig(
            deploy_base_url="http://localhost:3000",
            deploy_compose_file="docker-compose.test.yml",
        )
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir)
        compose_path = Path(tmpdir) / "docker-compose.test.yml"
        compose_path.write_text("version: '3'\n")

        result = provision(config, tmpdir, "myspec", "run123456789")
        self.assertIsNotNone(result)
        self.assertEqual(result.compose_project, "df-myspec-run12345")
        self.assertEqual(result.base_url, "http://localhost:3000")

        cmd = mock_run.call_args[0][0]
        self.assertIn("docker-compose", cmd)
        self.assertIn("up", cmd)

    @patch("darkfactory.deployment.environment.run")
    def test_provision_compose_failure_raises(self, mock_run):
        mock_run.return_value = RunResult(exit_code=1, stderr="compose error")
        config = PipelineConfig(
            deploy_base_url="http://localhost:3000",
            deploy_compose_file="docker-compose.test.yml",
        )
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir)
        compose_path = Path(tmpdir) / "docker-compose.test.yml"
        compose_path.write_text("version: '3'\n")

        with self.assertRaises(RuntimeError) as ctx:
            provision(config, tmpdir, "spec1", "run123")
        self.assertIn("compose error", str(ctx.exception))

    def test_provision_compose_file_missing(self):
        config = PipelineConfig(deploy_compose_file="nonexistent.yml")
        with self.assertRaises(FileNotFoundError):
            provision(config, "/tmp/empty", "spec1", "run123")

    @patch("darkfactory.deployment.environment.run_command")
    def test_provision_legacy_cmd(self, mock_run_cmd):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_run_cmd.return_value = mock_proc
        config = PipelineConfig(
            deploy_cmd="scripts/deploy.sh",
            deploy_base_url="http://localhost:8080",
        )
        result = provision(config, "/tmp/proj", "spec1", "run123")
        self.assertIsNotNone(result)
        self.assertEqual(result.base_url, "http://localhost:8080")

    @patch("darkfactory.deployment.environment.run")
    def test_provision_legacy_allow_shell(self, mock_run):
        mock_run.return_value = RunResult(exit_code=0)
        config = PipelineConfig(
            deploy_cmd="echo deploy",
            deploy_base_url="http://localhost:8080",
            deploy_cmd_allow_shell=True,
        )
        result = provision(config, "/tmp/proj", "spec1", "run123")
        self.assertIsNotNone(result)


class TestHealthCheck(unittest.TestCase):
    """Test health check delegation to health.check_http."""

    @patch("darkfactory.deployment.health.run_subprocess")
    def test_passes_immediately(self, mock_run):
        mock_run.return_value = RunResult(exit_code=0)
        self.assertTrue(health_check("http://localhost:3000", timeout=10))

    @patch("darkfactory.deployment.health.time")
    @patch("darkfactory.deployment.health.run_subprocess")
    def test_passes_after_retries(self, mock_run, mock_time):
        mock_run.side_effect = [
            RunResult(exit_code=1),
            RunResult(exit_code=1),
            RunResult(exit_code=0),
        ]
        mock_time.monotonic.side_effect = [0, 0, 2, 2, 5, 5, 8]
        mock_time.sleep = MagicMock()
        self.assertTrue(health_check("http://localhost:3000", timeout=60))

    @patch("darkfactory.deployment.health.time")
    @patch("darkfactory.deployment.health.run_subprocess")
    def test_times_out(self, mock_run, mock_time):
        mock_run.return_value = RunResult(exit_code=1)
        mock_time.monotonic.side_effect = [0, 0, 100]
        mock_time.sleep = MagicMock()
        self.assertFalse(health_check("http://localhost:3000", timeout=5))

    def test_returns_false_for_empty_url(self):
        self.assertFalse(health_check(""))


class TestTeardown(unittest.TestCase):
    """Test environment teardown."""

    @patch("darkfactory.deployment.environment.run")
    def test_compose_teardown(self, mock_run):
        mock_run.return_value = RunResult(exit_code=0)
        config = PipelineConfig()
        teardown(config, project_dir="/tmp/proj", compose_project="df-myspec-abc12345")
        cmd = mock_run.call_args_list[0][0][0]
        self.assertIn("docker-compose", cmd)
        self.assertIn("down", cmd)

    @patch("darkfactory.deployment.environment.run")
    def test_teardown_with_legacy_cmd(self, mock_run):
        mock_run.return_value = RunResult(exit_code=0)
        config = PipelineConfig(
            deploy_teardown_cmd="scripts/teardown.sh",
            deploy_cmd_allow_shell=True,
        )
        teardown(config, project_dir="/tmp/proj")
        cmd = mock_run.call_args_list[0][0][0]
        self.assertIn("bash", cmd)

    @patch("darkfactory.deployment.environment.run")
    def test_teardown_no_config_no_error(self, mock_run):
        config = PipelineConfig()
        teardown(config, project_dir="/tmp/proj")

    @patch("darkfactory.deployment.environment.run")
    def test_orphan_container_cleanup(self, mock_run):
        mock_run.side_effect = [
            RunResult(exit_code=0, stdout="abc123\ndef456\n"),
            RunResult(exit_code=0),
        ]
        config = PipelineConfig()
        teardown(config, project_dir="/tmp/proj", run_id="testrun")
        # Second call should be docker rm -f with the container IDs.
        rm_cmd = mock_run.call_args_list[1][0][0]
        self.assertIn("docker", rm_cmd)
        self.assertIn("rm", rm_cmd)
        self.assertIn("abc123", rm_cmd)


if __name__ == "__main__":
    unittest.main()
