"""Tests for models/config.py — PipelineConfig dataclass."""

import unittest

from darkfactory.errors import ConfigError
from darkfactory.models.config import PipelineConfig, ServiceDef


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

    def test_invalid_scenario_empty_retry_attempts(self) -> None:
        # Negative retry budget is a config error (mirrors the sa_max_retries
        # guard). 0 is valid (no retry = pre-change single-shot behavior).
        with self.assertRaises(ConfigError):
            PipelineConfig(scenario_empty_retry_attempts=-1)
        self.assertEqual(
            PipelineConfig(scenario_empty_retry_attempts=0).scenario_empty_retry_attempts, 0
        )

    def test_invalid_max_iterations(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig(max_iterations=0)

    def test_services_default_empty(self) -> None:
        cfg = PipelineConfig()
        self.assertEqual(cfg.services, [])


class TestServiceDef(unittest.TestCase):
    def test_valid_service(self) -> None:
        svc = ServiceDef(name="postgres", image="postgres:16-alpine")
        self.assertEqual(svc.name, "postgres")
        self.assertEqual(svc.image, "postgres:16-alpine")
        self.assertEqual(svc.env, {})
        self.assertEqual(svc.inject_env, {})
        self.assertEqual(svc.health_timeout, 30)

    def test_single_char_name(self) -> None:
        svc = ServiceDef(name="a", image="alpine:3")
        self.assertEqual(svc.name, "a")

    def test_empty_name_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            ServiceDef(name="", image="postgres:16")

    def test_empty_image_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            ServiceDef(name="postgres", image="")

    def test_non_dns_name_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            ServiceDef(name="My_Postgres", image="postgres:16")

    def test_dns_name_with_hyphens(self) -> None:
        svc = ServiceDef(name="my-db", image="postgres:16")
        self.assertEqual(svc.name, "my-db")

    def test_name_starting_with_hyphen_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            ServiceDef(name="-bad", image="postgres:16")

    def test_inject_env(self) -> None:
        svc = ServiceDef(
            name="postgres",
            image="postgres:16-alpine",
            inject_env={"DATABASE_URL": "postgres://localhost/test"},
        )
        self.assertEqual(svc.inject_env["DATABASE_URL"], "postgres://localhost/test")


if __name__ == "__main__":
    unittest.main()
