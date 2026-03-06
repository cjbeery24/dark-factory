"""Git worktree lifecycle — create, remove, cleanup.

Replaces lib/worktree.sh — worktree_create, worktree_remove, worktree_list,
worktree_cleanup_all, conflict_detect.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from darkfactory.config.schema import validate_spec_name
from darkfactory.errors import DarkFactoryError
from darkfactory.state.lock import FileLock
from darkfactory.utils.subprocess_runner import run

log = logging.getLogger(__name__)

# Worktree directory uses first 8 chars of run_id (same as branch suffix).
_WORKTREE_RUN_ID_LEN = 8


def create(
    project_dir: Path,
    spec_name: str,
    run_id: str,
    *,
    base_branch: str = "main",
    config_path: Path | None = None,
    spec_path: Path | None = None,
) -> Path:
    """Create a git worktree for a spec.

    Branch name: dark-factory/{spec}-{run_id[:8]}.
    Returns the worktree path.
    """
    validate_spec_name(spec_name)

    # Import here to avoid circular import at module level.
    from darkfactory.git.ops import branch_name

    branch = branch_name(spec_name, run_id)
    worktree_dir = (
        project_dir / ".dark-factory" / "worktrees"
        / f"{spec_name}-{run_id[:_WORKTREE_RUN_ID_LEN]}"
    )

    lock_dir = project_dir / ".dark-factory"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".worktree.lock"
    lock = FileLock(lock_path, timeout=30.0)

    with lock:
        # Create the worktree with a new branch.
        result = run(
            ["git", "-C", str(project_dir), "worktree", "add",
             "-b", branch, str(worktree_dir), base_branch],
        )
        if result.exit_code != 0:
            raise DarkFactoryError(
                f"failed to create worktree: {result.stderr}"
            )

    # Copy config and spec into worktree.
    if config_path and config_path.exists():
        dest = worktree_dir / ".dark-factory"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config_path, dest / config_path.name)

    if spec_path and spec_path.exists():
        dest = worktree_dir / ".dark-factory" / "specs"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(spec_path, dest / spec_path.name)

    log.info("created worktree: %s (branch %s)", worktree_dir, branch)
    return worktree_dir


def remove(
    project_dir: Path,
    worktree_path: Path,
    *,
    force: bool = False,
) -> bool:
    """Remove a worktree and its branch.

    Performs safety checks unless force=True:
    - Ensures no uncommitted changes.
    - Ensures no unpushed commits.

    Returns True if removed successfully.
    """
    if not worktree_path.exists():
        return True

    if not force:
        # Check for uncommitted changes.
        result = run(
            ["git", "-C", str(worktree_path), "status", "--porcelain"],
        )
        if result.exit_code == 0 and result.stdout.strip():
            log.warning("worktree has uncommitted changes: %s", worktree_path)
            return False

        # Check for unpushed commits.
        result = run(
            ["git", "-C", str(worktree_path), "log", "@{u}..HEAD", "--oneline"],
        )
        if result.exit_code == 0 and result.stdout.strip():
            log.warning("worktree has unpushed commits: %s", worktree_path)
            return False

    lock_dir = project_dir / ".dark-factory"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".worktree.lock"
    lock = FileLock(lock_path, timeout=30.0)

    with lock:
        # Get branch name before removing worktree.
        result = run(
            ["git", "-C", str(worktree_path), "branch", "--show-current"],
        )
        branch = result.stdout.strip() if result.exit_code == 0 else ""

        # Remove worktree.
        result = run(
            ["git", "-C", str(project_dir), "worktree", "remove",
             str(worktree_path), "--force"],
        )
        if result.exit_code != 0:
            log.warning("failed to remove worktree: %s", result.stderr)
            return False

        # Delete branch.
        if branch:
            run(
                ["git", "-C", str(project_dir), "branch", "-D", branch],
            )

    log.info("removed worktree: %s", worktree_path)
    return True


def list_worktrees(project_dir: Path) -> list[str]:
    """List all worktree paths."""
    result = run(
        ["git", "-C", str(project_dir), "worktree", "list", "--porcelain"],
    )
    if result.exit_code != 0:
        return []

    paths: list[str] = []
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            paths.append(line[9:])
    return paths


def cleanup_all(
    project_dir: Path,
    *,
    force: bool = False,
) -> int:
    """Remove all dark-factory worktrees. Returns count removed."""
    worktrees = list_worktrees(project_dir)
    count = 0
    for wt_path in worktrees:
        path = Path(wt_path)
        # Only remove worktrees under .dark-factory/worktrees/.
        if ".dark-factory" in str(path):
            if remove(project_dir, path, force=force):
                count += 1
    return count
