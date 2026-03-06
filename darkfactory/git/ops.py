"""Git branch/commit/push/PR operations via subprocess.

Replaces lib/git-ops.sh — gitops_preflight, gitops_create_branch,
gitops_commit_changes, gitops_push_branch, gitops_create_pr, etc.
"""

from __future__ import annotations

import logging
from pathlib import Path

from darkfactory.config.schema import validate_spec_name
from darkfactory.errors import DarkFactoryError
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import run

log = logging.getLogger(__name__)

# Branch suffix length — shared between ops.py and worktree.py.
BRANCH_RUN_ID_LEN = 8


def preflight(config: PipelineConfig, project_dir: Path) -> None:
    """Verify git repo, gh CLI auth, remote reachability, clean tree."""
    # Check we're in a git repo.
    result = run(["git", "-C", str(project_dir), "rev-parse", "--git-dir"])
    if result.exit_code != 0:
        raise DarkFactoryError(f"{project_dir} is not a git repository")

    # Check gh CLI auth.
    result = run(["gh", "auth", "status"], timeout=10)
    if result.exit_code != 0:
        raise DarkFactoryError("gh CLI not authenticated (run 'gh auth login')")

    # Check remote reachability.
    result = run(
        ["git", "-C", str(project_dir), "ls-remote", "--exit-code",
         config.git_remote, "HEAD"],
        timeout=15,
    )
    if result.exit_code != 0:
        log.warning("git remote %s not reachable", config.git_remote)

    # Warn on dirty tree.
    result = run(["git", "-C", str(project_dir), "status", "--porcelain"])
    if result.exit_code == 0 and result.stdout.strip():
        log.warning("working tree has uncommitted changes")


def branch_name(spec_name: str, run_id: str) -> str:
    """Compute the canonical branch name for a spec run."""
    return f"dark-factory/{spec_name}-{run_id[:BRANCH_RUN_ID_LEN]}"


def create_branch(
    spec_name: str,
    run_id: str,
    config: PipelineConfig,
    project_dir: Path,
) -> str:
    """Create and checkout a new branch for the spec.

    Returns the branch name.
    """
    validate_spec_name(spec_name)
    branch = branch_name(spec_name, run_id)
    run(
        ["git", "-C", str(project_dir), "checkout", "-b", branch,
         config.git_base_branch],
        check=True,
    )
    log.info("created branch: %s", branch)
    return branch


def commit_changes(
    spec_name: str,
    source_dirs: list[str],
    test_dir: str,
    project_dir: Path,
    *,
    message: str = "",
    skip_hooks: bool = False,
) -> bool:
    """Stage and commit implementation changes.

    Returns True if a commit was made, False if nothing to commit.
    """
    # Stage source dirs and test dir.
    paths_to_stage = [*source_dirs, test_dir]
    # Also stage common project files (exact filenames only).
    for pf in ["package.json", "package-lock.json", "pyproject.toml",
                "go.mod", "go.sum", "Cargo.toml", "Cargo.lock",
                "tsconfig.json"]:
        paths_to_stage.append(pf)

    _stage_paths(project_dir, paths_to_stage)

    # Check if there's anything staged.
    result = run(["git", "-C", str(project_dir), "diff", "--cached", "--quiet"])
    if result.exit_code == 0:
        log.info("nothing to commit")
        return False

    if not message:
        message = f"dark-factory: implement {spec_name}"

    cmd = ["git", "-C", str(project_dir), "commit", "-m", message]
    if skip_hooks:
        cmd.append("--no-verify")

    result = run(cmd)
    if result.exit_code != 0:
        log.warning("commit failed: %s", result.stderr)
        return False

    return True


def push_branch(
    branch: str,
    config: PipelineConfig,
    project_dir: Path,
) -> bool:
    """Push branch to remote. Returns True on success."""
    result = run(
        ["git", "-C", str(project_dir), "push", "-u",
         config.git_remote, branch],
        timeout=60,
    )
    return result.exit_code == 0


