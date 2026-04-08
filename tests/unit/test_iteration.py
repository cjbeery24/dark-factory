"""Unit tests for _step_implementation prompt construction in pipeline/iteration.py."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from tests.helpers import TempDirTestCase, make_project
from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import IterationState, SpecRun
from darkfactory.pipeline.iteration import (
    _step_classify,
    _step_implementation,
    _step_oracle,
    _parse_crashed_test_names,
    _parse_failing_test_names,
    apply_classification_routing,
    parse_defects_json,
    parse_feedback_classifications,
)
from darkfactory.pipeline.quality_gate import GateResult


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


class TestParseFeedbackClassifications(unittest.TestCase):
    """Tests for parse_feedback_classifications."""

    def test_valid_json_block(self) -> None:
        fb = 'Some text\n<!-- CLASSIFICATIONS: {"FR-2": "IMPL_BUG", "FR-7": "SPEC_AMBIGUOUS"} -->\nMore text'
        result = parse_feedback_classifications(fb)
        self.assertEqual(result, {"FR-2": "IMPL_BUG", "FR-7": "SPEC_AMBIGUOUS"})

    def test_all_classification_types(self) -> None:
        fb = '<!-- CLASSIFICATIONS: {"A": "IMPL_BUG", "B": "SPEC_AMBIGUOUS", "C": "SCENARIO_CRASH", "D": "ENV_ISSUE", "E": "BRITTLE"} -->'
        result = parse_feedback_classifications(fb)
        self.assertEqual(len(result), 5)
        self.assertEqual(result["C"], "SCENARIO_CRASH")
        self.assertEqual(result["D"], "ENV_ISSUE")

    def test_backward_compat_fixable(self) -> None:
        fb = '<!-- CLASSIFICATIONS: {"FR-2": "FIXABLE", "FR-7": "AMBIGUOUS"} -->'
        result = parse_feedback_classifications(fb)
        self.assertEqual(result, {"FR-2": "IMPL_BUG", "FR-7": "SPEC_AMBIGUOUS"})

    def test_case_insensitive(self) -> None:
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "impl_bug", "FR-2": "Spec_Ambiguous"} -->'
        result = parse_feedback_classifications(fb)
        self.assertEqual(result, {"FR-1": "IMPL_BUG", "FR-2": "SPEC_AMBIGUOUS"})

    def test_strips_whitespace_keys(self) -> None:
        fb = '<!-- CLASSIFICATIONS: {"FR-2 ": "IMPL_BUG"} -->'
        result = parse_feedback_classifications(fb)
        self.assertIn("FR-2", result)

    def test_missing_block_returns_empty(self) -> None:
        self.assertEqual(parse_feedback_classifications("no block here"), {})

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(parse_feedback_classifications(""), {})

    def test_malformed_json_returns_empty(self) -> None:
        fb = '<!-- CLASSIFICATIONS: {bad json} -->'
        self.assertEqual(parse_feedback_classifications(fb), {})

    def test_non_dict_json_returns_empty(self) -> None:
        fb = '<!-- CLASSIFICATIONS: ["IMPL_BUG"] -->'
        self.assertEqual(parse_feedback_classifications(fb), {})

    def test_multiple_flat_keys(self) -> None:
        # Greedy regex should capture full JSON including all key-value pairs.
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG", "meta": "BRITTLE"} -->'
        result = parse_feedback_classifications(fb)
        self.assertEqual(result["FR-1"], "IMPL_BUG")

    def test_unknown_classification_defaults_to_impl_bug(self) -> None:
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "UNKNOWN_TYPE"} -->'
        result = parse_feedback_classifications(fb)
        self.assertEqual(result, {"FR-1": "IMPL_BUG"})

    def test_multiline_json(self) -> None:
        fb = '<!-- CLASSIFICATIONS: {\n  "FR-1": "IMPL_BUG",\n  "FR-2": "BRITTLE"\n} -->'
        result = parse_feedback_classifications(fb)
        self.assertEqual(result, {"FR-1": "IMPL_BUG", "FR-2": "BRITTLE"})


class TestClassificationRouting(TempDirTestCase):
    """Tests for the classification routing logic that lives in run_iteration_loop.

    Since the routing logic is embedded in run_iteration_loop, these tests
    exercise it indirectly by replicating the inline logic with the same
    parse_feedback_classifications function and SpecRun/IterationState models.
    """

    @staticmethod
    def _apply_routing(
        spec_run: SpecRun,
        iter_state: IterationState,
        feedback_text: str,
    ) -> None:
        """Delegate to the real extracted function."""
        apply_classification_routing(spec_run, iter_state, feedback_text)

    def test_escalation_recommended_on_majority_ambiguous(self) -> None:
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        iter_state = IterationState(number=2)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "SPEC_AMBIGUOUS", "FR-2": "SPEC_AMBIGUOUS", "FR-3": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertTrue(spec_run.escalation_recommended)
        self.assertIn("2 SPEC_AMBIGUOUS", spec_run.escalation_reason)

    def test_no_escalation_on_majority_impl_bug(self) -> None:
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        iter_state = IterationState(number=2)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG", "FR-2": "IMPL_BUG", "FR-3": "SPEC_AMBIGUOUS"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertFalse(spec_run.escalation_recommended)

    def test_escalation_reset_clears_previous_latch(self) -> None:
        """Verify that a previously-set escalation gets cleared when new
        classifications no longer warrant it."""
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        spec_run.escalation_recommended = True
        spec_run.escalation_reason = "stale reason from previous iteration"
        iter_state = IterationState(number=2)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG", "FR-2": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertFalse(spec_run.escalation_recommended)
        self.assertEqual(spec_run.escalation_reason, "")

    def test_short_circuit_at_iteration_2_with_escalation(self) -> None:
        """When escalation is recommended at iteration >= 2, the pipeline
        should short-circuit (return False in the real loop)."""
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        iter_state = IterationState(number=2)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "SPEC_AMBIGUOUS", "FR-2": "BRITTLE"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        # Both conditions for short-circuit are met:
        self.assertTrue(spec_run.escalation_recommended)
        self.assertGreaterEqual(spec_run.iteration, 2)

    def test_no_short_circuit_at_iteration_1(self) -> None:
        """At iteration 1, escalation_recommended alone should NOT short-circuit."""
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "SPEC_AMBIGUOUS", "FR-2": "BRITTLE"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertTrue(spec_run.escalation_recommended)
        # iteration=1 < 2, so short-circuit condition is NOT met
        self.assertLess(spec_run.iteration, 2)

    def test_classification_counts_populated(self) -> None:
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        iter_state = IterationState(number=2)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG", "FR-2": "IMPL_BUG", "FR-3": "BRITTLE"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertEqual(iter_state.classification_counts, {"IMPL_BUG": 2, "BRITTLE": 1})
        self.assertEqual(len(spec_run.classification_history), 1)

    def test_env_issue_all_iter1_no_escalation(self) -> None:
        """All ENV_ISSUE at iteration 1 → no escalation (Implementer gets retry)."""
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "ENV_ISSUE", "FR-2": "ENV_ISSUE"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertFalse(spec_run.escalation_recommended)

    def test_env_issue_majority_iter2_escalates(self) -> None:
        """Majority ENV_ISSUE at iteration 2 → escalation recommended."""
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        iter_state = IterationState(number=2)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "ENV_ISSUE", "FR-2": "ENV_ISSUE", "FR-3": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertTrue(spec_run.escalation_recommended)
        self.assertEqual(spec_run.escalation_category, "ENV_ISSUE")

    def test_env_issue_minority_iter1_no_escalation(self) -> None:
        """Minority ENV_ISSUE at iteration 1 → no escalation."""
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "ENV_ISSUE", "FR-2": "IMPL_BUG", "FR-3": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertFalse(spec_run.escalation_recommended)

    def test_env_issue_escalation_requires_iter2(self) -> None:
        """ENV_ISSUE escalation requires iteration >= 2."""
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        iter_state = IterationState(number=2)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "ENV_ISSUE", "FR-2": "ENV_ISSUE"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertTrue(spec_run.escalation_recommended)
        self.assertEqual(spec_run.escalation_category, "ENV_ISSUE")

    def test_consecutive_server_errors_escalates(self) -> None:
        """server_error on 2 consecutive iterations → escalation."""
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        spec_run.consecutive_server_errors = 1  # already failed once
        iter_state = IterationState(number=2)
        iter_state.server_error = "drizzle-kit push failed"
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertTrue(spec_run.escalation_recommended)
        self.assertEqual(spec_run.escalation_category, "ENV_ISSUE")
        self.assertIn("server_setup_cmd", spec_run.escalation_reason)
        self.assertEqual(spec_run.consecutive_server_errors, 2)

    def test_server_error_resets_on_success(self) -> None:
        """server_error empty → resets consecutive counter."""
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        spec_run.consecutive_server_errors = 1
        iter_state = IterationState(number=2)
        iter_state.server_error = ""  # server succeeded
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertEqual(spec_run.consecutive_server_errors, 0)
        self.assertFalse(spec_run.escalation_recommended)


class TestForcedReclassification(TempDirTestCase):
    """Tests for forced mechanical reclassification of persistent IMPL_BUG."""

    @staticmethod
    def _apply_routing(
        spec_run: SpecRun,
        iter_state: IterationState,
        feedback_text: str,
    ) -> None:
        """Delegate to the real extracted function."""
        apply_classification_routing(spec_run, iter_state, feedback_text)

    def test_reclassifies_persistent_impl_bug(self) -> None:
        """IMPL_BUG in both iter 1 and 2 with high stable ratio → SPEC_AMBIGUOUS."""
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        spec_run.stable_failure_ratio = 1.0
        spec_run.classification_history.append({"FR-1": "IMPL_BUG", "FR-2": "IMPL_BUG"})
        iter_state = IterationState(number=2)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG", "FR-2": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertTrue(spec_run.escalation_recommended)
        self.assertIn("SPEC_AMBIGUOUS", spec_run.escalation_reason)
        self.assertEqual(iter_state.classification_counts.get("SPEC_AMBIGUOUS"), 2)
        self.assertEqual(iter_state.classification_counts.get("IMPL_BUG", 0), 0)

    def test_no_reclassify_without_history(self) -> None:
        """First iteration: no previous history → no reclassification."""
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        spec_run.stable_failure_ratio = 1.0
        iter_state = IterationState(number=1)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG", "FR-2": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertFalse(spec_run.escalation_recommended)
        self.assertEqual(iter_state.classification_counts.get("IMPL_BUG"), 2)

    def test_no_reclassify_low_stable_ratio(self) -> None:
        """stable_failure_ratio <= 0.5 → no reclassification."""
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        spec_run.stable_failure_ratio = 0.3
        spec_run.classification_history.append({"FR-1": "IMPL_BUG"})
        iter_state = IterationState(number=2)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertFalse(spec_run.escalation_recommended)
        self.assertEqual(iter_state.classification_counts.get("IMPL_BUG"), 1)

    def test_only_persistent_sections_reclassified(self) -> None:
        """Only sections IMPL_BUG in BOTH iterations get reclassified."""
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        spec_run.stable_failure_ratio = 0.8
        spec_run.classification_history.append(
            {"FR-1": "IMPL_BUG", "FR-2": "SPEC_AMBIGUOUS"}
        )
        iter_state = IterationState(number=2)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG", "FR-2": "IMPL_BUG", "FR-3": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        # FR-1: IMPL_BUG both iters → reclassified to SPEC_AMBIGUOUS
        # FR-2: SPEC_AMBIGUOUS in iter 1, IMPL_BUG in iter 2 → not reclassified
        # FR-3: not in iter 1 → not reclassified
        self.assertEqual(iter_state.classification_counts.get("SPEC_AMBIGUOUS"), 1)
        self.assertEqual(iter_state.classification_counts.get("IMPL_BUG"), 2)

    def test_history_preserves_raw_output(self) -> None:
        """classification_history stores raw Oracle output, not reclassified."""
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        spec_run.stable_failure_ratio = 1.0
        spec_run.classification_history.append({"FR-1": "IMPL_BUG"})
        iter_state = IterationState(number=2)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        # History entry is the raw Oracle output (IMPL_BUG)
        self.assertEqual(spec_run.classification_history[-1]["FR-1"], "IMPL_BUG")
        # But routing counts reflect reclassification (SPEC_AMBIGUOUS)
        self.assertEqual(iter_state.classification_counts.get("SPEC_AMBIGUOUS"), 1)


    def test_no_reclassify_when_server_error_current(self) -> None:
        """Server error in current iteration → skip reclassification."""
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        spec_run.stable_failure_ratio = 1.0
        spec_run.classification_history.append({"FR-1": "IMPL_BUG", "FR-2": "IMPL_BUG"})
        iter_state = IterationState(number=2)
        iter_state.server_error = "server_setup_cmd failed"
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG", "FR-2": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        # IMPL_BUG should NOT be reclassified to SPEC_AMBIGUOUS
        self.assertEqual(iter_state.classification_counts.get("IMPL_BUG"), 2)
        self.assertEqual(iter_state.classification_counts.get("SPEC_AMBIGUOUS", 0), 0)

    def test_no_reclassify_when_server_error_previous(self) -> None:
        """Server error in previous iteration → skip reclassification."""
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        spec_run.stable_failure_ratio = 1.0
        spec_run.classification_history.append({"FR-1": "IMPL_BUG", "FR-2": "IMPL_BUG"})
        # Populate iterations so spec_run.iterations[-2] exists
        prev_iter = IterationState(number=1)
        prev_iter.server_error = "server crashed last time"
        spec_run.iterations.append(prev_iter)
        iter_state = IterationState(number=2)
        spec_run.iterations.append(iter_state)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG", "FR-2": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        # IMPL_BUG should NOT be reclassified
        self.assertEqual(iter_state.classification_counts.get("IMPL_BUG"), 2)
        self.assertEqual(iter_state.classification_counts.get("SPEC_AMBIGUOUS", 0), 0)

    def test_reclassify_with_different_key_suffix(self) -> None:
        """Prev has 'FR-4', current has 'FR-4 Health endpoint' → reclassification fires."""
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        spec_run.stable_failure_ratio = 1.0
        spec_run.classification_history.append({"FR-4": "IMPL_BUG"})
        iter_state = IterationState(number=2)
        fb = '<!-- CLASSIFICATIONS: {"FR-4 Health endpoint": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertEqual(iter_state.classification_counts.get("SPEC_AMBIGUOUS"), 1)
        self.assertEqual(iter_state.classification_counts.get("IMPL_BUG", 0), 0)

    def test_reclassify_exact_match_still_works(self) -> None:
        """Exact key match still works after normalization change."""
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        spec_run.stable_failure_ratio = 1.0
        spec_run.classification_history.append({"FR-4": "IMPL_BUG"})
        iter_state = IterationState(number=2)
        fb = '<!-- CLASSIFICATIONS: {"FR-4": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertEqual(iter_state.classification_counts.get("SPEC_AMBIGUOUS"), 1)
        self.assertEqual(iter_state.classification_counts.get("IMPL_BUG", 0), 0)


class TestParseDefectsJson(TempDirTestCase):
    """Phase 4.6b.0.vi: unit-test gate for the defects.json parser."""

    def _write(self, payload: dict) -> "Path":
        p = self.tmp_dir / "defects.json"
        p.write_text(json.dumps(payload))
        return p

    def test_missing_file_returns_missing_error(self) -> None:
        rolled, err = parse_defects_json(self.tmp_dir / "nope.json")
        self.assertEqual(rolled, {})
        self.assertEqual(err, "missing")

    def test_unparseable_returns_unparseable_error(self) -> None:
        p = self.tmp_dir / "defects.json"
        p.write_text("{not json")
        rolled, err = parse_defects_json(p)
        self.assertEqual(rolled, {})
        self.assertEqual(err, "unparseable")

    def test_classifier_error_propagates(self) -> None:
        p = self._write({"schema_version": 1, "error": "count_mismatch", "defects": []})
        rolled, err = parse_defects_json(p)
        self.assertEqual(rolled, {})
        self.assertEqual(err, "count_mismatch")

    def test_rollup_uses_cited_anchor_ids(self) -> None:
        p = self._write({
            "schema_version": 1, "error": None,
            "defects": [
                {"test_name": "x", "category": "IMPL_BUG",
                 "cited_anchor_ids": ["FR-2"]},
                {"test_name": "y", "category": "BRITTLE",
                 "cited_anchor_ids": ["FR-3"]},
            ],
        })
        rolled, err = parse_defects_json(p)
        self.assertEqual(err, "")
        self.assertEqual(rolled, {"FR-2": "IMPL_BUG", "FR-3": "BRITTLE"})

    def test_rollup_falls_back_to_embedded_tag(self) -> None:
        p = self._write({
            "schema_version": 1, "error": None,
            "defects": [
                {"test_name": "handles comma [EC-5]", "category": "IMPL_BUG"},
            ],
        })
        rolled, _ = parse_defects_json(p)
        self.assertEqual(rolled, {"EC-5": "IMPL_BUG"})

    def test_rollup_picks_most_severe_on_divergence(self) -> None:
        p = self._write({
            "schema_version": 1, "error": None,
            "defects": [
                {"test_name": "a", "category": "BRITTLE",
                 "cited_anchor_ids": ["FR-1"]},
                {"test_name": "b", "category": "IMPL_BUG",
                 "cited_anchor_ids": ["FR-1"]},
                {"test_name": "c", "category": "UNKNOWN",
                 "cited_anchor_ids": ["FR-1"]},
            ],
        })
        rolled, _ = parse_defects_json(p)
        # IMPL_BUG wins the priority chain.
        self.assertEqual(rolled, {"FR-1": "IMPL_BUG"})

    def test_invalid_category_skipped(self) -> None:
        p = self._write({
            "schema_version": 1, "error": None,
            "defects": [
                {"test_name": "a", "category": "NOT_A_CATEGORY",
                 "cited_anchor_ids": ["FR-1"]},
                {"test_name": "b", "category": "IMPL_BUG",
                 "cited_anchor_ids": ["FR-2"]},
            ],
        })
        rolled, _ = parse_defects_json(p)
        self.assertEqual(rolled, {"FR-2": "IMPL_BUG"})

    def test_golden_shape_from_agent_definition(self) -> None:
        """Phase 4.6b.1c task iv: schema contract test.

        Locks the canonical defect-classification.json shape against the
        example in agents/defect-classifier.md. Run 9 will be the first
        run whose classifier actually writes a real file — this test
        ensures the consumer (parse_defects_json) stays in sync with the
        producer contract before Oracle starts reading new fields. If the
        agent definition's example shape changes, update this test in the
        same commit so the contract drift is auditable.
        """
        golden = {
            "iteration": 1,
            "schema_version": 1,
            "error": None,
            "defects": [
                {
                    "test_name": "handles comma decimal input [AC-3]",
                    "category": "IMPL_BUG",
                    "cited_anchor_ids": ["AC-3"],
                    "scope_filter_eligible": False,
                    "rationale": "Test exercises AC-3's stated predicate; implementation returns NaN for '1,23'.",
                },
            ],
        }
        p = self._write(golden)
        rolled, err = parse_defects_json(p)
        self.assertEqual(err, "")
        self.assertEqual(rolled, {"AC-3": "IMPL_BUG"})

        # All category values from agents/defect-classifier.md must be
        # accepted by the parser. If a new category is added to the agent
        # definition, it must also be added to _VALID_CLASSIFICATIONS, and
        # this test will fail otherwise.
        expected_categories = {
            "IMPL_BUG", "SPEC_AMBIGUOUS", "SCENARIO_CRASH",
            "ENV_ISSUE", "BRITTLE", "OUT_OF_SCOPE", "UNKNOWN",
        }
        from darkfactory.pipeline.iteration import _VALID_CLASSIFICATIONS
        self.assertEqual(set(_VALID_CLASSIFICATIONS), expected_categories)


class TestDefectsJsonSourceOfTruth(TempDirTestCase):
    """Phase 4.6b.0.v: when defects_path is supplied, it overrides Oracle prose.

    Regression guard proving that the source of truth for classifications
    actually moved from parsing CLASSIFICATIONS blocks in Oracle prose to
    reading the Defect Classifier's JSON output.
    """

    def test_defects_json_overrides_wrong_oracle_block(self) -> None:
        defects_path = self.tmp_dir / "defects-iter1.json"
        defects_path.write_text(json.dumps({
            "iteration": 1,
            "schema_version": 1,
            "error": None,
            "defects": [
                {
                    "test_name": "validates input [FR-2]",
                    "category": "BRITTLE",
                    "cited_anchor_ids": ["FR-2"],
                    "scope_filter_eligible": False,
                    "rationale": "asserts on log format",
                },
            ],
        }))
        # Deliberately-wrong Oracle prose block — must be IGNORED.
        lying_feedback = '<!-- CLASSIFICATIONS: {"FR-2": "IMPL_BUG"} -->'
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)

        apply_classification_routing(
            spec_run, iter_state, lying_feedback,
            defects_path=defects_path,
        )

        # Must reflect defects.json (BRITTLE), not the lying Oracle block.
        self.assertEqual(iter_state.classification_counts.get("BRITTLE"), 1)
        self.assertEqual(iter_state.classification_counts.get("IMPL_BUG", 0), 0)

    def test_defects_json_error_does_not_fall_back_to_prose(self) -> None:
        defects_path = self.tmp_dir / "defects-iter1.json"
        defects_path.write_text(json.dumps({
            "iteration": 1,
            "schema_version": 1,
            "error": "count_mismatch",
            "defects": [],
        }))
        stale_feedback = '<!-- CLASSIFICATIONS: {"FR-2": "IMPL_BUG"} -->'
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)

        apply_classification_routing(
            spec_run, iter_state, stale_feedback,
            defects_path=defects_path,
        )

        # Errored defects.json → empty classifications. No silent downgrade
        # to parsing the Oracle prose. Oracle itself is required to NO-SHIP.
        self.assertEqual(iter_state.classification_counts, {})


class TestOracleCrashContext(TempDirTestCase):
    """Tests for crash context in the Oracle prompt."""

    def _capture_oracle_prompt(self, spec_run, iter_state, config=None):
        """Invoke _step_oracle and return the prompt passed to invoke_agent."""
        config = config or _make_config()
        project_dir = make_project(self.tmp_dir)
        captured: list[str] = []

        def fake_invoke(role, prompt, **kwargs):
            captured.append(prompt)
            return AgentResult(role=AgentRole.ORACLE, exit_code=0, output="SHIP\nLooks good.")

        with patch("darkfactory.pipeline.iteration.invoke_agent", side_effect=fake_invoke):
            _step_oracle(spec_run, iter_state, config, {}, project_dir, dry_run=True)

        assert captured, "_step_oracle did not call invoke_agent"
        return captured[0]

    def test_crash_context_present_when_crashes(self) -> None:
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1,
            scenario_pass_count=7,
            scenario_fail_count=3,
            scenario_total=10,
            pass_rate=87,  # crash-adjusted (7/8)
            scenario_crashed=2,
            scenario_assert_failed=1,
        )
        prompt = self._capture_oracle_prompt(spec_run, iter_state)
        self.assertIn("Crash-adjusted pass rate", prompt)
        self.assertIn("2 scenario(s) crashed", prompt)
        self.assertIn("Raw pass rate (including crashes): 70%", prompt)
        self.assertIn("Adjusted pass rate (excluding crashes): 87%", prompt)

    def test_no_crash_context_when_zero_crashes(self) -> None:
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1,
            scenario_pass_count=8,
            scenario_fail_count=2,
            scenario_total=10,
            pass_rate=80,
            scenario_crashed=0,
        )
        prompt = self._capture_oracle_prompt(spec_run, iter_state)
        self.assertNotIn("Crash-adjusted", prompt)
        self.assertNotIn("crashed", prompt)

    def test_raw_rate_zero_total_safe(self) -> None:
        """When scenario_total is 0, raw_rate is 0 (no division by zero)."""
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1,
            scenario_pass_count=0,
            scenario_fail_count=0,
            scenario_total=0,
            pass_rate=0,
            scenario_crashed=0,
        )
        # scenario_crashed=0 → early exit, no crash context
        prompt = self._capture_oracle_prompt(spec_run, iter_state)
        self.assertNotIn("Crash-adjusted", prompt)


class TestParseCrashedTestNames(TempDirTestCase):
    """Tests for _parse_crashed_test_names()."""

    def _write_results(self, entries: list[dict]) -> None:
        reports = self.tmp_dir / ".dark-factory" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        path = reports / "my-spec-test-results-iter1.json"
        path.write_text(json.dumps(entries))

    def test_returns_crash_names(self) -> None:
        self._write_results([
            {"name": "test A", "status": "failed", "error_type": "crash"},
            {"name": "test B", "status": "failed", "error_type": "assertion"},
            {"name": "test C", "status": "passed", "error_type": "assertion"},
        ])
        names = _parse_crashed_test_names(self.tmp_dir, "my-spec", 1)
        self.assertEqual(names, ["test A"])

    def test_empty_on_no_crashes(self) -> None:
        self._write_results([
            {"name": "test A", "status": "failed", "error_type": "assertion"},
        ])
        names = _parse_crashed_test_names(self.tmp_dir, "my-spec", 1)
        self.assertEqual(names, [])

    def test_missing_file(self) -> None:
        names = _parse_crashed_test_names(self.tmp_dir, "my-spec", 1)
        self.assertEqual(names, [])

    def test_malformed_json(self) -> None:
        reports = self.tmp_dir / ".dark-factory" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "my-spec-test-results-iter1.json").write_text("not json")
        names = _parse_crashed_test_names(self.tmp_dir, "my-spec", 1)
        self.assertEqual(names, [])

    def test_multiple_crashes(self) -> None:
        self._write_results([
            {"name": "crash 1", "status": "failed", "error_type": "crash"},
            {"name": "crash 2", "status": "failed", "error_type": "crash"},
            {"name": "pass", "status": "passed", "error_type": "assertion"},
        ])
        names = _parse_crashed_test_names(self.tmp_dir, "my-spec", 1)
        self.assertEqual(names, ["crash 1", "crash 2"])


class TestStepClassifyGateResultAttribute(TempDirTestCase):
    """Regression for Phase 4.6b.1b / Run 7 blocker.

    Before the fix, _step_classify read ``gate_result.scenario_fail_count``
    — the name on IterationState, not GateResult — and crashed with
    AttributeError whenever the defect-classifier branch was entered with
    pass_rate < 100. The bridge at iteration.py:1338 masked the drift for
    runs that never reached iter-1 quality-gate completion with failures.

    This test drives _step_classify with a real GateResult carrying
    scenario_failed > 0 and asserts the failing-count numbers show up in
    the classifier prompt. Any future attempt to read a nonexistent
    attribute on GateResult will re-raise AttributeError and fail this test
    at CI time instead of at the next multi-minute pipeline run.
    """

    def _capture_classifier_prompt(self, gate_result: GateResult) -> str:
        project_dir = make_project(self.tmp_dir)
        (project_dir / ".dark-factory" / "reports").mkdir(parents=True, exist_ok=True)
        (project_dir / ".dark-factory" / "reports" / "my-spec-quality-gate-summary.md").write_text("stub")
        config = _make_config()
        spec_run = _make_spec_run(iteration=1)
        captured: list[str] = []

        def fake_invoke(role, prompt, **kwargs):
            captured.append(prompt)
            # Write a valid defects stub so _step_classify doesn't stamp an error.
            defects_path = project_dir / ".dark-factory" / "reports" / "my-spec-defects-iter1.json"
            defects_path.write_text(json.dumps({
                "iteration": 1,
                "schema_version": 1,
                "defects": [
                    {"test_name": "t1", "category": "IMPL_BUG"},
                    {"test_name": "t2", "category": "IMPL_BUG"},
                    {"test_name": "t3", "category": "SCENARIO_CRASH"},
                ],
            }))
            return AgentResult(role=AgentRole.DEFECT_CLASSIFIER, exit_code=0)

        with patch("darkfactory.pipeline.iteration.invoke_agent", side_effect=fake_invoke), \
             patch("darkfactory.pipeline.iteration.step_begin"):
            _step_classify(spec_run, gate_result, config, {}, project_dir, dry_run=True)

        assert captured, "_step_classify did not call invoke_agent"
        return captured[0]

    def test_reads_scenario_failed_not_scenario_fail_count(self) -> None:
        """Canonical GateResult field is scenario_failed; _step_classify must use it."""
        gate_result = GateResult(
            exit_code=1,
            scenario_passed=7,
            scenario_failed=2,
            scenario_total=10,
            pass_rate=70,
            scenario_crashed=1,
        )
        prompt = self._capture_classifier_prompt(gate_result)
        # Prompt injects scenario_fail_count label with the scenario_failed *value*.
        self.assertIn("scenario_fail_count: 2", prompt)
        self.assertIn("scenario_crashed:    1", prompt)
        # expected_defects = scenario_failed + scenario_crashed = 3.
        self.assertIn("expected_defects:    3", prompt)
        self.assertIn("Emit exactly 3 records", prompt)


if __name__ == "__main__":
    unittest.main()
