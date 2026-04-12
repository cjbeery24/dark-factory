"""Known-gaps skip list: read/write, shim generation, runner injection."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from darkfactory.models.config import PipelineConfig
from darkfactory.scenarios.known_gaps import (
    KNOWN_GAPS_FILE,
    SCHEMA_VERSION,
    SKIPPABLE_CATEGORIES,
    KnownGap,
    KnownGapsFile,
    build_pytest_conftest_source,
    build_vitest_setup_source,
    collect_skippable_names,
    extract_anchor,
    read_known_gaps,
    write_known_gaps,
)
from darkfactory.scenarios.runner import run_scenarios
from darkfactory.utils.subprocess_runner import RunResult


# --- constants + anchor parsing ----------------------------------------------


def test_skippable_categories_does_not_include_impl_bug() -> None:
    """IMPL_BUG must never flow through the skip list."""
    assert "IMPL_BUG" not in SKIPPABLE_CATEGORIES
    assert "SPEC_AMBIGUOUS" not in SKIPPABLE_CATEGORIES
    assert "ENV_ISSUE" in SKIPPABLE_CATEGORIES
    assert "BRITTLE" in SKIPPABLE_CATEGORIES
    assert "FLAKE" in SKIPPABLE_CATEGORIES


@pytest.mark.parametrize(
    "test_name,expected",
    [
        ("[AC-2] npm install is rejected", "AC-2"),
        ("[EC-10] fallback behavior", "EC-10"),
        ("[FR-1] happy path", "FR-1"),
        ("no anchor at all", ""),
        ("[lowercase-1] still nothing", ""),
        ("  [AC-1] leading whitespace", ""),  # must be at position 0
    ],
)
def test_extract_anchor(test_name: str, expected: str) -> None:
    assert extract_anchor(test_name) == expected


# --- file round-trip ---------------------------------------------------------


def test_round_trip(tmp_path: Path) -> None:
    gaps = KnownGapsFile(
        spec="demo",
        shipped_at="2026-04-11T22:58:07Z",
        gaps=[
            KnownGap(
                test_name="[AC-2] engine constraint",
                category="ENV_ISSUE",
                anchor="AC-2",
                added_at="2026-04-11T22:58:07Z",
                source="oracle_iter2",
                rationale="host npm version dependent",
            ),
        ],
    )
    write_known_gaps(tmp_path, gaps)

    loaded = read_known_gaps(tmp_path)
    assert loaded is not None
    assert loaded.spec == "demo"
    assert loaded.schema_version == SCHEMA_VERSION
    assert len(loaded.gaps) == 1
    assert loaded.gaps[0].test_name == "[AC-2] engine constraint"
    assert loaded.gaps[0].category == "ENV_ISSUE"
    assert loaded.gaps[0].anchor == "AC-2"


def test_read_missing_returns_none(tmp_path: Path) -> None:
    assert read_known_gaps(tmp_path) is None


def test_read_bad_json_returns_none(tmp_path: Path) -> None:
    (tmp_path / KNOWN_GAPS_FILE).write_text("{not json")
    assert read_known_gaps(tmp_path) is None


def test_read_schema_mismatch_returns_none(tmp_path: Path) -> None:
    """Unknown schema versions must never crash the runner."""
    (tmp_path / KNOWN_GAPS_FILE).write_text(
        json.dumps({"schema_version": 999, "spec": "x", "gaps": []})
    )
    assert read_known_gaps(tmp_path) is None


def test_read_ignores_non_dict_top_level(tmp_path: Path) -> None:
    (tmp_path / KNOWN_GAPS_FILE).write_text("[]")
    assert read_known_gaps(tmp_path) is None


def test_read_skips_invalid_gap_entries(tmp_path: Path) -> None:
    """Entries missing test_name / category are dropped silently."""
    (tmp_path / KNOWN_GAPS_FILE).write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "spec": "demo",
                "gaps": [
                    {"test_name": "good", "category": "ENV_ISSUE"},
                    {"test_name": "missing-category"},
                    {"category": "BRITTLE"},  # missing name
                    "not a dict",
                ],
            }
        )
    )
    loaded = read_known_gaps(tmp_path)
    assert loaded is not None
    assert [g.test_name for g in loaded.gaps] == ["good"]


# --- collect_skippable_names -------------------------------------------------


def _write_gaps(d: Path, *gaps: tuple[str, str]) -> None:
    """Write a gaps file with (test_name, category) tuples."""
    d.mkdir(parents=True, exist_ok=True)
    write_known_gaps(
        d,
        KnownGapsFile(
            spec=d.name,
            gaps=[KnownGap(test_name=n, category=c) for n, c in gaps],
        ),
    )


def test_collect_filters_non_skippable(tmp_path: Path) -> None:
    d = tmp_path / "spec"
    _write_gaps(
        d,
        ("[AC-1] env issue", "ENV_ISSUE"),
        ("[AC-2] real bug", "IMPL_BUG"),
        ("[AC-3] brittle", "BRITTLE"),
        ("[AC-4] ambiguous", "SPEC_AMBIGUOUS"),
    )
    names = collect_skippable_names([d])
    assert names == ["[AC-1] env issue", "[AC-3] brittle"]


def test_collect_dedupes_across_dirs(tmp_path: Path) -> None:
    d1 = tmp_path / "s1"
    d2 = tmp_path / "s2"
    _write_gaps(d1, ("[AC-1] shared", "ENV_ISSUE"))
    _write_gaps(d2, ("[AC-1] shared", "ENV_ISSUE"), ("[AC-2] other", "FLAKE"))
    names = collect_skippable_names([d1, d2])
    assert names == ["[AC-1] shared", "[AC-2] other"]


def test_collect_empty_when_no_files(tmp_path: Path) -> None:
    assert collect_skippable_names([tmp_path]) == []


# --- shim source generation --------------------------------------------------


def test_vitest_shim_uses_exact_name_set() -> None:
    src = build_vitest_setup_source(["[AC-1] foo", "[EC-2] bar"])
    assert "import { beforeEach } from 'vitest'" in src
    assert '"[AC-1] foo"' in src
    assert '"[EC-2] bar"' in src
    assert "ctx.task.name" in src
    assert "ctx.skip()" in src


def test_vitest_shim_handles_empty_list() -> None:
    """Generator doesn't crash on empty list (caller should short-circuit)."""
    src = build_vitest_setup_source([])
    assert "new Set([])" in src


