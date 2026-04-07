"""Neutral project layout detection helpers."""

from __future__ import annotations

from pathlib import Path

from darkfactory.layout.detect import (
    detect_candidate_source_dirs,
    detect_monorepo_markers,
)


def test_detects_pnpm_monorepo(tmp_path: Path) -> None:
    (tmp_path / "apps" / "api" / "src").mkdir(parents=True)
    (tmp_path / "apps" / "web" / "src").mkdir(parents=True)
    (tmp_path / "packages" / "shared" / "src").mkdir(parents=True)
    assert detect_monorepo_markers(tmp_path) == ["apps", "packages"]
    assert detect_candidate_source_dirs(tmp_path) == [
        "apps/api/src",
        "apps/web/src",
        "packages/shared/src",
    ]


def test_detects_go_cmd_layout(tmp_path: Path) -> None:
    (tmp_path / "cmd" / "server").mkdir(parents=True)
    assert detect_monorepo_markers(tmp_path) == ["cmd"]
    assert detect_candidate_source_dirs(tmp_path) == ["cmd"]


def test_skips_apps_child_without_src(tmp_path: Path) -> None:
    (tmp_path / "apps" / "docs").mkdir(parents=True)  # no src/
    (tmp_path / "apps" / "api" / "src").mkdir(parents=True)
    assert detect_candidate_source_dirs(tmp_path) == ["apps/api/src"]


def test_no_markers_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    assert detect_monorepo_markers(tmp_path) == []
    assert detect_candidate_source_dirs(tmp_path) == []


def test_lib_is_not_a_marker(tmp_path: Path) -> None:
    # lib/ is intentionally excluded — too many projects use it for
    # vendored assets, fonts, terraform modules, etc.
    (tmp_path / "lib").mkdir()
    assert detect_monorepo_markers(tmp_path) == []
