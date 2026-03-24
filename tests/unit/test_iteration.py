"""Unit tests for _step_implementation prompt construction in pipeline/iteration.py."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.helpers import TempDirTestCase, make_project
from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import IterationState, SpecRun
from darkfactory.pipeline.iteration import _step_implementation


def _make_config(max_iterations: int = 3) -> PipelineConfig:
    return PipelineConfig(
        model_id="claude-sonnet-4-20250514",
        source_dirs=["src"],
        max_iterations=max_iterations,
    )


def _make_spec_run(
    iteration: int,
    effective_max_iterations: int = 0,
    spec_name: str = "my-spec",
) -> SpecRun:
    sr = SpecRun(spec_name=spec_name)
    sr.iteration = iteration
    sr.effective_max_iterations = effective_max_iterations
    return sr


def _fake_agent_result() -> AgentResult:
    return AgentResult(role=AgentRole.IMPLEMENTER, exit_code=0)


def _capture_prompt(project_dir, config, spec_run, iter_state):
    """Invoke _step_implementation and return the prompt that was passed to invoke_agent."""
    captured: list[str] = []

    def fake_invoke(role, prompt, **kwargs):
        captured.append(prompt)
        return _fake_agent_result()

    with patch("darkfactory.pipeline.iteration.invoke_agent", side_effect=fake_invoke):
        _step_implementation(spec_run, iter_state, config, {}, project_dir, dry_run=True)

    assert captured, "_step_implementation did not call invoke_agent"
    return captured[0]


class TestScopeInstructionAbsentAtIteration1(TempDirTestCase):
    """At iteration 1, no scope instruction is added."""

    def test_scope_instruction_absent_at_iteration_1(self) -> None:
        project_dir = make_project(self.tmp_dir)
        config = _make_config(max_iterations=3)
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(number=1)

        prompt = _capture_prompt(project_dir, config, spec_run, iter_state)

        self.assertNotIn("Fix only", prompt)
        self.assertNotIn("final iteration", prompt)
        self.assertNotIn("Iteration 1 of", prompt)


class TestScopeInstructionPresentAtNonFinalIteration(TempDirTestCase):
    """At iteration 2 of 3, non-final scope instruction is added."""

    def test_scope_instruction_present_at_iteration_2(self) -> None:
        project_dir = make_project(self.tmp_dir)
        config = _make_config(max_iterations=3)
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        iter_state = IterationState(number=2)

        reviews_dir = project_dir / ".dark-factory" / "reviews"
        (reviews_dir / "my-spec-feedback-iter1.md").write_text("some feedback")

        prompt = _capture_prompt(project_dir, config, spec_run, iter_state)

        self.assertIn("Iteration 2 of 3", prompt)
        self.assertIn("Fix only what the feedback identifies", prompt)
        self.assertNotIn("final iteration", prompt)


class TestScopeInstructionFinalAtLastIteration(TempDirTestCase):
    """At the final iteration, the hard prohibition is used."""

    def test_scope_instruction_final_at_last_iteration(self) -> None:
        project_dir = make_project(self.tmp_dir)
        config = _make_config(max_iterations=3)
        spec_run = _make_spec_run(iteration=3, effective_max_iterations=3)
        iter_state = IterationState(number=3)

        reviews_dir = project_dir / ".dark-factory" / "reviews"
        (reviews_dir / "my-spec-feedback-iter2.md").write_text("some feedback")

        prompt = _capture_prompt(project_dir, config, spec_run, iter_state)

        self.assertIn("final iteration", prompt)
        self.assertIn("Do not make any other changes", prompt)
        # Non-final wording must not appear
        self.assertNotIn("Fix only what the feedback identifies", prompt)


class TestScopeInstructionSentinelFallback(TempDirTestCase):
    """When effective_max_iterations=0, max_iters falls back to config.max_iterations."""

    def test_sentinel_zero_uses_config_max_iterations(self) -> None:
        project_dir = make_project(self.tmp_dir)
        config = _make_config(max_iterations=2)
        # effective_max_iterations=0 is the sentinel; resolves to config.max_iterations=2
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=0)
        iter_state = IterationState(number=2)

        reviews_dir = project_dir / ".dark-factory" / "reviews"
        (reviews_dir / "my-spec-feedback-iter1.md").write_text("some feedback")

        prompt = _capture_prompt(project_dir, config, spec_run, iter_state)

        # iteration 2 == max_iters 2 → final tier
        self.assertIn("final iteration", prompt)
        self.assertIn("2 of 2", prompt)
        self.assertIn("Do not make any other changes", prompt)


class TestScopeInstructionDynamicMaxIterations(TempDirTestCase):
    """When effective_max_iterations < config.max_iterations, the tighter bound governs."""

    def test_effective_max_less_than_config_max(self) -> None:
        project_dir = make_project(self.tmp_dir)
        # Orchestrator set effective_max_iterations=2 but config allows 5
        config = _make_config(max_iterations=5)
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=2)
        iter_state = IterationState(number=2)

        reviews_dir = project_dir / ".dark-factory" / "reviews"
        (reviews_dir / "my-spec-feedback-iter1.md").write_text("some feedback")

        prompt = _capture_prompt(project_dir, config, spec_run, iter_state)

        # iteration 2 == effective_max_iterations 2 → final tier, not non-final
        self.assertIn("final iteration", prompt)
        self.assertIn("2 of 2", prompt)
        self.assertIn("Do not make any other changes", prompt)
        self.assertNotIn("Fix only what the feedback identifies", prompt)


class TestScopeInstructionMissingFeedbackUnaffected(TempDirTestCase):
    """When the feedback file is missing, no scope instruction is added."""

    def test_scope_instruction_missing_feedback_unaffected(self) -> None:
        project_dir = make_project(self.tmp_dir)
        config = _make_config(max_iterations=3)
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        iter_state = IterationState(number=2)

        # Do NOT create a feedback file — fallback path

        prompt = _capture_prompt(project_dir, config, spec_run, iter_state)

        self.assertNotIn("Fix only", prompt)
        self.assertNotIn("final iteration", prompt)
        self.assertNotIn("Iteration 2 of", prompt)


class TestScopeInstructionFreshStartReset(TempDirTestCase):
    """After FRESH_START resets to iteration 1, no scope instruction fires."""

    def test_scope_instruction_fresh_start_reset(self) -> None:
        project_dir = make_project(self.tmp_dir)
        config = _make_config(max_iterations=3)
        # Simulate a fresh-start reset: iteration is back to 1 even though
        # effective_max_iterations is set (it was set before the reset).
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)

        prompt = _capture_prompt(project_dir, config, spec_run, iter_state)

        self.assertNotIn("Fix only", prompt)
        self.assertNotIn("final iteration", prompt)


if __name__ == "__main__":
    unittest.main()
