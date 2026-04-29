"""Unit tests for _step_implementation prompt construction in pipeline/iteration.py."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import TempDirTestCase, make_project
from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import IterationState, SpecRun
from darkfactory.pipeline.iteration import (
    _step_classify,
    _step_implementation,
    _step_oracle,
    _step_review,
    _capture_iteration_end_sha,
    _format_cascade_summary,
    _format_defects_inline,
    _format_holdouts_inline,
    _format_pre_gate_summary_inline,
    _format_prior_iteration_context_inline,
    _format_substrate_findings_inline,
    _parse_crashed_test_names,
    _parse_failing_test_names,
    _run_preflight_with_bounded_retry,
    apply_classification_routing,
    parse_defects_json,
    parse_feedback_classifications,
)
from darkfactory.pipeline.quality_gate import PreGateResult
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

    def test_skip_majority_triggers_infra_escalation(self) -> None:
        """Phase 7: >50% skipped tests → ENV_ISSUE escalation."""
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        iter_state.scenario_skipped = 18
        iter_state.scenario_total = 8  # passed+failed only
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertTrue(spec_run.escalation_recommended)
        self.assertEqual(spec_run.escalation_category, "ENV_ISSUE")
        self.assertIn("Infrastructure failure", spec_run.escalation_reason)
        self.assertIn("18/26", spec_run.escalation_reason)

    def test_skip_below_threshold_no_infra_escalation(self) -> None:
        """Phase 7: <50% skipped → no infra escalation from skip logic."""
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        iter_state.scenario_skipped = 2
        iter_state.scenario_total = 20  # 20 ran, 2 skipped → 2/22 = 9%
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        # No escalation from skip logic (IMPL_BUG majority doesn't trigger either)
        self.assertFalse(spec_run.escalation_recommended)

    def test_skip_escalation_still_tracks_server_errors(self) -> None:
        """Phase 7: skip infra escalation must not skip server error tracking."""
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        spec_run.consecutive_server_errors = 1  # already failed once
        iter_state = IterationState(number=2)
        iter_state.scenario_skipped = 18
        iter_state.scenario_total = 8
        iter_state.server_error = "connection refused"
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        # Infra escalation set from skip logic
        self.assertTrue(spec_run.escalation_recommended)
        # Server error counter also incremented (no early return)
        self.assertEqual(spec_run.consecutive_server_errors, 2)

    def test_env_issue_escalation_fires_at_iteration_1(self) -> None:
        """Phase 8: ENV_ISSUE from skip-infra should escalate at iteration 1,
        not wait for iteration 2 like other categories."""
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        iter_state.scenario_skipped = 18
        iter_state.scenario_total = 8
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertTrue(spec_run.escalation_recommended)
        self.assertEqual(spec_run.escalation_category, "ENV_ISSUE")
        # The hard gate check: iteration=1 >= threshold=1 for ENV_ISSUE
        threshold = 1  # ENV_ISSUE lowers threshold to 1
        self.assertGreaterEqual(spec_run.iteration, threshold)

    def test_non_env_issue_does_not_fire_at_iteration_1(self) -> None:
        """Phase 8: non-ENV_ISSUE escalation should still require iteration >= 2."""
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "SPEC_AMBIGUOUS", "FR-2": "SPEC_AMBIGUOUS", "FR-3": "IMPL_BUG"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        self.assertTrue(spec_run.escalation_recommended)
        self.assertEqual(spec_run.escalation_category, "SPEC_AMBIGUOUS")
        # iteration=1 < threshold=2 for non-ENV_ISSUE — should NOT fire
        self.assertLess(spec_run.iteration, 2)


    def test_all_crashes_no_assertions_triggers_escalation(self) -> None:
        """When all failures are crashes (zero assertions), escalate as ENV_ISSUE."""
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        iter_state.scenario_crashed = 5
        iter_state.scenario_fail_count = 5
        iter_state.scenario_assert_failed = 0
        iter_state.scenario_total = 6
        fb = ""

        self._apply_routing(spec_run, iter_state, fb)

        self.assertTrue(spec_run.escalation_recommended)
        self.assertEqual(spec_run.escalation_category, "ENV_ISSUE")
        self.assertIn("crashed at collection", spec_run.escalation_reason)
        self.assertIn("5", spec_run.escalation_reason)

    def test_mixed_crashes_and_assertions_no_crash_escalation(self) -> None:
        """When some failures are assertions (not all crashes), crash logic does not fire."""
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        iter_state.scenario_crashed = 2
        iter_state.scenario_fail_count = 5
        iter_state.scenario_assert_failed = 3
        iter_state.scenario_total = 8
        fb = ""

        self._apply_routing(spec_run, iter_state, fb)

        # Crash-dominant logic should NOT fire (assert_failed > 0).
        # No other escalation source either (no classifications, no skips).
        self.assertFalse(spec_run.escalation_recommended)

    def test_zero_crashes_no_crash_escalation(self) -> None:
        """When there are zero crashes, crash logic does not fire."""
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        iter_state.scenario_crashed = 0
        iter_state.scenario_fail_count = 3
        iter_state.scenario_assert_failed = 3
        iter_state.scenario_total = 6
        fb = ""

        self._apply_routing(spec_run, iter_state, fb)

        self.assertFalse(spec_run.escalation_recommended)

    def test_env_override_all_env_issue_when_no_crashes(self) -> None:
        """Phase 4.6b.0.vi: when crashed=0 and total>0, all ENV_ISSUE flip to
        IMPL_BUG because executable scenarios cannot be environment failures."""
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        iter_state.scenario_crashed = 0
        iter_state.scenario_total = 10
        fb = (
            '<!-- CLASSIFICATIONS: {"FR-1": "ENV_ISSUE", '
            '"FR-2": "ENV_ISSUE", "FR-3": "ENV_ISSUE"} -->'
        )

        self._apply_routing(spec_run, iter_state, fb)

        # After override, the stored history entry should contain only IMPL_BUG.
        self.assertEqual(len(spec_run.classification_history), 1)
        self.assertEqual(
            spec_run.classification_history[0],
            {"FR-1": "IMPL_BUG", "FR-2": "IMPL_BUG", "FR-3": "IMPL_BUG"},
        )
        # Counts reflect post-override state.
        self.assertEqual(iter_state.classification_counts, {"IMPL_BUG": 3})
        # No escalation: nothing is ENV_ISSUE/SPEC_AMBIGUOUS/BRITTLE now.
        self.assertFalse(spec_run.escalation_recommended)

    def test_env_override_skipped_when_crashes_present(self) -> None:
        """Phase 4.6b.0.vi: crashed>0 → override does NOT fire; ENV_ISSUE stays."""
        spec_run = _make_spec_run(iteration=2, effective_max_iterations=3)
        iter_state = IterationState(number=2)
        iter_state.scenario_crashed = 3
        iter_state.scenario_fail_count = 3
        iter_state.scenario_assert_failed = 0
        iter_state.scenario_total = 5
        fb = (
            '<!-- CLASSIFICATIONS: {"FR-1": "ENV_ISSUE", '
            '"FR-2": "ENV_ISSUE", "FR-3": "IMPL_BUG"} -->'
        )

        self._apply_routing(spec_run, iter_state, fb)

        # History preserves ENV_ISSUE — override did not fire.
        self.assertEqual(len(spec_run.classification_history), 1)
        self.assertEqual(
            spec_run.classification_history[0],
            {"FR-1": "ENV_ISSUE", "FR-2": "ENV_ISSUE", "FR-3": "IMPL_BUG"},
        )
        # Crash-dominant escalation fires (separate path, unaffected by override).
        self.assertTrue(spec_run.escalation_recommended)
        self.assertEqual(spec_run.escalation_category, "ENV_ISSUE")

    def test_env_override_skipped_when_zero_scenarios(self) -> None:
        """Phase 4.6b.0.vi: scenario_total=0 → override does NOT fire.
        With no scenarios run, we can't prove ENV_ISSUE is wrong."""
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        iter_state.scenario_crashed = 0
        iter_state.scenario_total = 0
        fb = '<!-- CLASSIFICATIONS: {"FR-1": "ENV_ISSUE", "FR-2": "ENV_ISSUE"} -->'

        self._apply_routing(spec_run, iter_state, fb)

        # History preserves ENV_ISSUE — override did not fire.
        self.assertEqual(len(spec_run.classification_history), 1)
        self.assertEqual(
            spec_run.classification_history[0],
            {"FR-1": "ENV_ISSUE", "FR-2": "ENV_ISSUE"},
        )

    def test_env_override_leaves_non_env_entries_untouched(self) -> None:
        """Phase 4.6b.0.vi: mixed ENV_ISSUE + IMPL_BUG + SPEC_AMBIGUOUS with
        crashed=0 and total>0 → only ENV_ISSUE entries flip to IMPL_BUG."""
        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        iter_state.scenario_crashed = 0
        iter_state.scenario_total = 8
        fb = (
            '<!-- CLASSIFICATIONS: {"FR-1": "ENV_ISSUE", '
            '"FR-2": "IMPL_BUG", "FR-3": "SPEC_AMBIGUOUS", '
            '"FR-4": "BRITTLE"} -->'
        )

        self._apply_routing(spec_run, iter_state, fb)

        self.assertEqual(len(spec_run.classification_history), 1)
        # FR-1 flipped from ENV_ISSUE to IMPL_BUG; others unchanged.
        self.assertEqual(
            spec_run.classification_history[0],
            {
                "FR-1": "IMPL_BUG",
                "FR-2": "IMPL_BUG",
                "FR-3": "SPEC_AMBIGUOUS",
                "FR-4": "BRITTLE",
            },
        )
        self.assertEqual(
            iter_state.classification_counts,
            {"IMPL_BUG": 2, "SPEC_AMBIGUOUS": 1, "BRITTLE": 1},
        )


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


class TestFormatDefectsInline(TempDirTestCase):
    """Tests for _format_defects_inline (Phase 4.6b.1h)."""

    def _write_defects(self, data: dict, iteration: int = 1) -> None:
        reports = self.tmp_dir / ".dark-factory" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        path = reports / f"my-spec-defects-iter{iteration}.json"
        path.write_text(json.dumps(data))

    def test_missing_file_returns_empty(self) -> None:
        result = _format_defects_inline(self.tmp_dir, "my-spec", 1)
        self.assertEqual(result, "")

    def test_malformed_json_returns_empty(self) -> None:
        reports = self.tmp_dir / ".dark-factory" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "my-spec-defects-iter1.json").write_text("{bad json")
        result = _format_defects_inline(self.tmp_dir, "my-spec", 1)
        self.assertEqual(result, "")

    def test_classifier_error_returns_notice(self) -> None:
        self._write_defects({"error": "count_mismatch", "defects": []})
        result = _format_defects_inline(self.tmp_dir, "my-spec", 1)
        self.assertIn("UNAVAILABLE", result)
        self.assertIn("count_mismatch", result)

    def test_empty_defects_returns_empty(self) -> None:
        self._write_defects({"error": None, "defects": []})
        result = _format_defects_inline(self.tmp_dir, "my-spec", 1)
        self.assertEqual(result, "")

    def test_normal_defect_inlined(self) -> None:
        """Run 11 regression shape: AC-1 structured logging IMPL_BUG."""
        self._write_defects({
            "iteration": 1,
            "schema_version": 1,
            "error": None,
            "defects": [{
                "test_name": "[AC-1] starts successfully with valid env vars",
                "category": "IMPL_BUG",
                "cited_anchor_ids": ["AC-1"],
                "scope_filter_eligible": False,
                "rationale": "Test exercises structured JSON logging; impl outputs human-readable.",
            }],
        })
        result = _format_defects_inline(self.tmp_dir, "my-spec", 1)
        self.assertIn("IMPL_BUG", result)
        self.assertIn("AC-1", result)
        self.assertIn("[AC-1] starts successfully", result)
        self.assertIn("structured JSON logging", result)
        self.assertIn("authoritative", result)

    def test_non_string_anchor_ids_coerced(self) -> None:
        """Linus review item 1: non-string cited_anchor_ids must not crash."""
        self._write_defects({
            "error": None,
            "defects": [{
                "test_name": "some test",
                "category": "IMPL_BUG",
                "cited_anchor_ids": [1, None, "FR-3"],
                "rationale": "bad data",
            }],
        })
        result = _format_defects_inline(self.tmp_dir, "my-spec", 1)
        self.assertIn("IMPL_BUG", result)
        self.assertIn("FR-3", result)

    def test_truncation_at_cap(self) -> None:
        """Defect list exceeding 50 entries is truncated with a notice."""
        defects = [
            {"test_name": f"test_{i}", "category": "IMPL_BUG",
             "cited_anchor_ids": [f"FR-{i}"], "rationale": "bug"}
            for i in range(60)
        ]
        self._write_defects({"error": None, "defects": defects})
        result = _format_defects_inline(self.tmp_dir, "my-spec", 1)
        self.assertIn("test_49", result)
        self.assertNotIn("test_50", result)
        self.assertIn("10 more", result)


    def test_long_rationale_truncated(self) -> None:
        self._write_defects({
            "error": None,
            "defects": [{
                "test_name": "test",
                "category": "IMPL_BUG",
                "cited_anchor_ids": [],
                "rationale": "x" * 200,
            }],
        })
        result = _format_defects_inline(self.tmp_dir, "my-spec", 1)
        self.assertIn("..." , result)
        self.assertLessEqual(len([l for l in result.split("\n") if "xxx" in l][0]), 300)


class TestFormatSubstrateFindingsInline(TempDirTestCase):
    """Tests for ``_format_substrate_findings_inline`` (2026-04-28
    consumer-audit fix). Run #3 of the database-foundation pipeline
    landed substrate-findings.json with two real findings, but the
    Oracle's prompt-construction never opened the file — the result
    was Oracle classifying AC-4/AC-5/AC-24 cascades as independent
    IMPL_BUGs instead of recognizing substrate-bootstrap-not-applied
    as the root cause. This helper inlines findings the same way
    ``_format_defects_inline`` inlines per-test classifications."""

    def _write_findings(self, data: dict) -> None:
        reports = self.tmp_dir / ".dark-factory" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "my-spec-substrate-findings.json").write_text(
            json.dumps(data)
        )

    def test_missing_file_returns_empty(self) -> None:
        """No findings file = no inline block. Same fail-quietly shape
        as ``_format_defects_inline``."""
        result = _format_substrate_findings_inline(self.tmp_dir, "my-spec")
        self.assertEqual(result, "")

    def test_skip_status_returns_empty(self) -> None:
        """Substrate phase legitimately SKIPped (no manifest, no rules
        triggered) = no diagnostic bodies = no prompt block.
        Inlining an empty 'Substrate Verification Findings' header
        would noise up the prompt."""
        self._write_findings({"status": "skip", "findings": []})
        result = _format_substrate_findings_inline(self.tmp_dir, "my-spec")
        self.assertEqual(result, "")

    def test_pass_status_returns_empty(self) -> None:
        """Substrate phase ran and passed every check = no diagnostics
        to inline. Same rationale as skip."""
        self._write_findings({"status": "pass", "findings": []})
        result = _format_substrate_findings_inline(self.tmp_dir, "my-spec")
        self.assertEqual(result, "")

    def test_malformed_json_returns_empty(self) -> None:
        reports = self.tmp_dir / ".dark-factory" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "my-spec-substrate-findings.json").write_text(
            "{not json"
        )
        result = _format_substrate_findings_inline(self.tmp_dir, "my-spec")
        self.assertEqual(result, "")

    def test_fail_status_inlines_findings(self) -> None:
        """The shape from the 2026-04-28 run #3 gate: status=fail with
        the bootstrap and GUC findings the Oracle needs to see.
        Each finding's ``code`` and ``message`` must appear verbatim
        in the inlined block — that's the diagnostic the Oracle uses
        to recognize a cascade root cause.

        Linus follow-up: the helper returns DATA only — the Oracle-
        side cascade-classification directive lives at the call site
        in ``_step_oracle`` (see ``test_oracle_prompt_includes_substrate_directive``
        for the call-site contract). This test asserts the helper
        returns the bullet-formatted finding block with no policy
        directive embedded.
        """
        self._write_findings({
            "status": "fail",
            "findings": [
                {"severity": "error",
                 "code": "substrate-bootstrap-not-applied",
                 "message": "drizzle-kit migrate exit 0 but tracking table missing",
                 "path": ""},
                {"severity": "error",
                 "code": "substrate-guc-unregistered",
                 "message": "GUC 'app.tenant_id' is not registered after setup",
                 "path": ""},
            ],
        })
        result = _format_substrate_findings_inline(self.tmp_dir, "my-spec")
        # Plain header, no editorializing.
        self.assertIn("Substrate Verification Findings", result)
        # Both finding codes inlined verbatim — Oracle uses these to
        # write the CLASSIFICATIONS block.
        self.assertIn("substrate-bootstrap-not-applied", result)
        self.assertIn("substrate-guc-unregistered", result)
        # Diagnostic bodies inlined so Oracle reads "tracking table
        # missing" rather than treating AC-24 as an independent bug.
        self.assertIn("tracking table missing", result)
        self.assertIn("app.tenant_id", result)
        # The cascade directive belongs at the call site, NOT here.
        # If a future maintainer reintroduces it, this assertion fires
        # so the layering stays clean.
        self.assertNotIn("cascade", result.lower())
        self.assertNotIn("IMPL_BUG", result)

    def test_fail_with_empty_findings_list_returns_empty(self) -> None:
        """Defensive: status=fail but findings list is empty (shouldn't
        happen — substrate phase wouldn't write status=fail without
        findings — but the helper must not emit a header-only block if
        a malformed envelope ever lands)."""
        self._write_findings({"status": "fail", "findings": []})
        result = _format_substrate_findings_inline(self.tmp_dir, "my-spec")
        self.assertEqual(result, "")


