"""Tests for local-LLM routing: ModelProfile + per-role profile resolution.

Phase 2 of feat/local-llm-routing. Covers the config primitive only (parsing,
validation, resolution, backward-compat). Env injection is Phase 3.
"""

import logging
import unittest

from tests.helpers import TempDirTestCase
from darkfactory.config.loader import load_config
from darkfactory.errors import ConfigError
from darkfactory.models.agent import AgentRole
from darkfactory.models.config import (
    ModelProfile,
    PipelineConfig,
    resolve_model_profile,
)


class TestModelProfileValidation(unittest.TestCase):
    """ModelProfile.__post_init__ field validation."""

    def test_defaults(self) -> None:
        p = ModelProfile()
        self.assertEqual(p.base_url, "")
        self.assertEqual(p.max_output_tokens, 0)
        self.assertEqual(p.max_concurrency, 0)
        self.assertTrue(p.disable_thinking)

    def test_valid_custom_backend(self) -> None:
        p = ModelProfile(
            base_url="https://host.docker.internal:8081",
            auth_token_env="DF_LOCAL_TOKEN",
            model="qwen3-coder",
            max_output_tokens=8192,
            family="local",
            max_concurrency=1,
        )
        self.assertEqual(p.max_output_tokens, 8192)

    def test_custom_backend_requires_positive_cap(self) -> None:
        # base_url set but max_output_tokens unset (0) → reject (Neo rev-3).
        with self.assertRaises(ConfigError):
            ModelProfile(base_url="https://host.docker.internal:8081")

    def test_no_base_url_allows_zero_cap(self) -> None:
        # An Anthropic-style profile (no base_url) may leave cap 0 (family-derive).
        p = ModelProfile(model="claude-sonnet-4-6")
        self.assertEqual(p.max_output_tokens, 0)

    def test_negative_max_output_tokens(self) -> None:
        with self.assertRaises(ConfigError):
            ModelProfile(max_output_tokens=-1)

    def test_negative_max_concurrency(self) -> None:
        with self.assertRaises(ConfigError):
            ModelProfile(max_concurrency=-1)

    def test_bool_rejected_for_int_fields(self) -> None:
        # TOML bool is an int subclass — must be rejected, not silently coerced.
        with self.assertRaises(ConfigError):
            ModelProfile(max_output_tokens=True)  # type: ignore[arg-type]
        with self.assertRaises(ConfigError):
            ModelProfile(max_concurrency=True)  # type: ignore[arg-type]

    def test_string_fields_reject_non_str(self) -> None:
        # base_url/auth_token_env/model/family must be strings (Linus FIX-IT / Codex #1).
        with self.assertRaises(ConfigError):
            ModelProfile(base_url=123)  # type: ignore[arg-type]
        with self.assertRaises(ConfigError):
            ModelProfile(base_url=True, max_output_tokens=1)  # type: ignore[arg-type]
        with self.assertRaises(ConfigError):
            ModelProfile(model=123)  # type: ignore[arg-type]
        with self.assertRaises(ConfigError):
            ModelProfile(family=True)  # type: ignore[arg-type]

    def test_disable_thinking_must_be_bool(self) -> None:
        with self.assertRaises(ConfigError):
            ModelProfile(disable_thinking="false")  # type: ignore[arg-type]

    def test_ca_cert_non_str_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            ModelProfile(ca_cert=123)  # type: ignore[arg-type]

    def test_ca_cert_control_char_rejected(self) -> None:
        # ca_cert becomes a docker -v mount source; a newline must not pass. Spaces
        # are allowed (valid in filesystem paths) — only control chars are rejected.
        with self.assertRaises(ConfigError):
            ModelProfile(ca_cert="/path/to\ncert.pem")
        ok = ModelProfile(ca_cert="/path with space/cert.pem")
        self.assertEqual(ok.ca_cert, "/path with space/cert.pem")

    def test_ca_cert_colon_rejected(self) -> None:
        # ':' is the docker -v mount delimiter — a path containing it would corrupt
        # the mount spec, so reject at config time (Linus NIT-1).
        with self.assertRaises(ConfigError):
            ModelProfile(ca_cert="/weird:path/cert.pem")

    def test_https_without_ca_cert_warns_not_fails(self) -> None:
        # https + empty ca_cert is allowed (publicly-trusted cert is legitimate) but
        # warns (the common self-signed case would fail the handshake at runtime).
        with self.assertLogs("darkfactory.models.config", level="WARNING") as cm:
            p = ModelProfile(base_url="https://host.docker.internal:8443", max_output_tokens=8192)
        self.assertEqual(p.base_url, "https://host.docker.internal:8443")
        self.assertTrue(any("ca_cert" in m for m in cm.output))

    def test_https_with_ca_cert_no_warn(self) -> None:
        with self.assertNoLogs("darkfactory.models.config", level="WARNING"):
            ModelProfile(
                base_url="https://host.docker.internal:8443",
                ca_cert="/some/cert.pem", max_output_tokens=8192,
            )

    def test_custom_backend_rejects_reserved_cred_env(self) -> None:
        # auth_token_env on a custom backend must not alias a real Anthropic
        # credential var, else injection would leak the real token (Codex P1).
        for reserved in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
            with self.assertRaises(ConfigError, msg=reserved):
                ModelProfile(
                    base_url="https://h:8081", max_output_tokens=1,
                    auth_token_env=reserved,
                )

    def test_reserved_cred_env_allowed_without_base_url(self) -> None:
        # The guard is scoped to custom backends (base_url set); a non-backend
        # profile never reads auth_token_env, so it isn't restricted.
        p = ModelProfile(auth_token_env="CLAUDE_CODE_OAUTH_TOKEN")
        self.assertEqual(p.auth_token_env, "CLAUDE_CODE_OAUTH_TOKEN")


