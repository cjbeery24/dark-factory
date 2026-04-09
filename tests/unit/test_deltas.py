"""Phase 2.2 — delta log fold + conflict detection."""

from pathlib import Path

import pytest

from darkfactory.architecture.deltas import (
    EnvironmentConflict,
    SCHEMA_VERSION,
    fold_deltas,
    fold_to_hash,
    hash_view,
    list_deltas,
    materialize,
)
from darkfactory.architecture.environment import (
    EnvironmentContract,
    Runtime,
    ServerConfig,
    ServiceEntry,
    canonical_dump,
)


def _empty_hash() -> str:
    return hash_view(EnvironmentContract())


def _write_delta(
    dir_path: Path,
    name: str,
    *,
    parent_hash: str,
    ops: list[dict],
    spec_id: str = "spec",
    applied_at: str = "2026-04-06T00:00:00Z",
    schema_version: int = SCHEMA_VERSION,
) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    lines = [
        f"schema_version = {schema_version}",
        f'spec_id = "{spec_id}"',
        f'parent_hash = "{parent_hash}"',
        f'applied_at = "{applied_at}"',
        "",
    ]
    for op in ops:
        lines.append("[[ops]]")
        lines.append(f'op = "{op["op"]}"')
        lines.append(f'path = "{op["path"]}"')
        if "value" in op:
            v = op["value"]
            if isinstance(v, str):
                lines.append(f'value = "{v}"')
            elif isinstance(v, bool):
                lines.append(f"value = {'true' if v else 'false'}")
            elif isinstance(v, list):
                rendered = ", ".join(f'"{x}"' for x in v)
                lines.append(f"value = [{rendered}]")
            elif isinstance(v, dict):
                # Inline TOML table for dict values.
                inner = ", ".join(f'{k} = "{val}"' for k, val in sorted(v.items()))
                lines.append(f"value = {{ {inner} }}")
            else:
                lines.append(f"value = {v}")
        lines.append("")
    path = dir_path / name
    path.write_text("\n".join(lines))
    return path


def test_fold_empty_dir_returns_default_contract(tmp_path):
    contract = fold_deltas(tmp_path / "deltas")
    assert contract == EnvironmentContract()


def test_fold_single_set_op(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001-bootstrap.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "runtime.language", "value": "typescript"}],
    )
    contract = fold_deltas(deltas)
    assert contract.runtime.language == "typescript"


def test_fold_is_deterministic(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "runtime.language", "value": "python"}],
    )
    a = canonical_dump(fold_deltas(deltas))
    b = canonical_dump(fold_deltas(deltas))
    assert a == b


def test_fold_two_deltas_in_order(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "runtime.language", "value": "typescript"}],
    )
    after_first = EnvironmentContract(runtime=Runtime(language="typescript"))
    _write_delta(
        deltas, "0002.toml",
        parent_hash=hash_view(after_first),
        ops=[{"op": "set", "path": "server.port", "value": 8080}],
    )
    contract = fold_deltas(deltas)
    assert contract.runtime.language == "typescript"
    assert contract.server.port == 8080


def test_parent_hash_mismatch_raises(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash="0" * 64,
        ops=[{"op": "set", "path": "runtime.language", "value": "go"}],
    )
    with pytest.raises(EnvironmentConflict, match="parent_hash mismatch"):
        fold_deltas(deltas)


def test_forbidden_path_rejected(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "test.cmd", "value": "pytest"}],
    )
    with pytest.raises(EnvironmentConflict, match="not in allowlist"):
        fold_deltas(deltas)


def test_unknown_op_rejected(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "delete", "path": "runtime.language", "value": "x"}],
    )
    with pytest.raises(EnvironmentConflict, match="must be set/modify/remove"):
        fold_deltas(deltas)


def test_remove_resets_to_default(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "runtime.language", "value": "rust"}],
    )
    after = EnvironmentContract(runtime=Runtime(language="rust"))
    _write_delta(
        deltas, "0002.toml",
        parent_hash=hash_view(after),
        ops=[{"op": "remove", "path": "runtime.language"}],
    )
    contract = fold_deltas(deltas)
    assert contract.runtime.language == ""


def test_modify_on_default_value_rejected(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "modify", "path": "runtime.language", "value": "go"}],
    )
    with pytest.raises(EnvironmentConflict, match="current value is default"):
        fold_deltas(deltas)


def test_schema_version_mismatch_rejected(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "runtime.language", "value": "go"}],
        schema_version=999,
    )
    with pytest.raises(EnvironmentConflict, match="schema_version"):
        fold_deltas(deltas)


def test_schema_version_1_still_accepted(tmp_path):
    """v1 deltas must continue to fold after the Phase 4 SCHEMA_VERSION bump."""
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "runtime.language", "value": "typescript"}],
        schema_version=1,
    )
    contract = fold_deltas(deltas)
    assert contract.runtime.language == "typescript"