class TestFormatHoldoutsInline(TempDirTestCase):
    """Tests for ``_format_holdouts_inline`` (Phase 2 of the higher-
    priority queue plan, 2026-04-29). Closes deferred bug #2: holdouts.md
    was written by qa-holdout but had no consumer — the file existed
    only as a completion marker and a sweep target.

    Consumer choice rationale (Reviewer, NOT Oracle): adversarial
    pass/fail already flows through gate counters into Oracle's
    decision; the descriptive content is review-depth context for
    judging coverage adequacy and corroborating SPEC_AMBIGUOUS
    classifications. Adding to Oracle would force chain-failure-
    style cross-referencing of holdouts prose against defect
    classifications.
    """

    def _write_holdouts(self, content: str) -> None:
        reports = self.tmp_dir / ".dark-factory" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "my-spec-holdouts.md").write_text(content)

    def test_missing_file_returns_empty(self) -> None:
        result = _format_holdouts_inline(self.tmp_dir, "my-spec")
        self.assertEqual(result, "")

    def test_empty_file_returns_empty(self) -> None:
        self._write_holdouts("")
        result = _format_holdouts_inline(self.tmp_dir, "my-spec")
        self.assertEqual(result, "")

    def test_whitespace_only_file_returns_empty(self) -> None:
        self._write_holdouts("\n  \n\n   \n")
        result = _format_holdouts_inline(self.tmp_dir, "my-spec")
        self.assertEqual(result, "")

    def test_normal_holdouts_inlined(self) -> None:
        """The shape from the 13:50 run: executive summary + test
        coverage table + attack vector catalogue + spec-gap analysis."""
        body = (
            "# Holdouts Report: Database Foundation\n\n"
            "## Executive Summary\n\n"
            "Generated **252 adversarial test cases** across 8 test suites.\n\n"
            "## Test Coverage Overview\n\n"
            "| Test Suite | Test Cases | Focus Area |\n"
            "|------------|------------|------------|\n"
            "| `boundary-values.test.js` | 32 | Boundary values |\n"
            "| `injection-escape.test.js` | 64 | SQL injection |\n\n"
            "## Spec Gap Analysis\n\n"
            "1. UUID Constraints: No version/variant requirements specified\n"
        )
        self._write_holdouts(body)
        result = _format_holdouts_inline(self.tmp_dir, "my-spec")
        # Helper-emitted header.
        self.assertIn("QA Holdout Report (adversarial-coverage context)", result)
        # Verbatim agent content.
        self.assertIn("252 adversarial test cases", result)
        self.assertIn("boundary-values.test.js", result)
        self.assertIn("Spec Gap Analysis", result)
        self.assertIn("UUID Constraints", result)

    def test_long_report_truncated(self) -> None:
        body = "x" * 10_000
        self._write_holdouts(body)
        result = _format_holdouts_inline(self.tmp_dir, "my-spec")
        self.assertIn("truncated for prompt size", result)
        self.assertLess(len(result), 10_000 + 500)


class TestBootstrapCheckMessagePropagation(TempDirTestCase):
    """Phase 4.5 (Neo's R5-done-right, 2026-04-29): before burning a
    90-minute live run, prove mechanically that the bootstrap_check
    finding's message field flows verbatim through the new code path.

    Background: the 2026-04-28 13:50 run produced clean Gap 2 cascade
    aggregation but the implementer ignored the Oracle's fix-list
    across 2 more iterations. The Oracle's fix-list said "Fix upstream
    substrate/substrate-bootstrap-not-applied — Cascades to 11 tests"
    but lost the WHY ("comment-only baseline never creates the tracking
    table" — which is in the finding's `message` field but stripped
    by the Oracle's paraphrase). Phase 4 fixes this with mechanical
    injection in `_format_cascade_summary`.

    Fixtures saved at `tests/unit/fixtures/13_50_run/`:
    - `defects-iter1.json` (18 defects, 18/18 with upstream_cause)
    - `substrate-findings.json` (2 findings: bootstrap-not-applied +
      guc-unregistered)

    If these tests fail with Phase 4 active, Phase 4 didn't actually
    solve the problem; rethink before Phase 5's live run."""

    @property
    def _fixtures(self) -> Path:
        # Resolve relative to the test file location.
        return (
            Path(__file__).parent / "fixtures" / "13_50_run"
        )

    def _load_defects(self) -> list:
        with (self._fixtures / "defects-iter1.json").open() as f:
            return json.load(f)["defects"]

    def _load_canonical_messages(self) -> dict[tuple[str, str], str]:
        with (self._fixtures / "substrate-findings.json").open() as f:
            findings = json.load(f)["findings"]
        return {
            ("substrate", f["code"]): f["message"]
            for f in findings
        }

    def test_evidence_falls_back_to_classifier_when_no_canonical(self) -> None:
        """When canonical_messages is not provided, the cascade summary
        falls back to the longest-wins classifier evidence. This pins
        the graceful-degradation path (e.g. for pre_gate primitives
        where no canonical artifact exists)."""
        defects = self._load_defects()
        result = _format_cascade_summary(defects)

        self.assertIn("Cascade Summary", result)
        # The classifier's paraphrased evidence (shorter than canonical
        # message because the classifier dropped the SQL).
        self.assertIn(
            "SQL SELECT 1 FROM information_schema.tables "
            "WHERE table_name='__drizzle_migrations' returned no rows",
            result,
        )
        self.assertIn("from classifier defect", result)

    def test_canonical_message_overrides_classifier_evidence(self) -> None:
        """**The load-bearing Phase 4 contract**: when canonical
        messages are passed in (sourced from substrate-findings.json),
        the cascade summary uses the canonical text — not the
        classifier's paraphrased evidence. Closes Linus belated review
        B1+: the classifier paraphrased the bootstrap_check `message`
        in the 13:50 run, dropping the fix mechanism. Mechanical
        injection from the canonical artifact bypasses that."""
        defects = self._load_defects()
        canonical = self._load_canonical_messages()
        result = _format_cascade_summary(defects, canonical_messages=canonical)

        self.assertIn("Cascade Summary", result)
        # Canonical bootstrap_check message — INCLUDES the fix
        # mechanism the classifier dropped: "comment-only baseline
        # never creates the tracking table". (Length-bounded to 500
        # chars — full canonical message is 339 chars, under cap.)
        self.assertIn(
            "comment-only baseline never creates the tracking table",
            result,
        )
        # Canonical guc-unregistered message includes the literal SQL
        # the classifier dropped.
        self.assertIn(
            "`SELECT current_setting('app.tenant_id', true)`",
            result,
        )
        # Source label reflects canonical sourcing.
        self.assertIn("verbatim from upstream finding", result)
        # Classifier-fallback label does NOT appear when canonical wins.
        self.assertNotIn(
            "from classifier defect (canonical source unavailable)",
            result,
        )

    def test_evidence_renders_under_correct_cascade_row(self) -> None:
        """Each Evidence line must immediately follow its corresponding
        cascade row. Pinned to prevent a future refactor that puts all
        Evidence lines at the top/bottom of the block (which would
        break the visual binding the Oracle relies on).

        Linus belated review B2: anchor on bullet sigils (not bare
        primitive names) so a future docstring change that mentions a
        primitive name doesn't make the assertion self-referential.
        Plus a proximity assertion: Evidence must appear within 600
        chars of its cascade row, not 5 KB downstream."""
        defects = self._load_defects()
        canonical = self._load_canonical_messages()
        result = _format_cascade_summary(defects, canonical_messages=canonical)

        # Anchor on bullet sigils — only the cascade row itself starts
        # with this prefix.
        bootstrap_row = "- **substrate/substrate-bootstrap-not-applied**"
        guc_row = "- **substrate/substrate-guc-unregistered**"
        bootstrap_evidence_substr = (
            "comment-only baseline never creates the tracking table"
        )

        bootstrap_pos = result.find(bootstrap_row)
        bootstrap_evidence_pos = result.find(bootstrap_evidence_substr)
        guc_pos = result.find(guc_row)

        # The fixture has bootstrap (11 tests) before guc (7 tests)
        # because cascade_summary sorts descending by count.
        self.assertGreater(bootstrap_pos, 0)
        self.assertGreater(guc_pos, 0)
        self.assertGreater(bootstrap_evidence_pos, 0)
        # Evidence for bootstrap appears AFTER bootstrap row, BEFORE
        # guc row.
        self.assertLess(bootstrap_pos, bootstrap_evidence_pos)
        self.assertLess(bootstrap_evidence_pos, guc_pos)

        # Proximity: Evidence must be a sub-bullet immediately following
        # its cascade row, not far downstream. Anchor on the Evidence
        # line's own structural prefix ("  - Evidence (") rather than
        # measuring from the row line's start (cascade rows contain a
        # long sample of test names + "+N more" so row line lengths
        # vary heavily). The Evidence line's start should appear within
        # one row + line-break of the row's start.
        bootstrap_row_end = result.find("\n", bootstrap_pos)
        evidence_line_start = result.find(
            "  - Evidence (verbatim from upstream finding):",
            bootstrap_row_end,
        )
        self.assertGreater(evidence_line_start, bootstrap_row_end)
        # Evidence sub-bullet immediately follows the row (only the
        # newline between them).
        self.assertEqual(evidence_line_start, bootstrap_row_end + 1)

    def test_evidence_appears_in_defects_inline_via_format_defects_inline(self) -> None:
        """End-to-end check: the cascade summary is included in
        `_format_defects_inline`'s output (which is what the Oracle
        prompt actually inlines). When substrate-findings.json is
        present, the canonical bootstrap_check message — including
        the fix mechanism — must reach the Oracle prompt verbatim."""
        reports = self.tmp_dir / ".dark-factory" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        # Copy both fixtures so _load_canonical_upstream_messages can
        # source the canonical messages.
        with (self._fixtures / "defects-iter1.json").open() as f:
            defects_doc = json.load(f)
        (reports / "my-spec-defects-iter1.json").write_text(
            json.dumps(defects_doc)
        )
        with (self._fixtures / "substrate-findings.json").open() as f:
            findings_doc = json.load(f)
        (reports / "my-spec-substrate-findings.json").write_text(
            json.dumps(findings_doc)
        )

        result = _format_defects_inline(self.tmp_dir, "my-spec", 1)

        # Cascade summary inside the defects-inline block.
        self.assertIn("Cascade Summary", result)
        # Canonical bootstrap_check message — has the fix mechanism.
        self.assertIn(
            "comment-only baseline never creates the tracking table",
            result,
        )
        # Both cascade rows present.
        self.assertIn("- **substrate/substrate-bootstrap-not-applied**", result)
        self.assertIn("- **substrate/substrate-guc-unregistered**", result)
        # Per-defect upstream tags also present.
        self.assertIn("upstream: substrate/", result)
        # Source labeled as canonical.
        self.assertIn("verbatim from upstream finding", result)

    def test_per_evidence_500_char_cap(self) -> None:
        """Linus belated review #4: per-row evidence is bounded at
        500 chars to prevent a runaway classifier dump or oversized
        canonical message blowing the prompt."""
        defects = [
            {
                "test_name": "t1",
                "category": "SCENARIO_CRASH",
                "rationale": "x",
                "upstream_cause": {
                    "stage": "substrate",
                    "primitive": "x",
                    "evidence": "x" * 1000,  # very long evidence
                },
            },
            {
                "test_name": "t2",
                "category": "SCENARIO_CRASH",
                "rationale": "x",
                "upstream_cause": {
                    "stage": "substrate",
                    "primitive": "x",
                    "evidence": "x" * 1000,
                },
            },
        ]
        result = _format_cascade_summary(defects)
        # Truncation marker present.
        self.assertIn("... (truncated)", result)
        # Per-evidence body bounded — find Evidence line and assert
        # its length is reasonable (cap + label + line break).
        evidence_line = next(
            (ln for ln in result.splitlines() if "Evidence (" in ln),
            None,
        )
        self.assertIsNotNone(evidence_line)
        self.assertLess(len(evidence_line), 700)


class TestStepReviewIncludesHoldouts(TempDirTestCase):
    """Phase 2 wiring test: when holdouts.md exists, the Reviewer's
    prompt MUST contain the inlined block AND the role-side directive
    instructing how to use it for Robustness scoring + SPEC_AMBIGUOUS
    corroboration. Pins the Phase-2 producer-consumer wiring at the
    ``_step_review`` site so a future refactor of the prompt-build
    path can't silently regress to the pre-fix state where holdouts.md
    had zero consumers."""

    def _drive_and_capture(self, holdouts_content: str | None) -> str:
        project_dir = make_project(self.tmp_dir)
        reports = project_dir / ".dark-factory" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        # Reviewer reads gate summary inline.
        (reports / "my-spec-quality-gate-summary.md").write_text(
            "stub gate summary"
        )
        if holdouts_content is not None:
            (reports / "my-spec-holdouts.md").write_text(holdouts_content)

        config = _make_config()
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1, scenario_pass_count=10, scenario_fail_count=1,
            scenario_total=11, pass_rate=90,
        )
        captured: list[str] = []

        def fake_invoke(role, prompt, **kwargs):
            captured.append(prompt)
            (project_dir / ".dark-factory" / "reviews"
             / "my-spec-review.md").parent.mkdir(parents=True, exist_ok=True)
            (project_dir / ".dark-factory" / "reviews"
             / "my-spec-review.md").write_text("stub")
            return AgentResult(role=AgentRole.REVIEWER, exit_code=0)

        with patch("darkfactory.pipeline.iteration.invoke_agent",
                   side_effect=fake_invoke), \
             patch("darkfactory.pipeline.iteration.step_begin"), \
             patch("darkfactory.review.prepass.run_review_prepass",
                   return_value=None):
            _step_review(
                spec_run, iter_state, config, {}, project_dir, dry_run=True,
            )
        assert captured, "_step_review did not call invoke_agent"
        return captured[0]

    def test_reviewer_prompt_includes_holdouts_when_present(self) -> None:
        prompt = self._drive_and_capture(
            "# Holdouts Report\n\n## Spec Gap Analysis\n\n1. UUID Constraints\n"
        )
        self.assertIn("QA Holdout Report (adversarial-coverage context)", prompt)
        self.assertIn("Spec Gap Analysis", prompt)
        self.assertIn("UUID Constraints", prompt)
        # Role-side directive present (for Robustness scoring +
        # SPEC_AMBIGUOUS corroboration).
        self.assertIn("Robustness assessment", prompt)
        self.assertIn("SPEC_AMBIGUOUS", prompt)
        # Linus belated review #5: the "do not adjust gate counters"
        # belt-and-suspenders directive was dropped — the reviewer
        # cannot adjust gate counters anyway, so the directive was
        # dead prose training the agent to think otherwise.
        self.assertNotIn("do not adjust gate counters", prompt)

    def test_reviewer_prompt_omits_holdouts_when_missing(self) -> None:
        """When no holdouts.md exists, the helper returns empty and the
        directive must NOT fire — emitting "use the holdouts report"
        directive without the report would confuse the Reviewer."""
        prompt = self._drive_and_capture(None)
        self.assertNotIn("QA Holdout Report (adversarial-coverage context)", prompt)
        self.assertNotIn("Robustness assessment (10%", prompt)


