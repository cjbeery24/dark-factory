"""Tests for pipeline/orchestrator.py — main pipeline orchestration.

These tests mock all subprocess calls (Docker, git, etc.) and verify
the pipeline step sequence and state transitions.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.helpers import TempDirTestCase
from darkfactory.errors import ConfigError, DarkFactoryError
from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.pipeline.orchestrator import run_pipeline, _handle_resume, _write_interrupted_state
from darkfactory.pipeline.agents import extract_agent_text
from darkfactory.pipeline.iteration import _parse_oracle_decision
from darkfactory.models.pipeline import PipelineStatus, SpecRun


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

    def test_json_wrapped_ship(self) -> None:
        """extract_agent_text + _parse_oracle_decision composition for JSON-wrapped output."""
        raw = json.dumps({"type": "result", "result": "SHIP\n\nApproved.", "session_id": "abc"})
        extracted = extract_agent_text(raw)
        self.assertEqual(_parse_oracle_decision(extracted), "SHIP")


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


def _write_active_adapters_hints(project: Path, names: tuple[str, ...] = ("typescript-node",)) -> None:
    """Write a minimal project-hints.toml declaring ``active_adapters``.

    A5 requires every ``run_pipeline`` invocation to resolve a non-empty
    adapter set (unless ``no_adapter=True``). Tests that exercise code
    past the A5 gate need a project-hints.toml on disk — without one,
    ``load_project_hints`` returns ``ProjectHints.empty()`` and the
    pipeline aborts with the registry's "No adapters declared" error.
    """
    hints_path = project / ".dark-factory" / "project-hints.toml"
    hints_path.parent.mkdir(parents=True, exist_ok=True)
    adapters_literal = ", ".join(f'"{n}"' for n in names)
    hints_path.write_text(
        "[detected]\n"
        f"active_adapters = [{adapters_literal}]\n"
    )


class TestRunPipeline(TempDirTestCase):
    def _setup_project(self) -> Path:
        """Create minimal project structure."""
        p = self.tmp_dir
        spec_dir = p / ".dark-factory" / "specs"
        spec_dir.mkdir(parents=True)
        (spec_dir / "my-spec.md").write_text(
            "# My Spec\n\n"
            "<!-- dark-factory-metadata\nfeature-id: F-1\nspec_type: feature\n-->\n"
        )
        # Phase 4.5.1 preflight requires source_dirs to exist on disk.
        (p / "src").mkdir(exist_ok=True)
        (p / ".dark-factory" / "state").mkdir(parents=True, exist_ok=True)
        (p / ".dark-factory" / "audit").mkdir(parents=True, exist_ok=True)
        (p / ".dark-factory" / "reviews").mkdir(parents=True, exist_ok=True)
        (p / ".dark-factory" / "reports").mkdir(parents=True, exist_ok=True)
        # A5: declare an active adapter so fail-fast doesn't abort before
        # the test reaches its subject under test.
        _write_active_adapters_hints(p)
        return p

    def test_rejects_invalid_spec(self) -> None:
        config = PipelineConfig(test_cmd="pytest")
        with self.assertRaises(ConfigError):
            run_pipeline("../bad", config, self.tmp_dir, dry_run=True)

    def test_rejects_missing_spec(self) -> None:
        config = PipelineConfig(test_cmd="pytest")
        with self.assertRaises(DarkFactoryError):
            run_pipeline("nonexistent", config, self.tmp_dir, dry_run=True)

    def test_rejects_spec_missing_spec_type(self) -> None:
        """Phase 4.1: a spec without spec_type frontmatter must hard-fail
        before any pipeline work happens — no transitional default."""
        p = self.tmp_dir
        spec_dir = p / ".dark-factory" / "specs"
        spec_dir.mkdir(parents=True)
        # Has a metadata block but no spec_type field.
        (spec_dir / "no-type.md").write_text(
            "# No Type\n\n<!-- dark-factory-metadata\nfeature-id: F-1\n-->\n"
        )
        config = PipelineConfig(test_cmd="pytest")
        with self.assertRaises(DarkFactoryError) as ctx:
            run_pipeline("no-type", config, p, dry_run=True)
        self.assertIn("spec_type", str(ctx.exception))
        self.assertIn("migrate-frontmatter", str(ctx.exception))
        # Hard-fail must run before any FS side-effects: state/audit dirs
        # must NOT have been created on a rejected spec.
        self.assertFalse((p / ".dark-factory" / "state").exists())
        self.assertFalse((p / ".dark-factory" / "audit").exists())

    def test_rejects_spec_with_no_metadata_block(self) -> None:
        """A spec with no metadata block at all also fails the same check."""
        p = self.tmp_dir
        spec_dir = p / ".dark-factory" / "specs"
        spec_dir.mkdir(parents=True)
        (spec_dir / "bare.md").write_text("# Bare Spec\n\nNo metadata.\n")
        config = PipelineConfig(test_cmd="pytest")
        with self.assertRaises(DarkFactoryError) as ctx:
            run_pipeline("bare", config, p, dry_run=True)
        self.assertIn("spec_type", str(ctx.exception))

    def test_rejects_spec_with_unknown_spec_type(self) -> None:
        """Unknown spec_type values surface as ConfigError from parse_spec_type."""
        from darkfactory.errors import ConfigError as _CE
        p = self.tmp_dir
        spec_dir = p / ".dark-factory" / "specs"
        spec_dir.mkdir(parents=True)
        (spec_dir / "weird.md").write_text(
            "# Weird\n\n<!-- dark-factory-metadata\n"
            "feature-id: F-1\nspec_type: nonsense\n-->\n"
        )
        config = PipelineConfig(test_cmd="pytest")
        with self.assertRaises(_CE):
            run_pipeline("weird", config, p, dry_run=True)

    def test_allow_resume_false_skips_stale_state_check(self) -> None:
        """Phase 2.6 regression: `dark-factory validate` passes allow_resume=False
        so a stale state file from a prior `run` does NOT trigger the
        spec-hash-mismatch guard. Previously this path was papered over by
        passing fresh=True (0e0a0be), which was reverted in f136632 without
        the structural fix, re-introducing the user-visible hard failure."""
        p = self._setup_project()
        # Simulate a prior `run` that left a state file with a stale hash.
        state_file = p / ".dark-factory" / "state" / "my-spec.state"
        state_file.write_text(
            "spec=my-spec\nrun_id=prior\nspec_hash=stale_hash_from_prior_run\n"
            "status=interrupted\ncompleted_step=2\niteration=2\n"
        )
        config = PipelineConfig(test_cmd="pytest")
        # With allow_resume=False, the auto-resume + hash-mismatch path is
        # skipped entirely. dry_run=True short-circuits before any agent is
        # invoked; the spec-hash guard is the specific error we must NOT see.
        try:
            run_pipeline(
                "my-spec", config, p, dry_run=True,
                stop_after_step=1, allow_resume=False,
            )
        except DarkFactoryError as exc:
            self.assertNotIn("spec has changed", str(exc),
                "allow_resume=False must bypass the stale-hash guard")

    def test_allow_resume_true_still_catches_stale_state(self) -> None:
        """Symmetry check: the guard still fires for callers that opt in
        to resume (the default), otherwise the fix above would be silently
        relaxing `dark-factory run`'s resume-safety check too."""
        p = self._setup_project()
        state_file = p / ".dark-factory" / "state" / "my-spec.state"
        state_file.write_text(
            "spec=my-spec\nrun_id=prior\nspec_hash=stale_hash_from_prior_run\n"
            "status=interrupted\ncompleted_step=2\niteration=2\n"
        )
        config = PipelineConfig(test_cmd="pytest")
        with self.assertRaises(DarkFactoryError) as ctx:
            run_pipeline("my-spec", config, p, dry_run=True)
        self.assertIn("spec has changed", str(ctx.exception))

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
            f"test_integration:\n  module_system: esm\n"
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
        config = PipelineConfig(test_cmd="pytest")

        # Pre-write manifest so architecture lock skips (resume gate).
        self._write_stub_manifest(project)

        # Agent calls succeed.
        mock_precheck.return_value = MagicMock(passed=True, fatal=False, ambiguities=[], summary="ok")
        mock_agent.return_value = AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)
        mock_validate.return_value = (True, "PASS")
        mock_loop.return_value = True  # SHIP

        exit_code = run_pipeline("my-spec", config, project)
        self.assertEqual(exit_code, 0)
        mock_registry.assert_called_once()
        mock_notify.assert_called_once()

    @patch("darkfactory.pipeline.orchestrator.check_workspace_scripts")
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
    def test_bootstrap_spec_skips_workspace_check(
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
        mock_ws: MagicMock,
    ) -> None:
        """Bootstrap specs must not be subject to the workspace script
        consistency check — the whole point of a bootstrap spec is to
        create the tooling that check validates. Regression lock for the
        chicken-and-egg failure caught on the 2026-04-07 verification run.
        """
        project = self._setup_project()
        # Overwrite the default spec with a bootstrap-tagged one.
        (project / ".dark-factory" / "specs" / "my-spec.md").write_text(
            "# My Spec\n\n"
            "<!-- dark-factory-metadata\nfeature-id: F-1\nspec_type: bootstrap\n-->\n"
        )
        self._write_stub_manifest(project)

        mock_precheck.return_value = MagicMock(passed=True, fatal=False, ambiguities=[], summary="ok")
        mock_agent.return_value = AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)
        mock_validate.return_value = (True, "PASS")
        mock_loop.return_value = True

        run_pipeline("my-spec", config=PipelineConfig(test_cmd="pytest"), project_dir=project)
        mock_ws.assert_not_called()

    @patch("darkfactory.pipeline.orchestrator.check_workspace_scripts")
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
    def test_feature_spec_runs_workspace_check(
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
        mock_ws: MagicMock,
    ) -> None:
        """Feature specs still get the workspace drift check."""
        project = self._setup_project()  # default spec is spec_type: feature
        self._write_stub_manifest(project)

        mock_precheck.return_value = MagicMock(passed=True, fatal=False, ambiguities=[], summary="ok")
        mock_agent.return_value = AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)
        mock_validate.return_value = (True, "PASS")
        mock_loop.return_value = True
        mock_ws.return_value = MagicMock(passed=True, warnings=[], errors=[])

        run_pipeline("my-spec", config=PipelineConfig(test_cmd="pytest"), project_dir=project)
        mock_ws.assert_called_once()

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
        config = PipelineConfig(test_cmd="pytest")

        # Pre-write manifest so architecture lock skips (resume gate).
        self._write_stub_manifest(project)

        mock_precheck.return_value = MagicMock(passed=True, fatal=False, ambiguities=[], summary="ok")
        mock_agent.return_value = AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)
        mock_validate.return_value = (True, "PASS")
        mock_loop.return_value = False  # Escalation

        exit_code = run_pipeline("my-spec", config, project)
        self.assertEqual(exit_code, 2)
        mock_notify.assert_called_once()


