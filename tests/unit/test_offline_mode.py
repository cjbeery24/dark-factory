"""Phase 5 — offline mode + local-backend cost accounting.

Covers PipelineConfig.apply_offline_mode (force-all-roles + degraded marker, and
the zero/ambiguous errors) and the scorecard's local-line cost handling ($0-known,
local_invocations count, no whole-run void).
"""

import json
import unittest

from tests.helpers import TempDirTestCase
from darkfactory.benchmark.scorecard import parse_run_usage
from darkfactory.errors import ConfigError
from darkfactory.models.agent import AgentRole
from darkfactory.models.config import ModelProfile, PipelineConfig


def _local(host_port: str = "host.docker.internal:11434") -> ModelProfile:
    return ModelProfile(
        base_url=f"https://{host_port}", auth_token_env="DF_LOCAL_TOKEN",
        model="qwen3-coder", max_output_tokens=8192, family="local",
    )


class TestApplyOfflineMode(unittest.TestCase):
    def test_forces_all_roles(self) -> None:
        cfg = PipelineConfig(model_profiles={"local": _local()})
        cfg.apply_offline_mode()
        # EVERY role mapped to the local profile.
        for role in AgentRole:
            self.assertEqual(cfg.agent_model_profiles[role.value], "local")

    def test_clobbers_existing_mapping(self) -> None:
        # --offline discards any pre-existing per-role mapping (all roles, one backend).
        cfg = PipelineConfig(
            model_profiles={"local": _local()},
            agent_model_profiles={AgentRole.IMPLEMENTER.value: "default"},
        )
        cfg.apply_offline_mode()
        self.assertEqual(cfg.agent_model_profiles[AgentRole.IMPLEMENTER.value], "local")

    def test_no_local_profile_errors(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig().apply_offline_mode()
        # A default (no base_url) profile doesn't count as local.
        cfg = PipelineConfig(model_profiles={"d": ModelProfile(model="claude-sonnet-4-6")})
        with self.assertRaises(ConfigError):
            cfg.apply_offline_mode()

    def test_ambiguous_multiple_local_errors(self) -> None:
        cfg = PipelineConfig(model_profiles={
            "a": _local("host.docker.internal:11434"),
            "b": _local("host.docker.internal:3456"),
        })
        with self.assertRaises(ConfigError):
            cfg.apply_offline_mode()

class TestScorecardLocalCost(TempDirTestCase):
    def _telemetry(self, *lines: dict) -> object:
        p = self.tmp_dir / "run-telemetry.jsonl"
        p.write_text("".join(json.dumps(d) + "\n" for d in lines))
        return p

    @staticmethod
    def _line(model_id: str, backend: str, out: int, run_id: str = "r1") -> dict:
        return {
            "run_id": run_id, "model_id": model_id, "backend": backend,
            "input_tokens": 1000, "output_tokens": out,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
        }

    def test_all_local_run_costs_zero(self) -> None:
        p = self._telemetry(
            self._line("qwen3-coder", "local", 500),
            self._line("qwen3-coder", "local", 800),
        )
        u = parse_run_usage(p, run_id="r1")
        self.assertEqual(u.cost_usd, 0.0)          # known $0, NOT None
        self.assertEqual(u.local_invocations, 2)
        self.assertEqual(u.agent_invocations, 2)

    def test_hybrid_run_keeps_claude_cost(self) -> None:
        # One local + one sonnet: cost = the sonnet line's real cost, NOT voided.
        p = self._telemetry(
            self._line("qwen3-coder", "local", 500),
            self._line("claude-sonnet-4-6", "anthropic", 1000),
        )
        u = parse_run_usage(p, run_id="r1")
        self.assertIsNotNone(u.cost_usd)
        self.assertGreater(u.cost_usd, 0.0)         # sonnet output priced
        self.assertEqual(u.local_invocations, 1)

    def test_hybrid_ordering_independent(self) -> None:
        # local→claude→local: the interleaved local lines never void the Claude cost
        # and the count is right regardless of order (Linus NIT).
        p = self._telemetry(
            self._line("qwen3-coder", "local", 500),
            self._line("claude-sonnet-4-6", "anthropic", 1000),
            self._line("qwen3-coder", "local", 700),
        )
        u = parse_run_usage(p, run_id="r1")
        self.assertIsNotNone(u.cost_usd)
        self.assertGreater(u.cost_usd, 0.0)
        self.assertEqual(u.local_invocations, 2)
        self.assertEqual(u.agent_invocations, 3)

    def test_unrecognized_anthropic_line_still_voids(self) -> None:
        # A non-local unrecognized model still makes the total None (unchanged).
        p = self._telemetry(
            self._line("claude-sonnet-4-6", "anthropic", 1000),
            self._line("some-unknown-model", "anthropic", 500),
        )
        u = parse_run_usage(p, run_id="r1")
        self.assertIsNone(u.cost_usd)
        self.assertEqual(u.local_invocations, 0)

    def test_legacy_v2_line_without_backend_unchanged(self) -> None:
        # An old telemetry line with no "backend" key prices as before (anthropic).
        legacy = {
            "run_id": "r1", "model_id": "claude-sonnet-4-6",
            "input_tokens": 1000, "output_tokens": 1000,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
        }
        u = parse_run_usage(self._telemetry(legacy), run_id="r1")
        self.assertIsNotNone(u.cost_usd)
        self.assertEqual(u.local_invocations, 0)


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    unittest.main()