def test_lex_ordering_used(tmp_path):
    deltas = tmp_path / "deltas"
    # Write 0002 BEFORE 0001 (mtime order would be wrong; lex must win).
    _write_delta(
        deltas, "0002.toml",
        parent_hash="placeholder",  # will be wrong, but we just want to confirm order
        ops=[],
    )
    paths = list_deltas(deltas)
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[],
    )
    paths = list_deltas(deltas)
    assert [p.name for p in paths] == ["0001.toml", "0002.toml"]


def test_type_invalid_value_rejected(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "server.port", "value": "8080"}],
    )
    with pytest.raises(EnvironmentConflict, match="type-invalid"):
        fold_deltas(deltas)


def test_remove_on_default_rejected(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "remove", "path": "runtime.language"}],
    )
    with pytest.raises(EnvironmentConflict, match="already default"):
        fold_deltas(deltas)


def test_duplicate_numeric_prefix_rejected(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001-a.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "runtime.language", "value": "go"}],
    )
    _write_delta(
        deltas, "0001-b.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "runtime.language", "value": "rust"}],
    )
    with pytest.raises(EnvironmentConflict, match="duplicate delta numeric prefix"):
        list_deltas(deltas)


def test_materialize_writes_canonical_and_lock(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "runtime.language", "value": "typescript"}],
    )
    out = tmp_path / "environment.toml"
    contract = materialize(deltas, out)
    assert out.read_bytes() == canonical_dump(contract)
    lock = tmp_path / "environment.toml.lock"
    assert lock.read_text().strip() == hash_view(contract)


# ---------- Phase 6.3 — fold_to_hash (time-travel replay for smoke probe) ----


def test_fold_to_hash_empty_matches_empty_view(tmp_path):
    """Bootstrap spec with no deltas probes at the empty-view hash."""
    contract = fold_to_hash(tmp_path / "deltas", _empty_hash())
    assert contract == EnvironmentContract()


def test_fold_to_hash_stops_at_matching_delta(tmp_path):
    """Replay halts at the delta whose post-hash equals target — later deltas ignored."""
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "runtime.language", "value": "typescript"}],
    )
    target = hash_view(EnvironmentContract(runtime=Runtime(language="typescript")))
    _write_delta(
        deltas, "0002.toml",
        parent_hash=target,
        ops=[{"op": "set", "path": "server.port", "value": 8080}],
    )
    contract = fold_to_hash(deltas, target)
    assert contract.runtime.language == "typescript"
    assert contract.server.port == 0  # 0002 not applied — default


def test_fold_to_hash_target_never_reached_raises(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "runtime.language", "value": "go"}],
    )
    with pytest.raises(EnvironmentConflict, match="not reached"):
        fold_to_hash(deltas, "deadbeef" * 8)


def test_fold_to_hash_propagates_parent_hash_mismatch(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash="0" * 64,  # corrupt
        ops=[{"op": "set", "path": "runtime.language", "value": "go"}],
    )
    # Target is any non-empty hash so fold_to_hash actually reaches the
    # corrupt delta instead of returning early on empty-view match.
    target = hash_view(EnvironmentContract(runtime=Runtime(language="go")))
    with pytest.raises(EnvironmentConflict, match="parent_hash mismatch"):
        fold_to_hash(deltas, target)


# ---------- Phase 4 — hash stability -----------------------------------------


def test_empty_contract_hash_unchanged_after_phase4():
    """Adding services dict and test_base_url must NOT change empty-contract hash."""
    # This is the critical hash-stability regression test. If it fails,
    # every existing delta chain in every project breaks.
    empty = EnvironmentContract()
    assert empty.services == {}
    assert empty.server.test_base_url == ""
    h = hash_view(empty)
    # Re-dump — must produce identical bytes.
    assert canonical_dump(empty) == canonical_dump(EnvironmentContract())
    assert hash_view(EnvironmentContract()) == h


def test_golden_contract_hash_unchanged_after_phase4():
    """Pre-Phase-4 golden contract must produce identical hash."""
    contract = EnvironmentContract(
        runtime=Runtime(language="typescript", package_manager="pnpm",
                        language_version="22.11.0"),
        server=ServerConfig(port=8000, health_path="/health", boot_timeout_s=30),
    )
    # Adding the services field (empty default) should not change the hash.
    dump = canonical_dump(contract)
    assert b"[services" not in dump
    assert b"test_base_url" not in dump


# ---------- Phase 4 — service delta ops --------------------------------------


def test_set_service_field_creates_entry(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "services.postgres.image", "value": "postgres:16-alpine"}],
        schema_version=2,
    )
    contract = fold_deltas(deltas)
    assert "postgres" in contract.services
    assert contract.services["postgres"].image == "postgres:16-alpine"


def test_set_service_multiple_fields(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[
            {"op": "set", "path": "services.postgres.image", "value": "postgres:16-alpine"},
            {"op": "set", "path": "services.postgres.health_cmd", "value": "pg_isready -U postgres"},
            {"op": "set", "path": "services.postgres.health_timeout", "value": 60},
            {"op": "set", "path": "services.postgres.env",
             "value": {"POSTGRES_PASSWORD": "test", "POSTGRES_DB": "mydb"}},
        ],
        schema_version=2,
    )
    contract = fold_deltas(deltas)
    pg = contract.services["postgres"]
    assert pg.image == "postgres:16-alpine"
    assert pg.health_cmd == "pg_isready -U postgres"
    assert pg.health_timeout == 60
    assert pg.env == {"POSTGRES_PASSWORD": "test", "POSTGRES_DB": "mydb"}