class TestA5FailFast(TempDirTestCase):
    """Pin the A5 plan acceptance criterion: a project whose
    ``[detected].active_adapters`` is empty or contains an unknown name
    MUST abort with the registry error before any Docker-tier cost is
    paid. The check sits between preflight (adapter-independent config
    sanity) and Docker setup (where every further step spends dollars).

    The assertion pattern is: patch ``require_docker`` with a bomb, run
    the pipeline, and verify ``require_docker`` was never called. If
    the fail-fast check regresses to "after Docker setup", these tests
    flip from ``raises DarkFactoryError`` to ``raises AssertionError``,
    which is the louder failure we want — don't simplify the pattern.
    """

    def _setup_project_minimal(self) -> Path:
        """Create the minimum FS shape ``run_pipeline`` needs BEFORE the
        A5 gate — spec file + source_dirs + state dirs. Intentionally
        does NOT write a project-hints.toml; that's the variable each
        test controls."""
        p = self.tmp_dir
        spec_dir = p / ".dark-factory" / "specs"
        spec_dir.mkdir(parents=True)
        (spec_dir / "my-spec.md").write_text(
            "# My Spec\n\n"
            "<!-- dark-factory-metadata\nfeature-id: F-1\nspec_type: feature\n-->\n"
        )
        (p / "src").mkdir(exist_ok=True)
        return p

    @patch("darkfactory.pipeline.orchestrator.require_docker",
           side_effect=AssertionError("require_docker called before A5 fail-fast"))
    def test_empty_active_adapters_aborts_before_docker(
        self, mock_docker: MagicMock
    ) -> None:
        """A project with no project-hints.toml loads as
        ``ProjectHints.empty()`` (active_adapters=()) and must abort
        with the registry's remediation hint before Docker setup."""
        project = self._setup_project_minimal()
        config = PipelineConfig(test_cmd="pytest")

        with self.assertRaises(DarkFactoryError) as ctx:
            run_pipeline("my-spec", config, project)

        msg = str(ctx.exception)
        self.assertIn("No adapters declared", msg)
        self.assertIn("dark-factory init", msg)
        self.assertIn("--no-adapter", msg)
        mock_docker.assert_not_called()

    @patch("darkfactory.pipeline.orchestrator.require_docker",
           side_effect=AssertionError("require_docker called before A5 fail-fast"))
    def test_unknown_adapter_name_aborts_before_docker(
        self, mock_docker: MagicMock
    ) -> None:
        """A project declaring an unregistered adapter name aborts with
        the "No adapter for ecosystem 'X'" error before Docker setup.
        Pins the plan's fail-fast guarantee: a typo in
        project-hints.toml must not burn container-startup cost."""
        project = self._setup_project_minimal()
        _write_active_adapters_hints(project, names=("rust",))
        config = PipelineConfig(test_cmd="pytest")

        with self.assertRaises(DarkFactoryError) as ctx:
            run_pipeline("my-spec", config, project)

        msg = str(ctx.exception)
        self.assertIn("rust", msg)
        self.assertIn("Available:", msg)
        mock_docker.assert_not_called()

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    @patch("darkfactory.pipeline.orchestrator.validate_spec_result")
    @patch("darkfactory.pipeline.orchestrator.require_docker")
    @patch("darkfactory.pipeline.orchestrator.setup_networks")
    @patch("darkfactory.pipeline.orchestrator.setup_proxies")
    @patch("darkfactory.pipeline.orchestrator.run_iteration_loop")
    @patch("darkfactory.pipeline.orchestrator.notify_shipped")
    @patch("darkfactory.pipeline.orchestrator.registry_register")
    @patch("darkfactory.pipeline.orchestrator.has_scenario_files", return_value=True)
    @patch("darkfactory.spec.precheck.run_precheck")
    def test_no_adapter_flag_bypasses_fail_fast(
        self,
        mock_precheck: MagicMock,
        mock_scenarios: MagicMock,
        mock_registry: MagicMock,
        mock_notify: MagicMock,
        mock_loop: MagicMock,
        mock_proxies: MagicMock,
        mock_networks: MagicMock,
        mock_docker: MagicMock,
        mock_validate: MagicMock,
        mock_agent: MagicMock,
    ) -> None:
        """``--no-adapter`` is the documented escape hatch — the empty
        active_adapters project that would otherwise fail must run
        through to completion when the flag is set. Confirms the CLI
        surface actually reaches the orchestrator's bypass."""
        project = self._setup_project_minimal()
        # Mimic real TestRunPipeline fixture paths the pipeline writes to.
        (project / ".dark-factory" / "state").mkdir(parents=True, exist_ok=True)
        (project / ".dark-factory" / "audit").mkdir(parents=True, exist_ok=True)
        (project / ".dark-factory" / "reviews").mkdir(parents=True, exist_ok=True)
        (project / ".dark-factory" / "reports").mkdir(parents=True, exist_ok=True)

        # Stub manifest so architecture lock skips (same pattern as
        # TestRunPipeline — avoids needing the architect agent).
        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        from darkfactory.utils.hash import sha256_file
        spec_hash = sha256_file(project / ".dark-factory" / "specs" / "my-spec.md")
        (arch_dir / "my-spec-manifest.yaml").write_text(
            f"version: 1\nspec: my-spec\nspec_hash: \"{spec_hash}\"\n"
            f"language: node\nfile_map: []\n"
            f"test_integration:\n  module_system: esm\n"
        )

        mock_precheck.return_value = MagicMock(passed=True, fatal=False, ambiguities=[], summary="ok")
        mock_agent.return_value = AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)
        mock_validate.return_value = (True, "PASS")
        mock_loop.return_value = True  # SHIP

        exit_code = run_pipeline(
            "my-spec",
            PipelineConfig(test_cmd="pytest"),
            project,
            no_adapter=True,
        )
        self.assertEqual(exit_code, 0)
        # The bypass must actually reach Docker setup — otherwise the
        # flag doesn't do what it advertises.
        mock_docker.assert_called_once()


