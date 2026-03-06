"""Tests for models/config.py — PipelineConfig dataclass."""

import unittest

from darkfactory.errors import ConfigError
from darkfactory.models.config import PipelineConfig


class TestPipelineConfig(unittest.TestCase):
    def test_defaults(self) -> None:
        cfg = PipelineConfig()
        self.assertEqual(cfg.language, "node")
        self.assertEqual(cfg.source_dirs, ["src"])
        self.assertEqual(cfg.agent_max_turns, 200)
        self.assertEqual(cfg.autonomy_level, 3)
        self.assertEqual(cfg.max_iterations, 3)
        self.assertFalse(cfg.auto_pr)
        self.assertEqual(cfg.cost_budget_usd, 0.0)

    def test_category_weights(self) -> None:
        cfg = PipelineConfig()
        weights = cfg.category_weights
        self.assertEqual(weights["contract"], 100)
        self.assertEqual(weights["security"], 120)
        self.assertEqual(weights["adversarial"], 60)
        self.assertEqual(len(weights), 8)

    def test_override(self) -> None:
        cfg = PipelineConfig(language="python", max_iterations=5)
        self.assertEqual(cfg.language, "python")
        self.assertEqual(cfg.max_iterations, 5)

    def test_source_dirs_independent(self) -> None:
        """Ensure default mutable list is not shared between instances."""
        a = PipelineConfig()
        b = PipelineConfig()
        a.source_dirs.append("lib")
        self.assertEqual(b.source_dirs, ["src"])

    def test_invalid_autonomy_level(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig(autonomy_level=5)

    def test_invalid_pass_rate(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig(min_ship_pass_rate=200)

    def test_invalid_language(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig(language="fortran")

    def test_multi_run_k_exceeds_n(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig(multi_run_n=3, multi_run_k=5)

    def test_invalid_max_iterations(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig(max_iterations=0)


if __name__ == "__main__":
    unittest.main()
