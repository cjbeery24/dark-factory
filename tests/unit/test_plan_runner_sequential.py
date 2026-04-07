"""Phase 6.1/6.2 — unit tests for the sequential plan runner.

These test the runner's orchestration logic in isolation by stubbing
``run_pipeline`` and seeding shipped.jsonl directly. We intentionally do
not exercise the real pipeline here — that's what the Phase 6 exit
criteria integration run is for.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from darkfactory.models.config import PipelineConfig
from darkfactory.plan.runner import run_sequential_plan
from darkfactory.plan.state import ShippedSpec, append_shipped  # noqa: F401


def _write_plan(project: Path, name: str, rows: list[tuple[str, str, str]]) -> None:
    """Write a minimal plan markdown table + matching stub spec files.

    rows: list of (feature_id, feature_name, deps_str).
    """
    plans_dir = project / ".dark-factory" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Plan",
        "",
        "| ID | Feature Name | Spec Type | Depends On | Description |",
        "| --- | --- | --- | --- | --- |",
    ]
    for fid, fname, deps in rows:
        lines.append(f"| {fid} | {fname} | feature | {deps or '—'} | x |")
    (plans_dir / f"{name}.md").write_text("\n".join(lines) + "\n")

    specs_dir = project / ".dark-factory" / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    for _fid, fname, _deps in rows:
        slug = fname.lower().replace(" ", "-")
        (specs_dir / f"{slug}.md").write_text("# stub\n")


def _fake_shipped_row(project: Path, spec_id: str) -> None:
    append_shipped(
        project,
        ShippedSpec(
            spec_id=spec_id,
            spec_type="feature",
            shipped_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
            env_hash_at_ship="abc",
            delta_filename=None,
            scenario_archive_path=f".dark-factory/shipped-scenarios/{spec_id}",
            spec_origin=spec_id,
        ),
    )


def test_sequential_runner_ships_all_in_order(tmp_path: Path) -> None:
    project = tmp_path
    _write_plan(project, "p", [("F-1", "alpha", ""), ("F-2", "beta", "F-1")])

    calls: list[str] = []

    def fake_run_pipeline(spec_name, *a, **kw):
        calls.append(spec_name)
        _fake_shipped_row(project, spec_name)
        return 0

    with patch("darkfactory.pipeline.orchestrator.run_pipeline", side_effect=fake_run_pipeline):
        result = run_sequential_plan("p", PipelineConfig(), project, smoke_probe=False)

    assert calls == ["alpha", "beta"]  # topo order respected
    assert result.shipped == ["alpha", "beta"]
    assert result.failed is None
    assert result.skipped == []


def test_sequential_runner_halts_on_failure(tmp_path: Path) -> None:
    project = tmp_path
    _write_plan(
        project, "p",
        [("F-1", "alpha", ""), ("F-2", "beta", "F-1"), ("F-3", "gamma", "F-2")],
    )

    def fake_run_pipeline(spec_name, *a, **kw):
        if spec_name == "alpha":
            _fake_shipped_row(project, spec_name)
            return 0
        # beta fails — no shipped row, non-zero exit.
        return 2

    with patch("darkfactory.pipeline.orchestrator.run_pipeline", side_effect=fake_run_pipeline):
        result = run_sequential_plan("p", PipelineConfig(), project, smoke_probe=False)

    assert result.shipped == ["alpha"]
    assert result.failed == "beta"
    assert result.failed_exit_code == 2
    assert result.skipped == ["gamma"]  # gamma never ran


def test_sequential_runner_trusts_shipped_jsonl_not_exit_code(tmp_path: Path) -> None:
    """If run_pipeline returns 0 but no shipped row was written, treat as failure.

    Defends against a bug class where an orchestrator path returns 0 without
    actually shipping. shipped.jsonl is the source of truth.
    """
    project = tmp_path
    _write_plan(project, "p", [("F-1", "alpha", ""), ("F-2", "beta", "F-1")])

    def fake_run_pipeline(spec_name, *a, **kw):
        return 0  # liar — no row written

    with patch("darkfactory.pipeline.orchestrator.run_pipeline", side_effect=fake_run_pipeline):
        result = run_sequential_plan("p", PipelineConfig(), project, smoke_probe=False)

    assert result.shipped == []
    assert result.failed == "alpha"
    assert result.skipped == ["beta"]


def test_sequential_runner_resume_skips_already_shipped(tmp_path: Path) -> None:
    project = tmp_path
    _write_plan(project, "p", [("F-1", "alpha", ""), ("F-2", "beta", "F-1")])
    _fake_shipped_row(project, "alpha")  # pre-existing ship

    calls: list[str] = []

    def fake_run_pipeline(spec_name, *a, **kw):
        calls.append(spec_name)
        _fake_shipped_row(project, spec_name)
        return 0

    with patch("darkfactory.pipeline.orchestrator.run_pipeline", side_effect=fake_run_pipeline):
        result = run_sequential_plan("p", PipelineConfig(), project, resume=True, smoke_probe=False)

    assert calls == ["beta"]  # alpha skipped
    assert result.shipped == ["alpha", "beta"]
    assert result.newly_shipped == ["beta"]  # alpha was pre-existing
    assert result.failed is None


def test_sequential_runner_resume_halts_on_unresumed_failure(tmp_path: Path) -> None:
    """Resume from alpha-shipped, then beta fails — halt cleanly."""
    project = tmp_path
    _write_plan(
        project, "p",
        [("F-1", "alpha", ""), ("F-2", "beta", "F-1"), ("F-3", "gamma", "F-2")],
    )
    _fake_shipped_row(project, "alpha")

    def fake_run_pipeline(spec_name, *a, **kw):
        return 1  # beta fails, no row written

    with patch("darkfactory.pipeline.orchestrator.run_pipeline", side_effect=fake_run_pipeline):
        result = run_sequential_plan("p", PipelineConfig(), project, resume=True)

    assert result.shipped == ["alpha"]      # only the pre-existing ship
    assert result.newly_shipped == []
    assert result.failed == "beta"
    assert result.skipped == ["gamma"]
    assert "exited 1" in result.failed_reason


def test_sequential_runner_halts_on_stranded_prior_ship(tmp_path: Path) -> None:
    """When smoke probe finds a prior spec's env hash is unreachable, halt."""
    from darkfactory.architecture.deltas import hash_view
    from darkfactory.architecture.environment import EnvironmentContract

    project = tmp_path
    _write_plan(
        project, "p",
        [("F-1", "alpha", ""), ("F-2", "beta", "F-1"), ("F-3", "gamma", "F-2")],
    )
    empty = hash_view(EnvironmentContract())

    def fake_run_pipeline(spec_name, *a, **kw):
        # Alpha ships cleanly at empty-view hash (reachable).
        # Beta ships at a deliberately unreachable hash — when the probe
        # re-runs over prior ships after beta, alpha is still reachable
        # so the probe passes. To force a stranded, we want alpha to
        # become unreachable. Simplest setup: alpha ships with an
        # unreachable hash from the start; when beta ships, the post-beta
        # probe checks alpha and finds it stranded.
        if spec_name == "alpha":
            # Use a non-empty hash that won't be producible by the empty delta log.
            append_shipped(
                project,
                ShippedSpec(
                    spec_id="alpha", spec_type="feature",
                    shipped_at=__import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc),
                    env_hash_at_ship="cafe" * 16,  # unreachable
                    delta_filename=None,
                    scenario_archive_path=".dark-factory/shipped-scenarios/alpha",
                    spec_origin="alpha",
                ),
            )
        else:
            # beta ships at the empty-view hash (reachable) but smoke
            # probe will then check alpha and find it stranded.
            _fake_shipped_row(project, spec_name)
        return 0

    with patch("darkfactory.pipeline.orchestrator.run_pipeline", side_effect=fake_run_pipeline):
        # smoke_probe defaults to True
        result = run_sequential_plan("p", PipelineConfig(), project)

    # alpha "shipped" but then beta's post-ship probe detected alpha as stranded.
    assert result.newly_shipped == ["alpha", "beta"]
    assert result.failed == "beta"
    assert "stranded" in result.failed_reason
    assert result.failure_category == "probe_stranded"
    assert result.failed_exit_code == 0  # typed category, not subprocess code
    assert result.skipped == ["gamma"]
