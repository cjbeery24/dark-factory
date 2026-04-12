"""Phase 6.3 — union smoke probe tests.

Stubs run_scenarios and seeds shipped.jsonl + deltas directly so the
probe's orchestration logic (stranded vs regressed vs passed) is
exercised without a real scenario run.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from darkfactory.architecture.deltas import hash_view
from darkfactory.architecture.environment import EnvironmentContract, Runtime
from darkfactory.models.config import PipelineConfig
from darkfactory.plan.smoke_probe import probe_prior_ships
from darkfactory.plan.state import ShippedSpec, append_shipped
from darkfactory.utils.subprocess_runner import RunResult


def _seed_ship(
    project: Path, spec_id: str, env_hash: str, *, archive_files: dict[str, str] | None = None
) -> None:
    append_shipped(
        project,
        ShippedSpec(
            spec_id=spec_id,
            spec_type="feature",
            shipped_at=datetime.now(timezone.utc),
            env_hash_at_ship=env_hash,
            delta_filename=None,
            scenario_archive_path=f".dark-factory/shipped-scenarios/{spec_id}",
            spec_origin=spec_id,
        ),
    )
    if archive_files:
        # shipped-scenarios is where the registry stores the canonical archive.
        adir = project / ".dark-factory" / "shipped-scenarios" / spec_id
        adir.mkdir(parents=True, exist_ok=True)
        for name, body in archive_files.items():
            (adir / name).write_text(body)


def _write_delta(deltas_dir: Path, name: str, *, parent_hash: str, ops: list[dict]) -> None:
    deltas_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "schema_version = 1",
        'spec_id = "x"',
        f'parent_hash = "{parent_hash}"',
        'applied_at = "2026-04-07T00:00:00Z"',
        "",
    ]
    for op in ops:
        lines += ["[[ops]]", f'op = "{op["op"]}"', f'path = "{op["path"]}"']
        if "value" in op:
            v = op["value"]
            lines.append(f'value = "{v}"' if isinstance(v, str) else f"value = {v}")
        lines.append("")
    (deltas_dir / name).write_text("\n".join(lines))


def test_probe_no_prior_specs_passes(tmp_path: Path) -> None:
    result = probe_prior_ships(tmp_path, PipelineConfig(), exclude="current")
    assert result.ok
    assert result.passed == []


def test_probe_passes_when_env_reachable_and_scenarios_pass(tmp_path: Path) -> None:
    """Happy path: prior spec's env hash is still reachable, scenarios pass."""
    empty_hash = hash_view(EnvironmentContract())
    _seed_ship(tmp_path, "prior", empty_hash, archive_files={"01.test.ts": "x\n"})

    def fake_run(spec, config, project_dir, **kw):
        return RunResult(exit_code=0, stdout="", stderr="")

    with patch("darkfactory.plan.smoke_probe.run_scenarios", side_effect=fake_run):
        result = probe_prior_ships(tmp_path, PipelineConfig(), exclude="current")

    assert result.passed == ["prior"]
    assert result.stranded == []
    assert result.regressed == []
    assert result.ok


def test_probe_detects_stranded_when_hash_unreachable(tmp_path: Path) -> None:
    """Prior spec shipped at a hash that the delta log no longer produces."""
    _seed_ship(
        tmp_path, "prior",
        "deadbeef" * 8,  # hash nothing will fold to
        archive_files={"01.test.ts": "x\n"},
    )
    # Seed a real delta so the log exists but doesn't produce the target.
    empty_hash = hash_view(EnvironmentContract())
    deltas_dir = tmp_path / ".dark-factory" / "architecture" / "environment.deltas"
    _write_delta(
        deltas_dir, "0001-x.toml",
        parent_hash=empty_hash,
        ops=[{"op": "set", "path": "runtime.language", "value": "go"}],
    )

    result = probe_prior_ships(tmp_path, PipelineConfig(), exclude="current")
    assert result.stranded == ["prior"]
    assert not result.ok


def test_probe_detects_regression_when_scenarios_fail(tmp_path: Path) -> None:
    empty_hash = hash_view(EnvironmentContract())
    _seed_ship(tmp_path, "prior", empty_hash, archive_files={"01.test.ts": "x\n"})

    def fake_run(spec, config, project_dir, **kw):
        return RunResult(exit_code=1, stdout="", stderr="boom")

    with patch("darkfactory.plan.smoke_probe.run_scenarios", side_effect=fake_run):
        result = probe_prior_ships(tmp_path, PipelineConfig(), exclude="current")

    assert result.regressed == ["prior"]
    assert result.stranded == []
    assert not result.ok


def test_probe_refuses_clobber_when_target_is_real_dir(tmp_path: Path) -> None:
    """If a real (non-symlink) dir sits at the target path, fail loud."""
    empty_hash = hash_view(EnvironmentContract())
    _seed_ship(tmp_path, "prior", empty_hash, archive_files={"01.test.ts": "x\n"})
    # Stage a conflicting real directory at the expected symlink slot.
    conflicting = tmp_path / ".dark-factory" / "scenarios" / "prior"
    conflicting.mkdir(parents=True)
    (conflicting / "live.test.ts").write_text("live content\n")

    with pytest.raises(RuntimeError, match="already exists as a non-symlink"):
        probe_prior_ships(tmp_path, PipelineConfig(), exclude="current")


def test_probe_excludes_self(tmp_path: Path) -> None:
    empty_hash = hash_view(EnvironmentContract())
    _seed_ship(tmp_path, "current", empty_hash, archive_files={"01.test.ts": "x\n"})
    # Should not call run_scenarios for 'current'.
    with patch("darkfactory.plan.smoke_probe.run_scenarios") as m:
        result = probe_prior_ships(tmp_path, PipelineConfig(), exclude="current")
    assert m.call_count == 0
    assert result.passed == []


def test_probe_skips_archive_with_skip_marker(tmp_path: Path) -> None:
    """Archives with .smoke-probe-skip opt out of the probe entirely."""
    empty_hash = hash_view(EnvironmentContract())
    _seed_ship(
        tmp_path, "prior", empty_hash,
        archive_files={
            "01.test.ts": "x\n",
            ".smoke-probe-skip": "vitest not available until api-bootstrap ships",
        },
    )

    with patch("darkfactory.plan.smoke_probe.run_scenarios") as m:
        result = probe_prior_ships(tmp_path, PipelineConfig(), exclude="current")

    # run_scenarios was never called for 'prior'.
    assert m.call_count == 0
    assert result.skipped == ["prior"]
    assert result.passed == []
    assert result.regressed == []
    assert result.ok  # skipped is OK, not a failure


def test_probe_skip_marker_does_not_halt_subsequent_specs(tmp_path: Path) -> None:
    """A skipped spec must NOT stop later specs in the probe order."""
    empty_hash = hash_view(EnvironmentContract())
    _seed_ship(
        tmp_path, "older", empty_hash,
        archive_files={
            "01.test.ts": "x\n",
            ".smoke-probe-skip": "reason",
        },
    )
    _seed_ship(tmp_path, "newer", empty_hash, archive_files={"01.test.ts": "y\n"})

    def fake_run(spec, config, project_dir, **kw):
        return RunResult(exit_code=0, stdout="", stderr="")

    with patch("darkfactory.plan.smoke_probe.run_scenarios", side_effect=fake_run) as m:
        result = probe_prior_ships(tmp_path, PipelineConfig(), exclude="current")

    # Older was skipped (no run_scenarios call), newer ran and passed.
    assert m.call_count == 1
    assert "older" in result.skipped
    assert result.passed == ["newer"]
    assert result.ok