class TestArchitectureLock(TempDirTestCase):
    """Tests for the architecture lock pipeline step."""

    def _setup_project(self) -> Path:
        p = self.tmp_dir
        spec_dir = p / ".dark-factory" / "specs"
        spec_dir.mkdir(parents=True)
        (spec_dir / "my-spec.md").write_text(
            "# My Spec\n\n"
            "<!-- dark-factory-metadata\nfeature-id: F-1\nspec_type: feature\n-->\n\n"
            "## Functional Requirements\n- FR-1: Do something\n"
        )
        # Phase 4.5.1 preflight requires source_dirs to exist on disk.
        (p / "src").mkdir(exist_ok=True)
        (p / ".dark-factory" / "state").mkdir(parents=True, exist_ok=True)
        (p / ".dark-factory" / "audit").mkdir(parents=True, exist_ok=True)
        (p / ".dark-factory" / "reviews").mkdir(parents=True, exist_ok=True)
        (p / ".dark-factory" / "reports").mkdir(parents=True, exist_ok=True)
        _write_active_adapters_hints(p)
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
                f'test_integration:\n  module_system: esm\n'
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
        config = PipelineConfig(architecture_lock=False, test_cmd="pytest")

        mock_precheck.return_value = MagicMock(passed=True, fatal=False, ambiguities=[], summary="ok")
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
        config = PipelineConfig(test_cmd="pytest")
        barriers = configure_barriers(config.source_dirs)
        spec_hash = self._spec_hash(project)

        # Pre-write a manifest with matching hash.
        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        (arch_dir / "my-spec-manifest.yaml").write_text(
            f'version: 1\nspec: my-spec\nspec_hash: "{spec_hash}"\nlanguage: node\nfile_map: []\n'
            f'test_integration:\n  module_system: esm\n'
        )

        spec_run = SpecRun(spec_name="my-spec")
        spec_run.spec_hash = spec_hash
        # Should not raise — skips because hash matches.
        _step_architecture_lock(
            spec_run, config, barriers, project,
            active_adapters=[], dry_run=True,
        )

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    def test_stale_manifest_regenerates(self, mock_agent: MagicMock) -> None:
        """Architecture lock regenerates when spec_hash doesn't match."""
        from darkfactory.pipeline.orchestrator import _step_architecture_lock
        from darkfactory.docker.barriers import configure_barriers

        project = self._setup_project()
        config = PipelineConfig(test_cmd="pytest")
        barriers = configure_barriers(config.source_dirs)
        spec_hash = self._spec_hash(project)

        # Pre-write a manifest with WRONG hash.
        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = arch_dir / "my-spec-manifest.yaml"
        manifest_path.write_text(
            'version: 1\nspec: my-spec\nspec_hash: "wrong"\nlanguage: node\nfile_map: []\n'
            'test_integration:\n  module_system: esm\n'
        )

        mock_agent.side_effect = self._make_agent_writes_manifest(manifest_path, spec_hash)

        spec_run = SpecRun(spec_name="my-spec")
        spec_run.spec_hash = spec_hash
        _step_architecture_lock(
            spec_run, config, barriers, project,
            active_adapters=[], dry_run=True,
        )
        mock_agent.assert_called_once()

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    def test_fresh_deletes_manifest(self, mock_agent: MagicMock) -> None:
        """--fresh deletes existing manifest before regenerating."""
        from darkfactory.pipeline.orchestrator import _step_architecture_lock
        from darkfactory.docker.barriers import configure_barriers

        project = self._setup_project()
        config = PipelineConfig(test_cmd="pytest")
        barriers = configure_barriers(config.source_dirs)
        spec_hash = self._spec_hash(project)

        # Pre-write a manifest with matching hash (would normally skip).
        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = arch_dir / "my-spec-manifest.yaml"
        manifest_path.write_text(
            f'version: 1\nspec: my-spec\nspec_hash: "{spec_hash}"\nlanguage: node\nfile_map: []\n'
            f'test_integration:\n  module_system: esm\n'
        )

        mock_agent.side_effect = self._make_agent_writes_manifest(manifest_path, spec_hash)

        spec_run = SpecRun(spec_name="my-spec")
        spec_run.spec_hash = spec_hash
        _step_architecture_lock(
            spec_run, config, barriers, project,
            active_adapters=[], fresh=True, dry_run=True,
        )
        # Agent should be called because --fresh forces regeneration.
        mock_agent.assert_called_once()

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    def test_validation_failure_raises(self, mock_agent: MagicMock) -> None:
        """Manifest validation failure raises DarkFactoryError."""
        from darkfactory.pipeline.orchestrator import _step_architecture_lock
        from darkfactory.docker.barriers import configure_barriers

        project = self._setup_project()
        config = PipelineConfig(test_cmd="pytest")
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
                spec_run, config, barriers, project,
                active_adapters=[], dry_run=True,
            )

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    def test_prompt_injects_project_hints_consistency_rules(
        self, mock_agent: MagicMock,
    ) -> None:
        """Regression guard for the 2026-04-15 plumbing bug.

        The original defect: project-hints' ``[[policy.consistency_rules]]``
        entries were only checked *after* the architect ran (A1 gate),
        never surfaced in the architect's prompt. Wet run reproduced the
        bug — the architect emitted ``drizzle-kit push`` despite a
        project-hints rule forbidding it, because the rule wasn't in the
        prompt.

        This test writes a minimal project-hints.toml into the tmp
        project, invokes the architecture-lock step, and asserts the
        rule's name + forbidden-pattern + description all appear in the
        prompt passed to ``invoke_agent``.
        """
        from darkfactory.pipeline.orchestrator import _step_architecture_lock
        from darkfactory.docker.barriers import configure_barriers

        project = self._setup_project()
        config = PipelineConfig(test_cmd="pytest")
        barriers = configure_barriers(config.source_dirs)
        spec_hash = self._spec_hash(project)

        # Minimal but complete project-hints.toml — schema_version pinned
        # and one [[policy.consistency_rules]] block whose name and
        # forbidden_env patterns we can search for in the prompt.
        (project / ".dark-factory" / "project-hints.toml").write_text(
            'schema_version = 1\n'
            '\n'
            '[detected]\n'
            'language = "node"\n'
            '\n'
            '[[policy.consistency_rules]]\n'
            'name = "test-rule-migrate-vs-push"\n'
            'env_paths = ["commands.*"]\n'
            'spec_patterns = [\'\\bdb:migrate\\b\']\n'
            'forbidden_env = [\'\\bdrizzle-kit\\s+push\\b\']\n'
            'description = "test fixture: spec says migrate; env cannot push"\n'
        )

        # Set up the agent to write a valid manifest so the step returns
        # successfully — we only care about the prompt it received.
        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = arch_dir / "my-spec-manifest.yaml"
        mock_agent.side_effect = self._make_agent_writes_manifest(
            manifest_path, spec_hash,
        )

        spec_run = SpecRun(spec_name="my-spec")
        spec_run.spec_hash = spec_hash

        _step_architecture_lock(
            spec_run, config, barriers, project,
            active_adapters=[], dry_run=True,
        )

        mock_agent.assert_called_once()
        # The prompt is the second positional argument (role, prompt, ...).
        prompt = mock_agent.call_args.args[1]

        # The header tells the architect WHAT the section is for.
        self.assertIn("PROJECT POLICY CONSTRAINTS", prompt)
        # The rule name must appear — forensics hook + error-message
        # tie-back if A1 fires.
        self.assertIn("test-rule-migrate-vs-push", prompt)
        # The forbidden pattern must appear verbatim (regex form) —
        # this is what lets the architect actually avoid the violation.
        self.assertIn(r"\bdrizzle-kit\s+push\b", prompt)
        # The description must appear — the *why* the LLM uses to
        # generalize to near-miss cases the regex might not cover.
        self.assertIn("spec says migrate; env cannot push", prompt)

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    def test_fresh_unlinks_stale_substrate_manifest(
        self, mock_agent: MagicMock,
    ) -> None:
        """``--fresh`` must clean up the substrate manifest, not just the YAML.

        Regression guard for the round-2 hole Linus flagged on Piece 4
        of the substrate verification plan: if a spec edit removes the
        ``## Substrate Primitives`` block, the architect correctly stops
        emitting the substrate TOML, but the orchestrator's prior unlink
        only covered the YAML manifest. The stale TOML survived,
        ``_step_scenario_generation`` read ``is_file() == True`` and
        injected the scenario-architect addendum, and the Test Engineer
        generated substrate scenarios for entries the spec no longer
        demanded.
        """
        project = self._setup_project()
        config = PipelineConfig(test_cmd="pytest")
        from darkfactory.docker.barriers import configure_barriers
        barriers = configure_barriers(config.source_dirs)
        spec_hash = self._spec_hash(project)

        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = arch_dir / "my-spec-manifest.yaml"
        manifest_path.write_text(
            f'version: 1\nspec: my-spec\nspec_hash: "{spec_hash}"\n'
            f'language: node\nfile_map: []\n'
            f'test_integration:\n  module_system: esm\n'
        )
        # The stale substrate manifest from a prior run — the bug we're
        # guarding against. Content is irrelevant; the orchestrator only
        # checks ``is_file()``.
        substrate_dir = arch_dir / "substrate-manifests"
        substrate_dir.mkdir(parents=True, exist_ok=True)
        substrate_path = substrate_dir / "my-spec.toml"
        substrate_path.write_text("# stale substrate manifest from prior run\n")

        mock_agent.side_effect = self._make_agent_writes_manifest(
            manifest_path, spec_hash
        )

        spec_run = SpecRun(spec_name="my-spec")
        spec_run.spec_hash = spec_hash
        from darkfactory.pipeline.orchestrator import _step_architecture_lock
        _step_architecture_lock(
            spec_run, config, barriers, project,
            active_adapters=[], fresh=True, dry_run=True,
        )

        # The agent didn't re-emit a substrate TOML in our mock, so a
        # correct cleanup leaves the file gone (and a regression leaves
        # it on disk). That gap is the assertion.
        self.assertFalse(
            substrate_path.is_file(),
            "stale substrate manifest survived --fresh; the scenario "
            "step would falsely gate on it. Add the unlink to "
            "_unlink_architecture_artifacts in _step_architecture_lock."
        )

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    def test_stale_spec_hash_unlinks_substrate_manifest(
        self, mock_agent: MagicMock,
    ) -> None:
        """Same lifecycle, different trigger: spec edit changes the hash.

        The 'spec edited to drop ## Substrate Primitives' scenario hits
        this branch (line ~1149 in the orchestrator) rather than the
        --fresh branch. Both must clean up.
        """
        project = self._setup_project()
        config = PipelineConfig(test_cmd="pytest")
        from darkfactory.docker.barriers import configure_barriers
        barriers = configure_barriers(config.source_dirs)
        spec_hash = self._spec_hash(project)

        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = arch_dir / "my-spec-manifest.yaml"
        # Wrong hash — triggers the "stale, regenerate" branch.
        manifest_path.write_text(
            'version: 1\nspec: my-spec\nspec_hash: "wrong-hash"\n'
            'language: node\nfile_map: []\n'
            'test_integration:\n  module_system: esm\n'
        )
        substrate_dir = arch_dir / "substrate-manifests"
        substrate_dir.mkdir(parents=True, exist_ok=True)
        substrate_path = substrate_dir / "my-spec.toml"
        substrate_path.write_text("# stale substrate manifest from prior run\n")

        mock_agent.side_effect = self._make_agent_writes_manifest(
            manifest_path, spec_hash
        )

        spec_run = SpecRun(spec_name="my-spec")
        spec_run.spec_hash = spec_hash
        from darkfactory.pipeline.orchestrator import _step_architecture_lock
        _step_architecture_lock(
            spec_run, config, barriers, project,
            active_adapters=[], dry_run=True,
        )
        self.assertFalse(
            substrate_path.is_file(),
            "stale substrate manifest survived a spec_hash mismatch; "
            "_unlink_architecture_artifacts must cover this branch too."
        )


