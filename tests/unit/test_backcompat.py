"""Phase 2.4 — config.toml -> environment.toml backcompat shim."""

import tomli_w
from pathlib import Path

import pytest

from darkfactory.architecture.backcompat import (
    _BOOTSTRAP_FILENAME,
    _strip_operational_fields,
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
    # User-set source_dirs is migrated to the delta; auto-detected monorepo
    # paths are NOT emitted (detection branch is skipped).
    assert "custom/path" in text
    assert "apps/api/src" not in text


# --- Phase 5: operational field stripping ---


def _write_config_toml(tmp_path: Path, data: dict) -> Path:
    """Write a config.toml for testing."""
    config_path = tmp_path / ".dark-factory" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(tomli_w.dumps(data), encoding="utf-8")
    return config_path


def test_strips_operational_fields_from_config_toml(tmp_path):
    """After migration, operational fields are removed from config.toml."""
    _write_config_toml(tmp_path, {
        "core": {"language": "python", "test_cmd": "pytest"},
        "gates": {
            "build_cmd": "pip install -e .",
            "lint_cmd": "ruff check .",
            "timeout": 300,
            "min_ship_pass_rate": 80,
        },
    })
    config = _config(
        test_cmd="pytest",
        build_cmd="pip install -e .",
        lint_cmd="ruff check .",
        language="python",
    )
    ensure_environment_bootstrap(config, tmp_path)

    # Re-read config.toml after migration.
    import tomllib
    config_path = tmp_path / ".dark-factory" / "config.toml"
    result = tomllib.loads(config_path.read_text())

    # Operational fields stripped.
    assert "test_cmd" not in result.get("core", {})
    assert "build_cmd" not in result.get("gates", {})
    assert "lint_cmd" not in result.get("gates", {})
    # Tuning fields preserved.
    assert result["gates"]["timeout"] == 300
    assert result["gates"]["min_ship_pass_rate"] == 80
    # language stays (it's not in _OPERATIONAL_FIELDS).
    assert result["core"]["language"] == "python"


def test_preserves_tuning_fields_in_config_toml(tmp_path):
    """Tuning fields in [gates] survive the migration strip."""
    _write_config_toml(tmp_path, {
        "core": {"language": "node"},
        "gates": {
            "build_cmd": "pnpm build",
            "timeout": 600,
            "min_ship_pass_rate": 90,
            "hard_gate_build": True,
            "scenario_timeout": 120,
        },
        "autonomy": {"level": 2},
    })
    config = _config(build_cmd="pnpm build", language="node")
    ensure_environment_bootstrap(config, tmp_path)

    import tomllib
    config_path = tmp_path / ".dark-factory" / "config.toml"
    result = tomllib.loads(config_path.read_text())

    # Tuning preserved.
    assert result["gates"]["timeout"] == 600
    assert result["gates"]["min_ship_pass_rate"] == 90
    assert result["gates"]["hard_gate_build"] is True
    assert result["gates"]["scenario_timeout"] == 120
    # Non-gates sections untouched.
    assert result["autonomy"]["level"] == 2


def test_migration_includes_setup_cmd(tmp_path):
    """setup_cmd is migrated from PipelineConfig to the bootstrap delta."""
    config = _config(setup_cmd="alembic upgrade head")
    ensure_environment_bootstrap(config, tmp_path)
    env = load_environment(tmp_path)
    assert env.commands.setup == "alembic upgrade head"


def test_migration_includes_source_dirs(tmp_path):
    """User-set source_dirs are migrated to layout.source_dirs."""
    config = _config(source_dirs=["src/app", "src/lib"])
    ensure_environment_bootstrap(config, tmp_path)
    env = load_environment(tmp_path)
    assert env.layout.source_dirs == ["src/app", "src/lib"]


def test_migration_includes_test_dir(tmp_path):
    """User-set test_dir is migrated to layout.test_dirs."""
    config = _config(test_dir="spec")
    ensure_environment_bootstrap(config, tmp_path)
    env = load_environment(tmp_path)
    assert env.layout.test_dirs == ["spec"]


def test_migration_includes_server_env(tmp_path):
    """User-set server_env is migrated to server.env in the bootstrap delta."""
    config = _config(server_env={"DATABASE_URL": "postgres://localhost/db"})
    ensure_environment_bootstrap(config, tmp_path)
    env = load_environment(tmp_path)
    assert env.server.env == {"DATABASE_URL": "postgres://localhost/db"}


def test_strip_safety_guard_preserves_unmigrated_fields(tmp_path):
    """Fields not present in environment.toml are NOT stripped from config.toml."""
    # Create config.toml with a field that won't be migrated.
    _write_config_toml(tmp_path, {
        "core": {"language": "python", "scenario_test_cmd": "pytest -m scenario"},
    })
    # Create a minimal environment.toml without scenario_test.
    arch_dir = tmp_path / ".dark-factory" / "architecture"
    arch_dir.mkdir(parents=True, exist_ok=True)
    env_path = arch_dir / "environment.toml"
    env_path.write_text('[commands]\ntest = "pytest"\n', encoding="utf-8")

    _strip_operational_fields(tmp_path)

    import tomllib
    config_path = tmp_path / ".dark-factory" / "config.toml"
    result = tomllib.loads(config_path.read_text())
    # scenario_test_cmd kept because commands.scenario_test is empty in env.
    assert result["core"]["scenario_test_cmd"] == "pytest -m scenario"


def test_strip_idempotent(tmp_path):
    """Calling strip twice is a no-op the second time."""
    _write_config_toml(tmp_path, {
        "core": {"language": "node"},
        "gates": {"build_cmd": "pnpm build", "timeout": 300},
    })
    config = _config(build_cmd="pnpm build", language="node")
    ensure_environment_bootstrap(config, tmp_path)

    import tomllib
    config_path = tmp_path / ".dark-factory" / "config.toml"
    after_first = config_path.read_text()

    # Second call should not modify.
    assert _strip_operational_fields(tmp_path) is False
    assert config_path.read_text() == after_first


def test_strip_removes_empty_sections(tmp_path):
    """If all keys in a section are stripped, the section is removed."""
    _write_config_toml(tmp_path, {
        "core": {"language": "python"},
        "gates": {"build_cmd": "make", "test_cmd": "pytest"},
    })
    config = _config(build_cmd="make", test_cmd="pytest", language="python")
    ensure_environment_bootstrap(config, tmp_path)

    import tomllib
    config_path = tmp_path / ".dark-factory" / "config.toml"
    result = tomllib.loads(config_path.read_text())
    # [gates] had only operational fields → section removed.
    assert "gates" not in result


def test_strip_on_already_migrated_project(tmp_path):
    """Projects migrated by old code get stripped on next run."""
    # Simulate: env_path already exists, config.toml still has operational fields.
    config = _config(build_cmd="cargo build", test_cmd="cargo test", language="rust")
    ensure_environment_bootstrap(config, tmp_path)

    # Re-add operational fields to config.toml (simulating pre-Phase-5 state).
    _write_config_toml(tmp_path, {
        "core": {"language": "rust"},
        "gates": {"build_cmd": "cargo build", "test_cmd": "cargo test", "timeout": 600},
    })

    # Second call: env_path exists → returns False but still strips.
    config2 = _config(build_cmd="cargo build", test_cmd="cargo test", language="rust")
    result = ensure_environment_bootstrap(config2, tmp_path)
    assert result is False

    import tomllib
    config_path = tmp_path / ".dark-factory" / "config.toml"
    data = tomllib.loads(config_path.read_text())
    assert "build_cmd" not in data.get("gates", {})
    assert "test_cmd" not in data.get("gates", {})
    assert data["gates"]["timeout"] == 600


def test_strip_test_server_operational_fields(tmp_path):
    """[test_server] operational fields are stripped; timeouts are kept."""
    _write_config_toml(tmp_path, {
        "core": {"language": "node"},
        "test_server": {
            "server_cmd": "pnpm dev",
            "setup_cmd": "pnpm exec prisma db push",
            "health_path": "/healthz",
            "env": {"DATABASE_URL": "postgres://localhost/db"},
            "startup_timeout": 45,
            "browser_startup_timeout": 90,
        },
    })
    config = _config(
        test_server_cmd="pnpm dev",
        server_setup_cmd="pnpm exec prisma db push",
        server_health_path="/healthz",
        server_env={"DATABASE_URL": "postgres://localhost/db"},
        server_startup_timeout=45,
        language="node",
    )
    ensure_environment_bootstrap(config, tmp_path)

    import tomllib
    config_path = tmp_path / ".dark-factory" / "config.toml"
    result = tomllib.loads(config_path.read_text())
    ts = result.get("test_server", {})
    # Operational fields stripped.
    assert "server_cmd" not in ts
    assert "setup_cmd" not in ts
    assert "health_path" not in ts
    assert "env" not in ts
    # Tuning fields preserved.
    assert ts["startup_timeout"] == 45
    assert ts["browser_startup_timeout"] == 90
