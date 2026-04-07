"""Phase 3.3 — manifest.runtime → environment delta transformation."""

from pathlib import Path

import pytest

from darkfactory.architecture.amend import (
    AmendmentRejected,
    amend_environment_from_manifest,
)
from darkfactory.architecture.deltas import (
    EnvironmentConflict,
    list_deltas,
)
from darkfactory.architecture.environment import EnvironmentContract
from darkfactory.architecture.runtime import _reset_cache, load_environment
from darkfactory.spec.manifest import RuntimeDecl


@pytest.fixture(autouse=True)
def _clean():
    _reset_cache()
    yield
    _reset_cache()


_LEGACY_FIELD_TO_PATH = {
    "server_setup_cmd":      "commands.install",
    "test_server_cmd":       "commands.server_start",
    "server_health_path":    "server.health_path",
    "server_startup_timeout": "server.boot_timeout_s",
}


def _rt(**kw) -> RuntimeDecl:
    """Build a RuntimeDecl from legacy flat kwargs by translating into
    ``amendment_ops`` — Phase 3.3 fix moved the source of truth there."""
    ops = dict(kw.pop("amendment_ops", {}))
    for legacy, path in _LEGACY_FIELD_TO_PATH.items():
        if legacy in kw:
            value = kw.pop(legacy)
            if value in ("", 0, None):
                continue
            ops[path] = value
    rt = RuntimeDecl(**kw)
    rt.amendment_ops = ops
    # Project back to legacy flat fields so callers reading rt.test_server_cmd
    # in their assertions still see the value.
    for path, value in ops.items():
        for legacy, p in _LEGACY_FIELD_TO_PATH.items():
            if p == path:
                setattr(rt, legacy, value)
    return rt


def test_empty_runtime_returns_none(tmp_path):
    out = amend_environment_from_manifest(
        _rt(), spec_id="2026-04-07-foo",
        spec_type="feature", project_dir=tmp_path,
    )
    assert out is None
    # No deltas dir should be created when there's nothing to write.
    assert not (tmp_path / ".dark-factory" / "architecture" / "environment.deltas").exists()


def test_writes_delta_for_feature_with_command_amendments(tmp_path):
    out = amend_environment_from_manifest(
        _rt(test_server_cmd="pnpm dev", server_health_path="/healthz"),
        spec_id="2026-04-07-foo",
        spec_type="feature",
        project_dir=tmp_path,
    )
    assert out is not None
    assert out.name == "0001-2026-04-07-foo.toml"
    text = out.read_text()
    assert 'path = "commands.server_start"' in text
    assert 'value = "pnpm dev"' in text
    assert 'path = "server.health_path"' in text


def test_materializes_environment_toml(tmp_path):
    amend_environment_from_manifest(
        _rt(test_server_cmd="pnpm dev"),
        spec_id="2026-04-07-foo",
        spec_type="feature",
        project_dir=tmp_path,
    )
    env = load_environment(tmp_path)
    assert env.commands.server_start == "pnpm dev"


def test_increments_delta_number(tmp_path):
    amend_environment_from_manifest(
        _rt(test_server_cmd="pnpm dev"), spec_id="a",
        spec_type="feature", project_dir=tmp_path,
    )
    out2 = amend_environment_from_manifest(
        _rt(server_health_path="/x"), spec_id="b",
        spec_type="feature", project_dir=tmp_path,
    )
    assert out2.name.startswith("0002-")


def test_bootstrap_allows_full_amendment(tmp_path):
    # No bootstrap-only fields are reachable through RuntimeDecl in the
    # current schema, so this only proves bootstrap accepts the amendment
    # without raising. (runtime.language / layout.source_dirs route
    # through ArchitectureManifest.language and file_map respectively.)
    out = amend_environment_from_manifest(
        _rt(test_server_cmd="pnpm dev", server_startup_timeout=45),
        spec_id="2026-04-07-bootstrap",
        spec_type="bootstrap",
        project_dir=tmp_path,
    )
    assert out is not None
    env = load_environment(tmp_path)
    assert env.commands.server_start == "pnpm dev"
    assert env.server.boot_timeout_s == 45


def test_filename_slugifies_unsafe_chars(tmp_path):
    out = amend_environment_from_manifest(
        _rt(test_server_cmd="x"),
        spec_id="2026/04/07 foo!",
        spec_type="feature",
        project_dir=tmp_path,
    )
    assert "/" not in out.name
    assert " " not in out.name
    assert "!" not in out.name


