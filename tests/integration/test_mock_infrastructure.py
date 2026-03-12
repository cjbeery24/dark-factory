"""Meta-tests for the shared mock infrastructure.

Verify that SequenceMock, factory helpers, and mock fixtures
behave correctly before other suites depend on them.
"""

from __future__ import annotations

import pytest

from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.models.pipeline import PipelineStatus, SpecRun
from darkfactory.pipeline.quality_gate import GateResult

from tests.integration.conftest import (
    _SequenceMock,
    agent_result_factory,
    gate_result_factory,
    spec_run_factory,
)


@pytest.mark.integration
class TestSequenceMock:
    """SequenceMock pops results and records calls."""

    def test_pops_sequentially(self):
        seq = _SequenceMock(["a", "b", "c"])
        assert seq() == "a"
        assert seq() == "b"
        assert seq() == "c"

    def test_raises_on_exhaustion(self):
        seq = _SequenceMock(["a"])
        seq()
        with pytest.raises(IndexError, match="unexpected call #2"):
            seq()

    def test_records_calls(self):
        seq = _SequenceMock(["x", "y"])
        seq(1, 2, key="val")
        seq("a")
        assert len(seq.calls) == 2
        assert seq.calls[0] == ((1, 2), {"key": "val"})
        assert seq.calls[1] == (("a",), {})

    def test_remaining_count(self):
        seq = _SequenceMock(["a", "b", "c"])
        assert seq.remaining == 3
        seq()
        assert seq.remaining == 2

    def test_empty_raises_immediately(self):
        seq = _SequenceMock([])
        with pytest.raises(IndexError, match="unexpected call #1"):
            seq()


@pytest.mark.integration
class TestAgentResultFactory:
    """agent_result_factory builds AgentResult with defaults."""

    def test_defaults(self):
        result = agent_result_factory()
        assert isinstance(result, AgentResult)
        assert result.role == AgentRole.IMPLEMENTER
        assert result.exit_code == 0
        assert result.turns_used == 5
        assert result.output != ""

    def test_role_specific_output(self):
        for role in AgentRole:
            result = agent_result_factory(role=role)
            assert result.role is role
            assert result.output != ""

    def test_overrides(self):
        result = agent_result_factory(
            role=AgentRole.ORACLE,
            exit_code=1,
            output="NO-SHIP\nFailed.",
            turns_used=3,
        )
        assert result.role == AgentRole.ORACLE
        assert result.exit_code == 1
        assert result.output == "NO-SHIP\nFailed."
        assert result.turns_used == 3


@pytest.mark.integration
class TestSpecRunFactory:
    """spec_run_factory builds SpecRun with defaults."""

    def test_defaults(self):
        run = spec_run_factory()
        assert isinstance(run, SpecRun)
        assert run.spec_name == "add-multiply"
        assert run.status == PipelineStatus.RUNNING
        assert len(run.run_id) == 16

    def test_overrides(self):
        run = spec_run_factory(
            spec_name="fix-bug",
            status=PipelineStatus.SHIPPED,
            iteration=3,
        )
        assert run.spec_name == "fix-bug"
        assert run.status == PipelineStatus.SHIPPED
        assert run.iteration == 3


@pytest.mark.integration
class TestGateResultFactory:
    """gate_result_factory builds GateResult with defaults."""

    def test_defaults(self):
        result = gate_result_factory()
        assert isinstance(result, GateResult)
        assert result.exit_code == 0
        assert result.scenario_passed == 10
        assert result.scenario_failed == 0
        assert result.scenario_total == 10
        assert result.pass_rate == 100
        assert result.satisfaction_score == 90

    def test_failing_gate(self):
        result = gate_result_factory(exit_code=2, passed=0, failed=5)
        assert result.exit_code == 2
        assert result.scenario_total == 5
        assert result.pass_rate == 0
        assert result.satisfaction_score == 0

    def test_partial_pass(self):
        result = gate_result_factory(passed=7, failed=3)
        assert result.scenario_total == 10
        assert result.pass_rate == 70
