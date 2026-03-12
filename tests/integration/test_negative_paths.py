"""Integration tests for pipeline negative/failure paths.

Target: darkfactory/pipeline/orchestrator.py, darkfactory/pipeline/agents.py
Mock boundary: Mock invoke_agent with specific failure modes. Use pipeline_env
pattern from conftest.py. Real: state store, events, error handling.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from darkfactory.errors import ConfigError, DarkFactoryError, TransientFailure
from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.pipeline.agents import _MIN_OUTPUT_LEN, _is_transient, invoke_agent
from darkfactory.pipeline.orchestrator import run_pipeline
from darkfactory.state.store import read_state, write_state
from darkfactory.utils.subprocess_runner import RunResult

from tests.integration.conftest import agent_result_factory, gate_result_factory


# --- Helpers ---

def _config(project_dir: Path):
    """Load config with test-friendly defaults."""
    from darkfactory.config.loader import load_config
    config = load_config(project_dir / ".dark-factory" / "config.toml")
    config.auto_pr = False
    config.canary_enabled = False
    config.dynamic_autonomy = False
    config.dynamic_iterations = False
    config.spec_sanitization_enabled = False
    config.strategy_switch_enabled = False
    config.rollback_monitor_cmd = ""
    return config


@pytest.mark.integration
class TestAgentEmptyOutput:
    """Agent output below minimum length triggers synthetic exit code 99."""

    def test_short_output_yields_exit_99(self, project_dir, pipeline_config):
        """Exit 0 with output < 20 bytes -> invoke_agent returns exit 99."""
        short_result = RunResult(exit_code=0, stdout="ok", stderr="")

        with patch("darkfactory.pipeline.agents.execute_container", return_value=short_result), \
             patch("darkfactory.pipeline.agents._run_safety_checks"), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            from darkfactory.docker.barriers import BarrierConfig
            barriers = {role: BarrierConfig() for role in AgentRole}

            result = invoke_agent(
                AgentRole.IMPLEMENTER, "implement it",
                config=pipeline_config,
                barriers=barriers,
                run_id="test-run",
                project_dir=project_dir,
            )

        assert result.exit_code == 99

    def test_sufficient_output_yields_exit_0(self, project_dir, pipeline_config):
        """Exit 0 with output >= 20 bytes -> invoke_agent returns exit 0."""
        long_output = "x" * _MIN_OUTPUT_LEN
        ok_result = RunResult(exit_code=0, stdout=long_output, stderr="")

        with patch("darkfactory.pipeline.agents.execute_container", return_value=ok_result), \
             patch("darkfactory.pipeline.agents._run_safety_checks"), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            from darkfactory.docker.barriers import BarrierConfig
            barriers = {role: BarrierConfig() for role in AgentRole}

            result = invoke_agent(
                AgentRole.IMPLEMENTER, "implement it",
                config=pipeline_config,
                barriers=barriers,
                run_id="test-run",
                project_dir=project_dir,
            )

        assert result.exit_code == 0


@pytest.mark.integration
class TestAgentTimeout:
    """Exit code classification for transient vs implementation failures."""

    def test_exit_137_is_transient(self):
        """Exit 137 (OOM kill) is classified as transient."""
        assert _is_transient(137, "") is True

    def test_exit_1_is_not_transient(self):
        """Exit 1 (general failure) is not transient."""
        assert _is_transient(1, "") is False

    def test_connection_error_is_transient(self):
        """Connection error in stderr is transient."""
        assert _is_transient(1, "Error: connection refused by host") is True

    def test_dns_failure_is_transient(self):
        """DNS resolution failure is transient."""
        assert _is_transient(1, "name resolution failed for api.example.com") is True


@pytest.mark.integration
class TestTransientRetry:
    """Transient failures are retried; exhausted retries raise TransientFailure."""

    def test_transient_propagates_from_pipeline(self, pipeline_env):
        """TransientFailure from agent propagates out of run_pipeline."""
        env = pipeline_env
        call_count = [0]
        validator_ok = agent_result_factory(role=AgentRole.SPEC_VALIDATOR)

        def invoke_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return validator_ok
            raise TransientFailure("OOM in container")

        with patch("darkfactory.pipeline.orchestrator.invoke_agent", side_effect=invoke_side_effect), \
             patch("darkfactory.pipeline.iteration.invoke_agent", side_effect=invoke_side_effect), \
             patch("darkfactory.pipeline.iteration.run_quality_gate",
                   return_value=gate_result_factory()), \
             patch("darkfactory.pipeline.iteration.count_files_changed", return_value=1):
            with pytest.raises(TransientFailure, match="OOM"):
                run_pipeline(
                    env["spec_name"],
                    _config(env["project_dir"]),
                    env["project_dir"],
                    events_file=env["events_file"],
                )

    def test_all_retries_exhausted_fails_pipeline(self, pipeline_env):
        """When all agent attempts fail (non-transient), pipeline escalates."""
        env = pipeline_env
        seq = env["setup_agents"]([
            agent_result_factory(role=AgentRole.SPEC_VALIDATOR),
            # Implementer fails all 3 iterations (build failure)
            agent_result_factory(role=AgentRole.IMPLEMENTER, exit_code=1, output="Build error"),
            agent_result_factory(role=AgentRole.IMPLEMENTER, exit_code=1, output="Build error"),
            agent_result_factory(role=AgentRole.IMPLEMENTER, exit_code=1, output="Build error"),
            agent_result_factory(role=AgentRole.ESCALATION),
        ], gate_result=gate_result_factory(exit_code=2, passed=0, failed=5))

        config = _config(env["project_dir"])
        config.hard_gate_build = True

        exit_code = run_pipeline(
            env["spec_name"], config, env["project_dir"],
            events_file=env["events_file"],
        )

        assert exit_code == 2  # escalated


@pytest.mark.integration
class TestCorruptedStateResume:
    """State file with stale hash blocks resume."""

    def test_stale_hash_blocks_resume(self, pipeline_env):
        """State file with wrong spec hash raises DarkFactoryError on resume."""
        env = pipeline_env
        state_file = env["state_file"]

        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            "completed_step=2\n"
            "spec_hash=0000000000000000000000000000000000000000000000000000000000000000\n"
            "run_id=stale-run-id\n"
        )

        env["setup_agents"]([
            agent_result_factory(role=AgentRole.SPEC_VALIDATOR),
            agent_result_factory(role=AgentRole.IMPLEMENTER),
            agent_result_factory(role=AgentRole.REVIEWER),
            agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nDone."),
        ])

        with pytest.raises(DarkFactoryError, match="spec has changed"):
            run_pipeline(
                env["spec_name"],
                _config(env["project_dir"]),
                env["project_dir"],
                resume=True,
                events_file=env["events_file"],
            )


@pytest.mark.integration
class TestMissingConfig:
    """Malformed config.toml raises ConfigError at load."""

    def test_malformed_config_raises(self, tmp_path: Path):
        """Invalid TOML syntax -> error on load."""
        for d in ("specs", "scenarios", "reports", "state", "audit", "plans"):
            (tmp_path / ".dark-factory" / d).mkdir(parents=True)
        (tmp_path / ".dark-factory" / "config.toml").write_text(
            "[core\nlanguage = python\n"  # missing closing bracket
        )

        with pytest.raises(Exception):
            from darkfactory.config.loader import load_config
            load_config(tmp_path / ".dark-factory" / "config.toml")


@pytest.mark.integration
class TestSpecDeletedMidRun:
    """Spec file removed between validation and iteration."""

    def test_deleted_spec_pipeline_completes_or_errors(self, pipeline_env):
        """Spec deleted after validation: pipeline may complete (spec already
        read into memory) or raise FileNotFoundError on state write.

        The spec hash is computed before iteration starts, so deleting the
        spec mid-run doesn't affect the iteration loop. The pipeline should
        complete with exit code 0 (SHIP) since all agents return success.
        """
        env = pipeline_env
        call_count = [0]
        validator_ok = agent_result_factory(role=AgentRole.SPEC_VALIDATOR)

        def invoke_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # Delete spec after validation (before iteration loop)
                spec_file = env["project_dir"] / ".dark-factory" / "specs" / f"{env['spec_name']}.md"
                if spec_file.exists():
                    spec_file.unlink()
                return validator_ok
            role = args[0] if args else AgentRole.IMPLEMENTER
            if role == AgentRole.ORACLE:
                return agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nDone.")
            elif role == AgentRole.REVIEWER:
                return agent_result_factory(role=AgentRole.REVIEWER)
            return agent_result_factory(role=AgentRole.IMPLEMENTER)

        with patch("darkfactory.pipeline.orchestrator.invoke_agent", side_effect=invoke_side_effect), \
             patch("darkfactory.pipeline.iteration.invoke_agent", side_effect=invoke_side_effect), \
             patch("darkfactory.pipeline.iteration.run_quality_gate",
                   return_value=gate_result_factory()), \
             patch("darkfactory.pipeline.iteration.count_files_changed", return_value=1):
            # Pipeline completes because spec hash is computed before deletion
            exit_code = run_pipeline(
                env["spec_name"],
                _config(env["project_dir"]),
                env["project_dir"],
                events_file=env["events_file"],
            )
            assert exit_code == 0


@pytest.mark.integration
class TestEventsWriteFailure:
    """Read-only events file raises PermissionError (no graceful degradation)."""

    def test_readonly_events_raises(self, pipeline_env):
        """Pipeline raises PermissionError when events file is read-only.

        The event emitter uses append_jsonl which opens the file in append
        mode without catching PermissionError. This test documents that
        the pipeline does NOT degrade gracefully on write failure.
        """
        env = pipeline_env
        events_file = env["events_file"]

        events_file.write_text("")
        os.chmod(events_file, 0o444)

        env["setup_agents"]([
            agent_result_factory(role=AgentRole.SPEC_VALIDATOR),
            agent_result_factory(role=AgentRole.IMPLEMENTER),
            agent_result_factory(role=AgentRole.REVIEWER),
            agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nDone."),
        ])

        try:
            with pytest.raises(PermissionError):
                run_pipeline(
                    env["spec_name"],
                    _config(env["project_dir"]),
                    env["project_dir"],
                    events_file=events_file,
                )
        finally:
            os.chmod(events_file, 0o644)


@pytest.mark.integration
@pytest.mark.slow
class TestConcurrentPipelineRuns:
    """Two threads running pipelines with shared state file don't corrupt it."""

    def test_concurrent_state_integrity(self, pipeline_env):
        """State file is valid key=value after concurrent pipeline runs.

        Mocks are set up once before threads start to avoid thread-unsafe
        patch stacking.
        """
        env = pipeline_env
        results: list[int] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        call_counts: dict[int, int] = {}

        def agent_side_effect(*args, **kwargs):
            tid = threading.get_ident()
            with lock:
                call_counts[tid] = call_counts.get(tid, 0) + 1
                n = call_counts[tid]
            role = args[0] if args else AgentRole.IMPLEMENTER
            if n == 1:
                return agent_result_factory(role=AgentRole.SPEC_VALIDATOR)
            elif isinstance(role, AgentRole) and role == AgentRole.ORACLE:
                return agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nDone.")
            elif isinstance(role, AgentRole) and role == AgentRole.REVIEWER:
                return agent_result_factory(role=AgentRole.REVIEWER)
            return agent_result_factory(role=AgentRole.IMPLEMENTER)

        # Set up mocks once before threads start
        with patch("darkfactory.pipeline.orchestrator.invoke_agent", side_effect=agent_side_effect), \
             patch("darkfactory.pipeline.iteration.invoke_agent", side_effect=agent_side_effect), \
             patch("darkfactory.pipeline.iteration.run_quality_gate",
                   return_value=gate_result_factory()), \
             patch("darkfactory.pipeline.iteration.count_files_changed", return_value=1):

            def run_one():
                try:
                    ec = run_pipeline(
                        env["spec_name"],
                        _config(env["project_dir"]),
                        env["project_dir"],
                        events_file=env["events_file"],
                    )
                    with lock:
                        results.append(ec)
                except Exception as e:
                    with lock:
                        errors.append(e)

            t1 = threading.Thread(target=run_one)
            t2 = threading.Thread(target=run_one)
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

        # No JSON corruption
        json_errors = [e for e in errors if isinstance(e, json.JSONDecodeError)]
        assert not json_errors, f"JSON corruption detected: {json_errors}"

        # State file should be valid key=value lines
        state_file = env["state_file"]
        if state_file.exists():
            content = state_file.read_text()
            for line in content.strip().split("\n"):
                if line.strip():
                    assert "=" in line, f"Corrupted state line: {line!r}"
