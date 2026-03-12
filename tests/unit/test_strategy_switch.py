"""Tests for strategy switching (Phase 7A)."""

import unittest

from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import SpecRun
from darkfactory.models.satisfaction import TrajectoryType
from darkfactory.scenarios.trajectory import (
    StrategyAction,
    StrategyActionType,
    strategy_action,
    strategy_recommendation,
)


class TestStrategyAction(unittest.TestCase):
    def test_degrading_switches_model(self) -> None:
        action = strategy_action(TrajectoryType.DEGRADING)
        self.assertEqual(action.action, StrategyActionType.SWITCH_MODEL)

    def test_plateau_fresh_start(self) -> None:
        action = strategy_action(TrajectoryType.PLATEAU)
        self.assertEqual(action.action, StrategyActionType.FRESH_START)

    def test_oscillating_switches_model(self) -> None:
        action = strategy_action(TrajectoryType.OSCILLATING)
        self.assertEqual(action.action, StrategyActionType.SWITCH_MODEL)

    def test_improving_continues(self) -> None:
        action = strategy_action(TrajectoryType.IMPROVING)
        self.assertEqual(action.action, StrategyActionType.CONTINUE)

    def test_recovering_continues(self) -> None:
        action = strategy_action(TrajectoryType.RECOVERING)
        self.assertEqual(action.action, StrategyActionType.CONTINUE)

    def test_mixed_escalates(self) -> None:
        action = strategy_action(TrajectoryType.MIXED)
        self.assertEqual(action.action, StrategyActionType.ESCALATE)

    def test_insufficient_continues(self) -> None:
        action = strategy_action(TrajectoryType.INSUFFICIENT)
        self.assertEqual(action.action, StrategyActionType.CONTINUE)

    def test_str_backward_compat(self) -> None:
        action = strategy_action(TrajectoryType.IMPROVING)
        s = str(action)
        self.assertIn("positive trajectory", s)

    def test_strategy_recommendation_returns_string(self) -> None:
        rec = strategy_recommendation(TrajectoryType.DEGRADING)
        self.assertIsInstance(rec, str)
        self.assertTrue(len(rec) > 0)


class TestApplyStrategy(unittest.TestCase):
    def test_switch_model(self) -> None:
        from darkfactory.pipeline.iteration import _apply_strategy
        config = PipelineConfig(
            model_id="claude-sonnet-4-20250514",
            model_ids={"fallback": "claude-opus-4-20250514"},
        )
        spec_run = SpecRun(spec_name="test")
        result = _apply_strategy(spec_run, config, StrategyActionType.SWITCH_MODEL)
        self.assertTrue(result)
        self.assertEqual(spec_run.model_override, "claude-opus-4-20250514")
        self.assertEqual(spec_run.strategy_switches, 1)

    def test_switch_model_no_alternative(self) -> None:
        from darkfactory.pipeline.iteration import _apply_strategy
        config = PipelineConfig(model_id="claude-sonnet-4-20250514")
        spec_run = SpecRun(spec_name="test")
        result = _apply_strategy(spec_run, config, StrategyActionType.SWITCH_MODEL)
        self.assertFalse(result)
        self.assertEqual(spec_run.strategy_switches, 0)

    def test_switch_model_skip_same(self) -> None:
        from darkfactory.pipeline.iteration import _apply_strategy
        # All model_ids are the same as current — should fail.
        config = PipelineConfig(
            model_id="claude-sonnet-4-20250514",
            model_ids={"implementer": "claude-sonnet-4-20250514"},
        )
        spec_run = SpecRun(spec_name="test")
        result = _apply_strategy(spec_run, config, StrategyActionType.SWITCH_MODEL)
        self.assertFalse(result)

    def test_fresh_start_resets_iteration(self) -> None:
        from darkfactory.models.pipeline import IterationState
        from darkfactory.pipeline.iteration import _apply_strategy
        config = PipelineConfig()
        spec_run = SpecRun(spec_name="test")
        spec_run.iteration = 3
        spec_run.iterations = [IterationState(number=1), IterationState(number=2), IterationState(number=3)]
        spec_run.trajectory_class = TrajectoryType.DEGRADING
        spec_run.trajectory_strategy = "old"
        score_history = [50, 40, 30]

        result = _apply_strategy(spec_run, config, StrategyActionType.FRESH_START, score_history)

        self.assertTrue(result)
        self.assertEqual(spec_run.iteration, 0)
        self.assertEqual(spec_run.strategy_switches, 1)
        self.assertEqual(spec_run.iterations, [])
        self.assertIsNone(spec_run.trajectory_class)
        self.assertEqual(spec_run.trajectory_strategy, "")
        self.assertEqual(score_history, [])

    def test_continue_does_nothing(self) -> None:
        from darkfactory.pipeline.iteration import _apply_strategy
        config = PipelineConfig()
        spec_run = SpecRun(spec_name="test")
        result = _apply_strategy(spec_run, config, StrategyActionType.CONTINUE)
        self.assertFalse(result)
        self.assertEqual(spec_run.strategy_switches, 0)

    def test_escalate_does_nothing(self) -> None:
        from darkfactory.pipeline.iteration import _apply_strategy
        config = PipelineConfig()
        spec_run = SpecRun(spec_name="test")
        result = _apply_strategy(spec_run, config, StrategyActionType.ESCALATE)
        self.assertFalse(result)
        self.assertEqual(spec_run.strategy_switches, 0)


