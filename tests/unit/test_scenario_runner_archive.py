"""Phase 5.2 — scenario runner unions archived shipped-scenarios for feature/bugfix."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import patch

import pytest

from darkfactory.models.config import PipelineConfig
from darkfactory.scenarios.runner import (
    discover_shipped_scenario_dirs,
    run_scenarios,
)
from darkfactory.utils.subprocess_runner import RunResult


def _project(tmp_path: Path, *, archived: list[str], current_spec: str) -> Path:
    p = tmp_path / "project"
    (p / ".dark-factory" / "scenarios" / current_spec).mkdir(parents=True)
    (p / ".dark-factory" / "scenarios" / current_spec / "01.test.ts").write_text("x\n")
    arch_root = p / ".dark-factory" / "shipped-scenarios"
    if archived:
        arch_root.mkdir(parents=True)
        for spec_id in archived:
            d = arch_root / spec_id
            d.mkdir()
            (d / "01.test.ts").write_text("y\n")
        # Hidden + non-dir noise to make sure discover_ ignores it.
        (arch_root / ".registry-meta").write_text("noise")
    (p / "vitest.config.ts").write_text("")
    return p


def _config() -> PipelineConfig:
    cfg = PipelineConfig()
    return dataclasses.replace(
        cfg,
        scenario_test_cmd="npx vitest run {SCENARIOS_DIR}",
        gate_timeout=30,
    )


def test_discover_skips_hidden_and_files(tmp_path: Path) -> None:
    p = _project(tmp_path, archived=["alpha", "beta"], current_spec="curr")
    dirs = discover_shipped_scenario_dirs(p)
    assert [d.name for d in dirs] == ["alpha", "beta"]


def test_discover_excludes_named_spec(tmp_path: Path) -> None:
    p = _project(tmp_path, archived=["alpha", "curr"], current_spec="curr")
    dirs = discover_shipped_scenario_dirs(p, exclude="curr")
    assert [d.name for d in dirs] == ["alpha"]


def test_discover_returns_empty_when_no_archive(tmp_path: Path) -> None:
    p = tmp_path / "empty"
    p.mkdir()
    assert discover_shipped_scenario_dirs(p) == []


@pytest.mark.parametrize("spec_type", ["feature", "bugfix"])
def test_feature_and_bugfix_union_archive(tmp_path: Path, spec_type: str) -> None:
    p = _project(tmp_path, archived=["alpha", "beta"], current_spec="curr")
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return RunResult(exit_code=0, stdout="", stderr="")

    with patch("darkfactory.scenarios.runner.run", side_effect=fake_run):
        run_scenarios("curr", _config(), p, spec_type=spec_type)

    # bash -c "<expanded>"
    assert captured["cmd"][0] == "bash"
    expanded = captured["cmd"][2]
    assert "scenarios/curr" in expanded
    assert "shipped-scenarios/alpha" in expanded
    assert "shipped-scenarios/beta" in expanded


def test_bootstrap_does_not_union_archive(tmp_path: Path) -> None:
    p = _project(tmp_path, archived=["alpha"], current_spec="curr")
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return RunResult(exit_code=0, stdout="", stderr="")

    with patch("darkfactory.scenarios.runner.run", side_effect=fake_run):
        run_scenarios("curr", _config(), p, spec_type="bootstrap")

    expanded = captured["cmd"][2]
    assert "scenarios/curr" in expanded
    assert "shipped-scenarios" not in expanded


def test_refactor_does_not_union_archive(tmp_path: Path) -> None:
    p = _project(tmp_path, archived=["alpha"], current_spec="curr")
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return RunResult(exit_code=0, stdout="", stderr="")

    with patch("darkfactory.scenarios.runner.run", side_effect=fake_run):
        run_scenarios("curr", _config(), p, spec_type="refactor")

    expanded = captured["cmd"][2]
    assert "shipped-scenarios" not in expanded


def test_no_spec_type_keeps_legacy_single_dir(tmp_path: Path) -> None:
    # Defense for callers (like regression.py) that intentionally don't
    # pass spec_type — must not silently expand to the archive.
    p = _project(tmp_path, archived=["alpha"], current_spec="curr")
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return RunResult(exit_code=0, stdout="", stderr="")

    with patch("darkfactory.scenarios.runner.run", side_effect=fake_run):
        run_scenarios("curr", _config(), p)

    expanded = captured["cmd"][2]
    assert "shipped-scenarios" not in expanded


def test_paths_with_spaces_are_shell_quoted(tmp_path: Path) -> None:
    # Regression: an unquoted bash -c expansion would split a project
    # path like "/Users/some name/proj" into two args and the test runner
    # would see a nonexistent path. Every dir spliced into {SCENARIOS_DIR}
    # must round-trip through shlex.quote.
    parent = tmp_path / "with space"
    parent.mkdir()
    p = _project(parent, archived=["alpha"], current_spec="curr")
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return RunResult(exit_code=0, stdout="", stderr="")

    with patch("darkfactory.scenarios.runner.run", side_effect=fake_run):
        run_scenarios("curr", _config(), p, spec_type="feature")

    expanded = captured["cmd"][2]
    # Each dir token must be a single shell-quoted unit, not bare with a
    # raw space inside it.
    assert "'" in expanded or '"' in expanded
    # Round-trip through shlex.split to confirm the shell sees one token
    # per dir, not split-on-space.
    import shlex as _shlex
    tokens = _shlex.split(expanded)
    space_tokens = [t for t in tokens if "with space" in t]
    # Both the new dir and the archived dir live under "with space".
    assert len(space_tokens) == 2
    for t in space_tokens:
        assert "with space" in t  # not split


def test_feature_with_empty_archive_runs_normally(tmp_path: Path) -> None:
    p = _project(tmp_path, archived=[], current_spec="curr")
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return RunResult(exit_code=0, stdout="", stderr="")

    with patch("darkfactory.scenarios.runner.run", side_effect=fake_run):
        run_scenarios("curr", _config(), p, spec_type="feature")

    expanded = captured["cmd"][2]
    assert "scenarios/curr" in expanded
    assert "shipped-scenarios" not in expanded