def test_pytest_conftest_uses_item_name() -> None:
    src = build_pytest_conftest_source(["[AC-1] foo"])
    assert "pytest_collection_modifyitems" in src
    assert "item.name in _DF_KNOWN_GAPS" in src
    assert "[AC-1] foo" in src


# --- runner integration ------------------------------------------------------


def _vitest_project(tmp_path: Path, spec_name: str, *, gaps: list[tuple[str, str]] | None = None) -> Path:
    p = tmp_path / "project"
    (p / ".dark-factory" / "scenarios" / spec_name).mkdir(parents=True)
    (p / ".dark-factory" / "scenarios" / spec_name / "01.test.ts").write_text("x\n")
    (p / "vitest.config.ts").write_text("")
    (p / "package.json").write_text('{"name":"demo"}')
    if gaps is not None:
        _write_gaps(p / ".dark-factory" / "scenarios" / spec_name, *gaps)
    return p


def test_run_scenarios_writes_vitest_shim_when_gaps_present(tmp_path: Path) -> None:
    p = _vitest_project(
        tmp_path,
        "curr",
        gaps=[("[AC-2] host-dependent", "ENV_ISSUE")],
    )
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return RunResult(exit_code=0, stdout="", stderr="")

    with patch("darkfactory.scenarios.runner.run", side_effect=fake_run):
        run_scenarios("curr", PipelineConfig(), p)

    shim = p / ".dark-factory" / "scenarios" / ".df-known-gaps-skip.mjs"
    assert shim.is_file()
    assert "[AC-2] host-dependent" in shim.read_text()

    cmd = captured["cmd"]
    assert "vitest" in cmd
    assert "--setupFiles" in cmd
    idx = cmd.index("--setupFiles")
    assert cmd[idx + 1] == ".dark-factory/scenarios/.df-known-gaps-skip.mjs"


def test_run_scenarios_no_shim_when_no_gaps(tmp_path: Path) -> None:
    p = _vitest_project(tmp_path, "curr", gaps=None)

    def fake_run(cmd, **kwargs):
        return RunResult(exit_code=0, stdout="", stderr="")

    with patch("darkfactory.scenarios.runner.run", side_effect=fake_run):
        run_scenarios("curr", PipelineConfig(), p)

    shim = p / ".dark-factory" / "scenarios" / ".df-known-gaps-skip.mjs"
    assert not shim.exists()


def test_run_scenarios_only_impl_bug_no_shim(tmp_path: Path) -> None:
    """IMPL_BUG gaps in the file must never materialize a skip shim."""
    p = _vitest_project(
        tmp_path,
        "curr",
        gaps=[("[AC-1] real bug", "IMPL_BUG")],
    )

    def fake_run(cmd, **kwargs):
        return RunResult(exit_code=0, stdout="", stderr="")

    with patch("darkfactory.scenarios.runner.run", side_effect=fake_run):
        run_scenarios("curr", PipelineConfig(), p)

    shim = p / ".dark-factory" / "scenarios" / ".df-known-gaps-skip.mjs"
    assert not shim.exists()


def test_run_scenarios_unions_shim_across_archived_dirs(tmp_path: Path) -> None:
    """Gaps from archived specs are merged into the shim."""
    p = _vitest_project(tmp_path, "curr", gaps=None)
    archive = p / ".dark-factory" / "shipped-scenarios" / "alpha"
    archive.mkdir(parents=True)
    (archive / "01.test.ts").write_text("y\n")
    _write_gaps(archive, ("[AC-9] archived gap", "ENV_ISSUE"))

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return RunResult(exit_code=0, stdout="", stderr="")

    with patch("darkfactory.scenarios.runner.run", side_effect=fake_run):
        run_scenarios("curr", PipelineConfig(), p, spec_type="feature")

    shim = p / ".dark-factory" / "scenarios" / ".df-known-gaps-skip.mjs"
    assert shim.is_file()
    assert "[AC-9] archived gap" in shim.read_text()
    assert "--setupFiles" in captured["cmd"]


def test_run_scenarios_pytest_writes_conftest(tmp_path: Path) -> None:
    p = tmp_path / "project"
    (p / ".dark-factory" / "scenarios" / "curr").mkdir(parents=True)
    (p / ".dark-factory" / "scenarios" / "curr" / "test_one.py").write_text("")
    (p / "pyproject.toml").write_text("[project]\nname='x'\n")
    _write_gaps(
        p / ".dark-factory" / "scenarios" / "curr",
        ("[AC-1] py env", "ENV_ISSUE"),
    )

    def fake_run(cmd, **kwargs):
        return RunResult(exit_code=0, stdout="", stderr="")

    with patch("darkfactory.scenarios.runner.run", side_effect=fake_run):
        run_scenarios("curr", PipelineConfig(), p)

    conftest = p / ".dark-factory" / "scenarios" / "conftest.py"
    assert conftest.is_file()
    assert "[AC-1] py env" in conftest.read_text()
