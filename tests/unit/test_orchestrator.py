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

    def _write_stub_manifest(self, project: Path, spec_name: str = "my-spec") -> None:
        """Write a minimal valid architecture manifest for the given spec."""
        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        spec_file = project / ".dark-factory" / "specs" / f"{spec_name}.md"
        from darkfactory.utils.hash import sha256_file
        spec_hash = sha256_file(spec_file)
        (arch_dir / f"{spec_name}-manifest.yaml").write_text(
            f"version: 1\nspec: {spec_name}\nspec_hash: \"{spec_hash}\"\n"
            f"language: node\nfile_map: []\n"
        )

    @patch("darkfactory.spec.precheck.run_precheck")
    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    @patch("darkfactory.pipeline.orchestrator.validate_spec_result")
    @patch("darkfactory.pipeline.orchestrator.require_docker")
    @patch("darkfactory.pipeline.orchestrator.setup_networks")
    @patch("darkfactory.pipeline.orchestrator.setup_proxies")
    @patch("darkfactory.pipeline.orchestrator.run_iteration_loop")
    @patch("darkfactory.pipeline.orchestrator.notify_shipped")
    @patch("darkfactory.pipeline.orchestrator.registry_register")
    @patch("darkfactory.pipeline.orchestrator.has_scenario_files", return_value=True)
    def test_ship_path(
        self,
        mock_scenarios: MagicMock,
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

        # Pre-write manifest so architecture lock skips (resume gate).
        self._write_stub_manifest(project)

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
    @patch("darkfactory.pipeline.orchestrator.has_scenario_files", return_value=True)
    def test_escalation_path(
        self,
        mock_scenarios: MagicMock,
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

        # Pre-write manifest so architecture lock skips (resume gate).
        self._write_stub_manifest(project)

        mock_precheck.return_value = MagicMock(passed=True, ambiguities=[])
        mock_agent.return_value = AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)
        mock_validate.return_value = (True, "PASS")
        mock_loop.return_value = False  # Escalation

        exit_code = run_pipeline("my-spec", config, project)
        self.assertEqual(exit_code, 2)
        mock_notify.assert_called_once()


class TestArchitectureLock(TempDirTestCase):
    """Tests for the architecture lock pipeline step."""

    def _setup_project(self) -> Path:
        p = self.tmp_dir
        spec_dir = p / ".dark-factory" / "specs"
        spec_dir.mkdir(parents=True)
        (spec_dir / "my-spec.md").write_text("# My Spec\n## Functional Requirements\n- FR-1: Do something\n")
        (p / ".dark-factory" / "state").mkdir(parents=True, exist_ok=True)
        (p / ".dark-factory" / "audit").mkdir(parents=True, exist_ok=True)
        (p / ".dark-factory" / "reviews").mkdir(parents=True, exist_ok=True)
        (p / ".dark-factory" / "reports").mkdir(parents=True, exist_ok=True)
        return p

    def _spec_hash(self, project: Path, spec_name: str = "my-spec") -> str:
        from darkfactory.utils.hash import sha256_file
        return sha256_file(project / ".dark-factory" / "specs" / f"{spec_name}.md")

    def _make_agent_writes_manifest(self, manifest_path: Path, spec_hash: str):
        """Return a side_effect callable that writes a valid manifest."""
        def side_effect(*args, **kwargs):
            manifest_path.write_text(
                f'version: 1\nspec: my-spec\nspec_hash: "{spec_hash}"\n'
                f'language: node\nfile_map:\n  - path: src/index.ts\n'
                f'    action: create\n    purpose: entry point\n    satisfies: [FR-1]\n'
            )
            return AgentResult(role=AgentRole.ARCHITECT, exit_code=0)
        return side_effect

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    @patch("darkfactory.pipeline.orchestrator.validate_spec_result")
    @patch("darkfactory.pipeline.orchestrator.require_docker")
    @patch("darkfactory.pipeline.orchestrator.setup_networks")
    @patch("darkfactory.pipeline.orchestrator.setup_proxies")
    @patch("darkfactory.pipeline.orchestrator.run_iteration_loop")
    @patch("darkfactory.pipeline.orchestrator.has_scenario_files", return_value=True)
    @patch("darkfactory.spec.precheck.run_precheck")
    def test_skip_when_disabled(
        self,
        mock_precheck: MagicMock,
        mock_scenarios: MagicMock,
        mock_loop: MagicMock,
        mock_proxies: MagicMock,
        mock_networks: MagicMock,
        mock_docker: MagicMock,
        mock_validate: MagicMock,
        mock_agent: MagicMock,
    ) -> None:
        """Architecture lock step is skipped when config.architecture_lock=False."""
        project = self._setup_project()
        config = PipelineConfig(architecture_lock=False)

        mock_precheck.return_value = MagicMock(passed=True, ambiguities=[])
        mock_agent.return_value = AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)
        mock_validate.return_value = (True, "PASS")
        mock_loop.return_value = True

        run_pipeline("my-spec", config, project)
        # Architect agent should never have been invoked.
        for call in mock_agent.call_args_list:
            self.assertNotEqual(call[0][0], AgentRole.ARCHITECT)

    def test_resume_skips_current_manifest(self) -> None:
        """Architecture lock skips when manifest exists with matching spec_hash."""
        from darkfactory.pipeline.orchestrator import _step_architecture_lock
        from darkfactory.docker.barriers import configure_barriers

        project = self._setup_project()
        config = PipelineConfig()
        barriers = configure_barriers(config.source_dirs)
        spec_hash = self._spec_hash(project)

        # Pre-write a manifest with matching hash.
        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        (arch_dir / "my-spec-manifest.yaml").write_text(
            f'version: 1\nspec: my-spec\nspec_hash: "{spec_hash}"\nlanguage: node\nfile_map: []\n'
        )

        spec_run = SpecRun(spec_name="my-spec")
        spec_run.spec_hash = spec_hash
        # Should not raise — skips because hash matches.
        _step_architecture_lock(
            spec_run, config, barriers, project, dry_run=True,
        )

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    def test_stale_manifest_regenerates(self, mock_agent: MagicMock) -> None:
        """Architecture lock regenerates when spec_hash doesn't match."""
        from darkfactory.pipeline.orchestrator import _step_architecture_lock
        from darkfactory.docker.barriers import configure_barriers

        project = self._setup_project()
        config = PipelineConfig()
        barriers = configure_barriers(config.source_dirs)
        spec_hash = self._spec_hash(project)

        # Pre-write a manifest with WRONG hash.
        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = arch_dir / "my-spec-manifest.yaml"
        manifest_path.write_text(
            'version: 1\nspec: my-spec\nspec_hash: "wrong"\nlanguage: node\nfile_map: []\n'
        )

        mock_agent.side_effect = self._make_agent_writes_manifest(manifest_path, spec_hash)

        spec_run = SpecRun(spec_name="my-spec")
        spec_run.spec_hash = spec_hash
        _step_architecture_lock(
            spec_run, config, barriers, project, dry_run=True,
        )
        mock_agent.assert_called_once()

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    def test_fresh_deletes_manifest(self, mock_agent: MagicMock) -> None:
        """--fresh deletes existing manifest before regenerating."""
        from darkfactory.pipeline.orchestrator import _step_architecture_lock
        from darkfactory.docker.barriers import configure_barriers

        project = self._setup_project()
        config = PipelineConfig()
        barriers = configure_barriers(config.source_dirs)
        spec_hash = self._spec_hash(project)

        # Pre-write a manifest with matching hash (would normally skip).
        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = arch_dir / "my-spec-manifest.yaml"
        manifest_path.write_text(
            f'version: 1\nspec: my-spec\nspec_hash: "{spec_hash}"\nlanguage: node\nfile_map: []\n'
        )

        mock_agent.side_effect = self._make_agent_writes_manifest(manifest_path, spec_hash)

        spec_run = SpecRun(spec_name="my-spec")
        spec_run.spec_hash = spec_hash
        _step_architecture_lock(
            spec_run, config, barriers, project, fresh=True, dry_run=True,
        )
        # Agent should be called because --fresh forces regeneration.
        mock_agent.assert_called_once()

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    def test_validation_failure_raises(self, mock_agent: MagicMock) -> None:
        """Manifest validation failure raises DarkFactoryError."""
        from darkfactory.pipeline.orchestrator import _step_architecture_lock
        from darkfactory.docker.barriers import configure_barriers

        project = self._setup_project()
        config = PipelineConfig()
        barriers = configure_barriers(config.source_dirs)

        # Mock agent to write an invalid manifest (absolute path).
        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = arch_dir / "my-spec-manifest.yaml"

        def write_bad_manifest(*args, **kwargs):
            spec_hash = self._spec_hash(project)
            manifest_path.write_text(
                f'version: 1\nspec: my-spec\nspec_hash: "{spec_hash}"\n'
                f'language: node\nfile_map:\n  - path: /absolute/bad.ts\n'
                f'    action: create\n    purpose: bad\n    satisfies: [FR-1]\n'
            )
            return AgentResult(role=AgentRole.ARCHITECT, exit_code=0)

        mock_agent.side_effect = write_bad_manifest

        spec_run = SpecRun(spec_name="my-spec")
        from darkfactory.utils.hash import sha256_file
        spec_run.spec_hash = sha256_file(project / ".dark-factory" / "specs" / "my-spec.md")
        with self.assertRaises(DarkFactoryError):
            _step_architecture_lock(
                spec_run, config, barriers, project, dry_run=True,
            )


if __name__ == "__main__":
    unittest.main()