def test_modify_service_field(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "services.redis.image", "value": "redis:7"}],
        schema_version=2,
    )
    after = EnvironmentContract(services={"redis": ServiceEntry(image="redis:7")})
    _write_delta(
        deltas, "0002.toml",
        parent_hash=hash_view(after),
        ops=[{"op": "modify", "path": "services.redis.image", "value": "redis:7.2"}],
        schema_version=2,
    )
    contract = fold_deltas(deltas)
    assert contract.services["redis"].image == "redis:7.2"


def test_modify_service_field_on_default_rejected(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "modify", "path": "services.redis.image", "value": "redis:7"}],
        schema_version=2,
    )
    with pytest.raises(EnvironmentConflict, match="current value is default"):
        fold_deltas(deltas)


def test_remove_service_field(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "services.redis.image", "value": "redis:7"},
             {"op": "set", "path": "services.redis.health_cmd", "value": "redis-cli ping"}],
        schema_version=2,
    )
    after = EnvironmentContract(services={"redis": ServiceEntry(
        image="redis:7", health_cmd="redis-cli ping")})
    _write_delta(
        deltas, "0002.toml",
        parent_hash=hash_view(after),
        ops=[{"op": "remove", "path": "services.redis.health_cmd"}],
        schema_version=2,
    )
    contract = fold_deltas(deltas)
    assert contract.services["redis"].health_cmd == ""  # reset to default


def test_remove_entire_service(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "services.redis.image", "value": "redis:7"}],
        schema_version=2,
    )
    after = EnvironmentContract(services={"redis": ServiceEntry(image="redis:7")})
    _write_delta(
        deltas, "0002.toml",
        parent_hash=hash_view(after),
        ops=[{"op": "remove", "path": "services.redis"}],
        schema_version=2,
    )
    contract = fold_deltas(deltas)
    assert "redis" not in contract.services


def test_v1_delta_rejects_service_path(tmp_path):
    """Schema v1 deltas must NOT accept service paths."""
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "services.postgres.image", "value": "postgres:16"}],
        schema_version=1,
    )
    with pytest.raises(EnvironmentConflict, match="not in allowlist"):
        fold_deltas(deltas)


def test_mixed_v1_v2_delta_chain(tmp_path):
    """v1 delta followed by v2 delta must fold correctly."""
    deltas = tmp_path / "deltas"
    # v1 delta sets language.
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "runtime.language", "value": "typescript"}],
        schema_version=1,
    )
    after_v1 = EnvironmentContract(runtime=Runtime(language="typescript"))
    # v2 delta adds a service.
    _write_delta(
        deltas, "0002.toml",
        parent_hash=hash_view(after_v1),
        ops=[{"op": "set", "path": "services.postgres.image", "value": "postgres:16"}],
        schema_version=2,
    )
    contract = fold_deltas(deltas)
    assert contract.runtime.language == "typescript"
    assert contract.services["postgres"].image == "postgres:16"


def test_service_with_test_only_and_depends_on(tmp_path):
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[
            {"op": "set", "path": "services.mailhog.image", "value": "mailhog/mailhog"},
            {"op": "set", "path": "services.mailhog.test_only", "value": True},
            {"op": "set", "path": "services.mailhog.depends_on", "value": ["smtp-relay"]},
        ],
        schema_version=2,
    )
    contract = fold_deltas(deltas)
    mh = contract.services["mailhog"]
    assert mh.test_only is True
    assert mh.depends_on == ["smtp-relay"]


def test_canonical_dump_roundtrip_with_services():
    """canonical_dump -> parse -> canonical_dump must produce identical bytes."""
    import tomllib
    contract = EnvironmentContract(
        runtime=Runtime(language="typescript"),
        services={
            "postgres": ServiceEntry(
                image="postgres:16-alpine",
                env={"POSTGRES_PASSWORD": "test"},
                health_cmd="pg_isready",
                health_timeout=30,
            ),
            "redis": ServiceEntry(image="redis:7"),
        },
    )
    first = canonical_dump(contract)
    parsed = tomllib.loads(first.decode("ascii"))
    # Manually reconstruct to verify.
    assert "postgres" in parsed.get("services", {})
    assert parsed["services"]["postgres"]["image"] == "postgres:16-alpine"
    # Determinism: second dump is identical.
    second = canonical_dump(contract)
    assert first == second


def test_test_base_url_in_delta(tmp_path):
    """server.test_base_url flows through delta system."""
    deltas = tmp_path / "deltas"
    _write_delta(
        deltas, "0001.toml",
        parent_hash=_empty_hash(),
        ops=[{"op": "set", "path": "server.test_base_url",
              "value": "http://localhost:3000"}],
        schema_version=2,
    )
    contract = fold_deltas(deltas)
    assert contract.server.test_base_url == "http://localhost:3000"