class TestFormatPreGateSummaryInline(TempDirTestCase):
    """Tests for ``_format_pre_gate_summary_inline`` (Gap 2 of the
    2026-04-28 framework gap audit). Companion to substrate-findings
    inlining; surfaces Build/Lint/Typecheck failures so the
    defect-classifier can detect cascades whose root cause is a
    pre-gate failure (the "post-gate test failed because the build
    didn't even compile" case)."""

    def _write_pre_gate(self, content: str) -> None:
        reports = self.tmp_dir / ".dark-factory" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "my-spec-pre-gate-summary.md").write_text(content)

    def test_missing_file_returns_empty(self) -> None:
        result = _format_pre_gate_summary_inline(self.tmp_dir, "my-spec")
        self.assertEqual(result, "")

    def test_empty_file_returns_empty(self) -> None:
        self._write_pre_gate("")
        result = _format_pre_gate_summary_inline(self.tmp_dir, "my-spec")
        self.assertEqual(result, "")

    def test_whitespace_only_file_returns_empty(self) -> None:
        self._write_pre_gate("   \n\n   \n")
        result = _format_pre_gate_summary_inline(self.tmp_dir, "my-spec")
        self.assertEqual(result, "")

    def test_normal_summary_inlined(self) -> None:
        body = (
            "# Pre-Gate Summary\n\n"
            "- Build: PASS\n"
            "- Lint: PASS\n"
            "- Typecheck: FAIL (5 errors)\n\n"
            "## Typecheck failures\n"
            "src/foo.ts:42 - error TS2304: Cannot find name 'undeclared'\n"
        )
        self._write_pre_gate(body)
        result = _format_pre_gate_summary_inline(self.tmp_dir, "my-spec")
        self.assertIn("Pre-Gate Summary", result)
        self.assertIn("Typecheck: FAIL", result)
        self.assertIn("Cannot find name", result)
        # Header from the helper, not just verbatim agent body.
        self.assertIn("Build / Lint / Typecheck", result)

    def test_long_summary_truncated(self) -> None:
        body = "x" * 10_000
        self._write_pre_gate(body)
        result = _format_pre_gate_summary_inline(self.tmp_dir, "my-spec")
        self.assertIn("truncated for prompt size", result)
        # Bounded — never returns the full long body.
        self.assertLess(len(result), 10_000 + 500)


class TestFormatCascadeSummary(unittest.TestCase):
    """Tests for ``_format_cascade_summary`` (Gap 2 rendering half).
    Aggregates defects sharing a common ``upstream_cause`` so the Oracle
    reads 'fix upstream X; cascades to N tests' instead of treating
    each cascading test as an independent blocker.

    Run 5b iter-2 produced 18 SPEC_AMBIGUOUS records, all carrying the
    same POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION cause in their rationale
    prose. The classifier RECOGNIZED the cascade in rationales but had
    no structured channel to express it. Now the cascade flows through
    the optional ``upstream_cause`` field and this helper aggregates
    by (stage, primitive)."""

    def test_no_upstream_returns_empty(self) -> None:
        defects = [
            {"test_name": "t1", "category": "IMPL_BUG", "rationale": "x"},
            {"test_name": "t2", "category": "BRITTLE", "rationale": "y"},
        ]
        self.assertEqual(_format_cascade_summary(defects), "")

    def test_single_cascading_defect_returns_empty(self) -> None:
        """A solitary upstream-tagged defect has no aggregation value —
        the per-defect inline tag is sufficient. Avoid prompt noise."""
        defects = [{
            "test_name": "t1",
            "category": "SCENARIO_CRASH",
            "rationale": "x",
            "upstream_cause": {
                "stage": "substrate",
                "primitive": "bootstrap_check",
                "evidence": "FAIL row in substrate-findings.json",
            },
        }]
        self.assertEqual(_format_cascade_summary(defects), "")

    def test_multiple_defects_same_upstream_aggregate(self) -> None:
        """The Gap 2 happy path: ≥2 defects sharing (stage, primitive)
        produce an aggregated 'fix upstream X; cascades to N tests'
        block. Ordering: largest cascade first."""
        defects = [
            {
                "test_name": f"t{i}",
                "category": "SCENARIO_CRASH",
                "rationale": "cascades from substrate",
                "upstream_cause": {
                    "stage": "substrate",
                    "primitive": "bootstrap_check",
                    "evidence": "FAIL row",
                },
            }
            for i in range(5)
        ]
        result = _format_cascade_summary(defects)
        self.assertIn("Cascade Summary", result)
        self.assertIn("substrate/bootstrap_check", result)
        self.assertIn("cascades to 5 test(s)", result)
        # Sample of test names — first 3 + "more" indicator.
        self.assertIn("`t0`", result)
        self.assertIn("`t2`", result)
        self.assertIn("+2 more", result)
        # Structural directive present.
        self.assertIn("Fix the upstream cause", result)

    def test_multiple_distinct_cascades_ordered_by_size(self) -> None:
        """When two upstream causes both have ≥2 cascading tests, the
        aggregated summary lists them in descending count order so the
        Oracle reads the largest cascade first."""
        defects = [
            # Smaller cascade (2 tests).
            {"test_name": "small_a", "category": "ENV_ISSUE",
             "upstream_cause": {"stage": "pre_gate", "primitive": "typecheck",
                                "evidence": "ts2304"}},
            {"test_name": "small_b", "category": "ENV_ISSUE",
             "upstream_cause": {"stage": "pre_gate", "primitive": "typecheck",
                                "evidence": "ts2304"}},
            # Larger cascade (3 tests).
            {"test_name": "big_a", "category": "SCENARIO_CRASH",
             "upstream_cause": {"stage": "substrate", "primitive": "bootstrap",
                                "evidence": "FAIL"}},
            {"test_name": "big_b", "category": "SCENARIO_CRASH",
             "upstream_cause": {"stage": "substrate", "primitive": "bootstrap",
                                "evidence": "FAIL"}},
            {"test_name": "big_c", "category": "SCENARIO_CRASH",
             "upstream_cause": {"stage": "substrate", "primitive": "bootstrap",
                                "evidence": "FAIL"}},
        ]
        result = _format_cascade_summary(defects)
        # Big cascade appears before small cascade (larger-first ordering).
        big_pos = result.find("substrate/bootstrap")
        small_pos = result.find("pre_gate/typecheck")
        self.assertGreater(big_pos, 0)
        self.assertGreater(small_pos, 0)
        self.assertLess(big_pos, small_pos)
        self.assertIn("cascades to 3 test(s)", result)
        self.assertIn("cascades to 2 test(s)", result)


class TestFormatDefectsInlineUpstreamCause(TempDirTestCase):
    """Gap 2: per-defect upstream_cause tagging in the inline block.

    Companion to ``TestFormatCascadeSummary`` — that test pins the
    aggregated header; this pins the per-defect surfacing inside the
    classification list so the Oracle can connect aggregated cascades
    to specific tests."""

    def _write_defects(self, defects: list[dict]) -> None:
        reports = self.tmp_dir / ".dark-factory" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        path = reports / "my-spec-defects-iter1.json"
        path.write_text(json.dumps({
            "iteration": 1, "schema_version": 1, "error": None,
            "defects": defects,
        }))

    def test_per_defect_upstream_inlined(self) -> None:
        self._write_defects([
            {
                "test_name": "[AC-3] tenant isolation enforced",
                "category": "SCENARIO_CRASH",
                "cited_anchor_ids": ["AC-3"],
                "rationale": "cascades from substrate",
                "upstream_cause": {
                    "stage": "substrate",
                    "primitive": "guc_registered",
                    "evidence": "substrate-guc-unregistered",
                },
            },
        ])
        result = _format_defects_inline(self.tmp_dir, "my-spec", 1)
        self.assertIn("SCENARIO_CRASH", result)
        # Per-defect inline tag.
        self.assertIn("upstream: substrate/guc_registered", result)

    def test_defect_without_upstream_no_tag(self) -> None:
        self._write_defects([
            {
                "test_name": "[AC-1] something",
                "category": "IMPL_BUG",
                "cited_anchor_ids": ["AC-1"],
                "rationale": "bug",
            },
        ])
        result = _format_defects_inline(self.tmp_dir, "my-spec", 1)
        self.assertNotIn("upstream:", result)
        # Cascade summary section absent (no upstream tagged).
        self.assertNotIn("Cascade Summary", result)

    def test_aggregated_cascade_summary_precedes_per_defect_list(self) -> None:
        """Structural ordering: cascade summary first (so Oracle reads
        'fix upstream X' before scrolling 18 individual entries)."""
        self._write_defects([
            {
                "test_name": f"t{i}",
                "category": "SCENARIO_CRASH",
                "cited_anchor_ids": [],
                "rationale": "cascades",
                "upstream_cause": {
                    "stage": "substrate",
                    "primitive": "bootstrap_check",
                    "evidence": "FAIL",
                },
            }
            for i in range(3)
        ])
        result = _format_defects_inline(self.tmp_dir, "my-spec", 1)
        cascade_pos = result.find("Cascade Summary")
        list_pos = result.find("Defect Classifications (authoritative)")
        self.assertGreater(cascade_pos, 0)
        self.assertGreater(list_pos, 0)
        self.assertLess(
            cascade_pos, list_pos,
            "Cascade Summary must appear before per-defect list so Oracle "
            "reads structural directive before evidence",
        )


class TestFormatCascadeSummaryMalformedUpstream(unittest.TestCase):
    """Linus belated review #6a: malformed ``upstream_cause`` shapes
    must not poison the aggregation. The "?/?" footgun: two defects
    with empty/missing stage or primitive would otherwise lump together
    in a fake "?/?" bucket and produce a bogus 'cascades to 2 tests'
    line. The malformed entries still surface as per-defect inline
    tags via the defects-inline path; they just don't aggregate."""

    def test_upstream_missing_required_keys_skipped_from_aggregation(self) -> None:
        defects = [
            # Two defects with valid upstream — should aggregate.
            {"test_name": "valid_a", "category": "SCENARIO_CRASH",
             "upstream_cause": {"stage": "substrate", "primitive": "boot",
                                "evidence": "x"}},
            {"test_name": "valid_b", "category": "SCENARIO_CRASH",
             "upstream_cause": {"stage": "substrate", "primitive": "boot",
                                "evidence": "x"}},
            # Two defects with malformed upstream (missing primitive) —
            # MUST NOT lump together as "?/?".
            {"test_name": "bad_a", "category": "SCENARIO_CRASH",
             "upstream_cause": {"stage": "substrate"}},
            {"test_name": "bad_b", "category": "SCENARIO_CRASH",
             "upstream_cause": {"stage": "substrate"}},
        ]
        result = _format_cascade_summary(defects)
        self.assertIn("substrate/boot", result)
        self.assertIn("cascades to 2 test(s)", result)
        # Critical: no fake "?" bucket renders.
        self.assertNotIn("?/?", result)
        self.assertNotIn("substrate/?", result)
        self.assertNotIn("?/", result)

    def test_upstream_empty_dict_skipped(self) -> None:
        """{} is a valid dict but has no stage/primitive — must be
        rejected from aggregation, not treated as ?/? bucket."""
        defects = [
            {"test_name": "a", "category": "SCENARIO_CRASH",
             "upstream_cause": {}},
            {"test_name": "b", "category": "SCENARIO_CRASH",
             "upstream_cause": {}},
        ]
        result = _format_cascade_summary(defects)
        self.assertEqual(result, "")

    def test_upstream_as_list_silently_rejected(self) -> None:
        """upstream_cause: [] (list, not dict — agent confused the
        schema) must not crash the helper. isinstance check catches it."""
        defects = [
            {"test_name": "a", "category": "SCENARIO_CRASH",
             "upstream_cause": []},
            {"test_name": "b", "category": "SCENARIO_CRASH",
             "upstream_cause": []},
        ]
        # Should not raise.
        result = _format_cascade_summary(defects)
        # Neither defect aggregated — no block rendered.
        self.assertEqual(result, "")

    def test_distinct_upstream_causes_render_without_shared_group(self) -> None:
        """Linus #2 + Neo #4: when ≥2 distinct (stage, primitive) causes
        appear with one defect each, the structural fact 'multiple
        distinct upstream failures' is still worth surfacing. The
        block renders even with no shared cause."""
        defects = [
            {"test_name": "a", "category": "SCENARIO_CRASH",
             "upstream_cause": {"stage": "substrate", "primitive": "boot",
                                "evidence": "x"}},
            {"test_name": "b", "category": "ENV_ISSUE",
             "upstream_cause": {"stage": "pre_gate", "primitive": "typecheck",
                                "evidence": "y"}},
            {"test_name": "c", "category": "SCENARIO_CRASH",
             "upstream_cause": {"stage": "setup", "primitive": "migrate",
                                "evidence": "z"}},
        ]
        result = _format_cascade_summary(defects)
        # Block rendered — three distinct causes is a structural signal
        # ("substrate is on fire in 3 different ways").
        self.assertIn("Cascade Summary", result)
        self.assertIn("substrate/boot", result)
        self.assertIn("pre_gate/typecheck", result)
        self.assertIn("setup/migrate", result)
        # Each shows "cascades to 1 test(s)" — no false "shared cause" claim.
        for line in result.splitlines():
            if line.startswith("- **"):
                self.assertIn("cascades to 1 test(s)", line)


