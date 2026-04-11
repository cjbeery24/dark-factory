"""Phase 4.5.1 — refuse-to-run pre-flight for source_dirs barrier."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from darkfactory.docker.source_dirs_preflight import (
    check_source_dirs_preflight,
)
from darkfactory.errors import DarkFactoryError


def test_default_with_real_src_passes() -> None:
    with TemporaryDirectory() as t:
        d = Path(t)
        (d / "src").mkdir()
        check_source_dirs_preflight(d, ["src"])  # no raise


def test_default_with_apps_marker_fails() -> None:
    with TemporaryDirectory() as t:
        d = Path(t)
        (d / "src").mkdir()  # exists but ignored — monorepo trap
        (d / "apps").mkdir()
        with pytest.raises(DarkFactoryError, match="monorepo markers"):
            check_source_dirs_preflight(d, ["src"])


def test_default_with_packages_marker_fails() -> None:
    with TemporaryDirectory() as t:
        d = Path(t)
        (d / "src").mkdir()
        (d / "packages").mkdir()
        with pytest.raises(DarkFactoryError):
            check_source_dirs_preflight(d, ["src"])


def test_default_suggests_apps_src_paths() -> None:
    with TemporaryDirectory() as t:
        d = Path(t)
        (d / "src").mkdir()
        (d / "apps" / "api" / "src").mkdir(parents=True)
        (d / "apps" / "web" / "src").mkdir(parents=True)
        with pytest.raises(DarkFactoryError) as exc:
            check_source_dirs_preflight(d, ["src"])
        msg = str(exc.value)
        assert "apps/api/src" in msg and "apps/web/src" in msg


def test_explicit_source_dirs_with_monorepo_marker_passes() -> None:
    # User has set source_dirs explicitly — no longer the default — so
    # the monorepo marker check is irrelevant.
    with TemporaryDirectory() as t:
        d = Path(t)
        (d / "apps" / "api" / "src").mkdir(parents=True)
        check_source_dirs_preflight(d, ["apps/api/src"])  # no raise


def test_missing_source_dir_fails() -> None:
    with TemporaryDirectory() as t:
        d = Path(t)
        with pytest.raises(DarkFactoryError, match="do not exist"):
            check_source_dirs_preflight(d, ["nonexistent/path"])


def test_one_real_one_missing_fails() -> None:
    with TemporaryDirectory() as t:
        d = Path(t)
        (d / "real").mkdir()
        with pytest.raises(DarkFactoryError, match="do not exist"):
            check_source_dirs_preflight(d, ["real", "ghost"])


def test_greenfield_blank_project_with_default_src_passes() -> None:
    # Truly blank project: no src/, no monorepo markers. The framework
    # default ["src"] would fail check 2 (missing path) without the
    # greenfield bypass. The pipeline's first bootstrap spec must be
    # allowed to run so the Architect can populate source_dirs.
    with TemporaryDirectory() as t:
        d = Path(t)
        check_source_dirs_preflight(d, ["src"])  # no raise


def test_greenfield_bypass_does_not_mask_monorepo_trap() -> None:
    # A blank project that has apps/ but no src/ must still fail the
    # monorepo-trap check — the bypass only applies when there are no
    # monorepo markers at all.
    with TemporaryDirectory() as t:
        d = Path(t)
        (d / "apps").mkdir()
        with pytest.raises(DarkFactoryError, match="monorepo markers"):
            check_source_dirs_preflight(d, ["src"])


def test_greenfield_bypass_does_not_mask_existing_src() -> None:
    # A project with a real src/ and default source_dirs is the happy
    # path — bypass must not short-circuit it, and the existence check
    # must still run (trivially passes here).
    with TemporaryDirectory() as t:
        d = Path(t)
        (d / "src").mkdir()
        check_source_dirs_preflight(d, ["src"])  # no raise


def test_greenfield_bypass_only_applies_to_default() -> None:
    # If the user has explicitly set source_dirs (not the framework
    # default) to a nonexistent path, the existence check must still
    # fire — this is a real misconfiguration, not a greenfield bootstrap.
    with TemporaryDirectory() as t:
        d = Path(t)
        with pytest.raises(DarkFactoryError, match="do not exist"):
            check_source_dirs_preflight(d, ["lib"])
