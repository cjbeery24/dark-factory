"""Tests for pipeline/orchestrator.py — main pipeline orchestration.

These tests mock all subprocess calls (Docker, git, etc.) and verify
the pipeline step sequence and state transitions.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.helpers import TempDirTestCase
from darkfactory.errors import ConfigError, DarkFactoryError
from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.pipeline.orchestrator import run_pipeline, _handle_resume
from darkfactory.pipeline.iteration import _parse_oracle_decision
from darkfactory.models.pipeline import SpecRun


class TestParseOracleDecision(unittest.TestCase):
    def test_ship(self) -> None:
        self.assertEqual(_parse_oracle_decision("SHIP\nGood work."), "SHIP")

    def test_noship(self) -> None:
        self.assertEqual(_parse_oracle_decision("NO-SHIP\nNeeds fixes."), "NO-SHIP")

    def test_ship_mixed_case(self) -> None:
        self.assertEqual(_parse_oracle_decision("  Ship  \n"), "SHIP")

    def test_empty_output(self) -> None:
        self.assertEqual(_parse_oracle_decision(""), "NO-SHIP")

    def test_malformed(self) -> None:
        self.assertEqual(_parse_oracle_decision("maybe ship later"), "NO-SHIP")


class TestHandleResume(TempDirTestCase):
    def test_resume_unchanged_spec(self) -> None:
        state_file = self.tmp_dir / "spec.state"
        state_file.write_text("spec_hash=abc123\ncompleted_step=2\niteration=2\nrun_id=r1\n")
        spec_run = SpecRun(spec_name="test")
        _handle_resume(spec_run, state_file, "abc123")
        self.assertEqual(spec_run.completed_step, 2)
        self.assertEqual(spec_run.iteration, 2)
        self.assertEqual(spec_run.run_id, "r1")

    def test_resume_changed_spec(self) -> None:
        state_file = self.tmp_dir / "spec.state"
        state_file.write_text("spec_hash=abc123\ncompleted_step=2\n")
        spec_run = SpecRun(spec_name="test")
        with self.assertRaises(DarkFactoryError):
            _handle_resume(spec_run, state_file, "different_hash")


class TestRunPipeline(TempDirTestCase):
    def _setup_project(self) -> Path:
        """Create minimal project structure."""
        p = self.tmp_dir
        spec_dir = p / ".dark-factory" / "specs"
        spec_dir.mkdir(parents=True)
        (spec_dir / "my-spec.md").write_text("# My Spec\n")
        (p / ".dark-factory" / "state").mkdir(parents=True, exist_ok=True)
        (p / ".dark-factory" / "audit").mkdir(parents=True, exist_ok=True)
        (p / ".dark-factory" / "reviews").mkdir(parents=True, exist_ok=True)
        (p / ".dark-factory" / "reports").mkdir(parents=True, exist_ok=True)
        return p

    def test_rejects_invalid_spec(self) -> None:
        config = PipelineConfig()
        with self.assertRaises(ConfigError):
            run_pipeline("../bad", config, self.tmp_dir, dry_run=True)

    def test_rejects_missing_spec(self) -> None:
        config = PipelineConfig()
        with self.assertRaises(DarkFactoryError):
            run_pipeline("nonexistent", config, self.tmp_dir, dry_run=True)

    @patch("darkfactory.spec.precheck.run_precheck")
    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    @patch("darkfactory.pipeline.orchestrator.validate_spec_result")
    @patch("darkfactory.pipeline.orchestrator.require_docker")
    @patch("darkfactory.pipeline.orchestrator.setup_networks")
    @patch("darkfactory.pipeline.orchestrator.setup_proxies")
    @patch("darkfactory.pipeline.orchestrator.run_iteration_loop")
    @patch("darkfactory.pipeline.orchestrator.notify_shipped")
    @patch("darkfactory.pipeline.orchestrator.registry_register")
    def test_ship_path(
        self,
        mock_registry: MagicMock,
        mock_notify: MagicMock,
        mock_loop: MagicMock,
        mock_proxies: MagicMock,
        mock_networks: MagicMock,
        mock_docker: MagicMock,
        mock_validate: MagicMock,
        mock_agent: MagicMock,
        mock_precheck: MagicMock,
    ) -> None:
        project = self._setup_project()
        config = PipelineConfig()

        # Agent calls succeed.
        mock_precheck.return_value = MagicMock(passed=True, ambiguities=[])
        mock_agent.return_value = AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)
        mock_validate.return_value = (True, "PASS")
        mock_loop.return_value = True  # SHIP

        exit_code = run_pipeline("my-spec", config, project)
        self.assertEqual(exit_code, 0)
        mock_registry.assert_called_once()
        mock_notify.assert_called_once()

    @patch("darkfactory.spec.precheck.run_precheck")
    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    @patch("darkfactory.pipeline.orchestrator.validate_spec_result")
    @patch("darkfactory.pipeline.orchestrator.require_docker")
    @patch("darkfactory.pipeline.orchestrator.setup_networks")
    @patch("darkfactory.pipeline.orchestrator.setup_proxies")
    @patch("darkfactory.pipeline.orchestrator.run_iteration_loop")
    @patch("darkfactory.pipeline.orchestrator.notify_escalated")
    def test_escalation_path(
        self,
        mock_notify: MagicMock,
        mock_loop: MagicMock,
        mock_proxies: MagicMock,
        mock_networks: MagicMock,
        mock_docker: MagicMock,
        mock_validate: MagicMock,
        mock_agent: MagicMock,
        mock_precheck: MagicMock,
    ) -> None:
        project = self._setup_project()
        config = PipelineConfig()

        mock_precheck.return_value = MagicMock(passed=True, ambiguities=[])
        mock_agent.return_value = AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)
        mock_validate.return_value = (True, "PASS")
        mock_loop.return_value = False  # Escalation

        exit_code = run_pipeline("my-spec", config, project)
        self.assertEqual(exit_code, 2)
        mock_notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