class TestOracleDefectsAndGateOutcomes(TempDirTestCase):
    """Phase 4.6b.1h: _step_oracle prompt includes defect data and gate counts."""

    def setUp(self) -> None:
        super().setUp()
        self.project_dir = make_project(self.tmp_dir)

    def _capture_oracle_prompt(self, spec_run, iter_state, config=None):
        config = config or _make_config()
        captured: list[str] = []

        def fake_invoke(role, prompt, **kwargs):
            captured.append(prompt)
            return AgentResult(role=AgentRole.ORACLE, exit_code=0, output="SHIP\nOK")

        with patch("darkfactory.pipeline.iteration.invoke_agent", side_effect=fake_invoke):
            _step_oracle(spec_run, iter_state, config, {}, self.project_dir, dry_run=True)

        assert captured, "_step_oracle did not call invoke_agent"
        return captured[0]

    def _write_defects(self, data: dict, spec_name: str = "my-spec",
                       iteration: int = 1) -> None:
        reports = self.project_dir / ".dark-factory" / "reports"
        (reports / f"{spec_name}-defects-iter{iteration}.json").write_text(
            json.dumps(data),
        )

    def test_prompt_includes_defects_file_path(self) -> None:
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1, scenario_pass_count=33, scenario_fail_count=1,
            scenario_total=34, pass_rate=97,
        )
        prompt = self._capture_oracle_prompt(spec_run, iter_state)
        self.assertIn("defects-iter1.json", prompt)

    def test_prompt_inlines_defect_summary(self) -> None:
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1, scenario_pass_count=33, scenario_fail_count=1,
            scenario_total=34, pass_rate=97,
        )
        self._write_defects({
            "error": None,
            "defects": [{
                "test_name": "[AC-1] starts successfully",
                "category": "IMPL_BUG",
                "cited_anchor_ids": ["AC-1"],
                "rationale": "Structured logging not implemented.",
            }],
        })
        prompt = self._capture_oracle_prompt(spec_run, iter_state)
        self.assertIn("IMPL_BUG", prompt)
        self.assertIn("AC-1", prompt)
        self.assertIn("Structured logging", prompt)

    def test_prompt_includes_gate_outcome_counts(self) -> None:
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1, scenario_pass_count=33, scenario_fail_count=1,
            scenario_total=34, pass_rate=97,
        )
        prompt = self._capture_oracle_prompt(spec_run, iter_state)
        self.assertIn("33/34", prompt)
        self.assertIn("Blind scenarios FAIL", prompt)
        self.assertIn("hard gates", prompt)

    def test_gate_outcome_zero_scenarios_shows_na(self) -> None:
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1, scenario_pass_count=0, scenario_fail_count=0,
            scenario_total=0, pass_rate=0,
        )
        prompt = self._capture_oracle_prompt(spec_run, iter_state)
        self.assertIn("N/A", prompt)

    def test_graceful_when_defects_missing(self) -> None:
        """No defects file → prompt still has file path but no inline section."""
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1, scenario_pass_count=34, scenario_fail_count=0,
            scenario_total=34, pass_rate=100,
        )
        prompt = self._capture_oracle_prompt(spec_run, iter_state)
        self.assertIn("defects-iter1.json", prompt)
        # No inline defect section (## Defect Classifications) when file missing
        self.assertNotIn("## Defect Classifications (authoritative)", prompt)

    def _write_substrate_findings(
        self, data: dict, spec_name: str = "my-spec",
    ) -> None:
        reports = self.project_dir / ".dark-factory" / "reports"
        (reports / f"{spec_name}-substrate-findings.json").write_text(
            json.dumps(data),
        )

    def test_prompt_includes_substrate_directive_when_findings_present(self) -> None:
        """When substrate findings exist, the Oracle's prompt MUST
        include both (a) the data block from
        ``_format_substrate_findings_inline`` AND (b) the cascade-
        classification directive appended at the call site. The
        directive must NOT live in the formatter — pinning the layering
        from the call-site end so a future maintainer who moves the
        directive back into the formatter breaks this test instead of
        silently rewinding the cleanup.

        Substring-tightening (post-review): assertions use the
        load-bearing phrase fragments rather than single keywords. A
        weakening rewrite that swapped ``MUST be classified as a
        cascade`` for ``MAY be classified differently`` would still
        keep the word ``classified`` but the conjunction breaks; this
        test fires."""
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1, scenario_pass_count=10, scenario_fail_count=20,
            scenario_total=30, pass_rate=33,
        )
        self._write_substrate_findings({
            "status": "fail",
            "findings": [
                {"severity": "error",
                 "code": "substrate-bootstrap-not-applied",
                 "message": "drizzle-kit migrate exit 0 but tracking table missing",
                 "path": ""},
            ],
        })
        prompt = self._capture_oracle_prompt(spec_run, iter_state)
        # Data block from helper present.
        self.assertIn("Substrate Verification Findings", prompt)
        self.assertIn("substrate-bootstrap-not-applied", prompt)
        self.assertIn("tracking table missing", prompt)
        # Directive present — applied at the call site. Longer
        # substrings catch maintainers who keep the keywords but
        # weaken the imperative.
        self.assertIn("MUST be classified as a cascade", prompt)
        self.assertIn("NOT as an independent IMPL_BUG", prompt)
        # Directive instructs SCENARIO_CRASH / ENV_ISSUE classifications
        # for cascading failures (vs blanket IMPL_BUG, which was the
        # 2026-04-28 run #3 misclassification root cause).
        self.assertIn("SCENARIO_CRASH", prompt)
        self.assertIn("ENV_ISSUE", prompt)

    def test_oracle_prompt_renders_cascade_summary_from_classifier_output(self) -> None:
        """Gap 2 end-to-end: when the classifier writes defects with
        ``upstream_cause`` tags, the Oracle's prompt MUST contain BOTH
        the Cascade Summary aggregation AND the Oracle-side directive
        telling the Oracle how to act on it. (Neo belated review #2:
        the missing seam test that catches future refactors of
        _step_oracle silently breaking cascade visibility.)"""
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1, scenario_pass_count=10, scenario_fail_count=3,
            scenario_total=13, pass_rate=77,
        )
        self._write_defects({
            "iteration": 1, "schema_version": 1, "error": None,
            "defects": [
                {
                    "test_name": f"[AC-{i}] cascading failure",
                    "category": "SCENARIO_CRASH",
                    "cited_anchor_ids": [f"AC-{i}"],
                    "rationale": "cascades from substrate",
                    "upstream_cause": {
                        "stage": "substrate",
                        "primitive": "bootstrap_check",
                        "evidence": "FAIL row in substrate-findings.json",
                    },
                }
                for i in (1, 2, 3)
            ],
        })
        prompt = self._capture_oracle_prompt(spec_run, iter_state)
        # Cascade summary aggregation present (from _format_defects_inline).
        self.assertIn("Cascade Summary", prompt)
        self.assertIn("substrate/bootstrap_check", prompt)
        self.assertIn("cascades to 3 test(s)", prompt)
        # Per-defect upstream tag present in the per-defect list.
        self.assertIn("upstream: substrate/bootstrap_check", prompt)
        # Oracle-side role directive present (the parallel to the
        # substrate_block directive). Without this, the cascade is
        # rendered but the Oracle has no instruction for collapsing
        # the N entries into one fix-list line.
        self.assertIn("collapse cascading defects under a single", prompt)
        self.assertIn("fix upstream", prompt)
        # Structural ordering: the Oracle's directive must appear AFTER
        # the cascade summary so the Oracle reads the data first then
        # the rule for acting on it.
        cascade_pos = prompt.find("Cascade Summary")
        directive_pos = prompt.find("collapse cascading defects")
        self.assertGreater(cascade_pos, 0)
        self.assertGreater(directive_pos, 0)
        self.assertLess(cascade_pos, directive_pos)

    def test_oracle_prompt_omits_cascade_directive_when_no_upstream(self) -> None:
        """The Oracle-side cascade directive must be conditional on
        the Cascade Summary block actually rendering. A defects file
        with no upstream-tagged entries → no Cascade Summary → no
        Oracle-side cascade directive (would confuse the Oracle into
        looking for cascades that aren't there)."""
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1, scenario_pass_count=10, scenario_fail_count=1,
            scenario_total=11, pass_rate=90,
        )
        self._write_defects({
            "iteration": 1, "schema_version": 1, "error": None,
            "defects": [{
                "test_name": "[AC-1] something",
                "category": "IMPL_BUG",
                "cited_anchor_ids": ["AC-1"],
                "rationale": "normal bug, no cascade",
            }],
        })
        prompt = self._capture_oracle_prompt(spec_run, iter_state)
        self.assertNotIn("Cascade Summary", prompt)
        self.assertNotIn("collapse cascading defects", prompt)

    def test_prompt_omits_substrate_directive_when_no_findings(self) -> None:
        """The cascade directive is conditional on findings existing.
        Skip/pass/missing-file substrate phases must NOT emit the
        directive — emitting a "classify cascading failures" directive
        when there are no substrate findings would confuse the Oracle
        about what's cascading from what.

        Iteration shape (post-review): a FAILING iteration (pass_rate=33,
        20 fails) — not a passing one. The directive must be omitted
        even when classification reasoning IS happening, otherwise a
        future change that re-emits the directive only on FAIL gates
        would slip past a SHIP-shape iteration assertion."""
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1, scenario_pass_count=10, scenario_fail_count=20,
            scenario_total=30, pass_rate=33,
        )
        # Skip envelope: substrate phase ran with no manifest
        self._write_substrate_findings({"status": "skip", "findings": []})
        prompt = self._capture_oracle_prompt(spec_run, iter_state)
        self.assertNotIn("Substrate Verification Findings", prompt)
        self.assertNotIn("MUST be classified as a cascade", prompt)


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
        """Canonical GateResult field is scenario_failed; _step_classify must use it.

        Gate contract: scenario_failed already includes crashes (crashes ⊂
        failed). expected_records equals scenario_failed, not the sum.
        """
        gate_result = GateResult(
            exit_code=1,
            scenario_passed=7,
            scenario_failed=3,   # total failing (crashes + assert-fails)
            scenario_total=10,
            pass_rate=70,
            scenario_crashed=1,
            scenario_assert_failed=2,
        )
        prompt = self._capture_classifier_prompt(gate_result)
        # Prompt uses the canonical GateResult field name.
        self.assertIn("scenario_failed:  3", prompt)
        self.assertIn("scenario_crashed: 1", prompt)
        # expected_records = scenario_failed (crashes already included).
        self.assertIn("expected_records: 3", prompt)
        self.assertIn("Emit exactly 3 records", prompt)


class TestStepClassifyFailingTestNamesInPrompt(TempDirTestCase):
    """Phase 5.5 / B3: the classifier prompt must include the authoritative
    list of failing test names from the per-test results JSON.

    Run 3 of the Phase 5 benchmark (database-foundation, 91% iter-1 pass_rate)
    aborted with `count_mismatch` because the gate summary prose was a
    truncated slice of vitest stdout (5-block cap) and under-represented the
    4 failing tests. The classifier could not produce exactly 4 records from
    the evidence it saw. This test locks the fix: `_step_classify` now injects
    the failing test names directly from `iter_state.failing_test_names` (or,
    as fallback, the per-test results JSON) so the classifier has a
    definitive list of N entries to classify.
    """

    def _drive_and_capture(
        self,
        failing_names: list[str],
        crashed_names: list[str],
        *,
        use_iter_state: bool,
    ) -> str:
        project_dir = make_project(self.tmp_dir)
        reports = project_dir / ".dark-factory" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "my-spec-quality-gate-summary.md").write_text("stub")
        # Per-test results JSON that _parse_failing_test_names / _parse_crashed_test_names read.
        crashed_set = set(crashed_names)
        per_test = [
            {
                "name": n,
                "status": "failed",
                "error_type": "crash" if n in crashed_set else "assertion",
            }
            for n in failing_names
        ]
        (reports / "my-spec-test-results-iter1.json").write_text(
            json.dumps(per_test),
        )
        config = _make_config()
        spec_run = _make_spec_run(iteration=1)
        # Gate contract: scenario_failed includes crashes (crashes ⊂ failed).
        assert_fail_count = len([n for n in failing_names if n not in crashed_set])
        gate_result = GateResult(
            exit_code=1,
            scenario_passed=32,
            scenario_failed=len(failing_names),
            scenario_total=32 + len(failing_names),
            pass_rate=88,
            scenario_crashed=len(crashed_names),
            scenario_assert_failed=assert_fail_count,
        )
        captured: list[str] = []

        def fake_invoke(role, prompt, **kwargs):
            captured.append(prompt)
            defects_path = reports / "my-spec-defects-iter1.json"
            records = [
                {
                    "test_name": n,
                    "category": "SCENARIO_CRASH" if n in crashed_set else "IMPL_BUG",
                }
                for n in failing_names
            ]
            defects_path.write_text(json.dumps({
                "iteration": 1,
                "schema_version": 1,
                "defects": records,
            }))
            return AgentResult(role=AgentRole.DEFECT_CLASSIFIER, exit_code=0)

        from darkfactory.models.pipeline import IterationState
        iter_state = IterationState(number=1)
        if use_iter_state:
            iter_state.failing_test_names = list(failing_names)

        with patch("darkfactory.pipeline.iteration.invoke_agent", side_effect=fake_invoke), \
             patch("darkfactory.pipeline.iteration.step_begin"):
            _step_classify(
                spec_run, gate_result, config, {}, project_dir,
                iter_state=iter_state if use_iter_state else None,
                dry_run=True,
            )

        assert captured, "_step_classify did not call invoke_agent"
        return captured[0]

    def test_failing_test_names_from_iter_state_appear_in_prompt(self) -> None:
        """Run 3 reproducer: 4 failing tests from per-test JSON, iter_state
        carries the scope-filtered list. Prompt must enumerate all 4."""
        failing = [
            "RLS Infrastructure - Substrate Layer > substrate: RLS enabled on test tables",
            "Server startup behavior > [AC-2] exits with code 1 and logs error when DATABASE_URL is missing",
            "Edge Cases > [EC-5] transaction rollback also rolls back SET LOCAL",
            "Edge Cases > [EC-8] health check returns error when query times out",
        ]
        prompt = self._drive_and_capture(failing, [], use_iter_state=True)
        for name in failing:
            self.assertIn(name, prompt)
        self.assertIn("Failing tests (authoritative", prompt)
        self.assertIn("Assertion failures:", prompt)
        self.assertIn("Emit exactly 4 records", prompt)

    def test_fallback_reads_per_test_json_when_iter_state_absent(self) -> None:
        """Back-compat: when iter_state is None (older callers / tests),
        _step_classify must fall back to _parse_failing_test_names."""
        failing = ["test A fails", "test B fails"]
        prompt = self._drive_and_capture(failing, [], use_iter_state=False)
        self.assertIn("test A fails", prompt)
        self.assertIn("test B fails", prompt)
        self.assertIn("Emit exactly 2 records", prompt)

    def test_crashed_tests_appear_under_crashes_section(self) -> None:
        """Crashes must be listed separately so the classifier knows which
        N of the N+M entries require category=SCENARIO_CRASH."""
        failing = ["test A fails", "test B crashes", "test C fails"]
        crashed = ["test B crashes"]
        prompt = self._drive_and_capture(
            failing, crashed, use_iter_state=True,
        )
        self.assertIn("Crashes:", prompt)
        self.assertIn("    - test B crashes", prompt)
        # Assertion failures section exists and excludes the crash.
        assert_idx = prompt.index("Assertion failures:")
        crash_idx = prompt.index("Crashes:")
        self.assertLess(assert_idx, crash_idx)
        between = prompt[assert_idx:crash_idx]
        self.assertIn("test A fails", between)
        self.assertIn("test C fails", between)
        self.assertNotIn("test B crashes", between)


class TestStepClassifyCascadeContextInPrompt(TempDirTestCase):
    """Gap 2 (2026-04-28 framework gap audit): classifier prompt must
    inline substrate-findings.json and pre-gate-summary.md so cascade
    detection has the upstream evidence it needs.

    Run 5b iter-2 produced 18 SPEC_AMBIGUOUS records all carrying the
    same POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION cause in their rationale
    prose — the classifier RECOGNIZED the cascade in rationales but
    had no input channel to express it as a structured upstream link.
    These tests pin the new input channel: substrate findings + pre-gate
    summary + cascade directive present when there's upstream evidence;
    silently absent when there isn't.
    """

    def _drive_and_capture(
        self,
        *,
        substrate_findings: dict | None = None,
        pre_gate_summary: str | None = None,
    ) -> str:
        project_dir = make_project(self.tmp_dir)
        reports = project_dir / ".dark-factory" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "my-spec-quality-gate-summary.md").write_text("stub")
        if substrate_findings is not None:
            (reports / "my-spec-substrate-findings.json").write_text(
                json.dumps(substrate_findings),
            )
        if pre_gate_summary is not None:
            (reports / "my-spec-pre-gate-summary.md").write_text(pre_gate_summary)
        # Per-test results so _step_classify has a failing list to forward.
        per_test = [
            {"name": "[AC-3] something", "status": "failed", "error_type": "assertion"},
        ]
        (reports / "my-spec-test-results-iter1.json").write_text(json.dumps(per_test))
        config = _make_config()
        spec_run = _make_spec_run(iteration=1)
        gate_result = GateResult(
            exit_code=1, scenario_passed=10, scenario_failed=1,
            scenario_total=11, pass_rate=90, scenario_crashed=0,
            scenario_assert_failed=1,
        )
        captured: list[str] = []

        def fake_invoke(role, prompt, **kwargs):
            captured.append(prompt)
            (reports / "my-spec-defects-iter1.json").write_text(json.dumps({
                "iteration": 1, "schema_version": 1,
                "defects": [{"test_name": "[AC-3] something", "category": "IMPL_BUG"}],
            }))
            return AgentResult(role=AgentRole.DEFECT_CLASSIFIER, exit_code=0)

        from darkfactory.models.pipeline import IterationState
        iter_state = IterationState(number=1)
        iter_state.failing_test_names = ["[AC-3] something"]
        with patch("darkfactory.pipeline.iteration.invoke_agent", side_effect=fake_invoke), \
             patch("darkfactory.pipeline.iteration.step_begin"):
            _step_classify(
                spec_run, gate_result, config, {}, project_dir,
                iter_state=iter_state, dry_run=True,
            )
        assert captured, "_step_classify did not call invoke_agent"
        return captured[0]

    def test_substrate_findings_inlined_when_present(self) -> None:
        """The exact failure mode from run 5b: substrate-findings.json
        carries diagnostic codes the classifier needs to detect cascades.
        Without inlining, the classifier sees only post-gate test names."""
        prompt = self._drive_and_capture(
            substrate_findings={
                "status": "fail",
                "findings": [
                    {"severity": "error",
                     "code": "substrate-bootstrap-not-applied",
                     "message": "drizzle-kit migrate exit 0 but tracking table missing",
                     "path": ""},
                ],
            },
        )
        self.assertIn("Substrate Verification Findings", prompt)
        self.assertIn("substrate-bootstrap-not-applied", prompt)
        self.assertIn("tracking table missing", prompt)
        # Cascade directive present (substrate findings are non-empty).
        self.assertIn("Cascade Detection", prompt)
        self.assertIn("upstream_cause", prompt)

    def test_pre_gate_summary_inlined_when_present(self) -> None:
        prompt = self._drive_and_capture(
            pre_gate_summary=(
                "# Pre-Gate Summary\n\n"
                "- Build: PASS\n"
                "- Typecheck: FAIL\n"
            ),
        )
        self.assertIn("Pre-Gate Summary", prompt)
        self.assertIn("Typecheck: FAIL", prompt)
        # Cascade directive present (pre-gate summary is non-empty).
        self.assertIn("Cascade Detection", prompt)

    def test_cascade_directive_absent_when_no_upstream_evidence(self) -> None:
        """No substrate findings + no pre-gate summary = no cascade
        directive in prompt. We don't add directive prose without the
        evidence to act on it — that would be noise the classifier
        rationalizes against."""
        prompt = self._drive_and_capture()
        self.assertNotIn("Cascade Detection", prompt)
        self.assertNotIn("Substrate Verification Findings", prompt)
        self.assertNotIn("Pre-Gate Summary", prompt)

    def test_cascade_directive_names_optional_upstream_cause_field(self) -> None:
        """The directive must explicitly tell the agent how to set the
        new optional schema field — agent.md is also updated, but the
        prompt-side reminder ensures the field is set when applicable."""
        prompt = self._drive_and_capture(
            substrate_findings={
                "status": "fail",
                "findings": [
                    {"severity": "error", "code": "x", "message": "y", "path": ""},
                ],
            },
        )
        # Schema fields named.
        self.assertIn('"stage"', prompt)
        self.assertIn('"primitive"', prompt)
        self.assertIn('"evidence"', prompt)
        # Category preference.
        self.assertIn("SCENARIO_CRASH", prompt)
        self.assertIn("never SPEC_AMBIGUOUS", prompt)


