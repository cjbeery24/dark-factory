"""Phase 2.3 — load_environment / refresh_config_from_environment / step_begin."""

from pathlib import Path

import pytest

from darkfactory.architecture.deltas import materialize
from darkfactory.architecture.environment import (
    Commands,
    EnvironmentContract,
    Layout,
    Runtime,
    ServerConfig,
    canonical_dump,
)
from darkfactory.architecture.runtime import (
    _registry_snapshot,
    _reset_cache,
    _reset_registry,
    load_environment,
    refresh_config_from_environment,
    step_begin,
)
from darkfactory.models.config import PipelineConfig


@pytest.fixture(autouse=True)
def _clean_state():
    _reset_cache()
    _reset_registry()
    yield
    _reset_cache()
    _reset_registry()


# ----- load_environment ------------------------------------------------------


def test_load_environment_missing_returns_default(tmp_path):
    contract = load_environment(tmp_path)
    assert contract == EnvironmentContract()


def test_load_environment_parses_materialized_view(tmp_path):
    arch = tmp_path / ".dark-factory" / "architecture"
    env = EnvironmentContract(
        runtime=Runtime(language="typescript", package_manager="pnpm"),
        commands=Commands(server_start="pnpm dev", test="pnpm test"),
        server=ServerConfig(port=8080, health_path="/healthz", boot_timeout_s=45),
    )
    arch.mkdir(parents=True)
    (arch / "environment.toml").write_bytes(canonical_dump(env))
    loaded = load_environment(tmp_path)
    assert loaded == env


def test_load_environment_caches_by_mtime(tmp_path):
    arch = tmp_path / ".dark-factory" / "architecture"
    arch.mkdir(parents=True)
    env = EnvironmentContract(commands=Commands(test="v1"))
    (arch / "environment.toml").write_bytes(canonical_dump(env))
    a = load_environment(tmp_path)
    b = load_environment(tmp_path)
    assert a is b  # cached identity


def test_load_environment_invalidates_on_mtime_change(tmp_path):
    arch = tmp_path / ".dark-factory" / "architecture"
    arch.mkdir(parents=True)
    path = arch / "environment.toml"
    path.write_bytes(canonical_dump(EnvironmentContract(commands=Commands(test="v1"))))
    first = load_environment(tmp_path)
    # Bump mtime by writing new content. Sleep-free: mkstemp + replace makes
    # the new mtime distinct on every modern fs at nanosecond resolution.
    import time
    time.sleep(0.01)
    path.write_bytes(canonical_dump(EnvironmentContract(commands=Commands(test="v2"))))
    second = load_environment(tmp_path)
    assert first.commands.test == "v1"
    assert second.commands.test == "v2"


def test_load_environment_corrupt_file_degrades_to_default(tmp_path):
    arch = tmp_path / ".dark-factory" / "architecture"
    arch.mkdir(parents=True)
    (arch / "environment.toml").write_bytes(b"this is not toml [[[")
    loaded = load_environment(tmp_path)
    assert loaded == EnvironmentContract()


def test_load_environment_drops_type_mismatches(tmp_path):
    arch = tmp_path / ".dark-factory" / "architecture"
    arch.mkdir(parents=True)
    (arch / "environment.toml").write_text(
        '[server]\nport = "not-an-int"\nhealth_path = "/h"\n'
    )
    loaded = load_environment(tmp_path)
    assert loaded.server.port == 0  # dropped
    assert loaded.server.health_path == "/h"


# ----- refresh_config_from_environment ---------------------------------------


def test_refresh_overlays_non_empty_commands():
    config = PipelineConfig(test_cmd="OLD", build_cmd="OLD")
    env = EnvironmentContract(commands=Commands(test="NEW", build="NEW2"))
    refresh_config_from_environment(config, env)
    assert config.test_cmd == "NEW"
    assert config.build_cmd == "NEW2"


def test_refresh_skips_empty_env_values():
    config = PipelineConfig(test_cmd="KEEP")
    env = EnvironmentContract(commands=Commands())  # all empty
    refresh_config_from_environment(config, env)
    assert config.test_cmd == "KEEP"


def test_refresh_overlays_setup_cmd():
    """Phase 8: commands.setup flows through to config.setup_cmd."""
    config = PipelineConfig()
    env = EnvironmentContract(commands=Commands(setup="npx drizzle-kit push"))
    refresh_config_from_environment(config, env)
    assert config.setup_cmd == "npx drizzle-kit push"


def test_refresh_overlays_pre_setup_cmds():
    """Phase 3/2026-04-24: commands.pre_setup flows through to config.pre_setup_cmds."""
    config = PipelineConfig()
    env = EnvironmentContract(commands=Commands(pre_setup=[
        "mkdir -p apps/api/src/db/migrations/meta",
        "echo 'seed' > apps/api/src/db/migrations/meta/_journal.json",
    ]))
    refresh_config_from_environment(config, env)
    assert config.pre_setup_cmds == [
        "mkdir -p apps/api/src/db/migrations/meta",
        "echo 'seed' > apps/api/src/db/migrations/meta/_journal.json",
    ]
    # Fresh copy (not aliased to the env contract's list).
    assert config.pre_setup_cmds is not env.commands.pre_setup


