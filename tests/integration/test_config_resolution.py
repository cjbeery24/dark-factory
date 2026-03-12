"""Integration tests for config loading, defaults, validation, and migration."""

from __future__ import annotations

import pytest
from pathlib import Path

from darkfactory.config.loader import load_config, migrate_config, parse_bash_config
from darkfactory.errors import ConfigError
from darkfactory.models.config import PipelineConfig


@pytest.mark.integration
class TestDefaults:
    """Config defaults when no config file exists."""

    def test_defaults_when_no_config(self, project_dir_no_config: Path):
        config_path = project_dir_no_config / ".dark-factory" / "config.toml"
        assert not config_path.exists()
        cfg = load_config(config_path)
        assert isinstance(cfg, PipelineConfig)
        assert cfg.language == "node"  # default language
        assert cfg.agent_max_turns == 200
        assert cfg.autonomy_level == 3
        assert cfg.max_iterations == 3

    def test_defaults_are_consistent_types(self, project_dir_no_config: Path):
        config_path = project_dir_no_config / ".dark-factory" / "config.toml"
        cfg = load_config(config_path)
        assert isinstance(cfg.source_dirs, list)
        assert isinstance(cfg.hard_gate_build, bool)
        assert isinstance(cfg.gate_timeout, int)


@pytest.mark.integration
class TestTomlLoading:
    """Config loading from TOML files."""

    def test_toml_overrides_defaults(self, project_dir: Path):
        cfg = load_config(project_dir / ".dark-factory" / "config.toml")
        assert cfg.language == "python"
        assert cfg.source_dirs == ["src"]
        assert cfg.agent_max_turns == 5
        assert cfg.autonomy_level == 3
        assert cfg.max_iterations == 3
        assert cfg.gate_timeout == 30

    def test_unspecified_fields_keep_defaults(self, project_dir: Path):
        cfg = load_config(project_dir / ".dark-factory" / "config.toml")
        # These are not in the test config.toml, so they should have defaults
        assert cfg.git_remote == "origin"
        assert cfg.git_base_branch == "main"
        assert cfg.hard_gate_build is False
        assert cfg.docker_network == "df-pipeline"

    def test_invalid_toml_fails_clearly(self, tmp_path: Path):
        config_path = tmp_path / "bad.toml"
        config_path.write_text("[core\nlanguage = python\n")
        with pytest.raises(ConfigError, match="invalid TOML"):
            load_config(config_path)

    def test_non_table_section_fails(self, tmp_path: Path):
        config_path = tmp_path / "bad.toml"
        config_path.write_text('core = "not a table"\n')
        with pytest.raises(ConfigError, match="must be a table"):
            load_config(config_path)

    def test_partial_config_only_sets_specified(self, tmp_path: Path):
        config_path = tmp_path / "partial.toml"
        config_path.write_text('[core]\nlanguage = "go"\n')
        cfg = load_config(config_path)
        assert cfg.language == "go"
        assert cfg.agent_max_turns == 200  # default


@pytest.mark.integration
class TestConfigShMigration:
    """Legacy config.sh to TOML migration."""

    def test_simple_migration(self, tmp_path: Path):
        bash_config = tmp_path / "config.sh"
        bash_config.write_text(
            'LANGUAGE="python"\n'
            'SOURCE_DIRS="src:lib"\n'
            'AGENT_MAX_TURNS=10\n'
            'AUTONOMY_LEVEL=2\n'
            'HARD_GATE_BUILD=true\n'
        )
        toml_path = tmp_path / "config.toml"
        result = migrate_config(bash_config, toml_path)
        assert toml_path.exists()
        assert 'language = "python"' in result
        assert '["src", "lib"]' in result
        assert "max_turns = 10" in result
        assert "level = 2" in result
        assert "hard_gate_build = true" in result

    def test_migrated_config_is_loadable(self, tmp_path: Path):
        bash_config = tmp_path / "config.sh"
        bash_config.write_text(
            'LANGUAGE="python"\n'
            'AGENT_MAX_TURNS=10\n'
        )
        toml_path = tmp_path / "config.toml"
        migrate_config(bash_config, toml_path)
        cfg = load_config(toml_path)
        assert cfg.language == "python"
        assert cfg.agent_max_turns == 10

    def test_auth_tokens_not_written(self, tmp_path: Path):
        bash_config = tmp_path / "config.sh"
        bash_config.write_text(
            'LANGUAGE="python"\n'
            'CLAUDE_CODE_OAUTH_TOKEN=secret123\n'
            'ANTHROPIC_API_KEY=sk-ant-secret\n'
        )
        toml_path = tmp_path / "config.toml"
        result = migrate_config(bash_config, toml_path)
        assert "secret123" not in result
        assert "sk-ant-secret" not in result

    def test_shell_metacharacters_rejected(self, tmp_path: Path):
        bash_config = tmp_path / "config.sh"
        bash_config.write_text('LANGUAGE=$(whoami)\n')
        with pytest.raises(ConfigError, match="shell metacharacter"):
            parse_bash_config(bash_config)

    def test_export_prefix_stripped(self, tmp_path: Path):
        bash_config = tmp_path / "config.sh"
        bash_config.write_text('export LANGUAGE="python"\n')
        result = parse_bash_config(bash_config)
        assert result["LANGUAGE"] == "python"

    def test_comments_and_blanks_ignored(self, tmp_path: Path):
        bash_config = tmp_path / "config.sh"
        bash_config.write_text(
            '# This is a comment\n'
            '\n'
            'LANGUAGE="python"\n'
            '# Another comment\n'
        )
        result = parse_bash_config(bash_config)
        assert result == {"LANGUAGE": "python"}

    def test_boolean_values(self, tmp_path: Path):
        bash_config = tmp_path / "config.sh"
        bash_config.write_text(
            'HARD_GATE_BUILD=true\n'
            'AUTO_PR=false\n'
        )
        toml_path = tmp_path / "config.toml"
        result = migrate_config(bash_config, toml_path)
        assert "hard_gate_build = true" in result
        assert "auto_pr = false" in result