class TestStepClassifyRetryAndHardFail(TempDirTestCase):
    """Sprint 3 / B3: classifier errors retry once then hard-fail.

    Before B3, a count_mismatch / parse_failed / crash_split_mismatch
    was silently written to defects.json and the pipeline proceeded to
    Oracle on degraded input. B3 converts this into a retry-then-abort
    path so iteration budget is not spent on a known-degraded cycle.
    """

    def _make_gate_result(
        self, failed: int = 3, crashed: int = 1,
    ) -> GateResult:
        """Build a GateResult that satisfies the gate's count contract.

        ``failed`` is the total count of failing tests (crashes + assertion
        failures — crashes are a subset of failures, not disjoint). Default
        ``failed=3, crashed=1`` models 3 failing tests, 1 of which crashed.
        """
        return GateResult(
            exit_code=1,
            scenario_passed=7,
            scenario_failed=failed,
            scenario_total=10,
            pass_rate=70,
            scenario_crashed=crashed,
            scenario_assert_failed=failed - crashed,
        )

    def _prep_project(self):
        project_dir = make_project(self.tmp_dir)
        (project_dir / ".dark-factory" / "reports").mkdir(
            parents=True, exist_ok=True,
        )
        (project_dir / ".dark-factory" / "reports"
         / "my-spec-quality-gate-summary.md").write_text("stub")
        return project_dir

    def _write_valid_defects(
        self, project_dir, iteration: int = 1, failed: int = 3,
        crashed: int = 1,
    ) -> None:
        """Write a defects.json that satisfies the gate-counter contract.

        ``failed`` is the total failing-test count (includes ``crashed``).
        Emits exactly ``failed`` records: (failed - crashed) IMPL_BUG +
        ``crashed`` SCENARIO_CRASH. Matches the contract asserted in
        _classify_once (len(defects) == scenario_failed; SCENARIO_CRASH
        count == scenario_crashed).
        """
        defects_path = (
            project_dir / ".dark-factory" / "reports"
            / f"my-spec-defects-iter{iteration}.json"
        )
        assert_fail_count = failed - crashed
        records = (
            [{"test_name": f"f{i}", "category": "IMPL_BUG"}
             for i in range(assert_fail_count)]
            + [{"test_name": f"c{i}", "category": "SCENARIO_CRASH"}
               for i in range(crashed)]
        )
        defects_path.write_text(json.dumps({
            "iteration": iteration,
            "schema_version": 1,
            "error": None,
            "defects": records,
        }))

    def test_success_on_first_attempt_no_retry(self) -> None:
        """When the first classification is valid, _step_classify must
        not call invoke_agent a second time."""
        project_dir = self._prep_project()
        config = _make_config()
        spec_run = _make_spec_run(iteration=1)
        calls: list[int] = []

        def fake_invoke(role, prompt, **kwargs):
            calls.append(kwargs.get("attempt_offset", 0))
            self._write_valid_defects(project_dir)
            return AgentResult(
                role=AgentRole.DEFECT_CLASSIFIER, exit_code=0,
            )

        with patch(
            "darkfactory.pipeline.iteration.invoke_agent",
            side_effect=fake_invoke,
        ), patch("darkfactory.pipeline.iteration.step_begin"):
            _step_classify(
                spec_run, self._make_gate_result(),
                config, {}, project_dir, dry_run=True,
            )
        self.assertEqual(calls, [0])

    def test_retry_after_count_mismatch_then_succeed(self) -> None:
        """First attempt emits a count_mismatch; retry produces a valid
        file. _step_classify must return without raising, and the final
        on-disk file must carry no error field."""
        project_dir = self._prep_project()
        config = _make_config()
        spec_run = _make_spec_run(iteration=1)

        attempts: list[int] = []

        def fake_invoke(role, prompt, **kwargs):
            attempt = kwargs.get("attempt_offset", 0)
            attempts.append(attempt)
            defects_path = (
                project_dir / ".dark-factory" / "reports"
                / "my-spec-defects-iter1.json"
            )
            if attempt == 0:
                # Wrong count — classifier only emitted 1 record for
                # expected_total=3.
                defects_path.write_text(json.dumps({
                    "iteration": 1, "schema_version": 1, "error": None,
                    "defects": [
                        {"test_name": "t1", "category": "IMPL_BUG"},
                    ],
                }))
            else:
                self._write_valid_defects(project_dir)
            return AgentResult(
                role=AgentRole.DEFECT_CLASSIFIER, exit_code=0,
            )

        with patch(
            "darkfactory.pipeline.iteration.invoke_agent",
            side_effect=fake_invoke,
        ), patch("darkfactory.pipeline.iteration.step_begin"):
            _step_classify(
                spec_run, self._make_gate_result(),
                config, {}, project_dir, dry_run=True,
            )
        # attempt_offset is strided by (config.agent_retries + 1) so the
        # outer retry's inner events don't collide with the first outer
        # attempt's inner retries. Default agent_retries=1 ⇒ stride=2.
        self.assertEqual(attempts, [0, 2])
        # Final file must be valid (no error field).
        final = json.loads(
            (project_dir / ".dark-factory" / "reports"
             / "my-spec-defects-iter1.json").read_text()
        )
        self.assertIsNone(final.get("error"))

    def test_both_attempts_fail_raises_darkfactory_error(self) -> None:
        """Two consecutive pipeline-detected count mismatches → HardAbort.
        The final file on disk carries ``count_mismatch_pipeline`` (the
        pipeline-overwrite error code) so Oracle's own NO-SHIP veto fires
        if something upstream catches the exception and proceeds. This
        test also asserts the archive behavior: the agent's original (14
        records for expected_total=15) is preserved under .dark-factory/
        failures/ for post-mortem inspection."""
        from darkfactory.errors import DarkFactoryError

        project_dir = self._prep_project()
        config = _make_config()
        spec_run = _make_spec_run(iteration=1)

        attempts: list[int] = []

        def fake_invoke(role, prompt, **kwargs):
            attempts.append(kwargs.get("attempt_offset", 0))
            # Always wrong count.
            defects_path = (
                project_dir / ".dark-factory" / "reports"
                / "my-spec-defects-iter1.json"
            )
            defects_path.write_text(json.dumps({
                "iteration": 1, "schema_version": 1, "error": None,
                "defects": [
                    {"test_name": "t1", "category": "IMPL_BUG"},
                ],
            }))
            return AgentResult(
                role=AgentRole.DEFECT_CLASSIFIER, exit_code=0,
            )

        with patch(
            "darkfactory.pipeline.iteration.invoke_agent",
            side_effect=fake_invoke,
        ), patch("darkfactory.pipeline.iteration.step_begin"):
            with self.assertRaises(DarkFactoryError) as ctx:
                _step_classify(
                    spec_run, self._make_gate_result(),
                    config, {}, project_dir, dry_run=True,
                )
        # attempt_offset is strided by (config.agent_retries + 1) so the
        # outer retry's inner events don't collide with the first outer
        # attempt's inner retries. Default agent_retries=1 ⇒ stride=2.
        self.assertEqual(attempts, [0, 2])
        self.assertIn("count_mismatch_pipeline", str(ctx.exception))
        self.assertIn("2 attempts", str(ctx.exception))
        # File must be stamped with the split error code so post-mortems
        # can distinguish this (pipeline-side overwrite) from an agent
        # self-report.
        final = json.loads(
            (project_dir / ".dark-factory" / "reports"
             / "my-spec-defects-iter1.json").read_text()
        )
        self.assertEqual(final.get("error"), "count_mismatch_pipeline")
        # Archive invariant: the agent's original (14 records — wrong
        # count but real content) must survive under failures/. Without
        # this, the forensic trail the split-error-code buys us is lost.
        failures_dir = project_dir / ".dark-factory" / "failures"
        archives = sorted(failures_dir.glob(
            "my-spec-defects-iter1-agent-original-attempt*.json"
        ))
        self.assertEqual(
            len(archives), 2,
            msg=(
                f"Expected two archive files (one per pipeline overwrite), "
                f"got {len(archives)}: {[p.name for p in archives]}. "
                f"The archive preserves the agent's partial output before "
                f"the live file is reset to defects=[]."
            ),
        )
        archived = json.loads(archives[0].read_text())
        self.assertEqual(len(archived.get("defects", [])), 1)
        self.assertEqual(
            archived["defects"][0],
            {"test_name": "t1", "category": "IMPL_BUG"},
        )

    def test_classifier_exit_nonzero_both_attempts_raises(self) -> None:
        """Invocation-level failure (exit != 0) on both attempts maps
        to classifier_failed and raises after the retry."""
        from darkfactory.errors import DarkFactoryError

        project_dir = self._prep_project()
        config = _make_config()
        spec_run = _make_spec_run(iteration=1)

        def fake_invoke(role, prompt, **kwargs):
            return AgentResult(
                role=AgentRole.DEFECT_CLASSIFIER, exit_code=3,
            )

        with patch(
            "darkfactory.pipeline.iteration.invoke_agent",
            side_effect=fake_invoke,
        ), patch("darkfactory.pipeline.iteration.step_begin"):
            with self.assertRaises(DarkFactoryError) as ctx:
                _step_classify(
                    spec_run, self._make_gate_result(),
                    config, {}, project_dir, dry_run=True,
                )
        self.assertIn("classifier_failed", str(ctx.exception))

    def test_crash_split_mismatch_triggers_retry_and_hard_fail(self) -> None:
        """crash_split_mismatch is terminal after retry, same as the
        count_mismatch path — both leave Oracle with degraded input."""
        from darkfactory.errors import DarkFactoryError

        project_dir = self._prep_project()
        config = _make_config()
        spec_run = _make_spec_run(iteration=1)

        attempts: list[int] = []

        def fake_invoke(role, prompt, **kwargs):
            attempts.append(kwargs.get("attempt_offset", 0))
            # Gate contract: expected_total == scenario_failed == 3
            # (3 total failures, 1 of which crashed). Emit 3 records but
            # with ZERO SCENARIO_CRASH — the crash split is wrong.
            defects_path = (
                project_dir / ".dark-factory" / "reports"
                / "my-spec-defects-iter1.json"
            )
            defects_path.write_text(json.dumps({
                "iteration": 1, "schema_version": 1, "error": None,
                "defects": [
                    {"test_name": "a", "category": "IMPL_BUG"},
                    {"test_name": "b", "category": "IMPL_BUG"},
                    {"test_name": "c", "category": "IMPL_BUG"},
                ],
            }))
            return AgentResult(
                role=AgentRole.DEFECT_CLASSIFIER, exit_code=0,
            )

        with patch(
            "darkfactory.pipeline.iteration.invoke_agent",
            side_effect=fake_invoke,
        ), patch("darkfactory.pipeline.iteration.step_begin"):
            with self.assertRaises(DarkFactoryError) as ctx:
                _step_classify(
                    spec_run, self._make_gate_result(failed=3, crashed=1),
                    config, {}, project_dir, dry_run=True,
                )
        # attempt_offset is strided by (config.agent_retries + 1) so the
        # outer retry's inner events don't collide with the first outer
        # attempt's inner retries. Default agent_retries=1 ⇒ stride=2.
        self.assertEqual(attempts, [0, 2])
        self.assertIn("crash_split_mismatch", str(ctx.exception))


class TestBrittlePromotion(TempDirTestCase):
    """Phase 2.2: deterministic BRITTLE promotion via gate telemetry."""

    def _setup_brittle_env(
        self,
        defects: list[dict],
        gate_report_text: str,
        scenario_files: dict[str, str] | None = None,
    ) -> tuple["Path", "Path", "Path"]:
        """Create defects.json, gate report, and scenario dir.

        Returns (defects_path, gate_report_path, scenario_dir).
        """
        defects_path = self.tmp_dir / "defects.json"
        defects_path.write_text(json.dumps({
            "schema_version": 1, "error": None,
            "defects": defects,
        }))
        gate_report_path = self.tmp_dir / "gate-report.md"
        gate_report_path.write_text(gate_report_text)
        scenario_dir = self.tmp_dir / "scenarios"
        scenario_dir.mkdir(exist_ok=True)
        if scenario_files:
            for name, content in scenario_files.items():
                (scenario_dir / name).write_text(content)
        return defects_path, gate_report_path, scenario_dir

    def _make_defects(self, count: int, category: str = "IMPL_BUG") -> list[dict]:
        """Generate defects with [AC-N] test names and cited_anchor_ids."""
        return [
            {
                "test_name": f"[AC-{i}] test assertion {i}",
                "category": category,
                "cited_anchor_ids": [f"AC-{i}"],
            }
            for i in range(1, count + 1)
        ]

    def _gate_report_with_undefined(self, count: int) -> str:
        """Gate report where each [AC-N] test has 'expected undefined' output."""
        lines = []
        for i in range(1, count + 1):
            lines.append(
                f"FAIL [AC-{i}] test assertion {i}\n"
                f"  AssertionError: expected undefined to be truthy\n"
            )
        return "\n".join(lines)

    def _shared_setup_scenarios(self, count: int = 2) -> dict[str, str]:
        """Scenario files with console.log monkey-patching (shared setup)."""
        files: dict[str, str] = {}
        for i in range(1, count + 1):
            files[f"scenario-{i}.ts"] = (
                "import { describe, it, expect } from 'vitest';\n"
                "describe('test', () => {\n"
                "  const logs: string[] = [];\n"
                f"  console.log = (msg: string) => {{ logs.push(msg); }};\n"
                "  it('captures log', () => {\n"
                "    expect(logs.length).toBeGreaterThan(0);\n"
                "  });\n"
                "});\n"
            )
        return files

    def test_3_defects_with_all_conditions_promotes_to_brittle(self) -> None:
        """3 IMPL_BUG defects with expected-undefined + shared console.log
        setup → all promoted to BRITTLE."""
        defects = self._make_defects(3)
        gate_text = self._gate_report_with_undefined(3)
        scenarios = self._shared_setup_scenarios(2)
        defects_path, gate_report_path, scenario_dir = self._setup_brittle_env(
            defects, gate_text, scenarios,
        )

        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        iter_state.scenario_crashed = 0
        iter_state.scenario_total = 10

        apply_classification_routing(
            spec_run, iter_state, "",
            defects_path=defects_path,
            gate_report_path=gate_report_path,
            scenario_dir=scenario_dir,
        )

        self.assertEqual(len(spec_run.classification_history), 1)
        history = spec_run.classification_history[0]
        for section in ["AC-1", "AC-2", "AC-3"]:
            self.assertEqual(history[section], "BRITTLE", f"{section} not promoted")

    def test_below_threshold_no_promotion(self) -> None:
        """2 defects (below min_defects=3) → no promotion even if other
        conditions are met."""
        defects = self._make_defects(2)
        gate_text = self._gate_report_with_undefined(2)
        scenarios = self._shared_setup_scenarios(2)
        defects_path, gate_report_path, scenario_dir = self._setup_brittle_env(
            defects, gate_text, scenarios,
        )

        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        iter_state.scenario_crashed = 0
        iter_state.scenario_total = 10

        apply_classification_routing(
            spec_run, iter_state, "",
            defects_path=defects_path,
            gate_report_path=gate_report_path,
            scenario_dir=scenario_dir,
        )

        self.assertEqual(len(spec_run.classification_history), 1)
        history = spec_run.classification_history[0]
        # Both should remain IMPL_BUG — not promoted.
        for section in ["AC-1", "AC-2"]:
            self.assertEqual(history[section], "IMPL_BUG", f"{section} wrongly promoted")

    def test_crashes_present_no_promotion(self) -> None:
        """3 defects but scenario_crashed > 0 → BRITTLE block does not fire
        (guard condition excludes crash scenarios)."""
        defects = self._make_defects(3)
        gate_text = self._gate_report_with_undefined(3)
        scenarios = self._shared_setup_scenarios(2)
        defects_path, gate_report_path, scenario_dir = self._setup_brittle_env(
            defects, gate_text, scenarios,
        )

        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        iter_state.scenario_crashed = 2  # crashes present
        iter_state.scenario_total = 10
        iter_state.scenario_fail_count = 3
        iter_state.scenario_assert_failed = 1

        apply_classification_routing(
            spec_run, iter_state, "",
            defects_path=defects_path,
            gate_report_path=gate_report_path,
            scenario_dir=scenario_dir,
        )

        self.assertEqual(len(spec_run.classification_history), 1)
        history = spec_run.classification_history[0]
        for section in ["AC-1", "AC-2", "AC-3"]:
            self.assertEqual(history[section], "IMPL_BUG", f"{section} wrongly promoted")

    def test_no_shared_setup_no_promotion(self) -> None:
        """3 defects with expected-undefined but scenario files lack shared
        setup pattern → condition 4 fails, no promotion."""
        defects = self._make_defects(3)
        gate_text = self._gate_report_with_undefined(3)
        # Scenario files with no console.log/spyOn/process.stdout patterns.
        plain_scenarios = {
            "scenario-1.ts": "import { it } from 'vitest';\nit('works', () => {});\n",
            "scenario-2.ts": "import { it } from 'vitest';\nit('passes', () => {});\n",
        }
        defects_path, gate_report_path, scenario_dir = self._setup_brittle_env(
            defects, gate_text, plain_scenarios,
        )

        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        iter_state.scenario_crashed = 0
        iter_state.scenario_total = 10

        apply_classification_routing(
            spec_run, iter_state, "",
            defects_path=defects_path,
            gate_report_path=gate_report_path,
            scenario_dir=scenario_dir,
        )

        self.assertEqual(len(spec_run.classification_history), 1)
        history = spec_run.classification_history[0]
        for section in ["AC-1", "AC-2", "AC-3"]:
            self.assertEqual(history[section], "IMPL_BUG", f"{section} wrongly promoted")

    def test_mixed_eligible_and_ineligible_partial_promotion(self) -> None:
        """5 defects: 3 IMPL_BUG with matching undefined patterns + 1 BRITTLE
        (already) + 1 IMPL_BUG without the pattern → only the 3 matching are
        promoted."""
        defects = [
            {"test_name": "[AC-1] log startup", "category": "IMPL_BUG",
             "cited_anchor_ids": ["AC-1"]},
            {"test_name": "[AC-2] log redacted", "category": "IMPL_BUG",
             "cited_anchor_ids": ["AC-2"]},
            {"test_name": "[AC-3] request log", "category": "IMPL_BUG",
             "cited_anchor_ids": ["AC-3"]},
            {"test_name": "[FR-1] routes exist", "category": "BRITTLE",
             "cited_anchor_ids": ["FR-1"]},
            {"test_name": "[FR-2] validation works", "category": "IMPL_BUG",
             "cited_anchor_ids": ["FR-2"]},
        ]
        # Only AC-1..AC-3 have undefined patterns in the gate report.
        gate_text = (
            "FAIL [AC-1] log startup\n"
            "  AssertionError: expected undefined to be truthy\n\n"
            "FAIL [AC-2] log redacted\n"
            "  AssertionError: expected undefined to be truthy\n\n"
            "FAIL [AC-3] request log\n"
            "  AssertionError: expected undefined to be truthy\n\n"
            "FAIL [FR-1] routes exist\n"
            "  AssertionError: expected 404 to equal 200\n\n"
            "FAIL [FR-2] validation works\n"
            "  AssertionError: expected 'error' to equal 'success'\n\n"
        )
        scenarios = self._shared_setup_scenarios(2)
        defects_path, gate_report_path, scenario_dir = self._setup_brittle_env(
            defects, gate_text, scenarios,
        )

        spec_run = _make_spec_run(iteration=1, effective_max_iterations=3)
        iter_state = IterationState(number=1)
        iter_state.scenario_crashed = 0
        iter_state.scenario_total = 15

        apply_classification_routing(
            spec_run, iter_state, "",
            defects_path=defects_path,
            gate_report_path=gate_report_path,
            scenario_dir=scenario_dir,
        )

        self.assertEqual(len(spec_run.classification_history), 1)
        history = spec_run.classification_history[0]
        # AC-1, AC-2, AC-3 should be promoted to BRITTLE.
        for section in ["AC-1", "AC-2", "AC-3"]:
            self.assertEqual(history[section], "BRITTLE", f"{section} not promoted")
        # FR-1 was already BRITTLE (untouched). FR-2 has no undefined pattern.
        self.assertEqual(history["FR-1"], "BRITTLE")
        self.assertEqual(history["FR-2"], "IMPL_BUG")