class TestCoverageFloorUntestableWaiver(TempDirTestCase):
    """@untestable markers must actually unblock the coverage floor."""

    def _setup(self) -> tuple[Path, Path, SpecRun]:
        import json as _json
        project = self.tmp_dir
        spec_name = "demo"
        (project / ".dark-factory" / "specs").mkdir(parents=True)
        (project / ".dark-factory" / "specs" / f"{spec_name}.md").write_text(
            "# Demo\n### FR-1\nAlpha\n### FR-5\nBeta\n",
            encoding="utf-8",
        )
        state_dir = project / ".dark-factory" / "state" / spec_name
        state_dir.mkdir(parents=True)
        (state_dir / "anchor_manifest.json").write_text(
            _json.dumps({
                "fr_ids": ["FR-1", "FR-5"],
                "ac_ids": [],
                "ec_ids": [],
                "declared_intents": {},
            }),
            encoding="utf-8",
        )
        scenarios_dir = project / ".dark-factory" / "scenarios" / spec_name
        scenarios_dir.mkdir(parents=True)
        (scenarios_dir / "a.test.js").write_text(
            "/** @covers FR-1 */\ndescribe('x', ()=>{});\n",
            encoding="utf-8",
        )
        spec_run = SpecRun(spec_name=spec_name, run_id="r1")
        return project, scenarios_dir, spec_run

    def test_untestable_marker_waives_uncovered_anchor(self) -> None:
        from darkfactory.pipeline.orchestrator import _enforce_coverage_floor

        project, scenarios_dir, spec_run = self._setup()
        config = PipelineConfig(test_cmd="pytest", coverage_max_regen_attempts=2)

        def fake_invoke(role, prompt, **kwargs):
            # Simulate the SA emitting an @untestable stub on retry-1.
            stub = scenarios_dir / "untestable.stub.js"
            stub.write_text(
                "// @untestable FR-5: spec is structurally underspecified\n",
                encoding="utf-8",
            )
            return AgentResult(role=role, exit_code=0)

        with patch(
            "darkfactory.pipeline.orchestrator.invoke_agent",
            side_effect=fake_invoke,
        ):
            # Must not raise — FR-5 is waived by the untestable marker.
            _enforce_coverage_floor(
                spec_run, config, {}, project,
                scenarios_dir=scenarios_dir,
                base_prompt="base",
                events_file=None,
                audit_path=None,
                dry_run=True,
            )

    def test_sa_infra_failure_raises_immediately(self) -> None:
        from darkfactory.pipeline.orchestrator import _enforce_coverage_floor

        project, scenarios_dir, spec_run = self._setup()
        config = PipelineConfig(test_cmd="pytest", coverage_max_regen_attempts=2)

        def fake_invoke(role, prompt, **kwargs):
            return AgentResult(role=role, exit_code=1)

        with patch(
            "darkfactory.pipeline.orchestrator.invoke_agent",
            side_effect=fake_invoke,
        ):
            with self.assertRaises(DarkFactoryError) as ctx:
                _enforce_coverage_floor(
                    spec_run, config, {}, project,
                    scenarios_dir=scenarios_dir,
                    base_prompt="base",
                    events_file=None,
                    audit_path=None,
                    dry_run=True,
                )
            self.assertIn("coverage regen attempt 1 failed", str(ctx.exception))