def test_skips_zero_int_and_empty_string(tmp_path):
    # server_startup_timeout = 0 should NOT be migrated; empty string
    # commands likewise.
    out = amend_environment_from_manifest(
        _rt(test_server_cmd="", server_startup_timeout=0, server_health_path="/h"),
        spec_id="x", spec_type="feature", project_dir=tmp_path,
    )
    text = out.read_text()
    assert "commands.server_start" not in text  # empty
    assert "server.boot_timeout_s" not in text  # zero
    assert "server.health_path" in text         # set


def test_chains_parent_hash(tmp_path):
    """Two sequential deltas: second's parent_hash must match the
    materialized view AFTER the first delta. fold_deltas during the
    second amendment should produce that hash."""
    amend_environment_from_manifest(
        _rt(test_server_cmd="pnpm dev"), spec_id="a",
        spec_type="feature", project_dir=tmp_path,
    )
    second = amend_environment_from_manifest(
        _rt(server_health_path="/healthz"), spec_id="b",
        spec_type="feature", project_dir=tmp_path,
    )
    # If the chain were broken, the second materialize() inside the
    # amendment call would have raised EnvironmentConflict. Reaching here
    # is the assertion. Re-load to confirm both ops are present.
    env = load_environment(tmp_path)
    assert env.commands.server_start == "pnpm dev"
    assert env.server.health_path == "/healthz"


def test_returns_none_and_materializes_existing_for_empty_runtime_with_prior_deltas(tmp_path):
    """An empty runtime block is the common case for feature specs. It
    must NOT write a delta, but it MUST keep environment.toml in sync
    with the existing log (defends against half-migration recovery)."""
    amend_environment_from_manifest(
        _rt(test_server_cmd="pnpm dev"), spec_id="a",
        spec_type="feature", project_dir=tmp_path,
    )
    env_path = tmp_path / ".dark-factory" / "architecture" / "environment.toml"
    env_path.unlink()  # simulate half-migration

    out = amend_environment_from_manifest(
        _rt(), spec_id="b",
        spec_type="feature", project_dir=tmp_path,
    )
    assert out is None
    assert env_path.exists()
    env = load_environment(tmp_path)
    assert env.commands.server_start == "pnpm dev"


def test_rollback_restores_environment_toml_on_materialize_failure(tmp_path, monkeypatch):
    """If materialize raises after the delta has been written, the
    pre-existing environment.toml must be restored byte-for-byte AND the
    new delta must be removed. The next run must see exactly the prior
    state — no half-applied amendment."""
    # Establish a baseline environment.toml + 0001 delta.
    amend_environment_from_manifest(
        _rt(test_server_cmd="pnpm dev"), spec_id="baseline",
        spec_type="feature", project_dir=tmp_path,
    )
    env_path = tmp_path / ".dark-factory" / "architecture" / "environment.toml"
    deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
    baseline_bytes = env_path.read_bytes()
    baseline_delta_count = len(list_deltas(deltas_dir))

    # Force materialize to fail on the next call.
    import darkfactory.architecture.amend as amend_mod
    original = amend_mod.materialize
    def boom(*a, **kw):
        # Simulate a partial write before raising.
        env_path.write_bytes(b"corrupted partial write")
        raise EnvironmentConflict("synthetic")
    monkeypatch.setattr(amend_mod, "materialize", boom)

    with pytest.raises(EnvironmentConflict):
        amend_environment_from_manifest(
            _rt(server_health_path="/healthz"), spec_id="will-fail",
            spec_type="feature", project_dir=tmp_path,
        )

    # environment.toml restored to the exact pre-call bytes.
    assert env_path.read_bytes() == baseline_bytes
    # The new delta was rolled back.
    assert len(list_deltas(deltas_dir)) == baseline_delta_count


def test_rollback_removes_environment_toml_when_baseline_was_absent(tmp_path, monkeypatch):
    """If there was no environment.toml before the call (first amendment
    on a fresh project) and materialize fails, environment.toml must NOT
    exist after rollback — even if the failed materialize wrote a stub."""
    env_path = tmp_path / ".dark-factory" / "architecture" / "environment.toml"
    assert not env_path.exists()

    import darkfactory.architecture.amend as amend_mod
    def boom(*a, **kw):
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_bytes(b"stub")
        raise EnvironmentConflict("synthetic")
    monkeypatch.setattr(amend_mod, "materialize", boom)

    with pytest.raises(EnvironmentConflict):
        amend_environment_from_manifest(
            _rt(test_server_cmd="x"), spec_id="first",
            spec_type="feature", project_dir=tmp_path,
        )

    assert not env_path.exists()


