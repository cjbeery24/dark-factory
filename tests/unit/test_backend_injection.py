"""Phase 3 — profile-aware backend env injection + concurrency clamp.

Covers the container-level wiring: which credentials/env vars reach the agent
container for the default (Anthropic) vs a local backend, the auth-leak guard,
the thinking-disable hooks, the output-token cap, the --model flag, and the TE
parallel-fan-out clamp. Pure arg-building (no Docker execution).
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import make_project

from darkfactory.docker.args import build_claude_args
from darkfactory.docker.barriers import configure_barriers
from darkfactory.errors import ConfigError
from darkfactory.models.agent import AgentRole
from darkfactory.models.config import (
    ModelProfile,
    PipelineConfig,
    resolve_model_profile,
)
from darkfactory.pipeline.agents import _build_full_agent_docker_args
from darkfactory.pipeline.orchestrator import _te_batch_workers


def _env_values(args: list[str]) -> list[str]:
    """The VALUE string of every ``-e`` flag in a docker args list."""
    return [args[i + 1] for i, a in enumerate(args) if a == "-e" and i + 1 < len(args)]


def _mount_values(args: list[str]) -> list[str]:
    """The VALUE string of every ``-v`` flag in a docker args list."""
    return [args[i + 1] for i, a in enumerate(args) if a == "-v" and i + 1 < len(args)]


def _local_config(**profile_overrides) -> PipelineConfig:
    prof = ModelProfile(
        base_url="https://df-local:8081",
        auth_token_env="DF_LOCAL_TOKEN",
        model="qwen3-coder",
        max_output_tokens=8192,
        family="local",
        **profile_overrides,
    )
    return PipelineConfig(
        model_profiles={"local": prof},
        agent_model_profiles={AgentRole.ESCALATION.value: "local"},
    )


class _InjectionBase(unittest.TestCase):
    def _args_for(self, role: AgentRole, config: PipelineConfig) -> list[str]:
        barriers = configure_barriers(config.source_dirs)
        with tempfile.TemporaryDirectory() as tmp:
            make_project(Path(tmp))
            return _build_full_agent_docker_args(
                role, config=config, barriers=barriers,
                run_id="run1", project_dir=Path(tmp),
            )

    def _env_for(self, role: AgentRole, config: PipelineConfig) -> list[str]:
        return _env_values(self._args_for(role, config))


class TestDefaultBackendInjection(_InjectionBase):
    @mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "oauth-real", "ANTHROPIC_API_KEY": ""})
    def test_default_injects_real_token_no_base_url(self) -> None:
        env = self._env_for(AgentRole.IMPLEMENTER, PipelineConfig())
        self.assertTrue(any(e.startswith("CLAUDE_CODE_OAUTH_TOKEN=") for e in env))
        self.assertFalse(any(e.startswith("ANTHROPIC_BASE_URL=") for e in env))
        self.assertFalse(any(e.startswith("ANTHROPIC_AUTH_TOKEN=") for e in env))
        self.assertFalse(any(e.startswith("MAX_THINKING_TOKENS=") for e in env))

    @mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "", "ANTHROPIC_API_KEY": "api-real"})
    def test_default_falls_back_to_api_key(self) -> None:
        env = self._env_for(AgentRole.IMPLEMENTER, PipelineConfig())
        self.assertTrue(any(e.startswith("ANTHROPIC_API_KEY=") for e in env))
        self.assertFalse(any(e.startswith("CLAUDE_CODE_OAUTH_TOKEN=") for e in env))

    @mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "r", "ANTHROPIC_API_KEY": ""})
    def test_default_cap_family_derived(self) -> None:
        env = self._env_for(AgentRole.IMPLEMENTER, PipelineConfig(model_id="claude-sonnet-4-6"))
        self.assertIn("CLAUDE_CODE_MAX_OUTPUT_TOKENS=64000", env)

    @mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "r", "ANTHROPIC_API_KEY": ""})
    def test_default_arm_no_ca_mount(self) -> None:
        # Belt-and-suspenders: the CA mount + NODE_EXTRA_CA_CERTS live INSIDE the
        # local arm (if profile.base_url), so a default-backend agent must be free
        # of them — the regression that would silently break the real-Anthropic path.
        args = self._args_for(AgentRole.IMPLEMENTER, PipelineConfig())
        self.assertFalse(any("df-local-ca.pem" in m for m in _mount_values(args)))
        self.assertFalse(any(e.startswith("NODE_EXTRA_CA_CERTS=") for e in _env_values(args)))


class TestLocalBackendInjection(_InjectionBase):
    @mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "REAL-SECRET", "DF_LOCAL_TOKEN": "shim-tok"})
    def test_local_injects_base_url_and_shim_token(self) -> None:
        env = self._env_for(AgentRole.ESCALATION, _local_config())
        self.assertIn("ANTHROPIC_BASE_URL=https://df-local:8081", env)
        self.assertIn("ANTHROPIC_AUTH_TOKEN=shim-tok", env)

    @mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "REAL-SECRET", "DF_LOCAL_TOKEN": "shim-tok"})
    def test_real_token_never_leaks_to_local_container(self) -> None:
        # THE high-stakes assertion (AC-3): a local agent's container must never
        # receive the real Anthropic credential.
        env = self._env_for(AgentRole.ESCALATION, _local_config())
        self.assertFalse(any("REAL-SECRET" in e for e in env))
        self.assertFalse(any(e.startswith("CLAUDE_CODE_OAUTH_TOKEN=") for e in env))
        self.assertFalse(any(e.startswith("ANTHROPIC_API_KEY=") for e in env))

    @mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "r", "DF_LOCAL_TOKEN": "x"})
    def test_thinking_disabled_injected(self) -> None:
        env = self._env_for(AgentRole.ESCALATION, _local_config(disable_thinking=True))
        self.assertIn("MAX_THINKING_TOKENS=0", env)
        self.assertIn("DISABLE_INTERLEAVED_THINKING=1", env)

    @mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "r", "DF_LOCAL_TOKEN": "x"})
    def test_thinking_not_disabled_when_flag_false(self) -> None:
        env = self._env_for(AgentRole.ESCALATION, _local_config(disable_thinking=False))
        self.assertNotIn("MAX_THINKING_TOKENS=0", env)
        self.assertNotIn("DISABLE_INTERLEAVED_THINKING=1", env)

    @mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "r", "DF_LOCAL_TOKEN": "x"})
    def test_local_cap_from_profile(self) -> None:
        # _local_config sets max_output_tokens=8192; the cap env must reflect it,
        # NOT the sonnet/haiku family default of 64000.
        env = self._env_for(AgentRole.ESCALATION, _local_config())
        self.assertIn("CLAUDE_CODE_MAX_OUTPUT_TOKENS=8192", env)

    @mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "r", "DF_LOCAL_TOKEN": "x"})
    def test_ca_cert_mounted_when_set(self) -> None:
        # A local profile with ca_cert mounts the PEM read-only at the fixed path
        # and points NODE_EXTRA_CA_CERTS at it (so claude -p's TLS client trusts the
        # self-signed shim cert). Resolved to an absolute path for the bind mount.
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            f.write(b"-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n")
            ca = f.name
        try:
            args = self._args_for(AgentRole.ESCALATION, _local_config(ca_cert=ca))
        finally:
            os.unlink(ca)
        self.assertIn(
            f"{Path(ca).resolve()}:/etc/df-local-ca.pem:ro", _mount_values(args)
        )
        self.assertIn("NODE_EXTRA_CA_CERTS=/etc/df-local-ca.pem", _env_values(args))

    @mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "r", "DF_LOCAL_TOKEN": "x"})
    def test_no_ca_cert_no_mount(self) -> None:
        # A local profile without ca_cert (publicly-trusted shim) mounts nothing and
        # sets no NODE_EXTRA_CA_CERTS — relies on the container's system CA bundle.
        args = self._args_for(AgentRole.ESCALATION, _local_config())
        self.assertFalse(any("df-local-ca.pem" in m for m in _mount_values(args)))
        self.assertFalse(any(e.startswith("NODE_EXTRA_CA_CERTS=") for e in _env_values(args)))

    @mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "r", "DF_LOCAL_TOKEN": "x"})
    def test_ca_cert_missing_file_fails_fast(self) -> None:
        with self.assertRaises(ConfigError):
            self._args_for(
                AgentRole.ESCALATION,
                _local_config(ca_cert="/nonexistent/df-no-such-ca.pem"),
            )

    @mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "r"})
    def test_missing_auth_token_env_fails_fast(self) -> None:
        os.environ.pop("DF_LOCAL_TOKEN", None)
        with self.assertRaises(ConfigError):
            self._env_for(AgentRole.ESCALATION, _local_config())

    @mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "oauth-real"})
    def test_unmapped_role_uses_default_and_never_trips_fastfail(self) -> None:
        # IMPLEMENTER is NOT mapped to the local profile → default arm → must NOT
        # fail-fast on the (unset) DF_LOCAL_TOKEN, and gets the real token.
        os.environ.pop("DF_LOCAL_TOKEN", None)
        env = self._env_for(AgentRole.IMPLEMENTER, _local_config())
        self.assertTrue(any(e.startswith("CLAUDE_CODE_OAUTH_TOKEN=") for e in env))
        self.assertFalse(any(e.startswith("ANTHROPIC_BASE_URL=") for e in env))

    @mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "REAL", "DF_LOCAL_TOKEN": "x"})
    def test_injection_does_not_mutate_shared_profile(self) -> None:
        # Aliasing contract (Linus carry): injection READS profile fields, never
        # mutates the shared config object.
        cfg = _local_config()
        prof = cfg.model_profiles["local"]
        before = (prof.model, prof.base_url, prof.max_output_tokens, prof.disable_thinking)
        self._env_for(AgentRole.ESCALATION, cfg)
        self.assertEqual(
            (prof.model, prof.base_url, prof.max_output_tokens, prof.disable_thinking),
            before,
        )
        self.assertIs(cfg.model_profiles["local"], prof)


class TestModelFlag(unittest.TestCase):
    def _model_flag(self, role: AgentRole, config: PipelineConfig) -> str | None:
        barriers = configure_barriers(config.source_dirs)
        args = build_claude_args(role, config, barriers)
        for a in args:
            if a.startswith("--model="):
                return a
        return None

    def test_model_flag_from_local_profile(self) -> None:
        cfg = _local_config()
        self.assertEqual(self._model_flag(AgentRole.ESCALATION, cfg), "--model=qwen3-coder")

    def test_model_flag_honors_model_ids(self) -> None:
        cfg = PipelineConfig(model_ids={AgentRole.IMPLEMENTER.value: "claude-opus-4-8"})
        self.assertEqual(self._model_flag(AgentRole.IMPLEMENTER, cfg), "--model=claude-opus-4-8")

    def test_model_flag_default_global(self) -> None:
        cfg = PipelineConfig(model_id="claude-sonnet-4-6")
        self.assertEqual(self._model_flag(AgentRole.REVIEWER, cfg), "--model=claude-sonnet-4-6")


class TestTelemetryAttribution(unittest.TestCase):
    """invoke_agent's telemetry/cost resolved_model =
    ``model_override or resolve_model_profile(role, config).model or model_id``.
    A local-mapped role must be attributed to its ACTUAL model, not config.model_id
    (Linus Phase-3 FIX-IT — honest hybrid-routing measurement)."""

    def test_local_role_attributed_to_profile_model(self) -> None:
        cfg = _local_config()  # ESCALATION -> local (model="qwen3-coder")
        self.assertEqual(resolve_model_profile(AgentRole.ESCALATION, cfg).model, "qwen3-coder")
        self.assertNotEqual(
            resolve_model_profile(AgentRole.ESCALATION, cfg).model, cfg.model_id
        )

    def test_default_role_attributed_to_global_model(self) -> None:
        cfg = _local_config()
        self.assertEqual(
            resolve_model_profile(AgentRole.IMPLEMENTER, cfg).model, cfg.model_id
        )


class TestProfileThreading(_InjectionBase):
    """Phase-5 follow-up: the builders honor a caller-threaded profile (invoke_agent
    resolves ONCE and passes it), proven by passing a profile that DIFFERS from what
    config would resolve — the threaded one must win (no internal re-resolution)."""

    _THREADED = ModelProfile(
        base_url="https://host.docker.internal:11434", auth_token_env="DF_LOCAL_TOKEN",
        model="qwen3-threaded", max_output_tokens=8192, family="local",
    )

    def test_build_claude_args_uses_threaded_profile(self) -> None:
        # config has NO mapping → would resolve default (sonnet); threaded profile wins.
        cfg = PipelineConfig(model_id="claude-sonnet-4-6")
        barriers = configure_barriers(cfg.source_dirs)
        args = build_claude_args(AgentRole.IMPLEMENTER, cfg, barriers, profile=self._THREADED)
        self.assertIn("--model=qwen3-threaded", args)

    @mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "REAL", "DF_LOCAL_TOKEN": "shim"})
    def test_build_full_docker_args_uses_threaded_profile(self) -> None:
        # config would resolve default (real token); threaded local profile drives
        # base_url + withholds the real token.
        cfg = PipelineConfig(model_id="claude-sonnet-4-6")
        barriers = configure_barriers(cfg.source_dirs)
        with tempfile.TemporaryDirectory() as tmp:
            make_project(Path(tmp))
            args = _build_full_agent_docker_args(
                AgentRole.IMPLEMENTER, config=cfg, barriers=barriers,
                run_id="run1", project_dir=Path(tmp), profile=self._THREADED,
            )
        env = _env_values(args)
        self.assertIn("ANTHROPIC_BASE_URL=https://host.docker.internal:11434", env)
        self.assertFalse(any("REAL" in e for e in env))   # real token withheld


class TestTEConcurrencyClamp(unittest.TestCase):
    def _local_te_config(self, max_concurrency: int, **cfg_kw) -> PipelineConfig:
        prof = ModelProfile(
            base_url="https://df-local:8081", model="q",
            max_output_tokens=8192, max_concurrency=max_concurrency,
        )
        return PipelineConfig(
            model_profiles={"local": prof},
            agent_model_profiles={AgentRole.TEST_ENGINEER.value: "local"},
            **cfg_kw,
        )

    def test_default_te_unclamped(self) -> None:
        # No local TE profile → conservative default min(n, 2), unchanged.
        self.assertEqual(_te_batch_workers(4, PipelineConfig()), 2)

    def test_local_te_clamps_to_max_concurrency(self) -> None:
        self.assertEqual(_te_batch_workers(4, self._local_te_config(1)), 1)

    def test_local_clamp_is_hard_ceiling_over_user_override(self) -> None:
        # User asks for 4 workers, but the local box can serve 1 → 1 wins.
        cfg = self._local_te_config(1, scenario_batch_max_workers=4)
        self.assertEqual(_te_batch_workers(4, cfg), 1)

    def test_local_te_zero_concurrency_is_unbounded(self) -> None:
        self.assertEqual(_te_batch_workers(4, self._local_te_config(0)), 2)


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    unittest.main()