class TestComposeAdapterAddenda(unittest.TestCase):
    """Pin the consumer side of the empty-string opt-out sentinel.

    The contract harness pins the *adapter side*: an opt-out adapter
    returns ``""`` from ``spec_validator_addendum()``. Nothing pinned
    the *consumer side* — that the orchestrator drops empty strings on
    the floor instead of pasting them into the prompt as ``\\n\\n\\n\\n``
    separators. A future refactor of ``_compose_adapter_addenda`` that
    swapped ``if block:`` for ``if block is not None:`` would silently
    inflate every prompt by two blank lines per opt-out adapter; this
    test makes that regression load-bearing.
    """

    def test_opt_out_adapter_contributes_zero_bytes(self) -> None:
        from darkfactory.pipeline.orchestrator import _compose_adapter_addenda

        opt_out = MagicMock()
        opt_out.name = "opt-out"
        opt_out.spec_validator_addendum.return_value = ""

        out = _compose_adapter_addenda(
            [opt_out],
            lambda a: a.spec_validator_addendum(),
            log_label="spec_validator_addendum()",
        )
        self.assertEqual(out, "")

    def test_two_opt_out_adapters_still_contribute_zero_bytes(self) -> None:
        """The two-or-more-opt-out case is what actually trips the
        ``if block is not None`` regression. ``"\\n\\n".join([""])``
        returns ``""`` (single-element pass-through), so a single-opt-out
        test passes under both correct and buggy implementations.
        ``"\\n\\n".join(["", ""])`` returns ``"\\n\\n"`` — that's the
        regression this test pins.
        """
        from darkfactory.pipeline.orchestrator import _compose_adapter_addenda

        opt1 = MagicMock(); opt1.name = "opt1"
        opt1.spec_validator_addendum.return_value = ""
        opt2 = MagicMock(); opt2.name = "opt2"
        opt2.spec_validator_addendum.return_value = ""

        out = _compose_adapter_addenda(
            [opt1, opt2],
            lambda a: a.spec_validator_addendum(),
            log_label="spec_validator_addendum()",
        )
        self.assertEqual(out, "")

    def test_active_addendum_is_concatenated_in_order(self) -> None:
        from darkfactory.pipeline.orchestrator import _compose_adapter_addenda

        a1 = MagicMock(); a1.name = "a1"
        a2 = MagicMock(); a2.name = "a2"
        a1.architect_addendum.return_value = "first block\n"
        a2.architect_addendum.return_value = "second block\n"

        out = _compose_adapter_addenda(
            [a1, a2],
            lambda a: a.architect_addendum(),
            log_label="architect_addendum()",
        )
        self.assertEqual(out, "first block\n\nsecond block")

    def test_empty_addenda_are_filtered_between_real_blocks(self) -> None:
        from darkfactory.pipeline.orchestrator import _compose_adapter_addenda

        a1 = MagicMock(); a1.name = "a1"
        opt_out = MagicMock(); opt_out.name = "opt-out"
        a3 = MagicMock(); a3.name = "a3"
        a1.architect_addendum.return_value = "alpha"
        opt_out.architect_addendum.return_value = ""
        a3.architect_addendum.return_value = "gamma"

        out = _compose_adapter_addenda(
            [a1, opt_out, a3],
            lambda a: a.architect_addendum(),
            log_label="architect_addendum()",
        )
        self.assertEqual(out, "alpha\n\ngamma")

    def test_oserror_is_swallowed_other_adapters_still_contribute(self) -> None:
        from darkfactory.pipeline.orchestrator import _compose_adapter_addenda

        broken = MagicMock(); broken.name = "broken"
        broken.architect_addendum.side_effect = OSError("packaging bug")
        ok = MagicMock(); ok.name = "ok"
        ok.architect_addendum.return_value = "ok block"

        out = _compose_adapter_addenda(
            [broken, ok],
            lambda a: a.architect_addendum(),
            log_label="architect_addendum()",
        )
        self.assertEqual(out, "ok block")

    def test_programmer_errors_propagate(self) -> None:
        """``AttributeError`` / ``TypeError`` mean a broken adapter and
        must surface, not be hidden as 'continuing without contribution'.
        Pins the narrow ``OSError``-only catch."""
        from darkfactory.pipeline.orchestrator import _compose_adapter_addenda

        broken = MagicMock(); broken.name = "broken"
        broken.architect_addendum.side_effect = AttributeError("oops")

        with self.assertRaises(AttributeError):
            _compose_adapter_addenda(
                [broken],
                lambda a: a.architect_addendum(),
                log_label="architect_addendum()",
            )


