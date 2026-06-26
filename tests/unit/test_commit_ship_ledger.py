"""Tests for git_ops.commit_ship_ledger — durable ledger commit on SHIP.

Guards the database-foundation failure mode: the ship row must be committed
(durable) independent of auto_pr, without sweeping impl/other changes in.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from darkfactory.git import ops as git_ops


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README").write_text("x\n")
    _git(tmp_path, "add", "README")
    _git(tmp_path, "commit", "-q", "-m", "init")


def _shipped(tmp_path: Path, content: str = "row\n") -> None:
    led = tmp_path / ".dark-factory" / "plan"
    led.mkdir(parents=True, exist_ok=True)
    (led / "shipped.jsonl").write_text(content)


def _committed_files(tmp_path: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(tmp_path), "show", "--name-only", "--format=", "HEAD"],
        capture_output=True, text=True,
    ).stdout.split()
    return out


def test_commits_only_ledger_leaving_staged_impl_untouched(tmp_path: Path) -> None:
    """The subtle guarantee: a SEPARATELY STAGED change survives the partial commit."""
    _init_repo(tmp_path)
    _shipped(tmp_path)
    (tmp_path / "impl.py").write_text("print(1)\n")
    _git(tmp_path, "add", "impl.py")  # staged in the index BEFORE the ledger commit

    assert git_ops.commit_ship_ledger("2026-01-01-foo", tmp_path) is True

    # only the ledger landed in the commit
    assert _committed_files(tmp_path) == [".dark-factory/plan/shipped.jsonl"]
    # impl.py is NOT in the commit and is STILL staged afterwards
    staged = subprocess.run(
        ["git", "-C", str(tmp_path), "diff", "--cached", "--name-only"],
        capture_output=True, text=True,
    ).stdout
    assert "impl.py" in staged


def test_gitignored_ledger_is_not_silently_durable(tmp_path: Path) -> None:
    """A gitignored ledger must NOT be reported committed (it would silently re-lose it)."""
    _init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".dark-factory/\n")
    _git(tmp_path, "add", ".gitignore")
    _git(tmp_path, "commit", "-q", "-m", "ignore df")
    _shipped(tmp_path)

    assert git_ops.commit_ship_ledger("foo", tmp_path) is False
    # the ledger never entered history
    log = subprocess.run(
        ["git", "-C", str(tmp_path), "log", "--oneline"],
        capture_output=True, text=True,
    ).stdout.strip().splitlines()
    assert len(log) == 2  # init + ignore-df only; no ship-ledger commit


def test_noop_when_ledger_unchanged(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _shipped(tmp_path)
    assert git_ops.commit_ship_ledger("foo", tmp_path) is True   # first: commits
    assert git_ops.commit_ship_ledger("foo", tmp_path) is False  # second: nothing new


def test_best_effort_when_no_ledger(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    # no shipped.jsonl, no archive -> returns False, never raises
    assert git_ops.commit_ship_ledger("foo", tmp_path) is False


def test_includes_untracked_scenario_archive(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _shipped(tmp_path)
    arch = tmp_path / ".dark-factory" / "shipped-scenarios" / "2026-01-01-foo"
    arch.mkdir(parents=True)
    (arch / "b1.test.ts").write_text("// test\n")

    assert git_ops.commit_ship_ledger("2026-01-01-foo", tmp_path) is True
    files = set(_committed_files(tmp_path))
    assert ".dark-factory/plan/shipped.jsonl" in files
    assert ".dark-factory/shipped-scenarios/2026-01-01-foo/b1.test.ts" in files
