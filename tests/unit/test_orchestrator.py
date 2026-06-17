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
from darkfactory.pipeline.orchestrator import (
    run_pipeline, _handle_resume, _write_interrupted_state, _try_auto_resume,
)
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


def _stamp_yaml(spec_hash: str, names: list[str]) -> str:
    """Return the ``active_adapters`` + ``stamp_integrity`` YAML block
    that a real Move 3b stamp would produce for these inputs.

    Move 3b's cache gate rejects any manifest whose ``stamp_integrity``
    does not match ``_stamp_integrity(spec_hash, sorted_names)``. Pre-
    stamping test fixtures with a wrong integrity value silently
    flips positive-cache tests into the "unreadable" miss path, which
    is how this helper keeps the per-test hash computation honest —
    one definition, shared by every stamped fixture.
    """
    from darkfactory.pipeline.orchestrator import _stamp_integrity
    sorted_names = sorted(names)
    names_literal = "[" + ", ".join(sorted_names) + "]"
    integrity = _stamp_integrity(spec_hash, sorted_names)
    return (
        f"active_adapters: {names_literal}\n"
        f"stamp_integrity: \"{integrity}\"\n"
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
        """Write a minimal valid architecture manifest for the given spec.

        Move 3b: the cache gate keys on ``spec_hash`` + sorted
        ``active_adapters`` stamp + ``stamp_integrity`` binding both.
        Tests built on this helper rely on ``_setup_project`` having
        declared ``[typescript-node]`` via ``_write_active_adapters_hints``,
        so stamp the same value with a matching integrity — otherwise
        the cache gate treats the manifest as stale, unlinks it, and
        falls through to an architect invocation that these tests don't
        mock.
        """
        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        spec_file = project / ".dark-factory" / "specs" / f"{spec_name}.md"
        from darkfactory.utils.hash import sha256_file
        spec_hash = sha256_file(spec_file)
        (arch_dir / f"{spec_name}-manifest.yaml").write_text(
            f"version: 1\nspec: {spec_name}\nspec_hash: \"{spec_hash}\"\n"
            f"language: node\nfile_map: []\n"
            f"test_integration:\n  module_system: esm\n"
            + _stamp_yaml(spec_hash, ["typescript-node"])
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

    @patch("darkfactory.adapters.typescript.adapter.TypescriptNodeAdapter.check_workspace_consistency")
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

        After B-ii the orchestrator dispatches the check through
        ``EcosystemAdapter.check_workspace_consistency`` (the pnpm grammar
        moved out of the core), so the patch target is the adapter method
        rather than the old ``check_workspace_scripts`` core import.
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

    @patch("darkfactory.adapters.typescript.adapter.TypescriptNodeAdapter.check_workspace_consistency")
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
        """Feature specs still get the workspace drift check.

        After B-ii the check is dispatched through the active adapter's
        ``check_workspace_consistency`` hook; the patch target is the
        adapter method.
        """
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

    @patch("darkfactory.spec.precheck.run_precheck")
    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    @patch("darkfactory.pipeline.orchestrator.validate_spec_result")
    @patch("darkfactory.pipeline.orchestrator.require_docker")
    @patch("darkfactory.pipeline.orchestrator.setup_networks")
    @patch("darkfactory.pipeline.orchestrator.setup_proxies")
    @patch("darkfactory.pipeline.orchestrator.run_iteration_loop")
    @patch("darkfactory.pipeline.orchestrator.notify_escalated")
    @patch("darkfactory.pipeline.orchestrator.notify_shipped")
    @patch("darkfactory.pipeline.orchestrator.registry_register")
    @patch("darkfactory.pipeline.orchestrator.record_shipped_spec")
    @patch("darkfactory.pipeline.orchestrator.has_scenario_files", return_value=True)
    def test_stable_failure_path(
        self,
        mock_scenarios: MagicMock,
        mock_record_shipped: MagicMock,
        mock_registry: MagicMock,
        mock_notify_shipped: MagicMock,
        mock_notify_escalated: MagicMock,
        mock_loop: MagicMock,
        mock_proxies: MagicMock,
        mock_networks: MagicMock,
        mock_docker: MagicMock,
        mock_validate: MagicMock,
        mock_agent: MagicMock,
        mock_precheck: MagicMock,
    ) -> None:
        """Sprint 1 T1.4: a STABLE_FAILURE run exits 5, writes NO shipped.jsonl
        row, fires no ship/escalation side effects, and emits a distinct
        SPEC_STABLE_FAILURE event (never SPEC_SHIPPED)."""
        project = self._setup_project()
        config = PipelineConfig(test_cmd="pytest")
        self._write_stub_manifest(project)
        events_file = project / ".dark-factory" / "events.jsonl"

        mock_precheck.return_value = MagicMock(passed=True, fatal=False, ambiguities=[], summary="ok")
        mock_agent.return_value = AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)
        mock_validate.return_value = (True, "PASS")

        def _loop_side_effect(spec_run, *args, **kwargs):
            spec_run.stable_failure = True
            return False  # not shipped
        mock_loop.side_effect = _loop_side_effect

        exit_code = run_pipeline("my-spec", config, project, events_file=events_file)

        self.assertEqual(exit_code, 5)
        # No ship side effects.
        mock_notify_shipped.assert_not_called()
        mock_registry.assert_not_called()
        mock_record_shipped.assert_not_called()
        # Not an escalation either.
        mock_notify_escalated.assert_not_called()
        # No durable shipped.jsonl row (the integrity guarantee).
        shipped_jsonl = project / ".dark-factory" / "plan" / "shipped.jsonl"
        self.assertFalse(
            shipped_jsonl.exists() and shipped_jsonl.read_text().strip(),
            "STABLE_FAILURE must not append to shipped.jsonl",
        )
        # Distinct terminal event, never SPEC_SHIPPED.
        events = [
            json.loads(line)
            for line in events_file.read_text().strip().splitlines() if line
        ]
        names = [e.get("event") for e in events]
        self.assertIn("SPEC_STABLE_FAILURE", names)
        self.assertNotIn("SPEC_SHIPPED", names)

    def test_resume_treats_stable_failure_as_terminal(self) -> None:
        """Hole A: a stable_failure state must not be resumable (else --resume
        re-enters the loop and a second Oracle pass could SHIP)."""
        state_file = self.tmp_dir / "spec.state"
        state_file.write_text("status=stable_failure\nspec_hash=abc\ncompleted_step=3\n")
        spec_run = SpecRun(spec_name="test")
        spec_run.spec_hash = "abc"
        # Non-explicit resume: terminal → do not resume.
        self.assertFalse(_try_auto_resume(spec_run, state_file, explicit_resume=False))
        # Explicit resume: terminal → raise (use --fresh).
        with self.assertRaises(DarkFactoryError):
            _try_auto_resume(spec_run, state_file, explicit_resume=True)


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
        # TestRunPipeline — avoids needing the architect agent). The
        # Move 3b adapter-set stamp + integrity must match the active
        # adapter list this run will compute: ``no_adapter=True`` below
        # produces ``active_adapters=[]``, so the stamp is the empty
        # list and integrity binds it to spec_hash.
        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        from darkfactory.utils.hash import sha256_file
        spec_hash = sha256_file(project / ".dark-factory" / "specs" / "my-spec.md")
        (arch_dir / "my-spec-manifest.yaml").write_text(
            f"version: 1\nspec: my-spec\nspec_hash: \"{spec_hash}\"\n"
            f"language: node\nfile_map: []\n"
            f"test_integration:\n  module_system: esm\n"
            + _stamp_yaml(spec_hash, [])
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

        # Pre-write a manifest with matching hash. Move 3b added an
        # ``active_adapters`` + ``stamp_integrity`` stamp pair — a
        # manifest missing either (or with mismatched integrity) is
        # treated as stale so pre-3b manifests get one forced
        # regenerate at upgrade time. Stamp matches the empty
        # ``active_adapters=[]`` call below.
        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        (arch_dir / "my-spec-manifest.yaml").write_text(
            f'version: 1\nspec: my-spec\nspec_hash: "{spec_hash}"\nlanguage: node\nfile_map: []\n'
            f'test_integration:\n  module_system: esm\n'
            + _stamp_yaml(spec_hash, [])
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

    # -----------------------------------------------------------------
    # Move 3b — architect cache hardening
    #
    # The 2026-04-16 remediation plan introduces two additional cache-
    # invalidation axes on top of ``spec_hash``:
    #   (1) adapter-set drift — the cached manifest was built against a
    #       different ``active_adapters`` set, so per-adapter consistency
    #       rules, addenda, and substrate behavior no longer match.
    #   (2) substrate-manifest absence — some active adapter's
    #       ``spec_triggers(spec_text)`` fires (the spec demands that
    #       adapter's substrate), but the substrate TOML is missing on
    #       disk so scenario-gen would silently run without wire-contract
    #       coverage — the exact 2026-04-16 08:48 failure mode.
    # These tests pin both axes and the ``ARCHITECT_CACHE_INVALIDATED``
    # audit-event emission that makes the invalidation observable.
    # -----------------------------------------------------------------

    def _make_adapter_mock(
        self,
        name: str,
        *,
        spec_triggers: bool = False,
    ) -> MagicMock:
        """Build a stand-in adapter mock with just the surface the
        architecture-lock step touches. ``name`` can't go through the
        ``MagicMock(...)`` kwarg because MagicMock treats ``name`` as
        repr metadata — it must be assigned after construction.
        """
        adapter = MagicMock()
        adapter.name = name
        adapter.spec_triggers = MagicMock(return_value=spec_triggers)
        adapter.consistency_rules = MagicMock(return_value=[])
        adapter.architect_addendum = MagicMock(return_value="")
        return adapter

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    def test_cache_miss_on_adapter_set_drift(
        self, mock_agent: MagicMock,
    ) -> None:
        """A cached manifest whose ``active_adapters`` stamp differs
        from the current run's adapter set is stale. Regenerate, don't
        reuse.

        This is the 2026-04-16 class of bug: postgres activates at
        run-start via ``spec_triggers`` but the cached manifest was
        built when only typescript-node was active — reusing it runs
        the pipeline without postgres's architect addendum, consistency
        rules, or substrate manifest."""
        from darkfactory.pipeline.orchestrator import _step_architecture_lock
        from darkfactory.docker.barriers import configure_barriers

        project = self._setup_project()
        config = PipelineConfig(test_cmd="pytest")
        barriers = configure_barriers(config.source_dirs)
        spec_hash = self._spec_hash(project)

        # Stamp records ONLY typescript-node — run now has postgres too.
        # Integrity matches the stamped list + spec_hash so the drift
        # fails specifically on the adapter-set axis (not the
        # integrity axis, which would mask the signal under test).
        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = arch_dir / "my-spec-manifest.yaml"
        manifest_path.write_text(
            f'version: 1\nspec: my-spec\nspec_hash: "{spec_hash}"\n'
            f'language: node\nfile_map: []\n'
            f'test_integration:\n  module_system: esm\n'
            + _stamp_yaml(spec_hash, ["typescript-node"])
        )

        mock_agent.side_effect = self._make_agent_writes_manifest(
            manifest_path, spec_hash,
        )

        ts = self._make_adapter_mock("typescript-node")
        pg = self._make_adapter_mock("postgres", spec_triggers=True)

        spec_run = SpecRun(spec_name="my-spec")
        spec_run.spec_hash = spec_hash
        _step_architecture_lock(
            spec_run, config, barriers, project,
            active_adapters=[ts, pg], dry_run=True,
        )
        # Drift must force a regenerate — architect is invoked.
        mock_agent.assert_called_once()

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    def test_cache_miss_on_missing_substrate_when_required(
        self, mock_agent: MagicMock,
    ) -> None:
        """If any active adapter's ``spec_triggers(spec_text)`` is True
        and the substrate TOML is missing, the cache is stale even when
        ``spec_hash`` and ``active_adapters`` both match.

        Without this check, a run that wiped
        ``substrate-manifests/<spec>.toml`` (half-cleanup, operator
        error, or --regen-scenarios failure mid-flight) would replay
        the cached manifest and proceed without substrate coverage."""
        from darkfactory.pipeline.orchestrator import _step_architecture_lock
        from darkfactory.docker.barriers import configure_barriers

        project = self._setup_project()
        config = PipelineConfig(test_cmd="pytest")
        barriers = configure_barriers(config.source_dirs)
        spec_hash = self._spec_hash(project)

        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = arch_dir / "my-spec-manifest.yaml"
        # Stamp (+ integrity) matches the adapter set below. Substrate
        # TOML is intentionally absent — that's the invariant under
        # test.
        manifest_path.write_text(
            f'version: 1\nspec: my-spec\nspec_hash: "{spec_hash}"\n'
            f'language: node\nfile_map: []\n'
            f'test_integration:\n  module_system: esm\n'
            + _stamp_yaml(spec_hash, ["postgres", "typescript-node"])
        )

        mock_agent.side_effect = self._make_agent_writes_manifest(
            manifest_path, spec_hash,
        )

        ts = self._make_adapter_mock("typescript-node")
        pg = self._make_adapter_mock("postgres", spec_triggers=True)

        spec_run = SpecRun(spec_name="my-spec")
        spec_run.spec_hash = spec_hash
        _step_architecture_lock(
            spec_run, config, barriers, project,
            active_adapters=[ts, pg], dry_run=True,
        )
        mock_agent.assert_called_once()

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    def test_cache_hit_when_stamps_match_and_no_substrate_required(
        self, mock_agent: MagicMock,
    ) -> None:
        """Positive cache hit: ``spec_hash`` matches, adapter stamp
        matches sorted current-run names, and no active adapter needs
        substrate → architect is NOT invoked.

        Asymmetry with the two miss tests above pins which combinations
        allow reuse — a regression that accidentally over-invalidates
        burns an architect run every time."""
        from darkfactory.pipeline.orchestrator import _step_architecture_lock
        from darkfactory.docker.barriers import configure_barriers

        project = self._setup_project()
        config = PipelineConfig(test_cmd="pytest")
        barriers = configure_barriers(config.source_dirs)
        spec_hash = self._spec_hash(project)

        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        (arch_dir / "my-spec-manifest.yaml").write_text(
            f'version: 1\nspec: my-spec\nspec_hash: "{spec_hash}"\n'
            f'language: node\nfile_map: []\n'
            f'test_integration:\n  module_system: esm\n'
            + _stamp_yaml(spec_hash, ["typescript-node"])
        )

        ts = self._make_adapter_mock("typescript-node")

        spec_run = SpecRun(spec_name="my-spec")
        spec_run.spec_hash = spec_hash
        _step_architecture_lock(
            spec_run, config, barriers, project,
            active_adapters=[ts], dry_run=True,
        )
        mock_agent.assert_not_called()

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    def test_stamp_active_adapters_into_manifest_after_architect_run(
        self, mock_agent: MagicMock,
    ) -> None:
        """After a fresh architect run, the orchestrator stamps
        ``active_adapters: [sorted names]`` + matching
        ``stamp_integrity`` into the manifest YAML host-side. The
        architect doesn't own either field — adapter identity is
        orchestrator-computed (A5 registry + G1 ``spec_triggers``),
        and making the architect write it would risk hallucinated
        names silently tricking the cache gate."""
        import yaml as _yaml
        from darkfactory.pipeline.orchestrator import (
            _step_architecture_lock,
            _stamp_integrity,
        )
        from darkfactory.docker.barriers import configure_barriers

        project = self._setup_project()
        config = PipelineConfig(test_cmd="pytest")
        barriers = configure_barriers(config.source_dirs)
        spec_hash = self._spec_hash(project)

        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = arch_dir / "my-spec-manifest.yaml"

        mock_agent.side_effect = self._make_agent_writes_manifest(
            manifest_path, spec_hash,
        )

        # Deliberately out-of-order input — the stamp must be sorted
        # so two runs with the same set in different declaration
        # orders both cache-hit against each other.
        pg = self._make_adapter_mock("postgres")
        ts = self._make_adapter_mock("typescript-node")

        spec_run = SpecRun(spec_name="my-spec")
        spec_run.spec_hash = spec_hash
        _step_architecture_lock(
            spec_run, config, barriers, project,
            active_adapters=[pg, ts], dry_run=True,
        )

        stamped = _yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            stamped.get("active_adapters"),
            ["postgres", "typescript-node"],
            "active_adapters stamp must be sorted so declaration order "
            "is not a cache-invalidation axis",
        )
        # Integrity must be written alongside the stamp — missing
        # integrity is what the cache gate uses to reject hand-edited
        # stamps, so a stamp without integrity is worse than no stamp.
        self.assertEqual(
            stamped.get("stamp_integrity"),
            _stamp_integrity(spec_hash, ["postgres", "typescript-node"]),
            "stamp_integrity must bind the sorted adapter list to the "
            "spec_hash",
        )

    @patch(
        "darkfactory.pipeline.orchestrator._apply_manifest_to_environment"
    )
    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    def test_manifest_not_stamped_when_apply_fails(
        self,
        mock_agent: MagicMock,
        mock_apply: MagicMock,
    ) -> None:
        """Stamp-after-validate ordering guard.

        If ``_apply_manifest_to_environment`` raises (e.g. A1
        consistency rules fire on an illegal environment delta), the
        manifest on disk must NOT carry a Move 3b stamp. The stamp is
        the orchestrator's certificate that the manifest passed every
        post-architect validation; stamping before validation would
        let an unvalidated manifest pose as cache-hit material on the
        next run, which is the exact failure mode Linus flagged on
        the Move 3b review.
        """
        import yaml as _yaml
        from darkfactory.pipeline.orchestrator import _step_architecture_lock
        from darkfactory.docker.barriers import configure_barriers

        project = self._setup_project()
        config = PipelineConfig(test_cmd="pytest")
        barriers = configure_barriers(config.source_dirs)
        spec_hash = self._spec_hash(project)

        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = arch_dir / "my-spec-manifest.yaml"

        mock_agent.side_effect = self._make_agent_writes_manifest(
            manifest_path, spec_hash,
        )
        mock_apply.side_effect = DarkFactoryError(
            "simulated consistency-rule violation"
        )

        ts = self._make_adapter_mock("typescript-node")

        spec_run = SpecRun(spec_name="my-spec")
        spec_run.spec_hash = spec_hash
        with self.assertRaises(DarkFactoryError):
            _step_architecture_lock(
                spec_run, config, barriers, project,
                active_adapters=[ts], dry_run=True,
            )

        # The manifest is still on disk (we don't unlink the
        # fresh-architect output on apply failure — the exception
        # propagates and the caller decides), but it must carry NO
        # Move 3b stamp. The next run will then classify it as
        # ``adapter_set_drift`` (stamp absent) and regenerate, which
        # is the correct recovery.
        self.assertTrue(manifest_path.exists())
        parsed = _yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        self.assertNotIn(
            "active_adapters", parsed,
            "stamp leaked onto a manifest that failed validation — "
            "the cache gate would incorrectly trust it next run",
        )
        self.assertNotIn("stamp_integrity", parsed)

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    def test_architect_cache_invalidated_event_on_adapter_drift(
        self, mock_agent: MagicMock,
    ) -> None:
        """Every cache invalidation emits
        ``ARCHITECT_CACHE_INVALIDATED`` with a ``reason`` tag. This is
        what makes a silent regenerate (burned architect cost, no
        surface signal) observable in post-run audits."""
        import json as _json
        from darkfactory.pipeline.orchestrator import _step_architecture_lock
        from darkfactory.docker.barriers import configure_barriers

        project = self._setup_project()
        config = PipelineConfig(test_cmd="pytest")
        barriers = configure_barriers(config.source_dirs)
        spec_hash = self._spec_hash(project)

        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = arch_dir / "my-spec-manifest.yaml"
        # Stamp drifts from the adapter set below → cache miss on the
        # adapter-set axis. Integrity binds the stamped list + hash so
        # the miss is not attributed to the unreadable axis by
        # accident.
        manifest_path.write_text(
            f'version: 1\nspec: my-spec\nspec_hash: "{spec_hash}"\n'
            f'language: node\nfile_map: []\n'
            f'test_integration:\n  module_system: esm\n'
            + _stamp_yaml(spec_hash, ["typescript-node"])
        )
        mock_agent.side_effect = self._make_agent_writes_manifest(
            manifest_path, spec_hash,
        )

        events_file = project / ".dark-factory" / "events.jsonl"

        ts = self._make_adapter_mock("typescript-node")
        pg = self._make_adapter_mock("postgres", spec_triggers=True)

        spec_run = SpecRun(spec_name="my-spec", run_id="r1")
        spec_run.spec_hash = spec_hash
        _step_architecture_lock(
            spec_run, config, barriers, project,
            active_adapters=[ts, pg],
            events_file=events_file,
            dry_run=True,
        )

        # Find the emitted event. File is JSONL; scan for our type.
        self.assertTrue(events_file.exists(), "events file was not written")
        matches = [
            _json.loads(line)
            for line in events_file.read_text().splitlines()
            if line.strip()
            and _json.loads(line).get("event") == "ARCHITECT_CACHE_INVALIDATED"
        ]
        self.assertEqual(
            len(matches), 1,
            "expected exactly one ARCHITECT_CACHE_INVALIDATED event on "
            "adapter-set drift",
        )
        payload = matches[0].get("data", {})
        self.assertEqual(payload.get("reason"), "adapter_set_drift")
        # Reason alone is not enough for forensics — the prior+current
        # adapter sets let an auditor reconstruct "what changed"
        # without diffing git history.
        self.assertEqual(payload.get("prior_adapters"), ["typescript-node"])
        self.assertEqual(
            payload.get("current_adapters"),
            ["postgres", "typescript-node"],
        )

    @patch("darkfactory.pipeline.orchestrator.invoke_agent")
    def test_architect_cache_invalidated_event_on_substrate_missing(
        self, mock_agent: MagicMock,
    ) -> None:
        """Substrate-missing-when-required emits its own distinct
        ``reason`` so "stamp drifted" vs "substrate got wiped" don't
        blur together in cache-invalidation telemetry."""
        import json as _json
        from darkfactory.pipeline.orchestrator import _step_architecture_lock
        from darkfactory.docker.barriers import configure_barriers

        project = self._setup_project()
        config = PipelineConfig(test_cmd="pytest")
        barriers = configure_barriers(config.source_dirs)
        spec_hash = self._spec_hash(project)

        arch_dir = project / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = arch_dir / "my-spec-manifest.yaml"
        manifest_path.write_text(
            f'version: 1\nspec: my-spec\nspec_hash: "{spec_hash}"\n'
            f'language: node\nfile_map: []\n'
            f'test_integration:\n  module_system: esm\n'
            + _stamp_yaml(spec_hash, ["postgres", "typescript-node"])
        )
        mock_agent.side_effect = self._make_agent_writes_manifest(
            manifest_path, spec_hash,
        )

        events_file = project / ".dark-factory" / "events.jsonl"

        ts = self._make_adapter_mock("typescript-node")
        pg = self._make_adapter_mock("postgres", spec_triggers=True)

        spec_run = SpecRun(spec_name="my-spec", run_id="r1")
        spec_run.spec_hash = spec_hash
        _step_architecture_lock(
            spec_run, config, barriers, project,
            active_adapters=[ts, pg],
            events_file=events_file,
            dry_run=True,
        )

        matches = [
            _json.loads(line)
            for line in events_file.read_text().splitlines()
            if line.strip()
            and _json.loads(line).get("event") == "ARCHITECT_CACHE_INVALIDATED"
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(
            matches[0].get("data", {}).get("reason"),
            "substrate_manifest_missing",
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
        # These tests exercise the LEGACY hard-gate machinery (re-prompt /
        # vacuous-retry / raise), which is now opt-in (F, 2026-06-16). Enable it
        # explicitly; the advisory default is covered by
        # TestUnresolvedContractsAdvisoryDefault below.
        config = PipelineConfig(
            test_cmd="pytest", unresolved_contracts_hard_gate=True,
        )
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


class TestUnresolvedContractsAdvisoryDefault(TempDirTestCase):
    """F (2026-06-16): with the default config the LLM unresolved_contracts
    check is ADVISORY — real divergences are logged + recorded but do NOT abort
    the run, and the validator is NOT re-prompted. The legacy hard gate is
    opt-in (unresolved_contracts_hard_gate=true, exercised by
    TestSpecValidationUnresolvedContractsRetry)."""

    def _setup(self, *, hard_gate: bool):
        from darkfactory.docker.barriers import configure_barriers
        project = self.tmp_dir
        (project / ".dark-factory" / "specs").mkdir(parents=True)
        (project / ".dark-factory" / "specs" / "my-spec.md").write_text("# Spec\n")
        reviews = project / ".dark-factory" / "reviews"
        reviews.mkdir(parents=True)
        report_path = reviews / "my-spec-validation.md"
        # A REAL, non-vacuous unresolved_contracts entry — the exact shape that
        # hard-aborted run 2f013c56.
        report_path.write_text(
            "# Report\n\n```yaml\nunresolved_contracts:\n"
            "  - surface: Interface Contract\n"
            "    section: 5. Interface Contract - envSchema\n"
            "    sketches_diverged_on: 'details field is array vs object'\n"
            "```\n",
            encoding="utf-8",
        )
        spec_run = SpecRun(spec_name="my-spec")
        config = PipelineConfig(
            test_cmd="pytest", unresolved_contracts_hard_gate=hard_gate,
        )
        barriers = configure_barriers(config.source_dirs)
        return project, spec_run, config, barriers, reviews

    def _invoke(self, project, spec_run, config, barriers):
        from darkfactory.pipeline.orchestrator import _step_spec_validation
        agent = MagicMock(
            return_value=AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)
        )
        with patch("darkfactory.pipeline.orchestrator.invoke_agent", agent), \
             patch("darkfactory.pipeline.orchestrator.validate_spec_result",
                   return_value=(True, "PASS")):
            _step_spec_validation(
                spec_run, config, barriers, project, dry_run=False,
                active_adapters=[],
            )
        return agent

    def test_advisory_default_does_not_raise_on_real_contracts(self):
        project, spec_run, config, barriers, reviews = self._setup(hard_gate=False)
        # Must not raise (the bug: it used to abort the whole run).
        self._invoke(project, spec_run, config, barriers)
        # Advisory note recorded beside the validation report.
        advisory = reviews / "my-spec-unresolved-contracts-advisory.md"
        self.assertTrue(advisory.exists())
        self.assertIn("ADVISORY", advisory.read_text())
        self.assertIn("Interface Contract", advisory.read_text())

    def test_advisory_default_does_not_reprompt(self):
        project, spec_run, config, barriers, reviews = self._setup(hard_gate=False)
        # A VACUOUS entry (no sketches_diverged_on) — under the HARD gate this
        # triggers a re-prompt (a 2nd invoke_agent). In advisory mode the
        # re-prompt machinery is skipped entirely.
        (reviews / "my-spec-validation.md").write_text(
            "# Report\n\n```yaml\nunresolved_contracts:\n"
            "  - surface: Interface Contract\n"
            "    section: 5. Interface Contract - envSchema\n"
            "```\n",
            encoding="utf-8",
        )
        agent = self._invoke(project, spec_run, config, barriers)
        # Exactly ONE call — the initial validation. No re-prompt was issued
        # (the re-prompt machinery only exists to clean a HARD gate's signal).
        self.assertEqual(agent.call_count, 1)

    def test_hard_gate_true_still_raises_on_real_contracts(self):
        from darkfactory.errors import DarkFactoryError
        project, spec_run, config, barriers, _ = self._setup(hard_gate=True)
        with self.assertRaises(DarkFactoryError) as ctx:
            self._invoke(project, spec_run, config, barriers)
        self.assertIn("unresolved contracts", str(ctx.exception))


class TestSpecValidationFreeVariablesAudit(TempDirTestCase):
    """The Free Variables misclassification audit is SPEC-authoritative.

    Regression: it previously audited the validator's free-form report, so a
    non-deterministic LLM report that invented a Free Variables section with a
    wire-contract row produced a hallucinated abort (python notes-crud,
    2026-06-11 — same spec passed on a prior run). It now audits the author's
    spec; the validator's report no longer gates here.
    """

    def _setup(self, spec_body: str):
        from darkfactory.docker.barriers import configure_barriers
        project = self.tmp_dir
        (project / ".dark-factory" / "specs").mkdir(parents=True)
        (project / ".dark-factory" / "specs" / "my-spec.md").write_text(
            spec_body, encoding="utf-8")
        (project / ".dark-factory" / "reviews").mkdir(parents=True)
        report_path = project / ".dark-factory" / "reviews" / "my-spec-validation.md"
        spec_run = SpecRun(spec_name="my-spec")
        config = PipelineConfig(test_cmd="pytest")
        configure_barriers(config.source_dirs)
        return project, spec_run, config, report_path

    def _run(self, project, spec_run, config, report_path, report_text):
        from darkfactory.pipeline.orchestrator import _step_spec_validation
        from darkfactory.docker.barriers import configure_barriers
        barriers = configure_barriers(config.source_dirs)

        def side_effect(*args, **kwargs):
            report_path.write_text(report_text, encoding="utf-8")
            return AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0)

        with patch("darkfactory.pipeline.orchestrator.invoke_agent",
                   side_effect=side_effect), \
             patch("darkfactory.pipeline.orchestrator.validate_spec_result",
                   return_value=(True, "PASS")):
            _step_spec_validation(
                spec_run, config, barriers, project, dry_run=False,
                active_adapters=[],
            )

    # A validator report that hallucinates a Free Variables section with a
    # wire-contract row ("response body" is a SHAPE_TOKEN). unresolved_contracts
    # is empty so the run reaches the FV audit.
    _HALLUCINATED_REPORT = (
        "# Report\n\n```yaml\nunresolved_contracts: []\n```\n\n"
        "## Free Variables\n\n"
        "| Requirement | What Is Free |\n"
        "|---|---|\n"
        "| FR-4: Delete note | HTTP 204/404 status, empty response body |\n"
    )

    def test_hallucinated_report_fv_does_not_abort(self) -> None:
        # Spec has NO Free Variables section; the report invents one. Spec-
        # authoritative audit must NOT abort. (Pre-fix: this aborted.)
        project, spec_run, config, report_path = self._setup(
            "# Spec\n\n## 1. Problem\nIn-memory notes CRUD.\n")
        self._run(project, spec_run, config, report_path, self._HALLUCINATED_REPORT)
        # No DarkFactoryError raised → pass.

    def test_author_misclassified_fv_still_aborts(self) -> None:
        # True positive preserved: the SPEC author tabled a wire contract as a
        # free variable → still a hard abort, even with a clean report.
        spec = (
            "# Spec\n\n## 1. Problem\nNotes.\n\n"
            "## 9. Free Variables\n\n"
            "| ID | What Is Free |\n|---|---|\n"
            "| FV-1 | the response body shape on delete |\n"
        )
        project, spec_run, config, report_path = self._setup(spec)
        with self.assertRaises(DarkFactoryError) as ctx:
            self._run(project, spec_run, config, report_path,
                      "# Report\n\n```yaml\nunresolved_contracts: []\n```\n")
        self.assertIn("Free Variables", str(ctx.exception))


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