# ---------------------------------------------------------------------------
# B2: Oracle reads prior iteration feedback + changes-since-last-iter diff.
# Plan doc: docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md
# (Sprint 2, B2).
# ---------------------------------------------------------------------------


class TestPriorIterationContextFormatter(TempDirTestCase):
    """_format_prior_iteration_context_inline composition tests.

    Uses dry_run=True wherever we want to exercise the ``no git side-
    effects`` path. When dry_run=False we patch _capture_iteration_end_sha
    and _compute_diff_stat rather than shelling out to real git.
    """

    def setUp(self) -> None:
        super().setUp()
        self.project_dir = make_project(self.tmp_dir)

    def _write_feedback(
        self,
        iteration: int,
        content: str = "# Iteration Feedback\n## Spec Section Failures\nFR-2 bug.\n",
        spec_name: str = "my-spec",
    ) -> None:
        reviews = self.project_dir / ".dark-factory" / "reviews"
        (reviews / f"{spec_name}-feedback-iter{iteration}.md").write_text(content)

    def test_empty_on_iteration_1(self) -> None:
        """Iteration 1 has no prior iter, so the block must be empty."""
        spec_run = _make_spec_run(iteration=1)
        result = _format_prior_iteration_context_inline(
            spec_run, self.project_dir, current_sha="",
        )
        self.assertEqual(result, "")

    def test_empty_when_prior_feedback_missing(self) -> None:
        """If the prior iter's feedback file is absent, emit nothing."""
        spec_run = _make_spec_run(iteration=2)
        spec_run.iterations = [IterationState(number=1)]
        result = _format_prior_iteration_context_inline(
            spec_run, self.project_dir, current_sha="",
        )
        self.assertEqual(result, "")

    def test_dry_run_inlines_feedback_but_skips_diff(self) -> None:
        """Dry-run still inlines feedback (file-read only, no side
        effects) but skips the git diff shell-out. The B2 block is
        still emitted so prompt-construction tests can verify shape."""
        self._write_feedback(1, "# FB\nFR-2 broken\n")
        spec_run = _make_spec_run(iteration=2)
        spec_run.iterations = [IterationState(number=1)]
        # Patch _compute_diff_stat to assert it is NOT called in dry-run.
        with patch(
            "darkfactory.pipeline.iteration._compute_diff_stat",
        ) as diff_patch:
            result = _format_prior_iteration_context_inline(
                spec_run, self.project_dir, current_sha="",
                dry_run=True,
            )
            diff_patch.assert_not_called()
        self.assertIn("## Prior Iteration Context (B2)", result)
        self.assertIn("FR-2 broken", result)
        self.assertIn("No diff available", result)

    def test_inlines_prior_feedback_with_b2_header(self) -> None:
        """When iter>=2 and feedback exists, the block is emitted with
        the B2-tagged header and the feedback content."""
        self._write_feedback(
            1,
            "# Iteration Feedback\n## Spec Section Failures\n"
            "| # | Section | Category | Severity |\n"
            "| 1 | FR-2 | boundary | Critical |\n",
        )
        spec_run = _make_spec_run(iteration=2)
        spec_run.iterations = [IterationState(number=1)]
        with patch(
            "darkfactory.pipeline.iteration._compute_diff_stat",
            return_value=" src/fr2.ts | 10 ++++++----\n 1 file changed",
        ):
            result = _format_prior_iteration_context_inline(
                spec_run, self.project_dir, current_sha="abc123",
            )
        self.assertIn("## Prior Iteration Context (B2)", result)
        self.assertIn("Fixed`, `Still-Broken`", result)
        self.assertIn("FR-2 | boundary", result)
        self.assertIn("src/fr2.ts", result)

    def test_diff_base_prefers_prior_iter_end_sha(self) -> None:
        """If the prior IterationState recorded an end SHA, that's the
        diff base — not the preflight baseline."""
        self._write_feedback(1)
        prior = IterationState(number=1, iteration_end_sha="prior_sha_xyz")
        spec_run = _make_spec_run(iteration=2)
        spec_run.iterations = [prior]
        spec_run.preflight_base_ref = "preflight_sha_abc"

        seen: dict[str, str] = {}

        def fake_diff(_pd, base, head):
            seen["base"] = base
            seen["head"] = head
            return "some diff"

        with patch(
            "darkfactory.pipeline.iteration._compute_diff_stat",
            side_effect=fake_diff,
        ):
            result = _format_prior_iteration_context_inline(
                spec_run, self.project_dir, current_sha="current_sha_123",
            )
        self.assertEqual(seen["base"], "prior_sha_xyz")
        self.assertEqual(seen["head"], "current_sha_123")
        self.assertIn("iter 1 oracle-call state", result)

    def test_diff_base_falls_back_to_preflight_then_head(self) -> None:
        """Without a prior end SHA, fall back to preflight_base_ref;
        without that, fall back to HEAD."""
        self._write_feedback(1)

        # Case 1: preflight baseline present, no prior end SHA.
        spec_run = _make_spec_run(iteration=2)
        spec_run.iterations = [IterationState(number=1)]
        spec_run.preflight_base_ref = "preflight_sha_abc"
        seen: dict[str, str] = {}
        with patch(
            "darkfactory.pipeline.iteration._compute_diff_stat",
            side_effect=lambda pd, b, h: seen.setdefault("base", b),
        ):
            result = _format_prior_iteration_context_inline(
                spec_run, self.project_dir, current_sha="x",
            )
        self.assertEqual(seen["base"], "preflight_sha_abc")
        self.assertIn("run-start baseline", result)

        # Case 2: neither available — base is HEAD.
        spec_run2 = _make_spec_run(iteration=2)
        spec_run2.iterations = [IterationState(number=1)]
        seen2: dict[str, str] = {}
        with patch(
            "darkfactory.pipeline.iteration._compute_diff_stat",
            side_effect=lambda pd, b, h: seen2.setdefault("base", b),
        ):
            result2 = _format_prior_iteration_context_inline(
                spec_run2, self.project_dir, current_sha="x",
            )
        self.assertEqual(seen2["base"], "HEAD")
        self.assertIn("HEAD (no baseline captured", result2)

    def test_empty_current_sha_skips_diff_entirely(self) -> None:
        """When the oracle-step capture returns empty (e.g. non-git
        project or git unavailable), _compute_diff_stat MUST NOT be
        called with ``HEAD`` — ``git diff base..HEAD`` is a no-op
        because HEAD hasn't moved since the prior iteration's oracle
        call, so it would produce a misleading empty diff. The
        formatter must emit the "no diff available" message instead."""
        self._write_feedback(1)
        spec_run = _make_spec_run(iteration=2)
        spec_run.iterations = [IterationState(number=1)]
        with patch(
            "darkfactory.pipeline.iteration._compute_diff_stat",
        ) as diff_patch:
            result = _format_prior_iteration_context_inline(
                spec_run, self.project_dir, current_sha="",
            )
            diff_patch.assert_not_called()
        self.assertIn("## Prior Iteration Context (B2)", result)
        self.assertIn("No diff available", result)

    def test_empty_diff_produces_graceful_no_signal_message(self) -> None:
        """When the diff is empty (capture failed or no changes), still
        emit the B2 block with a clear ``no diff available`` note —
        don't swallow the prior feedback silently."""
        self._write_feedback(1)
        spec_run = _make_spec_run(iteration=2)
        spec_run.iterations = [IterationState(number=1)]
        with patch(
            "darkfactory.pipeline.iteration._compute_diff_stat",
            return_value="",
        ):
            result = _format_prior_iteration_context_inline(
                spec_run, self.project_dir, current_sha="",
            )
        self.assertIn("## Prior Iteration Context (B2)", result)
        self.assertIn("No diff available", result)


class TestPreflightOnlyPromptShape(TempDirTestCase):
    """Sprint 3 / B5: _step_implementation(preflight_only=True) produces
    a focused prompt that embeds only the preflight findings and
    forbids spec re-reads or behavioral changes.
    """

    def _capture_impl_prompt(
        self, *, pre_gate_result, preflight_only: bool,
    ) -> str:
        project_dir = make_project(self.tmp_dir)
        config = _make_config()
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(number=1)
        captured: list[str] = []

        def fake_invoke(role, prompt, **kwargs):
            captured.append(prompt)
            return _fake_agent_result()

        with patch(
            "darkfactory.pipeline.iteration.invoke_agent",
            side_effect=fake_invoke,
        ):
            _step_implementation(
                spec_run, iter_state, config, {}, project_dir,
                pre_gate_result=pre_gate_result,
                preflight_only=preflight_only,
                dry_run=True,
            )
        assert captured
        return captured[0]

    def test_preflight_only_prompt_omits_spec_read(self) -> None:
        """The B5 narrow prompt must NOT tell the implementer to read
        the spec — preflight findings are self-describing."""
        pg = PreGateResult(
            exit_code=1,
            output="FR-2 forbids `process.exit`\n  src/app.ts:12",
        )
        prompt = self._capture_impl_prompt(
            pre_gate_result=pg, preflight_only=True,
        )
        self.assertNotIn("Read the spec", prompt)
        self.assertIn("FR-2 forbids `process.exit`", prompt)
        self.assertIn("src/app.ts:12", prompt)
        # The narrow prompt must also warn that this is the only
        # opportunity — shapes the implementer's behavior.
        self.assertIn("only opportunity", prompt)
        self.assertIn("iteration will be aborted", prompt)

    def test_pre_gate_only_prompt_still_instructs_spec_read(self) -> None:
        """Regression: when preflight_only is False, the existing
        pre-gate prompt shape is preserved (tells implementer to read
        the spec + fix lint/typecheck/build)."""
        pg = PreGateResult(
            exit_code=1,
            output="src/app.ts(3,1): error TS2304: Cannot find name 'x'",
        )
        prompt = self._capture_impl_prompt(
            pre_gate_result=pg, preflight_only=False,
        )
        self.assertIn("Read the spec", prompt)
        self.assertIn("lint, typecheck, and build", prompt)
        self.assertIn("Cannot find name 'x'", prompt)


class TestPreflightBoundedRetry(TempDirTestCase):
    """Sprint 3 / B5: preflight-only retry policy.

    Verifies _run_preflight_with_bounded_retry's three paths: clean
    first pass (no-op), dirty-then-clean (one implementer invocation),
    dirty-then-dirty (DarkFactoryError raised with preflight output
    embedded).
    """

    def _run_helper(self, preflight_side_effect, *, dry_run: bool = False):
        project_dir = make_project(self.tmp_dir)
        config = _make_config()
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(number=1)
        impl_calls: list[dict] = []

        def fake_impl(spec_run_, iter_state_, config_, barriers_,
                      project_dir_, **kwargs):
            impl_calls.append({
                "preflight_only": kwargs.get("preflight_only", False),
                "pre_gate_result": kwargs.get("pre_gate_result"),
            })

        with patch(
            "darkfactory.pipeline.iteration._run_deterministic_preflight",
            side_effect=preflight_side_effect,
        ), patch(
            "darkfactory.pipeline.iteration._step_implementation",
            side_effect=fake_impl,
        ):
            _run_preflight_with_bounded_retry(
                spec_run, iter_state, config, {}, project_dir,
                lambda _m: None,
                dry_run=dry_run,
            )
        return impl_calls

    def test_clean_preflight_no_retry(self) -> None:
        """No violations on first pass ⇒ no implementer invocation."""
        impl_calls = self._run_helper(
            preflight_side_effect=[None],
        )
        self.assertEqual(impl_calls, [])

    def test_dirty_then_clean_invokes_implementer_once(self) -> None:
        """Violations on first pass, clean on retry ⇒ one implementer
        invocation with preflight_only=True."""
        initial = PreGateResult(exit_code=1, output="FR-2 violation")
        impl_calls = self._run_helper(
            preflight_side_effect=[initial, None],
        )
        self.assertEqual(len(impl_calls), 1)
        self.assertTrue(impl_calls[0]["preflight_only"])
        self.assertIs(impl_calls[0]["pre_gate_result"], initial)

    def test_dirty_then_still_dirty_raises(self) -> None:
        """Violations persist after retry ⇒ DarkFactoryError with the
        terminal preflight output embedded for operator diagnosis."""
        from darkfactory.errors import DarkFactoryError

        initial = PreGateResult(exit_code=1, output="FR-2 violation")
        terminal = PreGateResult(
            exit_code=1, output="FR-2 violation still present",
        )
        with self.assertRaises(DarkFactoryError) as ctx:
            self._run_helper(
                preflight_side_effect=[initial, terminal],
            )
        self.assertIn("FR-2 violation still present", str(ctx.exception))
        self.assertIn("focused implementer retry", str(ctx.exception))
        self.assertIn("Iteration aborted", str(ctx.exception))

    def test_terminal_output_is_truncated_to_2000_chars(self) -> None:
        """A pathological preflight output doesn't blow up the
        exception message; the truncation matches the pre-existing
        pre-gate exhaustion log pattern."""
        from darkfactory.errors import DarkFactoryError

        big = "x" * 10_000
        initial = PreGateResult(exit_code=1, output="short")
        terminal = PreGateResult(exit_code=1, output=big)
        with self.assertRaises(DarkFactoryError) as ctx:
            self._run_helper(
                preflight_side_effect=[initial, terminal],
            )
        # The truncated body is the last <=2000 chars of 'x' — an
        # oracle-consumable bounded amount, not the full 10_000.
        message = str(ctx.exception)
        # 2000 x's present, but not 3000.
        self.assertIn("x" * 2000, message)
        self.assertNotIn("x" * 3000, message)

    def test_dry_run_is_noop(self) -> None:
        """Dry-run: _run_deterministic_preflight returns None, so the
        helper is a no-op (no implementer call, no exception)."""
        impl_calls = self._run_helper(
            preflight_side_effect=[None],
            dry_run=True,
        )
        self.assertEqual(impl_calls, [])


