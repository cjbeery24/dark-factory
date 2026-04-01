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
    _step_implementation,
    _step_oracle,
    _parse_crashed_test_names,
    _parse_failing_test_names,
    apply_classification_routing,
    parse_feedback_classifications,
)


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

    def test_env_issue_all_iter1_escalates(self) -> None:
        """All ENV_ISSUE at iteration 1 → escalation recommended."""
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "ENV_ISSUE", "FR-2": "ENV_ISSUE"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertTrue(spec_run.escalation_recommended)
        self.assertEqual(spec_run.escalation_category, "ENV_ISSUE")
        self.assertIn("ENV_ISSUE", spec_run.escalation_reason)

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

    def test_env_issue_short_circuit_at_iter1(self) -> None:
        """ENV_ISSUE escalation should fire at iteration >= 1 (not >= 2)."""
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "ENV_ISSUE", "FR-2": "ENV_ISSUE"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertTrue(spec_run.escalation_recommended)
        self.assertEqual(spec_run.escalation_category, "ENV_ISSUE")
        # ENV_ISSUE short-circuit: min_iter=1
        min_iter = 1 if spec_run.escalation_category == "ENV_ISSUE" else 2
        self.assertGreaterEqual(spec_run.iteration, min_iter)

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


if __name__ == "__main__":
    unittest.main()