class TestResolveModelProfile(unittest.TestCase):
    """resolve_model_profile precedence and the synthesized default profile."""

    def test_unset_role_resolves_to_default(self) -> None:
        cfg = PipelineConfig(model_id="claude-sonnet-4-6")
        prof = resolve_model_profile(AgentRole.IMPLEMENTER, cfg)
        self.assertEqual(prof.base_url, "")          # Anthropic backend
        self.assertEqual(prof.model, "claude-sonnet-4-6")
        self.assertFalse(prof.disable_thinking)      # Claude supports thinking

    def test_default_profile_honors_model_ids(self) -> None:
        # Backward-compat: a role with a legacy [model.ids] override gets a default
        # profile whose model is that override; an unmapped role gets the global.
        cfg = PipelineConfig(
            model_id="claude-sonnet-4-6",
            model_ids={AgentRole.IMPLEMENTER.value: "claude-opus-4-8"},
        )
        self.assertEqual(resolve_model_profile(AgentRole.IMPLEMENTER, cfg).model, "claude-opus-4-8")
        self.assertEqual(resolve_model_profile(AgentRole.REVIEWER, cfg).model, "claude-sonnet-4-6")

    def test_profile_mapping_beats_model_ids(self) -> None:
        # Precedence: an [agents.models] profile mapping wins over [model.ids].
        local = ModelProfile(base_url="https://h:8081", model="qwen", max_output_tokens=8192)
        cfg = PipelineConfig(
            model_ids={AgentRole.IMPLEMENTER.value: "claude-opus-4-8"},
            model_profiles={"local": local},
            agent_model_profiles={AgentRole.IMPLEMENTER.value: "local"},
        )
        self.assertIs(resolve_model_profile(AgentRole.IMPLEMENTER, cfg), local)

    def test_default_profile_carries_global_cap(self) -> None:
        cfg = PipelineConfig(model_id="claude-opus-4-8", max_output_tokens=12345)
        prof = resolve_model_profile(AgentRole.ARCHITECT, cfg)
        self.assertEqual(prof.max_output_tokens, 12345)

    def test_per_role_profile_resolved(self) -> None:
        local = ModelProfile(base_url="https://h:8081", model="qwen", max_output_tokens=8192)
        cfg = PipelineConfig(
            model_profiles={"local": local},
            agent_model_profiles={AgentRole.ESCALATION.value: "local"},
        )
        # Mapped role gets the local profile…
        self.assertIs(resolve_model_profile(AgentRole.ESCALATION, cfg), local)
        # …an unmapped role still gets default.
        self.assertEqual(resolve_model_profile(AgentRole.REVIEWER, cfg).base_url, "")

    def test_resolve_returns_shared_profile_object(self) -> None:
        # Phase-2 contract: the resolver returns the SAME object stored in config
        # (no copy). Phase 3's injection MUST therefore apply model_override to a
        # COPY, never by mutating the resolved object (Linus carry-to-Phase-3). This
        # test locks the contract so a Phase-3 change can't regress it silently.
        local = ModelProfile(base_url="https://h:8081", model="q", max_output_tokens=8192)
        cfg = PipelineConfig(
            model_profiles={"local": local},
            agent_model_profiles={AgentRole.IMPLEMENTER.value: "local"},
        )
        self.assertIs(resolve_model_profile(AgentRole.IMPLEMENTER, cfg), local)

    def test_undefined_mapping_raises_at_resolve(self) -> None:
        # Bypass __post_init__ validation via object construction path: build a
        # config whose mapping points at a missing profile by mutating after init.
        cfg = PipelineConfig()
        cfg.agent_model_profiles = {AgentRole.IMPLEMENTER.value: "ghost"}
        with self.assertRaises(ConfigError):
            resolve_model_profile(AgentRole.IMPLEMENTER, cfg)


