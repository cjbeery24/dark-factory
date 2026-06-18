"""Tests for config/loader.py — TOML loading + bash migration."""

import unittest

from tests.helpers import TempDirTestCase
from darkfactory.config.loader import load_config, migrate_config, parse_bash_config
from darkfactory.errors import ConfigError


class TestLoadConfig(TempDirTestCase):
    def test_missing_file_returns_defaults(self) -> None:
        cfg = load_config(self.tmp_dir / "nope.toml")
        self.assertEqual(cfg.language, "node")
        self.assertEqual(cfg.autonomy_level, 3)

    def test_load_basic_toml(self) -> None:
        toml = self.tmp_dir / "config.toml"
        toml.write_text("""\
[core]
language = "python"
source_dirs = ["src", "lib"]

[autonomy]
level = 2
max_iterations = 5

[satisfaction.weights]
security = 150
""")
        cfg = load_config(toml)
        self.assertEqual(cfg.language, "python")
        self.assertEqual(cfg.source_dirs, ["src", "lib"])
        self.assertEqual(cfg.autonomy_level, 2)
        self.assertEqual(cfg.max_iterations, 5)
        self.assertEqual(cfg.weight_security, 150)
        # Unset values keep defaults.
        self.assertEqual(cfg.agent_max_turns, 200)

    def test_invalid_toml(self) -> None:
        toml = self.tmp_dir / "bad.toml"
        toml.write_text("this is not [valid toml")
        with self.assertRaises(ConfigError):
            load_config(toml)

    def test_section_not_table(self) -> None:
        toml = self.tmp_dir / "bad.toml"
        toml.write_text('core = "not a table"\n')
        with self.assertRaises(ConfigError):
            load_config(toml)

    def test_load_services(self) -> None:
        toml = self.tmp_dir / "config.toml"
        toml.write_text("""\
[[services]]
name = "postgres"
image = "postgres:16-alpine"
health_cmd = "pg_isready -U postgres"
health_timeout = 30
env = { POSTGRES_PASSWORD = "postgres", POSTGRES_DB = "testdb" }
inject_env = { DATABASE_URL = "postgres://postgres:postgres@postgres:5432/testdb" }

[[services]]
name = "redis"
image = "redis:7-alpine"
health_cmd = "redis-cli ping"
""")
        cfg = load_config(toml)
        self.assertEqual(len(cfg.services), 2)
        self.assertEqual(cfg.services[0].name, "postgres")
        self.assertEqual(cfg.services[0].image, "postgres:16-alpine")
        self.assertEqual(cfg.services[0].env["POSTGRES_PASSWORD"], "postgres")
        self.assertEqual(
            cfg.services[0].inject_env["DATABASE_URL"],
            "postgres://postgres:postgres@postgres:5432/testdb",
        )
        self.assertEqual(cfg.services[1].name, "redis")
        self.assertEqual(cfg.services[1].health_cmd, "redis-cli ping")

    def test_load_services_invalid_entry(self) -> None:
        toml = self.tmp_dir / "config.toml"
        toml.write_text("""\
[[services]]
name = "postgres"
image = "postgres:16"
bogus_field = true
""")
        with self.assertRaises(ConfigError):
            load_config(toml)

    def test_load_agent_timeout_overrides(self) -> None:
        # L2: per-role timeout override from [agents.timeout_overrides].
        toml = self.tmp_dir / "config.toml"
        toml.write_text(
            '[agents]\ntimeout = 900\n'
            '[agents.timeout_overrides]\n'
            'test-engineer = 1800\nqa-holdout = 1200\n'
        )
        cfg = load_config(toml)
        self.assertEqual(cfg.agent_timeout, 900)
        self.assertEqual(cfg.agent_timeout_overrides,
                         {"test-engineer": 1800, "qa-holdout": 1200})
        # resolution semantics used by invoke_agent
        self.assertEqual(
            cfg.agent_timeout_overrides.get("test-engineer", cfg.agent_timeout), 1800)
        self.assertEqual(
            cfg.agent_timeout_overrides.get("architect", cfg.agent_timeout), 900)

    def test_agent_timeout_overrides_non_int_rejected(self) -> None:
        toml = self.tmp_dir / "config.toml"
        toml.write_text('[agents.timeout_overrides]\ntest-engineer = "lots"\n')
        with self.assertRaises(ConfigError):
            load_config(toml)

    def test_agent_timeout_overrides_floor_and_type(self) -> None:
        # L2 (Linus+Codex): a fat-fingered 0/negative/float silently defeating the
        # override must be REJECTED (agent_timeout has the same >=1 floor).
        for bad in ("test-engineer = 0", "test-engineer = -5", "test-engineer = 1800.5"):
            toml = self.tmp_dir / "config.toml"
            toml.write_text(f"[agents.timeout_overrides]\n{bad}\n")
            with self.assertRaises(ConfigError, msg=bad):
                load_config(toml)

    def test_agent_timeout_overrides_unknown_role_warns_not_fails(self) -> None:
        # A misspelled role (test_engineer vs test-engineer) loads but warns —
        # it must not silently apply nor hard-fail.
        toml = self.tmp_dir / "config.toml"
        toml.write_text('[agents.timeout_overrides]\ntest_engineer = 1800\n')
        with self.assertLogs("darkfactory.models.config", level="WARNING") as cm:
            cfg = load_config(toml)
        self.assertEqual(cfg.agent_timeout_overrides, {"test_engineer": 1800})
        self.assertTrue(any("unknown role" in m for m in cm.output))

    def test_no_timeout_overrides_defaults_empty(self) -> None:
        toml = self.tmp_dir / "config.toml"
        toml.write_text('[core]\nlanguage = "node"\n')
        cfg = load_config(toml)
        self.assertEqual(cfg.agent_timeout_overrides, {})

    def test_load_no_services(self) -> None:
        toml = self.tmp_dir / "config.toml"
        toml.write_text('[core]\nlanguage = "node"\n')
        cfg = load_config(toml)
        self.assertEqual(cfg.services, [])