class TestCaptureIterationEndSha(TempDirTestCase):
    """_capture_iteration_end_sha: stash-first, HEAD fallback, ref pin.

    We patch the subprocess_runner.run entrypoint rather than shelling
    out to real git because the test needs to exercise specific
    exit-code/output combinations and verify the ref-pin command is
    issued with the expected args.
    """

    def setUp(self) -> None:
        super().setUp()
        self.project_dir = make_project(self.tmp_dir)

    def _make_run_recorder(self, responses):
        """Build a fake ``run`` that pops a response for each call.

        Each response is a dict {exit_code, stdout, stderr}. The recorder
        stores the (cmd, kwargs) tuples in ``calls`` for assertions.
        """
        from darkfactory.utils.subprocess_runner import RunResult

        calls: list[list[str]] = []
        it = iter(responses)

        def fake_run(cmd, *args, **kwargs):
            calls.append(list(cmd))
            r = next(it)
            return RunResult(
                exit_code=r.get("exit_code", 0),
                stdout=r.get("stdout", ""),
                stderr=r.get("stderr", ""),
                command=list(cmd),
            )
        return fake_run, calls

    def test_dirty_tree_returns_stash_sha_and_pins_ref(self) -> None:
        """Normal path: stash-create emits a SHA, which is then pinned
        via git update-ref to the caller's ref_name."""
        fake_run, calls = self._make_run_recorder([
            {"stdout": "deadbeef1234\n"},  # stash create
            {"stdout": ""},                 # update-ref
        ])
        with patch(
            "darkfactory.utils.subprocess_runner.run",
            side_effect=fake_run,
        ):
            sha = _capture_iteration_end_sha(
                self.project_dir,
                ref_name="refs/dark-factory/iter-end/x-1",
            )
        self.assertEqual(sha, "deadbeef1234")
        self.assertEqual(len(calls), 2)
        self.assertIn("stash", calls[0])
        self.assertEqual(
            calls[1][-3:],
            ["update-ref", "refs/dark-factory/iter-end/x-1",
             "deadbeef1234"],
        )

    def test_clean_tree_falls_back_to_head_rev_parse(self) -> None:
        """Empty stash-create output = clean tree. The function MUST
        fall back to ``git rev-parse HEAD`` instead of returning empty.
        A clean tree is a valid state, not a capture failure."""
        fake_run, calls = self._make_run_recorder([
            {"stdout": ""},             # stash create: clean
            {"stdout": "headsha999\n"}, # rev-parse HEAD
            {"stdout": ""},             # update-ref
        ])
        with patch(
            "darkfactory.utils.subprocess_runner.run",
            side_effect=fake_run,
        ):
            sha = _capture_iteration_end_sha(
                self.project_dir,
                ref_name="refs/dark-factory/iter-end/x-2",
            )
        self.assertEqual(sha, "headsha999")
        self.assertEqual(len(calls), 3)
        self.assertIn("rev-parse", calls[1])

    def test_stash_failure_returns_empty_no_fallback(self) -> None:
        """Non-zero stash exit = real git failure (not a clean tree).
        The function returns empty; no HEAD fallback, no pin attempt."""
        fake_run, calls = self._make_run_recorder([
            {"exit_code": 128, "stderr": "fatal: not a git repository"},
        ])
        with patch(
            "darkfactory.utils.subprocess_runner.run",
            side_effect=fake_run,
        ):
            sha = _capture_iteration_end_sha(
                self.project_dir,
                ref_name="refs/dark-factory/iter-end/x-3",
            )
        self.assertEqual(sha, "")
        self.assertEqual(len(calls), 1)

    def test_clean_tree_with_broken_head_returns_empty(self) -> None:
        """If stash is clean AND rev-parse HEAD fails (e.g. unborn
        branch), empty is the correct degradation."""
        fake_run, _calls = self._make_run_recorder([
            {"stdout": ""},                  # stash create
            {"exit_code": 128,               # rev-parse HEAD fails
             "stderr": "fatal: bad revision 'HEAD'"},
        ])
        with patch(
            "darkfactory.utils.subprocess_runner.run",
            side_effect=fake_run,
        ):
            sha = _capture_iteration_end_sha(
                self.project_dir,
                ref_name="refs/dark-factory/iter-end/x-4",
            )
        self.assertEqual(sha, "")

    def test_no_ref_name_skips_pin(self) -> None:
        """When ref_name is empty, update-ref MUST NOT be called."""
        fake_run, calls = self._make_run_recorder([
            {"stdout": "abc\n"},  # stash create only
        ])
        with patch(
            "darkfactory.utils.subprocess_runner.run",
            side_effect=fake_run,
        ):
            sha = _capture_iteration_end_sha(self.project_dir)
        self.assertEqual(sha, "abc")
        self.assertEqual(len(calls), 1)

    def test_pin_failure_is_soft(self) -> None:
        """If update-ref fails, the SHA is still returned — pinning is
        defense-in-depth, not a correctness requirement."""
        fake_run, _calls = self._make_run_recorder([
            {"stdout": "sha_ok\n"},                    # stash create
            {"exit_code": 1, "stderr": "pin failed"},  # update-ref
        ])
        with patch(
            "darkfactory.utils.subprocess_runner.run",
            side_effect=fake_run,
        ):
            sha = _capture_iteration_end_sha(
                self.project_dir,
                ref_name="refs/dark-factory/iter-end/x-5",
            )
        self.assertEqual(sha, "sha_ok")


class TestOracleStepThreadsPriorContext(TempDirTestCase):
    """_step_oracle integrates the B2 prior-context block and stamps the
    per-iteration end SHA for the next iteration to diff against."""

    def setUp(self) -> None:
        super().setUp()
        self.project_dir = make_project(self.tmp_dir)

    def _capture(self, spec_run, iter_state):
        config = _make_config()
        captured: list[str] = []

        def fake_invoke(role, prompt, **kwargs):
            captured.append(prompt)
            return AgentResult(
                role=AgentRole.ORACLE, exit_code=0, output="SHIP\nOK",
            )

        with patch(
            "darkfactory.pipeline.iteration.invoke_agent",
            side_effect=fake_invoke,
        ):
            _step_oracle(
                spec_run, iter_state, config, {}, self.project_dir,
                dry_run=True,
            )
        assert captured, "_step_oracle did not call invoke_agent"
        return captured[0]

    def test_iteration_1_has_no_prior_context_block(self) -> None:
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1, scenario_pass_count=30, scenario_fail_count=0,
            scenario_total=30, pass_rate=100,
        )
        prompt = self._capture(spec_run, iter_state)
        self.assertNotIn("Prior Iteration Context (B2)", prompt)

    def test_iteration_2_includes_prior_context_when_feedback_exists(self) -> None:
        """With feedback-iter1.md on disk, iter 2's oracle prompt carries
        the B2 block with the prior feedback content."""
        (self.project_dir / ".dark-factory" / "reviews"
         / "my-spec-feedback-iter1.md").write_text(
            "# Feedback\nFR-2 boundary handling is broken.\n"
        )
        spec_run = _make_spec_run(iteration=2)
        spec_run.iterations = [
            IterationState(number=1, iteration_end_sha="prior_sha"),
        ]
        iter_state = IterationState(
            number=2, scenario_pass_count=28, scenario_fail_count=2,
            scenario_total=30, pass_rate=93,
        )
        prompt = self._capture(spec_run, iter_state)
        self.assertIn("Prior Iteration Context (B2)", prompt)
        self.assertIn("FR-2 boundary handling is broken", prompt)

    def test_dry_run_does_not_capture_sha(self) -> None:
        """In dry-run, iteration_end_sha must remain empty — no git calls."""
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(number=1)
        # _capture_iteration_end_sha must NOT be invoked in dry-run.
        with patch(
            "darkfactory.pipeline.iteration._capture_iteration_end_sha",
        ) as capture_patch:
            self._capture(spec_run, iter_state)
            capture_patch.assert_not_called()
        self.assertEqual(iter_state.iteration_end_sha, "")


class TestRollbackToIterationCleansUntrackedFiles(TempDirTestCase):
    """R1-verify: ``_rollback_to_iteration`` must both revert tracked-file
    edits AND delete untracked files created during the regressed iteration.

    The rollback reference is pinned with ``git update-ref`` before the
    implementer runs; checkout restores tracked files to that state. But
    ``git checkout <ref> -- .`` does NOT remove files that were added
    *after* the ref was taken — only changes to already-tracked paths.
    Those untracked files are what ``git clean -fd <source_dir>`` deletes.
    This test uses a real git repo (no mocks) because the bug being
    defended against lives in the interaction between checkout and clean.
    """

    def setUp(self) -> None:
        super().setUp()
        from darkfactory.utils.subprocess_runner import run
        self._run = run
        self.project_dir = self.tmp_dir
        (self.project_dir / "src").mkdir()
        # Initial commit so HEAD is valid.
        self._git("init", "-q")
        self._git("config", "user.email", "t@t")
        self._git("config", "user.name", "t")
        (self.project_dir / "src" / "existing.py").write_text("v1\n")
        self._git("add", ".")
        self._git("commit", "-q", "-m", "initial")

    def _git(self, *args: str) -> str:
        result = self._run(
            ["git", "-C", str(self.project_dir), *args], timeout=10,
        )
        assert result.exit_code == 0, (
            f"git {args} failed: {result.stderr!r}"
        )
        return result.stdout

    def test_untracked_files_in_source_dir_are_cleaned(self) -> None:
        from darkfactory.pipeline.iteration import _rollback_to_iteration

        # Pin the clean state under a rollback ref (as the pipeline does
        # via _capture_iteration_end_sha + update-ref).
        head = self._git("rev-parse", "HEAD").strip()
        run_id = "testrun"
        ref = f"refs/dark-factory/iter-end/{run_id}-1"
        self._git("update-ref", ref, head)

        # Simulate a regressed iteration: modify a tracked file AND drop
        # two untracked files into the source dir.
        (self.project_dir / "src" / "existing.py").write_text("v2-regressed\n")
        (self.project_dir / "src" / "new_untracked.py").write_text("junk\n")
        (self.project_dir / "src" / "also_new.py").write_text("more junk\n")

        ok = _rollback_to_iteration(
            self.project_dir, run_id, target_iteration=1,
            source_dirs=["src"],
        )

        self.assertTrue(ok)
        self.assertEqual(
            (self.project_dir / "src" / "existing.py").read_text(), "v1\n",
            "tracked file was not reverted by git checkout",
        )
        self.assertFalse(
            (self.project_dir / "src" / "new_untracked.py").exists(),
            "untracked file in source dir survived rollback — git clean did not run",
        )
        self.assertFalse(
            (self.project_dir / "src" / "also_new.py").exists(),
            "untracked file in source dir survived rollback — git clean did not run",
        )

    def test_missing_rollback_ref_returns_false(self) -> None:
        from darkfactory.pipeline.iteration import _rollback_to_iteration

        ok = _rollback_to_iteration(
            self.project_dir, "no_such_run", target_iteration=7,
            source_dirs=["src"],
        )
        self.assertFalse(ok)


class TestClassifierIOHashEmission(TempDirTestCase):
    """Phase 5.6 B4: CLASSIFIER_IO_HASH emits on every return path.

    The hash event is the measurement instrument for classifier
    determinism — it must fire regardless of whether the attempt
    succeeded, errored, or was retried. The ``input_hash`` is a property
    of the input contract (sorted failing names + gate counters) and so
    must match across retries on the same input; only the ``output_hash``
    may diverge.
    """

    def _prep_project(self):
        project_dir = make_project(self.tmp_dir)
        (project_dir / ".dark-factory" / "reports").mkdir(
            parents=True, exist_ok=True,
        )
        (project_dir / ".dark-factory" / "reports"
         / "my-spec-quality-gate-summary.md").write_text("stub")
        return project_dir

    def _gate_result(self, failed: int = 3, crashed: int = 1) -> GateResult:
        """Build a GateResult honoring the gate's count contract.

        ``failed`` is total failing (includes ``crashed``). Default
        3 failing / 1 crashed / 2 assert-failed.
        """
        return GateResult(
            exit_code=1,
            scenario_passed=7,
            scenario_failed=failed,
            scenario_total=10,
            pass_rate=70,
            scenario_crashed=crashed,
            scenario_assert_failed=failed - crashed,
        )

    def _read_hash_events(self, events_file):
        from darkfactory.utils.json_helpers import read_jsonl
        return [
            r for r in read_jsonl(events_file)
            if r["event"] == "CLASSIFIER_IO_HASH"
        ]

    def test_emits_once_on_success(self) -> None:
        project_dir = self._prep_project()
        events_file = project_dir / "events.jsonl"
        config = _make_config()
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1,
            failing_test_names=["f0", "f1", "c0"],
        )

        def fake_invoke(role, prompt, **kwargs):
            defects_path = (
                project_dir / ".dark-factory" / "reports"
                / "my-spec-defects-iter1.json"
            )
            defects_path.write_text(json.dumps({
                "iteration": 1, "schema_version": 1, "error": None,
                "defects": [
                    {"test_name": "f0", "category": "IMPL_BUG"},
                    {"test_name": "f1", "category": "IMPL_BUG"},
                    {"test_name": "c0", "category": "SCENARIO_CRASH"},
                ],
            }))
            return AgentResult(
                role=AgentRole.DEFECT_CLASSIFIER, exit_code=0,
            )

        with patch(
            "darkfactory.pipeline.iteration.invoke_agent",
            side_effect=fake_invoke,
        ), patch("darkfactory.pipeline.iteration.step_begin"):
            _step_classify(
                spec_run, self._gate_result(),
                config, {}, project_dir,
                iter_state=iter_state,
                events_file=events_file,
                dry_run=True,
            )

        events = self._read_hash_events(events_file)
        self.assertEqual(len(events), 1)
        data = events[0]["data"]
        self.assertEqual(data["error"], "")
        self.assertEqual(data["defects_count"], 3)
        self.assertEqual(data["failing_test_count"], 3)
        self.assertEqual(len(data["input_hash"]), 64)
        self.assertEqual(len(data["output_hash"]), 64)
        self.assertEqual(data["attempt"], 0)

    def test_input_hash_matches_across_retries(self) -> None:
        """Two retries on the same input → identical input_hash,
        differing output_hash (pre-retry: count_mismatch stub, post-retry:
        valid). This is the canonical 'measurement instrument' property."""
        project_dir = self._prep_project()
        events_file = project_dir / "events.jsonl"
        config = _make_config()
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1,
            failing_test_names=["f0", "f1", "c0"],
        )

        call_count = [0]

        def fake_invoke(role, prompt, **kwargs):
            call_count[0] += 1
            defects_path = (
                project_dir / ".dark-factory" / "reports"
                / "my-spec-defects-iter1.json"
            )
            if call_count[0] == 1:
                # count_mismatch: emit 1 record when expected=3.
                defects_path.write_text(json.dumps({
                    "iteration": 1, "schema_version": 1, "error": None,
                    "defects": [
                        {"test_name": "f0", "category": "IMPL_BUG"},
                    ],
                }))
            else:
                defects_path.write_text(json.dumps({
                    "iteration": 1, "schema_version": 1, "error": None,
                    "defects": [
                        {"test_name": "f0", "category": "IMPL_BUG"},
                        {"test_name": "f1", "category": "IMPL_BUG"},
                        {"test_name": "c0", "category": "SCENARIO_CRASH"},
                    ],
                }))
            return AgentResult(
                role=AgentRole.DEFECT_CLASSIFIER, exit_code=0,
            )

        with patch(
            "darkfactory.pipeline.iteration.invoke_agent",
            side_effect=fake_invoke,
        ), patch("darkfactory.pipeline.iteration.step_begin"):
            _step_classify(
                spec_run, self._gate_result(),
                config, {}, project_dir,
                iter_state=iter_state,
                events_file=events_file,
                dry_run=True,
            )

        events = self._read_hash_events(events_file)
        self.assertEqual(len(events), 2)
        # input_hash is a property of the INPUT — must be stable across retries.
        self.assertEqual(
            events[0]["data"]["input_hash"],
            events[1]["data"]["input_hash"],
        )
        # First attempt: pipeline-detected count mismatch (agent emitted
        # 1 record for expected=3 without self-reporting an error, so the
        # pipeline-side overwrite path fires with the _pipeline suffix).
        self.assertEqual(
            events[0]["data"]["error"], "count_mismatch_pipeline",
        )
        self.assertEqual(events[0]["data"]["defects_count"], 0)
        # Second attempt: success.
        self.assertEqual(events[1]["data"]["error"], "")
        self.assertEqual(events[1]["data"]["defects_count"], 3)
        # Output hashes diverge.
        self.assertNotEqual(
            events[0]["data"]["output_hash"],
            events[1]["data"]["output_hash"],
        )

    def test_emits_on_classifier_exit_nonzero(self) -> None:
        """Invocation-level failure (exit != 0) still emits a hash event
        carrying error=classifier_failed."""
        from darkfactory.errors import DarkFactoryError

        project_dir = self._prep_project()
        events_file = project_dir / "events.jsonl"
        config = _make_config()
        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1, failing_test_names=["f0", "f1", "c0"],
        )

        def fake_invoke(role, prompt, **kwargs):
            return AgentResult(
                role=AgentRole.DEFECT_CLASSIFIER, exit_code=3,
            )

        with patch(
            "darkfactory.pipeline.iteration.invoke_agent",
            side_effect=fake_invoke,
        ), patch("darkfactory.pipeline.iteration.step_begin"):
            with self.assertRaises(DarkFactoryError):
                _step_classify(
                    spec_run, self._gate_result(),
                    config, {}, project_dir,
                    iter_state=iter_state,
                    events_file=events_file,
                    dry_run=True,
                )

        events = self._read_hash_events(events_file)
        # Two attempts (_CLASSIFIER_RETRIES=1) — both emit hash events.
        self.assertEqual(len(events), 2)
        for ev in events:
            self.assertEqual(ev["data"]["error"], "classifier_failed")
            self.assertEqual(ev["data"]["defects_count"], 0)

    def test_output_hash_stable_across_justification_drift(self) -> None:
        """The classifier output hash must ignore prose-only changes —
        justification drift between identical classifications should
        NOT register as output divergence. Covers the 'same labels,
        different prose' case the Phase 5.5 verify run raised."""
        from darkfactory.pipeline.iteration import _classifier_output_hash

        hash_a = _classifier_output_hash([
            {"test_name": "a", "category": "IMPL_BUG",
             "spec_section": "FR-1", "justification": "foo"},
            {"test_name": "b", "category": "SCENARIO_CRASH",
             "spec_section": "EC-2", "justification": "bar"},
        ])
        hash_b = _classifier_output_hash([
            {"test_name": "b", "category": "SCENARIO_CRASH",
             "spec_section": "EC-2", "justification": "totally different prose"},
            {"test_name": "a", "category": "IMPL_BUG",
             "spec_section": "FR-1", "justification": "also different"},
        ])
        self.assertEqual(hash_a, hash_b)

    def test_output_hash_diverges_on_category_change(self) -> None:
        """Category reassignment on the same test name MUST change the
        output hash — the Phase 5.5 ENV_ISSUE → IMPL_BUG drift signal."""
        from darkfactory.pipeline.iteration import _classifier_output_hash

        hash_a = _classifier_output_hash([
            {"test_name": "a", "category": "ENV_ISSUE"},
        ])
        hash_b = _classifier_output_hash([
            {"test_name": "a", "category": "IMPL_BUG"},
        ])
        self.assertNotEqual(hash_a, hash_b)


