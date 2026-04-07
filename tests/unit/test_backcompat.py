"""Phase 2.4 — config.toml -> environment.toml backcompat shim."""

from pathlib import Path

import pytest

from darkfactory.architecture.backcompat import (
    _BOOTSTRAP_FILENAME,
    ensure_environment_bootstrap,
)
from darkfactory.architecture.runtime import (
    _reset_cache,
    load_environment,
)
from darkfactory.models.config import PipelineConfig


@pytest.fixture(autouse=True)
def _clean_cache():
    _reset_cache()
    yield
    _reset_cache()


def _config(**kw) -> PipelineConfig:
    return PipelineConfig(**kw)


def test_synthesizes_when_missing(tmp_path):
    config = _config(
        test_cmd="pnpm test",
        build_cmd="pnpm build",
        server_health_path="/healthz",
        server_startup_timeout=45,
        language="node",
    )
    assert ensure_environment_bootstrap(config, tmp_path) is True
    env_path = tmp_path / ".dark-factory" / "architecture" / "environment.toml"
    assert env_path.exists()
    bootstrap = tmp_path / ".dark-factory" / "architecture" / "environment.deltas" / _BOOTSTRAP_FILENAME
    assert bootstrap.exists()


def test_round_trips_through_load_environment(tmp_path):
    config = _config(
        test_cmd="pnpm test",
        test_server_cmd="pnpm dev",
        server_health_path="/healthz",  # differs from default "/health"
        server_startup_timeout=60,      # differs from default 30
        language="python",              # differs from default "node"
    )
    ensure_environment_bootstrap(config, tmp_path)
    env = load_environment(tmp_path)
    assert env.commands.test == "pnpm test"
    assert env.commands.server_start == "pnpm dev"
    assert env.server.health_path == "/healthz"
    assert env.server.boot_timeout_s == 60
    assert env.runtime.language == "python"


def test_idempotent_when_environment_already_exists(tmp_path):
    config = _config(test_cmd="v1")
    ensure_environment_bootstrap(config, tmp_path)
    # Second call should be a no-op (returns False).
    config2 = _config(test_cmd="v2")  # different config
    assert ensure_environment_bootstrap(config2, tmp_path) is False
    # And the materialized view stays at v1 — the second call must NOT
    # rewrite the bootstrap delta.
    env = load_environment(tmp_path)
    assert env.commands.test == "v1"


def test_skips_empty_config_fields(tmp_path):
    config = _config(test_cmd="pnpm test")  # everything else default
    ensure_environment_bootstrap(config, tmp_path)
    bootstrap = (
        tmp_path / ".dark-factory" / "architecture" / "environment.deltas" / _BOOTSTRAP_FILENAME
    )
    text = bootstrap.read_text()
    assert "commands.test" in text
    # Empty defaults must NOT have been written as ops.
    assert "commands.build" not in text
    assert "commands.lint" not in text


def test_framework_defaults_are_not_migrated(tmp_path):
    # Only fields the user actually set (differing from PipelineConfig
    # defaults) should be baked into the bootstrap delta. Framework
    # defaults must NOT be recorded as user "decisions" in the
    # append-only log.
    config = _config(test_cmd="x")
    ensure_environment_bootstrap(config, tmp_path)
    bootstrap = (
        tmp_path / ".dark-factory" / "architecture" / "environment.deltas" / _BOOTSTRAP_FILENAME
    )
    text = bootstrap.read_text()
    assert "commands.test" in text
    assert "server.boot_timeout_s" not in text  # default 30, not user-set
    assert "server.health_path" not in text     # default "/health", not user-set
    assert "runtime.language" not in text       # default "node", not user-set


def test_resumes_existing_bootstrap_without_environment(tmp_path):
    """Half-migration: deltas/0000-... exists but environment.toml does not.
    Re-running should fold + materialize the existing delta, not rewrite it."""
    deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
    deltas_dir.mkdir(parents=True)
    # Hand-write a bootstrap with a specific marker value.
    from darkfactory.architecture.deltas import hash_view
    from darkfactory.architecture.environment import EnvironmentContract
    parent = hash_view(EnvironmentContract())
    (deltas_dir / _BOOTSTRAP_FILENAME).write_text(
        f'schema_version = 1\n'
        f'spec_id = "bootstrap-from-config-toml"\n'
        f'parent_hash = "{parent}"\n'
        f'applied_at = "2026-04-06T00:00:00Z"\n'
        f'\n[[ops]]\nop = "set"\npath = "commands.test"\nvalue = "MARKER"\n'
    )
    # Pass a config that would otherwise produce a different bootstrap.
    config = _config(test_cmd="DIFFERENT")
    assert ensure_environment_bootstrap(config, tmp_path) is True
    env = load_environment(tmp_path)
    assert env.commands.test == "MARKER"  # the existing delta won, not the config


def test_logs_deprecation_warning(tmp_path, caplog):
    config = _config(test_cmd="pnpm test")
    with caplog.at_level("WARNING"):
        ensure_environment_bootstrap(config, tmp_path)
    assert any("deprecated" in r.message for r in caplog.records)


def test_string_with_quotes_and_backslashes_escapes(tmp_path):
    config = _config(test_cmd='echo "hi" \\ bye')
    ensure_environment_bootstrap(config, tmp_path)
    env = load_environment(tmp_path)
    assert env.commands.test == 'echo "hi" \\ bye'


def test_seeds_layout_source_dirs_for_monorepo(tmp_path):
    # pnpm monorepo: synthesizer should detect apps/*/src and bake them
    # into the bootstrap delta so the Phase 4.5.1 pre-flight does not
    # trip on the very first run.
    (tmp_path / "apps" / "api" / "src").mkdir(parents=True)
    (tmp_path / "packages" / "shared" / "src").mkdir(parents=True)
    config = _config(test_cmd="pnpm test")  # source_dirs left at default
    ensure_environment_bootstrap(config, tmp_path)
    env = load_environment(tmp_path)
    assert env.layout.source_dirs == ["apps/api/src", "packages/shared/src"]


def test_does_not_seed_layout_source_dirs_for_flat_project(tmp_path):
    # Flat project with no monorepo markers: synthesizer must not
    # invent paths. Falls through to framework default.
    (tmp_path / "src").mkdir()
    config = _config(test_cmd="pytest")
    ensure_environment_bootstrap(config, tmp_path)
    bootstrap = (
        tmp_path / ".dark-factory" / "architecture" / "environment.deltas" / _BOOTSTRAP_FILENAME
    )
    assert "layout.source_dirs" not in bootstrap.read_text()


def test_does_not_overwrite_user_set_source_dirs(tmp_path):
    # User explicitly set source_dirs in config.toml — synthesizer must
    # honor that and skip detection, even on a monorepo layout.
    (tmp_path / "apps" / "api" / "src").mkdir(parents=True)
    config = _config(source_dirs=["custom/path"])
    ensure_environment_bootstrap(config, tmp_path)
    bootstrap = (
        tmp_path / ".dark-factory" / "architecture" / "environment.deltas" / _BOOTSTRAP_FILENAME
    )
    text = bootstrap.read_text()
    # The detection branch is skipped because the user diverged from default.
    # source_dirs is not in _CONFIG_TO_ENV (it lives at layout, not commands),
    # so currently nothing is emitted — the user's existing config.toml stays
    # the source of truth via the runtime merge layer. The important assertion
    # is that we did NOT auto-emit the detected apps/api/src path.
    assert "apps/api/src" not in text