def test_refresh_empty_pre_setup_does_not_clobber():
    config = PipelineConfig(pre_setup_cmds=["pre-existing"])
    env = EnvironmentContract(commands=Commands(pre_setup=[]))
    refresh_config_from_environment(config, env)
    assert config.pre_setup_cmds == ["pre-existing"]


def test_refresh_overlays_server_fields():
    config = PipelineConfig()
    env = EnvironmentContract(
        server=ServerConfig(health_path="/health", boot_timeout_s=60),
    )
    refresh_config_from_environment(config, env)
    assert config.server_health_path == "/health"
    assert config.server_startup_timeout == 60


def test_refresh_zero_int_does_not_clobber():
    config = PipelineConfig(server_startup_timeout=30)
    env = EnvironmentContract(server=ServerConfig(boot_timeout_s=0))
    refresh_config_from_environment(config, env)
    assert config.server_startup_timeout == 30


def test_refresh_source_dirs_when_layout_set():
    config = PipelineConfig(source_dirs=["src"])
    env = EnvironmentContract(layout=Layout(source_dirs=["apps/api/src", "packages"]))
    refresh_config_from_environment(config, env)
    assert config.source_dirs == ["apps/api/src", "packages"]


def test_refresh_resurrection_documented_behavior():
    """Pin the documented Phase 2.3 behavior: empty env values let
    config.toml resurrect. This is a sharp edge — see runtime.py module
    docstring 'No "unset" semantics in Phase 2.3'. If someone changes
    this, they need to read that note first."""
    config = PipelineConfig(test_cmd="from-config-toml")
    # Env was previously set, then "cleared" — which the contract represents
    # as the empty default.
    env = EnvironmentContract(commands=Commands(test=""))
    refresh_config_from_environment(config, env)
    assert config.test_cmd == "from-config-toml"  # NOT ""


def test_refresh_empty_layout_does_not_clobber_source_dirs():
    config = PipelineConfig(source_dirs=["src"])
    env = EnvironmentContract()  # empty layout
    refresh_config_from_environment(config, env)
    assert config.source_dirs == ["src"]


# ----- step_begin ------------------------------------------------------------


def test_step_begin_records_registry(tmp_path):
    config = PipelineConfig()
    step_begin("spec-1", "implementer", config, tmp_path)
    snap = _registry_snapshot()
    assert any(k.spec_id == "spec-1" and k.step_id == "implementer" for k in snap)


def test_step_begin_refreshes_config(tmp_path):
    arch = tmp_path / ".dark-factory" / "architecture"
    arch.mkdir(parents=True)
    env = EnvironmentContract(commands=Commands(test="pnpm test"))
    (arch / "environment.toml").write_bytes(canonical_dump(env))
    config = PipelineConfig(test_cmd="OLD")
    out = step_begin("spec-1", "gate", config, tmp_path)
    assert out is config  # mutated in place
    assert config.test_cmd == "pnpm test"


def test_step_begin_two_cycles_clears_prior_entry(tmp_path):
    config = PipelineConfig()
    step_begin("spec-1", "iter", config, tmp_path)
    snap1 = _registry_snapshot()
    step_begin("spec-1", "iter", config, tmp_path)
    snap2 = _registry_snapshot()
    # Same key — both snapshots have exactly one entry under it.
    assert len(snap1) == 1
    assert len(snap2) == 1
    # The prior entry was deleted then re-recorded — counter resets to 1.
    assert list(snap2.values())[0] == 1


def test_step_begin_with_no_environment_file_is_a_noop(tmp_path):
    config = PipelineConfig(test_cmd="UNCHANGED")
    step_begin("spec-1", "gate", config, tmp_path)
    assert config.test_cmd == "UNCHANGED"


def test_step_begin_after_materialize_picks_up_delta(tmp_path):
    from darkfactory.architecture.deltas import hash_view
    deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
    deltas_dir.mkdir(parents=True)
    parent = hash_view(EnvironmentContract())
    (deltas_dir / "0001-bootstrap.toml").write_text(
        f'schema_version = 1\n'
        f'spec_id = "bootstrap"\n'
        f'parent_hash = "{parent}"\n'
        f'applied_at = "2026-04-06T00:00:00Z"\n'
        f'\n[[ops]]\nop = "set"\npath = "commands.test"\nvalue = "pnpm test"\n'
    )
    out_path = tmp_path / ".dark-factory" / "architecture" / "environment.toml"
    materialize(deltas_dir, out_path)

    config = PipelineConfig(test_cmd="OLD")
    step_begin("spec-1", "gate", config, tmp_path)
    assert config.test_cmd == "pnpm test"
