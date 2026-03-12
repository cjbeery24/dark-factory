"""Integration tests for the iteration loop (steps 3-6).

Mock boundary: invoke_agent and run_quality_gate are mocked. Trajectory
classification, strategy actions, composite scoring, cost estimation,
and anomaly checks are real.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from darkfactory.config.loader import load_config
from darkfactory.docker.barriers import configure_barriers
from darkfactory.models.agent import AgentRole
from darkfactory.models.pipeline import SpecRun
from darkfactory.models.satisfaction import TrajectoryType
from darkfactory.pipeline.iteration import run_iteration_loop
from darkfactory.scenarios.trajectory import StrategyActionType, strategy_action

from tests.integration.conftest import (
    _SequenceMock,
    agent_result_factory,
    gate_result_factory,
    spec_run_factory,
)


# --- Helpers ---

def _config(project_dir: Path):
    """Load config with test-friendly defaults."""
    config = load_config(project_dir / ".dark-factory" / "config.toml")
    config.strategy_switch_enabled = False
    config.deploy_cmd = ""
    config.rollback_monitor_cmd = ""
    config.cost_budget_usd = 0.0
    config.min_satisfaction_score = 0
    config.min_ship_pass_rate = 0
    return config


def _barriers(config):
    return configure_barriers(config.source_dirs)


def _spec_run(spec_name: str = "add-multiply", **overrides) -> SpecRun:
    """Build a SpecRun with iteration-loop-friendly defaults."""
    defaults = {
        "effective_autonomy": 3,
        "effective_max_iterations": 3,
    }
    defaults.update(overrides)
    return spec_run_factory(spec_name=spec_name, **defaults)


def _agent_roles(seq: _SequenceMock) -> list[AgentRole]:
    return [call[0][0] for call in seq.calls]


@pytest.fixture
def iter_env(project_dir, sample_spec):
    """Set up iteration loop test environment.

    Yields a dict with project_dir, spec_name, events_file, barriers,
    config, and setup_iter() to install mocks.
    """
    events_file = project_dir / ".dark-factory" / "events.jsonl"
    config = _config(project_dir)
    barriers = _barriers(config)

    patches: list[Any] = []

    def setup_iter(
        results: list,
        gate_result=None,
    ) -> _SequenceMock:
        seq = _SequenceMock(results)
        p1 = patch("darkfactory.pipeline.iteration.invoke_agent", new=seq)
        p2 = patch(
            "darkfactory.pipeline.iteration.run_quality_gate",
            return_value=gate_result or gate_result_factory(),
        )
        p3 = patch("darkfactory.pipeline.iteration.count_files_changed", return_value=1)
        p1.start()
        p2.start()
        p3.start()
        patches.extend([p1, p2, p3])
        return seq

    yield {
        "project_dir": project_dir,
        "spec_name": sample_spec,
        "events_file": events_file,
        "config": config,
        "barriers": barriers,
        "setup_iter": setup_iter,
    }

    for p in patches:
        p.stop()


# --- Tests ---

@pytest.mark.integration
class TestIterationShip:
    """Single iteration SHIP path."""

    def test_single_iteration_ship(self, iter_env):
        env = iter_env
        seq = env["setup_iter"]([
            agent_result_factory(role=AgentRole.IMPLEMENTER),
            agent_result_factory(role=AgentRole.REVIEWER),
            agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nAll good."),
        ])

        spec_run = _spec_run(env["spec_name"])
        shipped = run_iteration_loop(
            spec_run, env["config"], env["barriers"], env["project_dir"],
            events_file=env["events_file"],
        )

        assert shipped is True
        assert _agent_roles(seq) == [
            AgentRole.IMPLEMENTER, AgentRole.REVIEWER, AgentRole.ORACLE,
        ]
        assert seq.remaining == 0
        assert spec_run.iteration == 1
        assert len(spec_run.iterations) == 1
        assert spec_run.iterations[0].oracle_verdict == "SHIP"


@pytest.mark.integration
class TestTrajectoryClassification:
    """Trajectory analysis triggers correct behavior."""

    def test_improving_continues(self, iter_env):
        """Scores 40→60→80: IMPROVING trajectory, strategy CONTINUE."""
        env = iter_env
        gate_scores = [
            gate_result_factory(satisfaction_score=40, pass_rate=40),
            gate_result_factory(satisfaction_score=60, pass_rate=60),
            gate_result_factory(satisfaction_score=80, pass_rate=80),
        ]
        # 3 iterations of NO-SHIP, NO-SHIP, then SHIP on 3rd
        results = []
        for _ in range(2):
            results.extend([
                agent_result_factory(role=AgentRole.IMPLEMENTER),
                agent_result_factory(role=AgentRole.REVIEWER),
                agent_result_factory(role=AgentRole.ORACLE, output="NO-SHIP\nNeeds work."),
            ])
        results.extend([
            agent_result_factory(role=AgentRole.IMPLEMENTER),
            agent_result_factory(role=AgentRole.REVIEWER),
            agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nDone."),
        ])

        # Need per-iteration gate results — patch with a sequence
        gate_seq = iter(gate_scores)
        seq = _SequenceMock(results)
        with patch("darkfactory.pipeline.iteration.invoke_agent", new=seq), \
             patch("darkfactory.pipeline.iteration.run_quality_gate", side_effect=gate_seq), \
             patch("darkfactory.pipeline.iteration.count_files_changed", return_value=1):
            spec_run = _spec_run(env["spec_name"])
            shipped = run_iteration_loop(
                spec_run, env["config"], env["barriers"], env["project_dir"],
                events_file=env["events_file"],
            )

        assert shipped is True
        assert spec_run.trajectory_class == TrajectoryType.IMPROVING

    def test_degrading_triggers_early_escalation(self, iter_env):
        """Scores 80→60→40: DEGRADING trajectory triggers early escalation at iter 3."""
        env = iter_env
        gate_scores = [
            gate_result_factory(satisfaction_score=80, pass_rate=80),
            gate_result_factory(satisfaction_score=60, pass_rate=60),
            gate_result_factory(satisfaction_score=40, pass_rate=40),
        ]
        results = []
        for _ in range(3):
            results.extend([
                agent_result_factory(role=AgentRole.IMPLEMENTER),
                agent_result_factory(role=AgentRole.REVIEWER),
                agent_result_factory(role=AgentRole.ORACLE, output="NO-SHIP\nNot ready."),
            ])

        seq = _SequenceMock(results)
        with patch("darkfactory.pipeline.iteration.invoke_agent", new=seq), \
             patch("darkfactory.pipeline.iteration.run_quality_gate", side_effect=iter(gate_scores)), \
             patch("darkfactory.pipeline.iteration.count_files_changed", return_value=1):
            spec_run = _spec_run(env["spec_name"])
            shipped = run_iteration_loop(
                spec_run, env["config"], env["barriers"], env["project_dir"],
                events_file=env["events_file"],
            )

        assert shipped is False
        assert spec_run.trajectory_class == TrajectoryType.DEGRADING

    def test_plateau_triggers_early_escalation(self, iter_env):
        """Scores 50→52→51: PLATEAU triggers early escalation at iter 3."""
        env = iter_env
        gate_scores = [
            gate_result_factory(satisfaction_score=50, pass_rate=50),
            gate_result_factory(satisfaction_score=52, pass_rate=52),
            gate_result_factory(satisfaction_score=51, pass_rate=51),
        ]
        results = []
        for _ in range(3):
            results.extend([
                agent_result_factory(role=AgentRole.IMPLEMENTER),
                agent_result_factory(role=AgentRole.REVIEWER),
                agent_result_factory(role=AgentRole.ORACLE, output="NO-SHIP\nStalled."),
            ])

        seq = _SequenceMock(results)
        with patch("darkfactory.pipeline.iteration.invoke_agent", new=seq), \
             patch("darkfactory.pipeline.iteration.run_quality_gate", side_effect=iter(gate_scores)), \
             patch("darkfactory.pipeline.iteration.count_files_changed", return_value=1):
            spec_run = _spec_run(env["spec_name"])
            shipped = run_iteration_loop(
                spec_run, env["config"], env["barriers"], env["project_dir"],
                events_file=env["events_file"],
            )

        assert shipped is False
        assert spec_run.trajectory_class == TrajectoryType.PLATEAU


@pytest.mark.integration
class TestStrategySwitching:
    """Strategy switching when enabled."""

    def test_degrading_switches_model(self, iter_env):
        """DEGRADING trajectory with strategy_switch_enabled triggers model switch.

        Iter 1: score 80, Iter 2: score 60, Iter 3: score 40 → DEGRADING.
        Strategy switch fires at iter 3 (continues instead of escalating).
        Iter 4: score 90 → SHIP.
        """
        env = iter_env
        config = env["config"]
        config.strategy_switch_enabled = True
        config.strategy_max_switches = 1
        config.model_ids = {
            "implementer": "claude-sonnet-4-20250514",
            "reviewer": "claude-opus-4-20250514",
        }
        config.model_id = "claude-sonnet-4-20250514"
        config.max_iterations = 5

        # Iter 1: score 80, NO-SHIP → iter 2
        # Iter 2: score 60, NO-SHIP → iter 3
        # Iter 3: score 40, DEGRADING → strategy switch → continue → iter 4
        # Iter 4: score 90, SHIP
        # Trajectory check fires BEFORE review/oracle, so iter 3 has
        # no review/oracle (implement + gate only).
        gate_results = [
            gate_result_factory(satisfaction_score=80, pass_rate=80),
            gate_result_factory(satisfaction_score=60, pass_rate=60),
            gate_result_factory(satisfaction_score=40, pass_rate=40),
            gate_result_factory(satisfaction_score=90, pass_rate=90),
        ]

        # Iter 1, 2: implement + review + oracle (3 agents each)
        # Iter 3: implement only (strategy switch skips review/oracle)
        # Iter 4: implement + review + oracle
        results = [
            # Iter 1
            agent_result_factory(role=AgentRole.IMPLEMENTER),
            agent_result_factory(role=AgentRole.REVIEWER),
            agent_result_factory(role=AgentRole.ORACLE, output="NO-SHIP\nNot ready."),
            # Iter 2
            agent_result_factory(role=AgentRole.IMPLEMENTER),
            agent_result_factory(role=AgentRole.REVIEWER),
            agent_result_factory(role=AgentRole.ORACLE, output="NO-SHIP\nNot ready."),
            # Iter 3 — only implementer (strategy switch fires before review)
            agent_result_factory(role=AgentRole.IMPLEMENTER),
            # Iter 4 — after switch
            agent_result_factory(role=AgentRole.IMPLEMENTER),
            agent_result_factory(role=AgentRole.REVIEWER),
            agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nGood."),
        ]

        gate_deque = iter(gate_results)
        seq = _SequenceMock(results)
        with patch("darkfactory.pipeline.iteration.invoke_agent", new=seq), \
             patch("darkfactory.pipeline.iteration.run_quality_gate", side_effect=gate_deque), \
             patch("darkfactory.pipeline.iteration.count_files_changed", return_value=1):
            spec_run = _spec_run(
                env["spec_name"],
                effective_max_iterations=5,
            )
            shipped = run_iteration_loop(
                spec_run, config, env["barriers"], env["project_dir"],
                events_file=env["events_file"],
            )

        assert shipped is True
        assert spec_run.strategy_switches == 1
        assert spec_run.model_override == "claude-opus-4-20250514"


@pytest.mark.integration
class TestOracleOverrides:
    """Oracle SHIP overridden by floor checks and composite anomaly."""

    def test_satisfaction_floor_overrides_ship(self, iter_env):
        """Oracle says SHIP but satisfaction below floor → NO-SHIP.

        Use improving scores (30→40→50) to avoid PLATEAU/DEGRADING early
        escalation, while keeping all below the floor of 80.
        """
        env = iter_env
        config = env["config"]
        config.min_satisfaction_score = 80

        gate_results = [
            gate_result_factory(satisfaction_score=30, pass_rate=30),
            gate_result_factory(satisfaction_score=40, pass_rate=40),
            gate_result_factory(satisfaction_score=50, pass_rate=50),
        ]
        results = []
        for _ in range(3):
            results.extend([
                agent_result_factory(role=AgentRole.IMPLEMENTER),
                agent_result_factory(role=AgentRole.REVIEWER),
                agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nLooks fine."),
            ])

        seq = _SequenceMock(results)
        with patch("darkfactory.pipeline.iteration.invoke_agent", new=seq), \
             patch("darkfactory.pipeline.iteration.run_quality_gate", side_effect=iter(gate_results)), \
             patch("darkfactory.pipeline.iteration.count_files_changed", return_value=1):
            spec_run = _spec_run(env["spec_name"])
            shipped = run_iteration_loop(
                spec_run, config, env["barriers"], env["project_dir"],
                events_file=env["events_file"],
            )

        assert shipped is False
        # All oracle verdicts should be overridden to NO-SHIP
        for it in spec_run.iterations:
            assert it.oracle_verdict == "NO-SHIP"

    def test_pass_rate_floor_overrides_ship(self, iter_env):
        """Oracle says SHIP but pass_rate below floor → NO-SHIP.

        Use improving pass rates (60→70→80) to avoid trajectory escalation,
        while keeping all below the floor of 90.
        """
        env = iter_env
        config = env["config"]
        config.min_ship_pass_rate = 90

        gate_results = [
            gate_result_factory(satisfaction_score=95, pass_rate=60),
            gate_result_factory(satisfaction_score=96, pass_rate=70),
            gate_result_factory(satisfaction_score=97, pass_rate=80),
        ]
        results = []
        for _ in range(3):
            results.extend([
                agent_result_factory(role=AgentRole.IMPLEMENTER),
                agent_result_factory(role=AgentRole.REVIEWER),
                agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nAll good."),
            ])

        seq = _SequenceMock(results)
        with patch("darkfactory.pipeline.iteration.invoke_agent", new=seq), \
             patch("darkfactory.pipeline.iteration.run_quality_gate", side_effect=iter(gate_results)), \
             patch("darkfactory.pipeline.iteration.count_files_changed", return_value=1):
            spec_run = _spec_run(env["spec_name"])
            shipped = run_iteration_loop(
                spec_run, config, env["barriers"], env["project_dir"],
                events_file=env["events_file"],
            )

        assert shipped is False
        for it in spec_run.iterations:
            assert it.oracle_verdict == "NO-SHIP"


@pytest.mark.integration
class TestBuildFailure:
    """Build failure (gate exit 2) skips review and oracle."""

    def test_hard_gate_build_skips_review_oracle(self, iter_env):
        env = iter_env
        config = env["config"]
        config.hard_gate_build = True

        gate = gate_result_factory(exit_code=2, passed=0, failed=5)
        results = []
        for _ in range(3):
            results.append(agent_result_factory(role=AgentRole.IMPLEMENTER))

        seq = env["setup_iter"](results, gate_result=gate)
        spec_run = _spec_run(env["spec_name"])
        shipped = run_iteration_loop(
            spec_run, config, env["barriers"], env["project_dir"],
            events_file=env["events_file"],
        )

        assert shipped is False
        roles = _agent_roles(seq)
        assert AgentRole.REVIEWER not in roles
        assert AgentRole.ORACLE not in roles
        assert roles == [AgentRole.IMPLEMENTER] * 3
        assert seq.remaining == 0


@pytest.mark.integration
class TestEvaluatorInvocation:
    """Evaluator step invoked when deploy_cmd is configured."""

    def test_evaluator_skipped_no_deploy_cmd(self, iter_env):
        """Without deploy_cmd, evaluator is not invoked."""
        env = iter_env
        seq = env["setup_iter"]([
            agent_result_factory(role=AgentRole.IMPLEMENTER),
            agent_result_factory(role=AgentRole.REVIEWER),
            agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nDone."),
        ])

        spec_run = _spec_run(env["spec_name"])
        shipped = run_iteration_loop(
            spec_run, env["config"], env["barriers"], env["project_dir"],
            events_file=env["events_file"],
        )

        assert shipped is True
        assert AgentRole.EVALUATOR not in _agent_roles(seq)

    def test_evaluator_invoked_when_deploy_cmd(self, iter_env):
        """With deploy_cmd, evaluator agent is invoked between gate and review."""
        env = iter_env
        config = env["config"]
        config.deploy_cmd = "echo deploy"

        # Create evaluator.md so the agent gets invoked
        (env["project_dir"] / "agents" / "evaluator.md").parent.mkdir(parents=True, exist_ok=True)
        (env["project_dir"] / "agents" / "evaluator.md").write_text("# Evaluator\n")

        seq = _SequenceMock([
            agent_result_factory(role=AgentRole.IMPLEMENTER),
            agent_result_factory(role=AgentRole.EVALUATOR),
            agent_result_factory(role=AgentRole.REVIEWER),
            agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nDone."),
        ])

        with patch("darkfactory.pipeline.iteration.invoke_agent", new=seq), \
             patch("darkfactory.pipeline.iteration.run_quality_gate",
                   return_value=gate_result_factory()), \
             patch("darkfactory.pipeline.iteration.count_files_changed", return_value=1), \
             patch("darkfactory.pipeline.iteration.deploy", return_value=True), \
             patch("darkfactory.pipeline.iteration.health_check", return_value=True), \
             patch("darkfactory.pipeline.iteration.teardown"):
            spec_run = _spec_run(env["spec_name"])
            shipped = run_iteration_loop(
                spec_run, config, env["barriers"], env["project_dir"],
                events_file=env["events_file"],
            )

        assert shipped is True
        roles = _agent_roles(seq)
        assert AgentRole.EVALUATOR in roles
        eval_idx = roles.index(AgentRole.EVALUATOR)
        impl_idx = roles.index(AgentRole.IMPLEMENTER)
        review_idx = roles.index(AgentRole.REVIEWER)
        assert impl_idx < eval_idx < review_idx


@pytest.mark.integration
class TestMaxIterationsExhaustion:
    """Normal max iterations exhaustion (no trajectory escalation)."""

    def test_improving_but_never_ships(self, iter_env):
        """Scores 40→60→80: IMPROVING trajectory, but oracle always NO-SHIP.

        Runs out of iterations normally (not trajectory-triggered escalation).
        """
        env = iter_env
        gate_results = [
            gate_result_factory(satisfaction_score=40, pass_rate=40),
            gate_result_factory(satisfaction_score=60, pass_rate=60),
            gate_result_factory(satisfaction_score=80, pass_rate=80),
        ]
        results = []
        for _ in range(3):
            results.extend([
                agent_result_factory(role=AgentRole.IMPLEMENTER),
                agent_result_factory(role=AgentRole.REVIEWER),
                agent_result_factory(role=AgentRole.ORACLE, output="NO-SHIP\nNot yet."),
            ])

        seq = _SequenceMock(results)
        with patch("darkfactory.pipeline.iteration.invoke_agent", new=seq), \
             patch("darkfactory.pipeline.iteration.run_quality_gate", side_effect=iter(gate_results)), \
             patch("darkfactory.pipeline.iteration.count_files_changed", return_value=1):
            spec_run = _spec_run(env["spec_name"])
            shipped = run_iteration_loop(
                spec_run, env["config"], env["barriers"], env["project_dir"],
                events_file=env["events_file"],
            )

        assert shipped is False
        assert spec_run.trajectory_class == TrajectoryType.IMPROVING
        assert len(spec_run.iterations) == 3
        assert seq.remaining == 0


@pytest.mark.integration
class TestIterationStateTracking:
    """Iteration state is correctly populated."""

    def test_gate_results_recorded_in_iteration_state(self, iter_env):
        env = iter_env
        gate = gate_result_factory(passed=8, failed=2, satisfaction_score=85)
        seq = env["setup_iter"](
            [
                agent_result_factory(role=AgentRole.IMPLEMENTER),
                agent_result_factory(role=AgentRole.REVIEWER),
                agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nDone."),
            ],
            gate_result=gate,
        )

        spec_run = _spec_run(env["spec_name"])
        run_iteration_loop(
            spec_run, env["config"], env["barriers"], env["project_dir"],
            events_file=env["events_file"],
        )

        it = spec_run.iterations[0]
        assert it.scenario_pass_count == 8
        assert it.scenario_fail_count == 2
        assert it.scenario_total == 10
        assert it.satisfaction_score == 85
        assert it.oracle_verdict == "SHIP"


@pytest.mark.integration
class TestOscillatingTrajectory:
    """Oscillating score pattern triggers SWITCH_MODEL strategy."""

    def test_oscillating_triggers_switch(self, iter_env):
        """Scores 60→40→70→30: alternating deltas → OSCILLATING trajectory."""
        env = iter_env
        config = env["config"]
        config.max_iterations = 4
        gate_scores = [
            gate_result_factory(satisfaction_score=60, pass_rate=60),
            gate_result_factory(satisfaction_score=40, pass_rate=40),
            gate_result_factory(satisfaction_score=70, pass_rate=70),
            gate_result_factory(satisfaction_score=30, pass_rate=30),
        ]
        results = []
        for _ in range(4):
            results.extend([
                agent_result_factory(role=AgentRole.IMPLEMENTER),
                agent_result_factory(role=AgentRole.REVIEWER),
                agent_result_factory(role=AgentRole.ORACLE, output="NO-SHIP\nUnstable."),
            ])

        seq = _SequenceMock(results)
        with patch("darkfactory.pipeline.iteration.invoke_agent", new=seq), \
             patch("darkfactory.pipeline.iteration.run_quality_gate", side_effect=iter(gate_scores)), \
             patch("darkfactory.pipeline.iteration.count_files_changed", return_value=1):
            spec_run = _spec_run(env["spec_name"], effective_max_iterations=4)
            shipped = run_iteration_loop(
                spec_run, config, env["barriers"], env["project_dir"],
                events_file=env["events_file"],
            )

        assert shipped is False
        assert spec_run.trajectory_class == TrajectoryType.OSCILLATING


@pytest.mark.integration
class TestFeedbackLeakageDetection:
    """Oracle feedback with scenario names triggers leakage warning."""

    def test_scenario_filename_leakage(self, tmp_path: Path):
        """Feedback containing scenario filenames (NN-*.test.*) is flagged."""
        audit_path = tmp_path / "audit.log"
        feedback = (
            "NO-SHIP\n\n## Feedback\n"
            "- 01-contract_test_multiply.test.py failed on edge case\n"
        )

        from darkfactory.audit.checker import validate_oracle_feedback
        leaks = validate_oracle_feedback(feedback, audit_path)

        assert len(leaks) == 1
        assert "01-contract" in leaks[0]
        assert audit_path.exists()
        assert "SEMANTIC LEAKAGE" in audit_path.read_text()

    def test_assertion_pattern_leakage(self, tmp_path: Path):
        """Feedback containing test assertion calls is flagged."""
        audit_path = tmp_path / "audit.log"
        feedback = (
            "NO-SHIP\n\n## Feedback\n"
            "- The function fails when assert(result == 5) is checked\n"
        )

        from darkfactory.audit.checker import validate_oracle_feedback
        leaks = validate_oracle_feedback(feedback, audit_path)

        assert len(leaks) == 1
        assert "assert" in leaks[0]
        assert audit_path.exists()


@pytest.mark.integration
class TestCompositeAnomalyBlocksShip:
    """Composite anomaly score >= COMPOSITE_BLOCK overrides oracle SHIP."""

    def test_composite_block_forces_no_ship(self, iter_env):
        """Oracle says SHIP, but anomaly flags push composite score >= 70 → NO-SHIP."""
        env = iter_env
        config = env["config"]
        # Set thresholds low enough that our single-iteration run trips them:
        # turns > max_spec_turns AND wall > max_spec_wall → 30 + 20 = 50,
        # plus files > max_files → +15 = 65, plus barrier violation → +40 = 105 >= 70.
        config.anomaly_max_spec_turns = 0
        config.anomaly_max_spec_wall_s = 0
        config.anomaly_max_files_written = 0

        gate = gate_result_factory(satisfaction_score=95, pass_rate=95)
        # Implementer result with a barrier violation to push composite over threshold
        impl_result = agent_result_factory(
            role=AgentRole.IMPLEMENTER, barrier_violations=["leaked"],
        )
        results = [
            impl_result,
            agent_result_factory(role=AgentRole.REVIEWER),
            agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nAll good."),
        ]

        seq = _SequenceMock(results)
        with patch("darkfactory.pipeline.iteration.invoke_agent", new=seq), \
             patch("darkfactory.pipeline.iteration.run_quality_gate", return_value=gate), \
             patch("darkfactory.pipeline.iteration.count_files_changed", return_value=999):
            spec_run = _spec_run(env["spec_name"], effective_max_iterations=1)
            shipped = run_iteration_loop(
                spec_run, config, env["barriers"], env["project_dir"],
                events_file=env["events_file"],
            )

        assert shipped is False
        assert spec_run.iterations[0].oracle_verdict == "NO-SHIP"