def test_architect_yaml_roundtrip_to_environment_toml(tmp_path):
    """Phase 3.3 regression: literal architect-emitted manifest YAML
    must parse → produce a non-empty amendment_ops → write a delta →
    materialize an environment.toml that contains the architect's values.

    This is the test that would have caught the parser/amend mismatch
    surfaced by the budget-brain-v2 Phase 3 exit-gate run.
    """
    from darkfactory.spec.manifest import parse_manifest

    yaml_text = """
version: 1
spec: 2026-03-28-api-bootstrap
spec_hash: "deadbeef"
language: node

file_map:
  - path: apps/api/src/server.ts
    action: create
    purpose: "Fastify server bootstrap"
    satisfies: [FR-1]

runtime:
  language: node
  package_manager: pnpm
  commands:
    build: "pnpm install && pnpm run build"
    test: "pnpm test"
    server_start: "cd apps/api && pnpm dev"
    typecheck: "pnpm -r typecheck"
    lint: "pnpm exec eslint ."
  server:
    health_path: "/health"
    port: 3001
    boot_timeout_s: 30
"""
    parse_result = parse_manifest(yaml_text)
    assert parse_result.errors == [], f"parse errors: {parse_result.errors}"
    assert parse_result.manifest is not None

    rt = parse_result.manifest.runtime
    # Every architect-emitted scalar must land in amendment_ops under its
    # canonical env-contract path.
    expected_paths = {
        "runtime.language":        "node",
        "runtime.package_manager": "pnpm",
        "commands.build":          "pnpm install && pnpm run build",
        "commands.test":           "pnpm test",
        "commands.server_start":   "cd apps/api && pnpm dev",
        "commands.typecheck":      "pnpm -r typecheck",
        "commands.lint":           "pnpm exec eslint .",
        "server.health_path":      "/health",
        "server.port":             3001,
        "server.boot_timeout_s":   30,
    }
    assert rt.amendment_ops == expected_paths

    # Now feed it through amend → materialize.
    delta_path = amend_environment_from_manifest(
        rt,
        spec_id="2026-03-28-api-bootstrap",
        spec_type="bootstrap",
        project_dir=tmp_path,
    )
    assert delta_path is not None
    assert delta_path.name.startswith("0001-")

    env_path = tmp_path / ".dark-factory" / "architecture" / "environment.toml"
    env = load_environment(tmp_path)
    assert env.commands.server_start == "cd apps/api && pnpm dev"
    assert env.commands.build == "pnpm install && pnpm run build"
    assert env.commands.lint == "pnpm exec eslint ."
    assert env.commands.typecheck == "pnpm -r typecheck"
    assert env.commands.test == "pnpm test"
    assert env.server.health_path == "/health"
    assert env.server.port == 3001
    assert env.server.boot_timeout_s == 30
    assert env.runtime.language == "node"
    assert env.runtime.package_manager == "pnpm"


def test_architect_yaml_feature_rejects_bootstrap_only_paths(tmp_path):
    """A feature spec emitting runtime.language must be rejected."""
    from darkfactory.spec.manifest import parse_manifest

    yaml_text = """
version: 1
spec: 2026-04-07-some-feature
spec_hash: "deadbeef"
language: node

file_map:
  - path: src/x.ts
    action: create
    purpose: "x"
    satisfies: [FR-1]

runtime:
  language: node
  commands:
    server_start: "pnpm dev"
"""
    parse_result = parse_manifest(yaml_text)
    assert parse_result.errors == []
    rt = parse_result.manifest.runtime
    assert "runtime.language" in rt.amendment_ops

    with pytest.raises(AmendmentRejected):
        amend_environment_from_manifest(
            rt,
            spec_id="2026-04-07-some-feature",
            spec_type="feature",
            project_dir=tmp_path,
        )


def test_architect_yaml_unknown_path_rejected_at_parse():
    """An unknown amendment path must be a parse error, not silently dropped."""
    from darkfactory.spec.manifest import parse_manifest

    yaml_text = """
version: 1
spec: x
spec_hash: "x"
language: node
file_map:
  - {path: x, action: create, purpose: x, satisfies: [FR-1]}
runtime:
  commands:
    bogus_command: "should not be allowed"
"""
    parse_result = parse_manifest(yaml_text)
    assert any("commands.bogus_command" in e for e in parse_result.errors)


def test_only_one_delta_after_three_calls(tmp_path):
    """Counts of files in the deltas dir match the number of non-empty
    amendments — empty amendments must not leave stray files."""
    amend_environment_from_manifest(
        _rt(test_server_cmd="x"), spec_id="a",
        spec_type="feature", project_dir=tmp_path,
    )
    amend_environment_from_manifest(
        _rt(), spec_id="b",
        spec_type="feature", project_dir=tmp_path,
    )
    amend_environment_from_manifest(
        _rt(), spec_id="c",
        spec_type="feature", project_dir=tmp_path,
    )
    deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
    assert len(list_deltas(deltas_dir)) == 1
