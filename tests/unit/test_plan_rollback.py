"""Phase 6.5 — plan rollback tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from darkfactory.architecture.deltas import hash_view
from darkfactory.architecture.environment import EnvironmentContract, Runtime
from darkfactory.plan.rollback import (
    RollbackError,
    load_rolled_back,
    rollback_log_path,
    rollback_plan_to,
)
from darkfactory.plan.state import ShippedSpec, append_shipped


def _ship(project: Path, spec_id: str, *, delta: str | None = None, env_hash: str = "h") -> None:
    append_shipped(
        project,
        ShippedSpec(
            spec_id=spec_id, spec_type="feature",
            shipped_at=datetime.now(timezone.utc),
            env_hash_at_ship=env_hash,
            delta_filename=delta,
            scenario_archive_path=f".dark-factory/shipped-scenarios/{spec_id}",
            spec_origin=spec_id,
        ),
    )


def _write_delta(deltas_dir: Path, name: str, *, parent_hash: str, path: str, value: str) -> None:
    deltas_dir.mkdir(parents=True, exist_ok=True)
    body = (
        "schema_version = 1\n"
        f'spec_id = "x"\n'
        f'parent_hash = "{parent_hash}"\n'
        'applied_at = "2026-04-07T00:00:00Z"\n\n'
        "[[ops]]\n"
        'op = "set"\n'
        f'path = "{path}"\n'
        f'value = "{value}"\n'
    )
    (deltas_dir / name).write_text(body)


def test_rollback_target_not_in_shipped_raises(tmp_path: Path) -> None:
    _ship(tmp_path, "alpha")
    with pytest.raises(RollbackError, match="not in shipped.jsonl"):
        rollback_plan_to(tmp_path, "nonexistent")


def test_rollback_to_target_inclusive(tmp_path: Path) -> None:
    """Rolling back 'to' alpha keeps alpha, removes beta and gamma."""
    empty = hash_view(EnvironmentContract())
    deltas = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"

    # Seed three deltas: alpha→ts, beta→port 8080, gamma→host "x"
    _write_delta(deltas, "0001-alpha.toml", parent_hash=empty,
                 path="runtime.language", value="typescript")
    after_a = hash_view(EnvironmentContract(runtime=Runtime(language="typescript")))
    _ship(tmp_path, "alpha", delta="0001-alpha.toml", env_hash=after_a)

    # beta's delta needs an int value — rewrite manually
    body = (
        "schema_version = 1\n"
        'spec_id = "beta"\n'
        f'parent_hash = "{after_a}"\n'
        'applied_at = "2026-04-07T00:00:00Z"\n\n'
        "[[ops]]\n"
        'op = "set"\n'
        'path = "server.port"\n'
        "value = 8080\n"
    )
    (deltas / "0002-beta.toml").write_text(body)
    contract_b = EnvironmentContract(runtime=Runtime(language="typescript"))
    contract_b.server.port = 8080
    after_b = hash_view(contract_b)
    _ship(tmp_path, "beta", delta="0002-beta.toml", env_hash=after_b)

    _write_delta(deltas, "0003-gamma.toml", parent_hash=after_b,
                 path="server.host", value="example.com")
    _ship(tmp_path, "gamma", delta="0003-gamma.toml", env_hash="gh")

    # Roll back to alpha.
    result = rollback_plan_to(tmp_path, "alpha")

    assert result.target == "alpha"
    assert result.rolled_back_specs == ["beta", "gamma"]
    assert set(result.removed_deltas) == {"0002-beta.toml", "0003-gamma.toml"}
    assert result.new_env_hash == after_a  # rematerialized to alpha state

    # Delta files removed from disk.
    assert not (deltas / "0002-beta.toml").exists()
    assert not (deltas / "0003-gamma.toml").exists()
    assert (deltas / "0001-alpha.toml").exists()

    # Rollback log written.
    assert rollback_log_path(tmp_path).exists()
    assert load_rolled_back(tmp_path) == {"beta", "gamma"}


def test_rollback_dry_run_does_not_mutate(tmp_path: Path) -> None:
    empty = hash_view(EnvironmentContract())
    deltas = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
    _write_delta(deltas, "0001-alpha.toml", parent_hash=empty,
                 path="runtime.language", value="go")
    _ship(tmp_path, "alpha", delta="0001-alpha.toml",
          env_hash=hash_view(EnvironmentContract(runtime=Runtime(language="go"))))
    _write_delta(
        deltas, "0002-beta.toml",
        parent_hash=hash_view(EnvironmentContract(runtime=Runtime(language="go"))),
        path="runtime.version", value="1.22",
    )
    _ship(tmp_path, "beta", delta="0002-beta.toml", env_hash="h2")

    result = rollback_plan_to(tmp_path, "alpha", dry_run=True)
    assert result.rolled_back_specs == ["beta"]
    assert result.removed_deltas == ["0002-beta.toml"]
    # Nothing actually removed.
    assert (deltas / "0002-beta.toml").exists()
    assert not rollback_log_path(tmp_path).exists()


def test_rollback_refuses_when_delta_missing(tmp_path: Path) -> None:
    """Half-applied rollback would be worse than no rollback."""
    _ship(tmp_path, "alpha")
    _ship(tmp_path, "beta", delta="0002-beta.toml")  # delta never actually written
    with pytest.raises(RollbackError, match="not found"):
        rollback_plan_to(tmp_path, "alpha")


def test_rollback_skips_already_rolled_back(tmp_path: Path) -> None:
    """A second rollback of the same range should be a no-op on the rolled-back rows."""
    empty = hash_view(EnvironmentContract())
    deltas = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
    _write_delta(deltas, "0001-alpha.toml", parent_hash=empty,
                 path="runtime.language", value="rust")
    after_a = hash_view(EnvironmentContract(runtime=Runtime(language="rust")))
    _ship(tmp_path, "alpha", delta="0001-alpha.toml", env_hash=after_a)
    _write_delta(deltas, "0002-beta.toml", parent_hash=after_a,
                 path="runtime.version", value="1.80")
    _ship(tmp_path, "beta", delta="0002-beta.toml", env_hash="h")

    rollback_plan_to(tmp_path, "alpha")
    # Second rollback — beta is already rolled back; nothing more to do.
    result2 = rollback_plan_to(tmp_path, "alpha")
    assert result2.rolled_back_specs == []
    assert result2.removed_deltas == []


def test_rollback_committed_record_and_load_shipped_filters(tmp_path: Path) -> None:
    """After rollback, load_shipped() must not return rolled-back specs unless asked."""
    from darkfactory.plan.state import load_shipped

    _ship(tmp_path, "alpha")
    _ship(tmp_path, "beta")
    _ship(tmp_path, "gamma")

    rollback_plan_to(tmp_path, "alpha")

    # Default load: rolled-back specs filtered.
    current = load_shipped(tmp_path)
    assert [r.spec_id for r in current] == ["alpha"]

    # Raw history: all three still visible for audit.
    raw = load_shipped(tmp_path, include_rolled_back=True)
    assert [r.spec_id for r in raw] == ["alpha", "beta", "gamma"]


def test_rollback_writes_pending_then_committed(tmp_path: Path) -> None:
    """Two-phase commit: rollbacks.jsonl gets pending + committed records."""
    import json as _json

    _ship(tmp_path, "alpha")
    _ship(tmp_path, "beta")

    rollback_plan_to(tmp_path, "alpha")

    log_content = rollback_log_path(tmp_path).read_text().strip().splitlines()
    statuses = [_json.loads(line)["status"] for line in log_content]
    assert statuses == ["pending", "committed"]


def test_rollback_no_deltas_on_feature_specs(tmp_path: Path) -> None:
    """Rolling back pure-feature specs (no delta_filename) just records the rollback."""
    _ship(tmp_path, "alpha")
    _ship(tmp_path, "beta")   # no delta
    _ship(tmp_path, "gamma")  # no delta

    result = rollback_plan_to(tmp_path, "alpha")
    assert result.rolled_back_specs == ["beta", "gamma"]
    assert result.removed_deltas == []
    assert load_rolled_back(tmp_path) == {"beta", "gamma"}
