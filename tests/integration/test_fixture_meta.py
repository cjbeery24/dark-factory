"""Meta-tests for integration test fixtures.

Validates that _SequenceMock, mock_invoke_agent, agent_result_factory,
and spec_run_factory behave correctly — so downstream tests can rely
on them without question.
"""

from __future__ import annotations

import pytest

from darkfactory.models.agent import AgentRole
from darkfactory.models.pipeline import PipelineStatus

from tests.integration.conftest import (
    _SequenceMock,
    agent_result_factory,
    gate_result_factory,
    spec_run_factory,
)


@pytest.mark.integration
class TestSequenceMock:
    """_SequenceMock pops results in order and raises on overrun."""

    def test_pops_sequentially(self):
        seq = _SequenceMock([10, 20, 30])
        assert seq() == 10
        assert seq() == 20
        assert seq() == 30
        assert seq.remaining == 0

    def test_raises_on_overrun(self):
        seq = _SequenceMock([1])
        seq()
        with pytest.raises(IndexError, match="exhausted"):
            seq()

    def test_records_calls(self):
        seq = _SequenceMock(["a", "b"])
        seq("x", key="val")
        seq("y")
        assert seq.call_count == 2
        assert seq.calls[0] == (("x",), {"key": "val"})
        assert seq.calls[1] == (("y",), {})


@pytest.mark.integration
class TestFactoryDefaults:
    """Factory helpers produce valid objects with sensible defaults."""

    def test_agent_result_factory_defaults(self):
        result = agent_result_factory()
        assert result.role == AgentRole.IMPLEMENTER
        assert result.exit_code == 0
        assert result.turns_used == 5
        assert result.output == "Implemented FR-1, FR-2, FR-3. Files: src/calculator.py"
        assert result.wall_seconds == 10.0

    def test_spec_run_factory_defaults(self):
        run = spec_run_factory()
        assert run.spec_name == "add-multiply"
        assert len(run.run_id) == 16
        assert run.status == PipelineStatus.RUNNING

    def test_gate_result_factory_defaults(self):
        gate = gate_result_factory()
        assert gate.exit_code == 0
        assert gate.scenario_passed == 10
        assert gate.scenario_failed == 0
        assert gate.scenario_total == 10
        assert gate.pass_rate == 100
        assert gate.satisfaction_score == 90


@pytest.mark.integration
class TestMockInvokeAgentWiring:
    """mock_invoke_agent fixture patches correctly and cleans up."""

    def test_mock_invoke_agent_patches_and_cleans_up(self, mock_invoke_agent):
        result = agent_result_factory(role=AgentRole.SPEC_VALIDATOR)
        seq = mock_invoke_agent([result])

        from darkfactory.pipeline.agents import invoke_agent
        got = invoke_agent(AgentRole.SPEC_VALIDATOR, "test prompt")

        assert got.role == AgentRole.SPEC_VALIDATOR
        assert seq.call_count == 1
        assert seq.calls[0][0][0] == AgentRole.SPEC_VALIDATOR
        assert seq.remaining == 0
