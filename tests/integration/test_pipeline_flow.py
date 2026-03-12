"""Integration tests for the 7-step pipeline orchestrator.

Mock boundary: invoke_agent and run_quality_gate are mocked. All other
modules (state, events, config, trust, cost, audit) are real.
Docker infrastructure (require_docker, setup_networks, setup_proxies)
is mocked to no-op. Git operations are mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from darkfactory.errors import DarkFactoryError, TransientFailure
from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.pipeline.orchestrator import run_pipeline
from darkfactory.state.store import read_state, write_state

from tests.integration.conftest import (
    _SequenceMock,
    agent_result_factory,
    gate_result_factory,
)


# --- Helpers (before test classes) ---

def _read_events(events_file: Path) -> list[dict]:
    """Read all events from a JSONL file."""
    if not events_file.exists():
        return []
    events = []
    with events_file.open() as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                events.append(json.loads(stripped))
    return events


def _agent_roles(seq: _SequenceMock) -> list[AgentRole]:
    """Extract the AgentRole from each recorded invoke_agent call."""
    return [call[0][0] for call in seq.calls]


def _config(project_dir: Path):
    """Load config from the test project with test-friendly defaults."""
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


def _spec_hash(project_dir: Path, spec_name: str) -> str:
    """Compute spec hash for resume tests."""
    from darkfactory.utils.hash import sha256_file
    spec_file = project_dir / ".dark-factory" / "specs" / f"{spec_name}.md"
    return sha256_file(spec_file)


def _ship_responses() -> list[AgentResult]:
    """Agent responses for a single-iteration SHIP.

    Order: validator, implementer, reviewer, oracle.
    No Scenario Architect because has_scenario_files defaults to True.
    """
    return [
        agent_result_factory(role=AgentRole.SPEC_VALIDATOR),
        agent_result_factory(role=AgentRole.IMPLEMENTER),
        agent_result_factory(role=AgentRole.REVIEWER),
        agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nAll good."),
    ]


@pytest.mark.integration
class TestPipelineShip:
    """Full pipeline run ending in SHIP."""

    def test_full_pipeline_ship_first_iteration(self, pipeline_env):
        env = pipeline_env
        seq = env["setup_agents"](_ship_responses())

        exit_code = run_pipeline(
            env["spec_name"],
            _config(env["project_dir"]),
            env["project_dir"],
            events_file=env["events_file"],
        )

        assert exit_code == 0
        assert _agent_roles(seq) == [
            AgentRole.SPEC_VALIDATOR,
            AgentRole.IMPLEMENTER,
            AgentRole.REVIEWER,
            AgentRole.ORACLE,
        ]
        assert seq.remaining == 0

        status = read_state(env["state_file"], "status")
        assert status == "shipped"

        events = _read_events(env["events_file"])
        event_types = [e["event"] for e in events]
        assert "SPEC_START" in event_types
        assert "SPEC_SHIPPED" in event_types

    def test_pipeline_event_sequence_ordered(self, pipeline_env):
        env = pipeline_env
        env["setup_agents"](_ship_responses())

        run_pipeline(
            env["spec_name"],
            _config(env["project_dir"]),
            env["project_dir"],
            events_file=env["events_file"],
        )

        events = _read_events(env["events_file"])
        timestamps = [e["ts"] for e in events]
        assert timestamps == sorted(timestamps)


@pytest.mark.integration
class TestPipelineScenarioArchitect:
    """Scenario Architect is invoked when no scenarios exist."""

    def test_sa_invoked_when_no_scenarios(self, pipeline_env):
        env = pipeline_env
        seq = env["setup_agents"](
            [
                agent_result_factory(role=AgentRole.SPEC_VALIDATOR),
                agent_result_factory(role=AgentRole.SCENARIO_ARCHITECT),
                agent_result_factory(role=AgentRole.IMPLEMENTER),
                agent_result_factory(role=AgentRole.REVIEWER),
                agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nDone."),
            ],
            has_scenarios=False,
        )

        exit_code = run_pipeline(
            env["spec_name"],
            _config(env["project_dir"]),
            env["project_dir"],
            events_file=env["events_file"],
        )

        assert exit_code == 0
        assert _agent_roles(seq) == [
            AgentRole.SPEC_VALIDATOR,
            AgentRole.SCENARIO_ARCHITECT,
            AgentRole.IMPLEMENTER,
            AgentRole.REVIEWER,
            AgentRole.ORACLE,
        ]
        assert seq.remaining == 0


@pytest.mark.integration
class TestPipelineValidationFailure:
    """Spec validation failure stops the pipeline."""

    def test_validation_failure_raises(self, pipeline_env):
        env = pipeline_env
        env["setup_agents"](
            [agent_result_factory(role=AgentRole.SPEC_VALIDATOR)],
            validation_result=(False, "missing Functional Requirements section"),
        )

        with pytest.raises(DarkFactoryError, match="spec validation failed"):
            run_pipeline(
                env["spec_name"],
                _config(env["project_dir"]),
                env["project_dir"],
                events_file=env["events_file"],
            )


@pytest.mark.integration
class TestPipelineNoShipThenShip:
    """Pipeline with NO-SHIP then SHIP across iterations."""

    def test_two_iterations(self, pipeline_env):
        env = pipeline_env
        seq = env["setup_agents"]([
            agent_result_factory(role=AgentRole.SPEC_VALIDATOR),
            # Iteration 1
            agent_result_factory(role=AgentRole.IMPLEMENTER),
            agent_result_factory(role=AgentRole.REVIEWER),
            agent_result_factory(role=AgentRole.ORACLE, output="NO-SHIP\nFix FR-3."),
            # Iteration 2
            agent_result_factory(role=AgentRole.IMPLEMENTER),
            agent_result_factory(role=AgentRole.REVIEWER),
            agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nAll good."),
        ])

        exit_code = run_pipeline(
            env["spec_name"],
            _config(env["project_dir"]),
            env["project_dir"],
            events_file=env["events_file"],
        )

        assert exit_code == 0
        assert _agent_roles(seq) == [
            AgentRole.SPEC_VALIDATOR,
            AgentRole.IMPLEMENTER, AgentRole.REVIEWER, AgentRole.ORACLE,
            AgentRole.IMPLEMENTER, AgentRole.REVIEWER, AgentRole.ORACLE,
        ]
        assert seq.remaining == 0


@pytest.mark.integration
class TestPipelineEscalation:
    """Pipeline reaching max iterations -> escalation."""

    def test_max_iterations_escalation(self, pipeline_env):
        env = pipeline_env
        responses = [agent_result_factory(role=AgentRole.SPEC_VALIDATOR)]
        for _ in range(3):
            responses.extend([
                agent_result_factory(role=AgentRole.IMPLEMENTER),
                agent_result_factory(role=AgentRole.REVIEWER),
                agent_result_factory(role=AgentRole.ORACLE, output="NO-SHIP\nNot ready."),
            ])
        responses.append(agent_result_factory(role=AgentRole.ESCALATION))
        seq = env["setup_agents"](responses)

        exit_code = run_pipeline(
            env["spec_name"],
            _config(env["project_dir"]),
            env["project_dir"],
            events_file=env["events_file"],
        )

        assert exit_code == 2
        roles = _agent_roles(seq)
        assert roles[-1] == AgentRole.ESCALATION
        assert roles.count(AgentRole.IMPLEMENTER) == 3

        status = read_state(env["state_file"], "status")
        assert status == "escalated"

        events = _read_events(env["events_file"])
        event_types = [e["event"] for e in events]
        assert "SPEC_ESCALATED" in event_types


@pytest.mark.integration
class TestPipelineBuildFailure:
    """Build failure (gate exit 2) skips review and oracle."""

    def test_build_failure_skips_review_oracle(self, pipeline_env):
        env = pipeline_env
        responses = [agent_result_factory(role=AgentRole.SPEC_VALIDATOR)]
        for _ in range(3):
            responses.append(agent_result_factory(role=AgentRole.IMPLEMENTER))
        responses.append(agent_result_factory(role=AgentRole.ESCALATION))
        seq = env["setup_agents"](
            responses,
            gate_result=gate_result_factory(exit_code=2, passed=0, failed=5),
        )

        config = _config(env["project_dir"])
        config.hard_gate_build = True

        exit_code = run_pipeline(
            env["spec_name"], config, env["project_dir"],
            events_file=env["events_file"],
        )

        assert exit_code == 2
        roles = _agent_roles(seq)
        assert AgentRole.REVIEWER not in roles
        assert AgentRole.ORACLE not in roles
        assert roles.count(AgentRole.IMPLEMENTER) == 3


@pytest.mark.integration
class TestPipelineResume:
    """Resume from checkpoint skips completed steps."""

    def test_resume_skips_validation_and_scenarios(self, pipeline_env):
        env = pipeline_env

        write_state(env["state_file"], "completed_step", "2")
        write_state(env["state_file"], "spec_hash",
                     _spec_hash(env["project_dir"], env["spec_name"]))
        write_state(env["state_file"], "run_id", "resume-test-id")

        seq = env["setup_agents"]([
            agent_result_factory(role=AgentRole.IMPLEMENTER),
            agent_result_factory(role=AgentRole.REVIEWER),
            agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nDone."),
        ])

        exit_code = run_pipeline(
            env["spec_name"],
            _config(env["project_dir"]),
            env["project_dir"],
            resume=True,
            events_file=env["events_file"],
        )

        assert exit_code == 0
        roles = _agent_roles(seq)
        assert AgentRole.SPEC_VALIDATOR not in roles
        assert AgentRole.SCENARIO_ARCHITECT not in roles
        assert roles == [AgentRole.IMPLEMENTER, AgentRole.REVIEWER, AgentRole.ORACLE]


@pytest.mark.integration
class TestPipelineDryRun:
    """Dry run mode passes dry_run=True to agents."""

    def test_dry_run_passes_flag(self, pipeline_env):
        env = pipeline_env
        seq = env["setup_agents"](_ship_responses())

        run_pipeline(
            env["spec_name"],
            _config(env["project_dir"]),
            env["project_dir"],
            dry_run=True,
            events_file=env["events_file"],
        )

        for _call_args, call_kwargs in seq.calls:
            assert call_kwargs.get("dry_run") is True


@pytest.mark.integration
class TestPipelineSpecChange:
    """Spec change between runs blocks resume."""

    def test_spec_change_raises(self, pipeline_env):
        env = pipeline_env

        write_state(env["state_file"], "completed_step", "2")
        write_state(env["state_file"], "spec_hash", "stale-hash-000")
        write_state(env["state_file"], "run_id", "old-run-id")

        with pytest.raises(DarkFactoryError, match="spec has changed"):
            run_pipeline(
                env["spec_name"],
                _config(env["project_dir"]),
                env["project_dir"],
                resume=True,
            )


@pytest.mark.integration
class TestPipelineBudgetExceeded:
    """Budget exceeded aborts the pipeline."""

    def test_budget_exceeded_aborts(self, pipeline_env):
        env = pipeline_env
        # Provide enough responses for validator + implementer (budget check is after gate).
        seq = env["setup_agents"]([
            agent_result_factory(role=AgentRole.SPEC_VALIDATOR),
            agent_result_factory(role=AgentRole.IMPLEMENTER),
        ])

        config = _config(env["project_dir"])
        config.cost_budget_usd = 0.0001

        exit_code = run_pipeline(
            env["spec_name"], config, env["project_dir"],
            events_file=env["events_file"],
        )

        assert exit_code == 3  # aborted
        assert _agent_roles(seq) == [AgentRole.SPEC_VALIDATOR, AgentRole.IMPLEMENTER]
        status = read_state(env["state_file"], "status")
        assert status == "failed"


@pytest.mark.integration
class TestPipelineAutonomy:
    """Autonomy level affects pipeline behavior."""

    def test_autonomy_level_1_hold(self, pipeline_env):
        env = pipeline_env
        env["setup_agents"](_ship_responses())

        config = _config(env["project_dir"])
        config.autonomy_level = 1

        exit_code = run_pipeline(
            env["spec_name"], config, env["project_dir"],
            events_file=env["events_file"],
        )

        assert exit_code == 4  # hold
        status = read_state(env["state_file"], "status")
        assert status == "hold"

        hold_file = env["project_dir"] / ".dark-factory" / "state" / f"{env['spec_name']}.hold"
        assert hold_file.exists()
        hold_content = hold_file.read_text()
        assert f"spec={env['spec_name']}" in hold_content
        assert "run_id=" in hold_content

    def test_autonomy_level_3_ships_no_hold(self, pipeline_env):
        env = pipeline_env
        env["setup_agents"](_ship_responses())

        config = _config(env["project_dir"])
        config.autonomy_level = 3

        exit_code = run_pipeline(
            env["spec_name"], config, env["project_dir"],
            events_file=env["events_file"],
        )

        assert exit_code == 0
        hold_file = env["project_dir"] / ".dark-factory" / "state" / f"{env['spec_name']}.hold"
        assert not hold_file.exists()


@pytest.mark.integration
class TestPipelineCostTracking:
    """Cost events emitted during pipeline execution."""

    def test_cost_events_emitted(self, pipeline_env):
        env = pipeline_env
        env["setup_agents"](_ship_responses())

        run_pipeline(
            env["spec_name"],
            _config(env["project_dir"]),
            env["project_dir"],
            events_file=env["events_file"],
        )

        events = _read_events(env["events_file"])
        event_types = [e["event"] for e in events]
        assert "ITER_COST" in event_types
        assert "COST_SUMMARY" in event_types


@pytest.mark.integration
class TestPipelineAuditTrail:
    """Audit path threaded through every agent invocation."""

    def test_audit_path_passed_to_all_agents(self, pipeline_env):
        """Full SHIP run — audit_path kwarg present in every invoke_agent call."""
        env = pipeline_env
        seq = env["setup_agents"](_ship_responses())

        run_pipeline(
            env["spec_name"],
            _config(env["project_dir"]),
            env["project_dir"],
            events_file=env["events_file"],
        )

        for i, (_args, kwargs) in enumerate(seq.calls):
            assert "audit_path" in kwargs, f"call {i} missing audit_path kwarg"
            audit_path = kwargs["audit_path"]
            assert audit_path is not None
            assert str(audit_path).endswith(".log")


@pytest.mark.integration
class TestPipelineTransientFailure:
    """TransientFailure from invoke_agent propagates out of run_pipeline."""

    def test_transient_failure_propagates(self, pipeline_env):
        """invoke_agent raising TransientFailure is not swallowed by the pipeline."""
        env = pipeline_env
        validator_ok = agent_result_factory(role=AgentRole.SPEC_VALIDATOR)
        call_count = [0]

        def invoke_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return validator_ok
            raise TransientFailure("connection reset")

        with patch("darkfactory.pipeline.orchestrator.invoke_agent", side_effect=invoke_side_effect), \
             patch("darkfactory.pipeline.iteration.invoke_agent", side_effect=invoke_side_effect), \
             patch("darkfactory.pipeline.iteration.run_quality_gate",
                   return_value=gate_result_factory()), \
             patch("darkfactory.pipeline.iteration.count_files_changed", return_value=1):
            with pytest.raises(TransientFailure, match="connection reset"):
                run_pipeline(
                    env["spec_name"],
                    _config(env["project_dir"]),
                    env["project_dir"],
                    events_file=env["events_file"],
                )

        assert call_count[0] == 2  # validator succeeded, implementer raised