class TestSpecValidationUnresolvedContractsRetry(TempDirTestCase):
    """Tests for _step_spec_validation unresolved_contracts retry paths."""

    def _setup(self) -> tuple[Path, SpecRun, PipelineConfig, Path]:
        from darkfactory.docker.barriers import configure_barriers
        project = self.tmp_dir
        (project / ".dark-factory" / "specs").mkdir(parents=True)
        (project / ".dark-factory" / "specs" / "my-spec.md").write_text("# Spec\n")
        reviews = project / ".dark-factory" / "reviews"
        reviews.mkdir(parents=True)
        report_path = reviews / "my-spec-validation.md"
        spec_run = SpecRun(spec_name="my-spec")
        config = PipelineConfig(test_cmd="pytest")
        # barriers not actually used by the mocked invoke_agent
        _ = configure_barriers(config.source_dirs)
        return project, spec_run, config, report_path

    def _vacuous_report(self) -> str:
        return (
            "# Report\n\n"
            "```yaml\n"
            "unresolved_contracts:\n"
            "  - surface: Interface Contract\n"
            "    section: 5. Interface Contract - envSchema\n"
            "```\n"
        )

    def _good_empty_report(self) -> str:
        return "# Report\n\n```yaml\nunresolved_contracts: []\n```\n"

    def _no_block_report(self) -> str:
        return "# Report\n\nno yaml here.\n"

    def _real_reason_report(self) -> str:
        return (
            "# Report\n\n"
            "```yaml\n"
            "unresolved_contracts:\n"
            "  - surface: Interface Contract\n"
            "    section: 5. Interface Contract - envSchema\n"
            "    sketches_diverged_on: 'details field is array vs object of keys'\n"
            "```\n"
        )

    def _run(self, project, spec_run, config, report_path, reports: list[str]):
        """Invoke _step_spec_validation with invoke_agent writing successive reports."""
        from darkfactory.pipeline.orchestrator import _step_spec_validation
        from darkfactory.docker.barriers import configure_barriers

        barriers = configure_barriers(config.source_dirs)
        calls = {"n": 0}

        def side_effect(*args, **kwargs):
            idx = calls["n"]
            calls["n"] += 1
            if idx < len(reports):
                report_path.write_text(reports[idx])
            return AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)

        with patch("darkfactory.pipeline.orchestrator.invoke_agent",
                   side_effect=side_effect) as mock_agent, \
             patch("darkfactory.pipeline.orchestrator.validate_spec_result",
                   return_value=(True, "PASS")):
            _step_spec_validation(
                spec_run, config, barriers, project, dry_run=False,
                    active_adapters=[],
            )
        return mock_agent

    def test_print_format_regression_no_question_mark(self) -> None:
        """Step 4.4: missing sketches_diverged_on must not print as ': ?'."""
        from darkfactory.pipeline.orchestrator import _step_spec_validation
        from darkfactory.docker.barriers import configure_barriers

        project, spec_run, config, report_path = self._setup()
        barriers = configure_barriers(config.source_dirs)

        # Both attempts return the vacuous (missing field) entry, so after
        # retry the orchestrator raises with the printed format.
        def side_effect(*args, **kwargs):
            report_path.write_text(self._vacuous_report())
            return AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)

        with patch("darkfactory.pipeline.orchestrator.invoke_agent",
                   side_effect=side_effect), \
             patch("darkfactory.pipeline.orchestrator.validate_spec_result",
                   return_value=(True, "PASS")):
            with self.assertRaises(DarkFactoryError) as ctx:
                _step_spec_validation(
                    spec_run, config, barriers, project, dry_run=False,
                    active_adapters=[],
                )
        msg = str(ctx.exception)
        # No bare ': ?' divergence value.
        self.assertNotIn(": ?", msg)
        # Explicit-missing sentinel present for the sketches_diverged_on field.
        self.assertIn("<no sketches_diverged_on field emitted by validator>", msg)
        # The existing `surface` / `section` fields are populated in the
        # vacuous report, so the explicit-missing sentinels for those fields
        # need not appear. Verify the print loop has replaced the bare "?"
        # defaults by ensuring none of "(?)" or " ? " sneak in.
        self.assertNotIn("(?)", msg)

    def test_print_format_surface_and_section_sentinels(self) -> None:
        """Step 4.4 (Neo addition): surface and section ? defaults replaced too."""
        from darkfactory.pipeline.orchestrator import _step_spec_validation
        from darkfactory.docker.barriers import configure_barriers

        project, spec_run, config, report_path = self._setup()
        barriers = configure_barriers(config.source_dirs)

        # Entry missing surface, section, AND sketches_diverged_on, but still
        # a dict so parse_unresolved_contracts yields a one-element list.
        report_text = (
            "```yaml\n"
            "unresolved_contracts:\n"
            "  - foo: bar\n"
            "```\n"
        )

        def side_effect(*args, **kwargs):
            report_path.write_text(report_text)
            return AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)

        with patch("darkfactory.pipeline.orchestrator.invoke_agent",
                   side_effect=side_effect), \
             patch("darkfactory.pipeline.orchestrator.validate_spec_result",
                   return_value=(True, "PASS")):
            with self.assertRaises(DarkFactoryError) as ctx:
                _step_spec_validation(
                    spec_run, config, barriers, project, dry_run=False,
                    active_adapters=[],
                )
        msg = str(ctx.exception)
        self.assertNotIn(": ?", msg)
        self.assertIn("<no surface field emitted by validator>", msg)
        self.assertIn("<no section field emitted by validator>", msg)
        self.assertIn("<no sketches_diverged_on field emitted by validator>", msg)

    def test_vacuous_retry_then_empty_proceeds(self) -> None:
        """Step 4.1: vacuous -> retry -> empty list -> proceed."""
        project, spec_run, config, report_path = self._setup()
        mock_agent = self._run(
            project, spec_run, config, report_path,
            [self._vacuous_report(), self._good_empty_report()],
        )
        self.assertEqual(mock_agent.call_count, 2)

    def test_vacuous_retry_still_vacuous_fails_loud(self) -> None:
        """Step 4.2: vacuous -> retry -> still vacuous -> raw output in error."""
        from darkfactory.pipeline.orchestrator import _step_spec_validation
        from darkfactory.docker.barriers import configure_barriers

        project, spec_run, config, report_path = self._setup()
        barriers = configure_barriers(config.source_dirs)

        def side_effect(*args, **kwargs):
            report_path.write_text(self._vacuous_report())
            return AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)

        with patch("darkfactory.pipeline.orchestrator.invoke_agent",
                   side_effect=side_effect), \
             patch("darkfactory.pipeline.orchestrator.validate_spec_result",
                   return_value=(True, "PASS")):
            with self.assertRaises(DarkFactoryError) as ctx:
                _step_spec_validation(
                    spec_run, config, barriers, project, dry_run=False,
                    active_adapters=[],
                )
        msg = str(ctx.exception)
        self.assertIn("Interface Contract", msg)
        self.assertIn("Raw validator output", msg)
        self.assertNotIn(": ?", msg)

    def test_vacuous_retry_block_dropped(self) -> None:
        """Step 4.3: vacuous -> retry drops block entirely -> distinct error."""
        from darkfactory.pipeline.orchestrator import _step_spec_validation
        from darkfactory.docker.barriers import configure_barriers

        project, spec_run, config, report_path = self._setup()
        barriers = configure_barriers(config.source_dirs)

        reports = [self._vacuous_report(), self._no_block_report()]
        calls = {"n": 0}

        def side_effect(*args, **kwargs):
            idx = calls["n"]
            calls["n"] += 1
            report_path.write_text(reports[idx])
            return AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)

        with patch("darkfactory.pipeline.orchestrator.invoke_agent",
                   side_effect=side_effect), \
             patch("darkfactory.pipeline.orchestrator.validate_spec_result",
                   return_value=(True, "PASS")):
            with self.assertRaises(DarkFactoryError) as ctx:
                _step_spec_validation(
                    spec_run, config, barriers, project, dry_run=False,
                    active_adapters=[],
                )
        self.assertIn("dropped the `unresolved_contracts:` block on the vacuous-reason retry",
                      str(ctx.exception))