class TestPipelineConfigCrossValidation(unittest.TestCase):
    def test_reserved_default_profile_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig(model_profiles={"default": ModelProfile()})

    def test_mapping_to_undefined_profile_fails(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig(agent_model_profiles={AgentRole.IMPLEMENTER.value: "nope"})

    def test_mapping_to_default_is_ok(self) -> None:
        cfg = PipelineConfig(agent_model_profiles={AgentRole.IMPLEMENTER.value: "default"})
        self.assertEqual(cfg.agent_model_profiles[AgentRole.IMPLEMENTER.value], "default")

    def test_global_max_output_tokens_rejects_bool(self) -> None:
        # A bool here would pass an isinstance check (bool subclasses int) but then
        # crash the synthesized default profile — reject it upfront (Codex #3).
        with self.assertRaises(ConfigError):
            PipelineConfig(max_output_tokens=True)

    def test_unknown_role_warns_not_fails(self) -> None:
        local = ModelProfile(base_url="https://h:8081", model="q", max_output_tokens=8192)
        with self.assertLogs("darkfactory.models.config", level="WARNING") as cm:
            cfg = PipelineConfig(
                model_profiles={"local": local},
                agent_model_profiles={"test_engineer": "local"},  # typo: underscore
            )
        self.assertTrue(any("unknown role" in m for m in cm.output))
        self.assertIn("test_engineer", cfg.agent_model_profiles)


class TestLoaderModelProfiles(TempDirTestCase):
    def test_load_profiles_and_mapping(self) -> None:
        toml = self.tmp_dir / "config.toml"
        toml.write_text("""\
[model]
id = "claude-sonnet-4-6"

[model.profiles.local]
base_url = "https://host.docker.internal:8081"
auth_token_env = "DF_LOCAL_TOKEN"
model = "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
max_output_tokens = 8192
family = "local"
max_concurrency = 1

[agents.models]
escalation = "local"
defect-classifier = "local"
""")
        cfg = load_config(toml)
        self.assertIn("local", cfg.model_profiles)
        prof = cfg.model_profiles["local"]
        self.assertEqual(prof.base_url, "https://host.docker.internal:8081")
        self.assertEqual(prof.max_output_tokens, 8192)
        self.assertEqual(prof.max_concurrency, 1)
        self.assertEqual(
            cfg.agent_model_profiles,
            {"escalation": "local", "defect-classifier": "local"},
        )
        # End-to-end: a mapped role resolves to the local profile.
        self.assertIs(resolve_model_profile(AgentRole.ESCALATION, cfg), prof)

    def test_profiles_not_a_table_raises(self) -> None:
        # A present-but-non-table profiles value is a misconfigured opt-in → raise,
        # including FALSY scalars (key-presence, not truthiness — Linus NIT, Codex #2b).
        for val in ('"nope"', "false", "0", '""'):
            toml = self.tmp_dir / "config.toml"
            toml.write_text(f"[model]\nprofiles = {val}\n")
            with self.assertRaises(ConfigError, msg=f"profiles = {val}"):
                load_config(toml)

    def test_agents_models_not_a_table_raises(self) -> None:
        # [agents.models] is the routing opt-in switch; any present scalar (truthy
        # OR falsy) must raise, not silently leave the mapping empty (Codex #2/#2b).
        for val in ('"local"', "false", "0", '""'):
            toml = self.tmp_dir / "config.toml"
            toml.write_text(f"[agents]\nmodels = {val}\n")
            with self.assertRaises(ConfigError, msg=f"models = {val}"):
                load_config(toml)

    def test_profile_bad_field_raises(self) -> None:
        toml = self.tmp_dir / "config.toml"
        toml.write_text("""\
[model.profiles.local]
base_url = "https://h:8081"
max_output_tokens = 0
""")
        # Custom backend with no positive cap → ModelProfile.__post_init__ rejects.
        with self.assertRaises(ConfigError):
            load_config(toml)

    def test_profile_unknown_key_raises(self) -> None:
        toml = self.tmp_dir / "config.toml"
        toml.write_text("""\
[model.profiles.local]
base_url = "https://h:8081"
max_output_tokens = 8192
bogus_key = "x"
""")
        with self.assertRaises(ConfigError):
            load_config(toml)

    def test_reserved_default_via_toml_raises(self) -> None:
        toml = self.tmp_dir / "config.toml"
        toml.write_text("""\
[model.profiles.default]
base_url = "https://h:8081"
max_output_tokens = 8192
""")
        with self.assertRaises(ConfigError):
            load_config(toml)


class TestBackwardCompat(TempDirTestCase):
    def test_model_ids_still_works(self) -> None:
        # Existing [model.ids] config must be untouched by the new fields.
        toml = self.tmp_dir / "config.toml"
        toml.write_text("""\
[model]
id = "claude-sonnet-4-6"

[model.ids]
implementer = "claude-opus-4-8"
""")
        cfg = load_config(toml)
        self.assertEqual(cfg.model_ids, {"implementer": "claude-opus-4-8"})
        self.assertEqual(cfg.model_profiles, {})
        self.assertEqual(cfg.agent_model_profiles, {})

    def test_no_model_config_is_all_default(self) -> None:
        cfg = PipelineConfig()
        self.assertEqual(cfg.model_profiles, {})
        # Every role resolves to the default Anthropic backend.
        for role in AgentRole:
            self.assertEqual(resolve_model_profile(role, cfg).base_url, "")


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()
