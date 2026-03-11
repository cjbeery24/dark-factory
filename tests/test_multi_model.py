"""Tests for multi-model agent selection (Phase 7A)."""

import unittest

from darkfactory.docker.args import build_claude_args
from darkfactory.docker.barriers import configure_barriers
from darkfactory.models.agent import AgentRole
from darkfactory.models.config import PipelineConfig


class TestMultiModelConfig(unittest.TestCase):
    def test_model_ids_default_empty(self) -> None:
        config = PipelineConfig()
        self.assertEqual(config.model_ids, {})

    def test_model_ids_from_config(self) -> None:
        config = PipelineConfig(model_ids={
            "implementer": "claude-opus-4-20250514",
            "reviewer": "claude-haiku-4-20250514",
        })
        self.assertEqual(config.model_ids["implementer"], "claude-opus-4-20250514")
        self.assertEqual(config.model_ids["reviewer"], "claude-haiku-4-20250514")


class TestMultiModelClaudeArgs(unittest.TestCase):
    def test_per_role_model_selected(self) -> None:
        config = PipelineConfig(
            model_id="claude-sonnet-4-20250514",
            model_ids={"implementer": "claude-opus-4-20250514"},
        )
        barriers = configure_barriers(["src"])
        args = build_claude_args(AgentRole.IMPLEMENTER, config, barriers)
        self.assertIn("--model=claude-opus-4-20250514", args)
        self.assertNotIn("--model=claude-sonnet-4-20250514", args)

    def test_fallback_to_global_model(self) -> None:
        config = PipelineConfig(
            model_id="claude-sonnet-4-20250514",
            model_ids={"implementer": "claude-opus-4-20250514"},
        )
        barriers = configure_barriers(["src"])
        # Reviewer not in model_ids — should fall back to global.
        args = build_claude_args(AgentRole.REVIEWER, config, barriers)
        self.assertIn("--model=claude-sonnet-4-20250514", args)

    def test_empty_model_ids_uses_global(self) -> None:
        config = PipelineConfig(model_id="claude-sonnet-4-20250514")
        barriers = configure_barriers(["src"])
        args = build_claude_args(AgentRole.IMPLEMENTER, config, barriers)
        self.assertIn("--model=claude-sonnet-4-20250514", args)


class TestMultiModelConfigParsing(unittest.TestCase):
    def test_loader_handles_model_ids(self) -> None:
        from darkfactory.config.loader import _build_config
        data = {
            "model": {
                "id": "claude-sonnet-4-20250514",
                "ids": {
                    "implementer": "claude-opus-4-20250514",
                    "oracle": "claude-opus-4-20250514",
                },
            },
        }
        config = _build_config(data)
        self.assertEqual(config.model_id, "claude-sonnet-4-20250514")
        self.assertEqual(config.model_ids["implementer"], "claude-opus-4-20250514")
        self.assertEqual(config.model_ids["oracle"], "claude-opus-4-20250514")

    def test_loader_no_ids_section(self) -> None:
        from darkfactory.config.loader import _build_config
        data = {"model": {"id": "claude-sonnet-4-20250514"}}
        config = _build_config(data)
        self.assertEqual(config.model_ids, {})

    def test_loader_empty_ids(self) -> None:
        from darkfactory.config.loader import _build_config
        data = {"model": {"id": "claude-sonnet-4-20250514", "ids": {}}}
        config = _build_config(data)
        self.assertEqual(config.model_ids, {})


if __name__ == "__main__":
    unittest.main()