class TestWriteInterruptedState(TempDirTestCase):
    """Verify _write_interrupted_state respects terminal pipeline statuses."""

    def _make_spec_run(self, status: PipelineStatus) -> SpecRun:
        sr = SpecRun(spec_name="test-spec")
        sr.status = status
        sr.iteration = 2
        sr.completed_step = 5
        return sr

    def test_writes_interrupted_when_running(self) -> None:
        state_file = self.tmp_dir / "test.state"
        state_file.write_text("status=running\n")
        sr = self._make_spec_run(PipelineStatus.RUNNING)
        _write_interrupted_state(state_file, sr)
        content = state_file.read_text()
        self.assertIn("status=interrupted", content)

    def test_skips_when_shipped(self) -> None:
        state_file = self.tmp_dir / "test.state"
        state_file.write_text("status=shipped\n")
        sr = self._make_spec_run(PipelineStatus.SHIPPED)
        _write_interrupted_state(state_file, sr)
        content = state_file.read_text()
        self.assertIn("status=shipped", content)
        self.assertNotIn("interrupted", content)

    def test_skips_when_deployed(self) -> None:
        state_file = self.tmp_dir / "test.state"
        state_file.write_text("status=deployed\n")
        sr = self._make_spec_run(PipelineStatus.DEPLOYED)
        _write_interrupted_state(state_file, sr)
        self.assertNotIn("interrupted", state_file.read_text())

    def test_skips_when_escalated(self) -> None:
        state_file = self.tmp_dir / "test.state"
        state_file.write_text("status=escalated\n")
        sr = self._make_spec_run(PipelineStatus.ESCALATED)
        _write_interrupted_state(state_file, sr)
        self.assertNotIn("interrupted", state_file.read_text())

    def test_skips_when_rolled_back(self) -> None:
        state_file = self.tmp_dir / "test.state"
        state_file.write_text("status=rolled_back\n")
        sr = self._make_spec_run(PipelineStatus.ROLLED_BACK)
        _write_interrupted_state(state_file, sr)
        self.assertNotIn("interrupted", state_file.read_text())

    def test_skips_when_aborted(self) -> None:
        state_file = self.tmp_dir / "test.state"
        state_file.write_text("status=aborted\n")
        sr = self._make_spec_run(PipelineStatus.ABORTED)
        _write_interrupted_state(state_file, sr)
        self.assertNotIn("interrupted", state_file.read_text())


if __name__ == "__main__":
    unittest.main()