class TestIterationTemporalSignature(TempDirTestCase):
    """Phase 6 I4: per-iteration temporal signature helper.

    Measures tests moved between pass/fail sets and per-category
    classifier distribution delta across iterations. Emitted after the
    classifier step so the defects.json files for both iters are on
    disk when the helper runs.
    """

    def _prep(self):
        project_dir = make_project(self.tmp_dir)
        reports = project_dir / ".dark-factory" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        return project_dir, reports

    def _write_defects(self, reports_dir, iteration: int,
                       records: list[dict]) -> None:
        (reports_dir / f"my-spec-defects-iter{iteration}.json").write_text(
            json.dumps({
                "iteration": iteration, "schema_version": 1, "error": None,
                "defects": records,
            })
        )

    def _read_signature_events(self, events_file):
        from darkfactory.utils.json_helpers import read_jsonl
        return [
            r for r in read_jsonl(events_file)
            if r["event"] == "ITERATION_TEMPORAL_SIGNATURE"
        ]

    def test_iter1_emits_with_empty_prior(self) -> None:
        """Iter 1 has no prior — prior_failure_set_hash is the hash of []
        and all set movements are 0 except tests_moved_to_fail."""
        from darkfactory.pipeline.iteration import (
            _emit_iteration_temporal_signature,
            _failure_set_hash,
        )

        project_dir, reports = self._prep()
        events_file = project_dir / "events.jsonl"
        self._write_defects(reports, 1, [
            {"test_name": "a", "category": "IMPL_BUG"},
            {"test_name": "b", "category": "ENV_ISSUE"},
        ])

        spec_run = _make_spec_run(iteration=1)
        iter_state = IterationState(
            number=1, failing_test_names=["a", "b"],
        )
        spec_run.iterations.append(iter_state)

        _emit_iteration_temporal_signature(
            spec_run, iter_state, project_dir, events_file,
        )
        events = self._read_signature_events(events_file)
        self.assertEqual(len(events), 1)
        data = events[0]["data"]
        self.assertEqual(data["prior_failure_set_hash"], _failure_set_hash([]))
        self.assertEqual(data["tests_moved_to_pass"], 0)
        self.assertEqual(data["tests_moved_to_fail"], 2)
        self.assertEqual(data["tests_unchanged"], 0)
        # No prior classification — current categories all show as positive delta.
        self.assertEqual(
            data["classification_distribution_delta"],
            {"IMPL_BUG": 1, "ENV_ISSUE": 1},
        )

    def test_zero_gain_iteration_detected(self) -> None:
        """Identical failing set across iter 1 → iter 2 yields
        tests_unchanged == len(failing) and both movement counts zero.
        This is the zero-gain-iteration signal the Phase 5.5 verify run
        surfaced post-hoc."""
        from darkfactory.pipeline.iteration import (
            _emit_iteration_temporal_signature,
        )

        project_dir, reports = self._prep()
        events_file = project_dir / "events.jsonl"
        self._write_defects(reports, 1, [
            {"test_name": "a", "category": "IMPL_BUG"},
            {"test_name": "b", "category": "IMPL_BUG"},
        ])
        self._write_defects(reports, 2, [
            {"test_name": "a", "category": "IMPL_BUG"},
            {"test_name": "b", "category": "IMPL_BUG"},
        ])

        spec_run = _make_spec_run(iteration=2)
        prior = IterationState(number=1, failing_test_names=["a", "b"])
        current = IterationState(number=2, failing_test_names=["a", "b"])
        spec_run.iterations.extend([prior, current])

        _emit_iteration_temporal_signature(
            spec_run, current, project_dir, events_file,
        )
        data = self._read_signature_events(events_file)[0]["data"]
        # Byte-identical failing sets.
        self.assertEqual(
            data["prior_failure_set_hash"],
            data["current_failure_set_hash"],
        )
        self.assertEqual(data["tests_moved_to_pass"], 0)
        self.assertEqual(data["tests_moved_to_fail"], 0)
        self.assertEqual(data["tests_unchanged"], 2)
        # Same category counts → empty delta.
        self.assertEqual(data["classification_distribution_delta"], {})

    def test_classifier_drift_surfaces_in_distribution_delta(self) -> None:
        """Same failing test set, different classifier labels → non-empty
        distribution delta. This is the Phase 5.5 ENV_ISSUE→IMPL_BUG
        drift pattern (4 ENV_ISSUE iter1 → 0 ENV_ISSUE iter2)."""
        from darkfactory.pipeline.iteration import (
            _emit_iteration_temporal_signature,
        )

        project_dir, reports = self._prep()
        events_file = project_dir / "events.jsonl"
        # Iter 1: 4 ENV_ISSUE, 16 IMPL_BUG.
        self._write_defects(reports, 1, (
            [{"test_name": f"e{i}", "category": "ENV_ISSUE"} for i in range(4)]
            + [{"test_name": f"i{j}", "category": "IMPL_BUG"} for j in range(16)]
        ))
        # Iter 2: same test names, all relabeled IMPL_BUG.
        self._write_defects(reports, 2, (
            [{"test_name": f"e{i}", "category": "IMPL_BUG"} for i in range(4)]
            + [{"test_name": f"i{j}", "category": "IMPL_BUG"} for j in range(16)]
        ))

        failing = [f"e{i}" for i in range(4)] + [f"i{j}" for j in range(16)]
        spec_run = _make_spec_run(iteration=2)
        prior = IterationState(number=1, failing_test_names=failing)
        current = IterationState(number=2, failing_test_names=failing)
        spec_run.iterations.extend([prior, current])

        _emit_iteration_temporal_signature(
            spec_run, current, project_dir, events_file,
        )
        data = self._read_signature_events(events_file)[0]["data"]
        # Set is byte-identical — zero-gain.
        self.assertEqual(data["tests_unchanged"], 20)
        # Distribution delta exposes the relabel: 4 lost from ENV_ISSUE,
        # 4 gained by IMPL_BUG.
        self.assertEqual(
            data["classification_distribution_delta"],
            {"ENV_ISSUE": -4, "IMPL_BUG": 4},
        )

    def test_mixed_movement_and_delta(self) -> None:
        """One test moves to pass, one stays failing, one new fail appears;
        classification of the carry-over changes."""
        from darkfactory.pipeline.iteration import (
            _emit_iteration_temporal_signature,
        )

        project_dir, reports = self._prep()
        events_file = project_dir / "events.jsonl"
        self._write_defects(reports, 1, [
            {"test_name": "passed", "category": "IMPL_BUG"},
            {"test_name": "carryover", "category": "ENV_ISSUE"},
        ])
        self._write_defects(reports, 2, [
            {"test_name": "carryover", "category": "IMPL_BUG"},
            {"test_name": "newfail", "category": "IMPL_BUG"},
        ])

        spec_run = _make_spec_run(iteration=2)
        prior = IterationState(
            number=1, failing_test_names=["passed", "carryover"],
        )
        current = IterationState(
            number=2, failing_test_names=["carryover", "newfail"],
        )
        spec_run.iterations.extend([prior, current])

        _emit_iteration_temporal_signature(
            spec_run, current, project_dir, events_file,
        )
        data = self._read_signature_events(events_file)[0]["data"]
        self.assertEqual(data["tests_moved_to_pass"], 1)  # passed
        self.assertEqual(data["tests_moved_to_fail"], 1)  # newfail
        self.assertEqual(data["tests_unchanged"], 1)      # carryover
        self.assertNotEqual(
            data["prior_failure_set_hash"],
            data["current_failure_set_hash"],
        )
        # Iter1: {IMPL_BUG:1, ENV_ISSUE:1}; iter2: {IMPL_BUG:2}.
        # Delta: IMPL_BUG +1, ENV_ISSUE -1.
        self.assertEqual(
            data["classification_distribution_delta"],
            {"IMPL_BUG": 1, "ENV_ISSUE": -1},
        )

    def test_handles_missing_prior_defects_file(self) -> None:
        """If a prior defects.json is missing (e.g. classifier aborted
        in that iter), _load_defect_records returns [] — prior category
        counts are empty and the helper does not raise."""
        from darkfactory.pipeline.iteration import (
            _emit_iteration_temporal_signature,
        )

        project_dir, reports = self._prep()
        events_file = project_dir / "events.jsonl"
        # Only iter 2 has a defects file.
        self._write_defects(reports, 2, [
            {"test_name": "a", "category": "IMPL_BUG"},
        ])

        spec_run = _make_spec_run(iteration=2)
        prior = IterationState(number=1, failing_test_names=["a"])
        current = IterationState(number=2, failing_test_names=["a"])
        spec_run.iterations.extend([prior, current])

        _emit_iteration_temporal_signature(
            spec_run, current, project_dir, events_file,
        )
        data = self._read_signature_events(events_file)[0]["data"]
        # Empty prior counts → positive delta for current categories.
        self.assertEqual(
            data["classification_distribution_delta"], {"IMPL_BUG": 1},
        )


class TestScopeFilterRecomputesCrashAndAssert(TempDirTestCase):
    """Bug C widened: the scope filter must re-partition scenario_crashed /
    scenario_assert_failed against the in-scope name set.

    Before the fix, _apply_scope_filter updated SCENARIO_FAILED / TOTAL /
    PASSED / RATE but left SCENARIO_CRASHED / SCENARIO_ASSERT_FAILED at
    pre-filter values. When an out-of-scope test had crashed, the
    invariant ``assert_failed + crashed == failed`` broke and the
    classifier prompt asked the LLM for more SCENARIO_CRASH records than
    there were failures in scope. Phase 2 iter-2 archive showed
    SCENARIO_FAILED=21 but SCENARIO_ASSERT_FAILED=22 — this test locks
    the fix.
    """

    def test_out_of_scope_crash_shrinks_crashed_count(self) -> None:
        from darkfactory.pipeline.iteration import _apply_scope_filter
        from darkfactory.spec.anchors import AnchorManifest, manifest_path

        project_dir = make_project(self.tmp_dir)
        reports = project_dir / ".dark-factory" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        specs = project_dir / ".dark-factory" / "specs"
        specs.mkdir(parents=True, exist_ok=True)
        (specs / "my-spec.md").write_text("# spec\n", encoding="utf-8")

        # Manifest has AC-1 but NOT AC-99.
        manifest = AnchorManifest(ac_ids=["AC-1"])
        manifest.to_json_path(manifest_path(project_dir, "my-spec"))

        # Three failing tests: one assert-fail in-scope, one crash
        # in-scope, one crash out-of-scope (AC-99 not in manifest).
        in_scope_assert = "returns expected value [AC-1]"
        in_scope_crash = "validates input [AC-1]"
        out_of_scope_crash = "checks unknown anchor [AC-99]"
        per_test = [
            {"name": in_scope_assert, "status": "failed", "error_type": "assertion"},
            {"name": in_scope_crash, "status": "failed", "error_type": "crash"},
            {"name": out_of_scope_crash, "status": "failed", "error_type": "crash"},
        ]
        (reports / "my-spec-test-results-iter1.json").write_text(
            json.dumps(per_test), encoding="utf-8",
        )
        # Seed the canonical counts.env so _rewrite_counts_env_post_filter
        # has something to rewrite (the scope filter writes back to disk).
        (reports / "my-spec-scenario-counts.env").write_text(
            "SCENARIO_PASSED=7\nSCENARIO_FAILED=3\nSCENARIO_TOTAL=10\n"
            "SCENARIO_RATE=70\nSCENARIO_CRASHED=2\nSCENARIO_ASSERT_FAILED=1\n",
            encoding="utf-8",
        )

        iter_state = IterationState(
            number=1,
            failing_test_names=[
                in_scope_assert, in_scope_crash, out_of_scope_crash,
            ],
            scenario_fail_count=3,
            scenario_pass_count=7,
            scenario_total=10,
            pass_rate=70,
            scenario_crashed=2,
            scenario_assert_failed=1,
        )
        gate_result = GateResult(
            exit_code=1,
            scenario_passed=7,
            scenario_failed=3,
            scenario_total=10,
            pass_rate=70,
            scenario_crashed=2,
            scenario_assert_failed=1,
        )

        spec_run = _make_spec_run(iteration=1)
        _apply_scope_filter(spec_run, iter_state, gate_result, project_dir)

        # One out-of-scope failure was removed.
        self.assertEqual(iter_state.scenario_fail_count, 2)
        self.assertEqual(gate_result.scenario_failed, 2)
        # That failure was a crash → scenario_crashed shrank by 1.
        self.assertEqual(iter_state.scenario_crashed, 1)
        self.assertEqual(gate_result.scenario_crashed, 1)
        # Assert-failed count is unchanged (no out-of-scope asserts removed).
        self.assertEqual(iter_state.scenario_assert_failed, 1)
        self.assertEqual(gate_result.scenario_assert_failed, 1)
        # Invariant: assert_failed + crashed == failed.
        self.assertEqual(
            iter_state.scenario_fail_count,
            iter_state.scenario_assert_failed + iter_state.scenario_crashed,
        )
        # Raw (pre-filter) values preserved.
        self.assertEqual(iter_state.scenario_crashed_raw, 2)
        self.assertEqual(iter_state.scenario_assert_failed_raw, 1)
        self.assertEqual(iter_state.scenario_fail_count_raw, 3)
        # Disk counts.env also has the filtered values.
        text = (reports / "my-spec-scenario-counts.env").read_text()
        self.assertIn("SCENARIO_CRASHED=1", text)
        self.assertIn("SCENARIO_ASSERT_FAILED=1", text)
        self.assertIn("SCENARIO_CRASHED_RAW=2", text)
        self.assertIn("SCENARIO_ASSERT_FAILED_RAW=1", text)


if __name__ == "__main__":
    unittest.main()
