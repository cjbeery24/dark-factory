"""Tests for config/diagnostics.py — resolved_config_dump with provenance."""

from darkfactory.config.diagnostics import resolved_config_dump
from darkfactory.models.config import PipelineConfig


def test_resolved_config_dump_shows_provenance():
    config = PipelineConfig()
    config.build_cmd = "pnpm build"
    config.set_provenance("build_cmd", "environment.toml")
    config.lint_cmd = "eslint ."
    config.set_provenance("lint_cmd", "config.toml:[gates]")
    dump = resolved_config_dump(config)
    assert "build_cmd = 'pnpm build'  # source: environment.toml" in dump
    assert "lint_cmd = 'eslint .'  # source: config.toml:[gates]" in dump


def test_resolved_config_dump_default_fields_show_default():
    config = PipelineConfig()
    dump = resolved_config_dump(config)
    assert "build_cmd = ''  # source: default" in dump
    assert "lint_cmd = ''  # source: default" in dump


def test_resolved_config_dump_includes_all_operational_fields():
    config = PipelineConfig()
    dump = resolved_config_dump(config)
    for field_name in (
        "language", "package_manager", "source_dirs", "test_dir",
        "build_cmd", "lint_cmd", "typecheck_cmd", "test_cmd",
        "scenario_test_cmd", "setup_cmd", "test_server_cmd",
        "server_setup_cmd", "server_health_path", "server_startup_timeout",
        "browser_base_url", "browser_server_cmd", "browser_startup_timeout",
    ):
        assert field_name in dump, f"{field_name} missing from dump"