class TestParseBashConfig(TempDirTestCase):
    def test_basic_parse(self) -> None:
        cfg = self.tmp_dir / "config.sh"
        cfg.write_text("""\
# Comment
LANGUAGE="python"
SOURCE_DIRS="src:lib"
AUTONOMY_LEVEL=2
AUTO_PR=true
""")
        result = parse_bash_config(cfg)
        self.assertEqual(result["LANGUAGE"], "python")
        self.assertEqual(result["SOURCE_DIRS"], "src:lib")
        self.assertEqual(result["AUTONOMY_LEVEL"], "2")
        self.assertEqual(result["AUTO_PR"], "true")

    def test_reject_metacharacters(self) -> None:
        cfg = self.tmp_dir / "config.sh"
        cfg.write_text('FOO="$(whoami)"\n')
        with self.assertRaises(ConfigError):
            parse_bash_config(cfg)

    def test_reject_non_assignment(self) -> None:
        cfg = self.tmp_dir / "config.sh"
        cfg.write_text("source other.sh\n")
        with self.assertRaises(ConfigError):
            parse_bash_config(cfg)

    def test_single_quotes(self) -> None:
        cfg = self.tmp_dir / "config.sh"
        cfg.write_text("LANGUAGE='go'\n")
        result = parse_bash_config(cfg)
        self.assertEqual(result["LANGUAGE"], "go")


class TestMigrateConfig(TempDirTestCase):
    def test_basic_migration(self) -> None:
        sh = self.tmp_dir / "config.sh"
        sh.write_text("""\
LANGUAGE="python"
SOURCE_DIRS="src:lib"
AUTONOMY_LEVEL=2
AUTO_PR=true
COST_ALERT_THRESHOLDS="50,80,100"
COST_BUDGET_USD=10.0
""")
        out = self.tmp_dir / "config.toml"
        toml_str = migrate_config(sh, out)

        self.assertIn('[core]', toml_str)
        self.assertIn('language = "python"', toml_str)
        self.assertIn('source_dirs = ["src", "lib"]', toml_str)
        self.assertIn('[autonomy]', toml_str)
        self.assertIn('level = 2', toml_str)
        self.assertIn('[git]', toml_str)
        self.assertIn('auto_pr = true', toml_str)
        self.assertIn('alert_thresholds = [50, 80, 100]', toml_str)
        self.assertIn('budget_usd = 10.0', toml_str)

        # Verify the output file is valid TOML that loads.
        cfg = load_config(out)
        self.assertEqual(cfg.language, "python")
        self.assertEqual(cfg.source_dirs, ["src", "lib"])
        self.assertEqual(cfg.autonomy_level, 2)

    def test_nested_section_migration(self) -> None:
        sh = self.tmp_dir / "config.sh"
        sh.write_text("""\
LANGUAGE="node"
SCENARIO_WEIGHT_SECURITY=150
SCENARIO_WEIGHT_CONTRACT=100
""")
        out = self.tmp_dir / "config.toml"
        toml_str = migrate_config(sh, out)

        self.assertIn("[satisfaction.weights]", toml_str)
        self.assertIn("security = 150", toml_str)

        cfg = load_config(out)
        self.assertEqual(cfg.weight_security, 150)
        self.assertEqual(cfg.weight_contract, 100)

    def test_export_prefix(self) -> None:
        sh = self.tmp_dir / "config.sh"
        sh.write_text("""\
export LANGUAGE="python"
export AUTO_PR=true
""")
        result = parse_bash_config(sh)
        self.assertEqual(result["LANGUAGE"], "python")
        self.assertEqual(result["AUTO_PR"], "true")

    def test_auth_tokens_excluded(self) -> None:
        sh = self.tmp_dir / "config.sh"
        sh.write_text("""\
LANGUAGE="node"
CLAUDE_CODE_OAUTH_TOKEN="secret123"
ANTHROPIC_API_KEY="sk-ant-xxx"
""")
        out = self.tmp_dir / "config.toml"
        toml_str = migrate_config(sh, out)

        self.assertNotIn("secret123", toml_str)
        self.assertNotIn("sk-ant-xxx", toml_str)


if __name__ == "__main__":
    unittest.main()