class TestMaxSwitchesGuard(unittest.TestCase):
    def test_max_switches_exhausted_blocks_further_switches(self) -> None:
        """When strategy_switches >= max, the loop guard prevents _apply_strategy."""
        config = PipelineConfig(
            model_ids={"fallback": "claude-opus-4-20250514"},
            strategy_switch_enabled=True,
            strategy_max_switches=2,
        )
        spec_run = SpecRun(spec_name="test")
        spec_run.strategy_switches = 2
        # The iteration loop condition is:
        #   config.strategy_switch_enabled and spec_run.strategy_switches < config.strategy_max_switches
        # With switches=2 and max=2, this should be False.
        should_try_switch = (
            config.strategy_switch_enabled
            and spec_run.strategy_switches < config.strategy_max_switches
        )
        self.assertFalse(should_try_switch)

    def test_switch_after_one_still_allowed(self) -> None:
        config = PipelineConfig(
            model_ids={"fallback": "claude-opus-4-20250514"},
            strategy_switch_enabled=True,
            strategy_max_switches=2,
        )
        spec_run = SpecRun(spec_name="test")
        spec_run.strategy_switches = 1
        should_try_switch = (
            config.strategy_switch_enabled
            and spec_run.strategy_switches < config.strategy_max_switches
        )
        self.assertTrue(should_try_switch)


class TestStrategyConfig(unittest.TestCase):
    def test_defaults(self) -> None:
        config = PipelineConfig()
        self.assertFalse(config.strategy_switch_enabled)
        self.assertEqual(config.strategy_max_switches, 2)

    def test_negative_max_switches_rejected(self) -> None:
        with self.assertRaises(Exception):
            PipelineConfig(strategy_max_switches=-1)

    def test_config_parsing(self) -> None:
        from darkfactory.config.loader import _build_config
        data = {
            "agents": {
                "strategy": {
                    "switch_enabled": True,
                    "max_switches": 3,
                },
            },
        }
        config = _build_config(data)
        self.assertTrue(config.strategy_switch_enabled)
        self.assertEqual(config.strategy_max_switches, 3)


class TestSpecRunModelOverride(unittest.TestCase):
    def test_default_empty(self) -> None:
        run = SpecRun(spec_name="test")
        self.assertEqual(run.model_override, "")
        self.assertEqual(run.strategy_switches, 0)

    def test_set_override(self) -> None:
        run = SpecRun(spec_name="test")
        run.model_override = "claude-opus-4-20250514"
        self.assertEqual(run.model_override, "claude-opus-4-20250514")


if __name__ == "__main__":
    unittest.main()