def create_pr(
    spec_name: str,
    config: PipelineConfig,
    project_dir: Path,
    *,
    report_path: Path | None = None,
    run_id: str = "",
    satisfaction_score: int = 0,
    trust_score: int = 0,
) -> str:
    """Create a PR via gh CLI.

    Returns the PR URL, or empty string on failure.
    """
    # Check for existing PR.
    result = run(
        ["gh", "pr", "view", "--json", "url", "-q", ".url"],
        cwd=str(project_dir),
    )
    if result.exit_code == 0 and result.stdout.strip():
        url = result.stdout.strip()
        log.info("PR already exists: %s", url)
        return url

    title = f"dark-factory: {spec_name}"
    body_parts = [f"## {spec_name}\n"]
    body_parts.append(f"Run ID: `{run_id}`\n")
    if satisfaction_score:
        body_parts.append(f"Satisfaction: {satisfaction_score}/100\n")
    if trust_score:
        body_parts.append(f"Trust: {trust_score}/100\n")

    if report_path and report_path.exists():
        body_parts.append("\n### Quality Gate Report\n")
        report_content = report_path.read_text()
        if len(report_content) > 2000:
            report_content = report_content[:2000] + "\n...(truncated)"
        body_parts.append(report_content)

    body = "\n".join(body_parts)

    result = run(
        ["gh", "pr", "create",
         "--title", title,
         "--body", body,
         "--base", config.git_base_branch,
         "--label", "dark-factory"],
        cwd=str(project_dir),
        timeout=30,
    )
    if result.exit_code == 0:
        url = result.stdout.strip()
        log.info("created PR: %s", url)
        return url

    log.warning("PR creation failed: %s", result.stderr)
    return ""


def merge_queue_add(project_dir: Path) -> bool:
    """Add the current PR to the merge queue. Returns True on success."""
    result = run(
        ["gh", "pr", "merge", "--merge", "--auto"],
        cwd=str(project_dir),
        timeout=15,
    )
    return result.exit_code == 0


def auto_merge_high_trust(project_dir: Path, trust_score: int) -> bool:
    """Auto-merge if trust score >= 80. Returns True if merge was requested."""
    if trust_score < 80:
        return False
    result = run(
        ["gh", "pr", "merge", "--auto", "--squash"],
        cwd=str(project_dir),
        timeout=15,
    )
    if result.exit_code == 0:
        log.info("auto-merge requested (trust=%d)", trust_score)
        return True
    return False


def push_worktree(
    worktree_path: Path,
    config: PipelineConfig,
) -> bool:
    """Push the worktree's branch to remote."""
    result = run(
        ["git", "-C", str(worktree_path), "branch", "--show-current"],
    )
    if result.exit_code != 0:
        return False
    b = result.stdout.strip()
    return push_branch(b, config, worktree_path)


def count_files_changed(project_dir: Path, source_dirs: list[str]) -> int:
    """Count files changed (staged + untracked) in source dirs."""
    count = 0
    for src in source_dirs:
        # Staged/modified files.
        result = run(
            ["git", "-C", str(project_dir), "diff", "--name-only", "HEAD", "--", src],
        )
        if result.exit_code == 0 and result.stdout.strip():
            count += len(result.stdout.strip().splitlines())
        # Untracked files.
        result = run(
            ["git", "-C", str(project_dir), "ls-files", "--others",
             "--exclude-standard", "--", src],
        )
        if result.exit_code == 0 and result.stdout.strip():
            count += len(result.stdout.strip().splitlines())
    return count


# --- Helpers ---

def _stage_paths(project_dir: Path, paths: list[str]) -> None:
    """Stage files/dirs that exist, batched into a single git add call."""
    existing = [p for p in paths if (project_dir / p).exists()]
    if existing:
        run(["git", "-C", str(project_dir), "add", *existing])
