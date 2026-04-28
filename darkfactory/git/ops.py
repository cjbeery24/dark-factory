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


def close_pr(project_dir: Path, comment: str = "", branch: str = "") -> bool:
    """Close a PR without merging.

    If branch is provided, closes that branch's PR. Otherwise closes the
    current branch's PR. Used on pre-merge canary failure.
    Returns True on success.
    """
    cmd = ["gh", "pr", "close"]
    if branch:
        cmd.append(branch)
    if comment:
        cmd.extend(["--comment", comment])
    result = run(cmd, cwd=str(project_dir), timeout=15)
    if result.exit_code == 0:
        log.info("PR closed")
        return True
    log.warning("PR close failed: %s", result.stderr)
    return False


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


# --- Per-iteration diff capture (companion to count_files_changed) ---
#
# count_files_changed reports HOW MANY files moved between two SHAs.
# capture_iteration_diff records WHAT the implementer actually changed so
# post-mortem can reason about iter-1 vs iter-2 intent. Both are scoped
# to source/test/manifest paths — lockfiles, build artifacts, and the
# .dark-factory artifact mirror are excluded so the patch stays grep-
# and detect_changes-friendly. No gzip on purpose: the friction on
# casual inspection is not worth the small win on disk.

# 1 MB cap. Above this we degrade to --stat + head/tail rather than
# write a multi-MB blob; the --stat alone is usually enough to recognize
# the touched files, and the head/tail give just enough evidence of
# intent without grep-hostility.
_DIFF_SIZE_CAP_BYTES = 1_000_000
_DIFF_TAIL_LINES = 500

_MANIFEST_FILES: tuple[str, ...] = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "go.mod",
    "Cargo.toml",
    "tsconfig.json",
)

# git pathspec exclusions. ``:(exclude,glob)`` enables shell globs
# (``**`` = any depth). All lockfiles use the glob form so monorepo
# layouts with nested ``frontend/package-lock.json`` etc. are excluded
# too — the literal ``:(exclude)package-lock.json`` form would only
# match the repo-root file and let nested lockfiles balloon the patch
# (the exact failure mode we're preventing). Build artifacts and
# __pycache__ are noise; the .dark-factory tree is the artifact mirror —
# including it would recursively swallow the archive itself on
# follow-up iterations.
_EXCLUDE_PATHSPECS: tuple[str, ...] = (
    ":(exclude,glob)**/package-lock.json",
    ":(exclude,glob)**/pnpm-lock.yaml",
    ":(exclude,glob)**/yarn.lock",
    ":(exclude,glob)**/uv.lock",
    ":(exclude,glob)**/Cargo.lock",
    ":(exclude,glob)**/Pipfile.lock",
    ":(exclude,glob)**/dist/**",
    ":(exclude,glob)**/*.tsbuildinfo",
    ":(exclude,glob)**/__pycache__/**",
    ":(exclude,glob).dark-factory/**",
)


def capture_iteration_diff(
    project_dir: Path,
    start_sha: str,
    end_sha: str,
    source_dirs: list[str],
    test_dir: str,
    out_path: Path,
) -> bool:
    """Write a per-iteration diff patch from ``start_sha`` to ``end_sha``.

    Includes source/test/manifest paths; excludes lockfiles, build
    artifacts, and the ``.dark-factory/`` tree. Above
    ``_DIFF_SIZE_CAP_BYTES`` the full diff is replaced by ``--stat`` +
    first/last ``_DIFF_TAIL_LINES`` lines plus a truncation marker.

    Returns True when a patch file was written (an empty diff still
    writes the file — "implementer made no scoped changes" is a
    signal). Returns False on missing SHAs, git failure, or write
    failure; the caller logs and continues.

    Side-effect free w.r.t. the working tree: ``git diff`` reads only.
    """
    if not start_sha or not end_sha:
        return False

    pathspecs: list[str] = [
        *source_dirs,
        test_dir,
        *_MANIFEST_FILES,
        *_EXCLUDE_PATHSPECS,
    ]
    diff_cmd = [
        "git", "-C", str(project_dir),
        "diff", f"{start_sha}..{end_sha}",
        "--",
        *pathspecs,
    ]
    diff_result = run(diff_cmd, timeout=30)
    if diff_result.exit_code != 0:
        log.debug(
            "capture_iteration_diff: git diff exit %d (%s..%s); stderr=%s",
            diff_result.exit_code, start_sha[:12], end_sha[:12],
            diff_result.stderr.strip()[:200],
        )
        return False

    diff_text = diff_result.stdout

    if len(diff_text.encode("utf-8", errors="replace")) > _DIFF_SIZE_CAP_BYTES:
        diff_text = _truncate_diff_for_archive(
            project_dir, start_sha, end_sha, pathspecs, diff_text,
        )

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(diff_text, encoding="utf-8", errors="replace")
        return True
    except OSError as e:
        log.warning(
            "capture_iteration_diff: write failed for %s (%s)", out_path, e,
        )
        return False


def _truncate_diff_for_archive(
    project_dir: Path,
    start_sha: str,
    end_sha: str,
    pathspecs: list[str],
    full_diff: str,
) -> str:
    """Replace an over-cap diff with --stat + head/tail + truncation marker.

    Only invoked from ``capture_iteration_diff`` when the full diff
    exceeds ``_DIFF_SIZE_CAP_BYTES``. The --stat is always small (one
    line per touched file); head/tail give surface evidence of intent
    without hauling the entire blob into the archive.
    """
    stat_cmd = [
        "git", "-C", str(project_dir),
        "diff", "--stat", f"{start_sha}..{end_sha}",
        "--",
        *pathspecs,
    ]
    stat_result = run(stat_cmd, timeout=30)
    if stat_result.exit_code != 0:
        # Truncation is itself a degraded mode; --stat failing on top of
        # that is worth a log line so a post-mortem reader knows why the
        # truncated patch is missing the stat block. Debug suffices —
        # the size-cap marker in the body already announces the
        # degradation.
        log.debug(
            "_truncate_diff_for_archive: --stat failed exit=%d stderr=%s",
            stat_result.exit_code, stat_result.stderr.strip()[:200],
        )
        stat_text = "(stat unavailable)\n"
    else:
        stat_text = stat_result.stdout

    lines = full_diff.splitlines()
    full_bytes = len(full_diff.encode("utf-8", errors="replace"))
    if len(lines) <= 2 * _DIFF_TAIL_LINES:
        # Few lines but huge bytes (e.g. minified one-liner). Just keep
        # everything — the head/tail dance would emit duplicate content.
        body = full_diff
    else:
        head = "\n".join(lines[:_DIFF_TAIL_LINES])
        tail = "\n".join(lines[-_DIFF_TAIL_LINES:])
        omitted = len(lines) - 2 * _DIFF_TAIL_LINES
        body = (
            f"## First {_DIFF_TAIL_LINES} lines\n{head}\n\n"
            f"## ... [{omitted} lines truncated] ...\n\n"
            f"## Last {_DIFF_TAIL_LINES} lines\n{tail}\n"
        )
    assembled = (
        f"# Diff exceeded {_DIFF_SIZE_CAP_BYTES // 1024} KB cap "
        f"({full_bytes} bytes). Truncated to --stat + head/tail.\n\n"
        "## --stat\n"
        f"{stat_text}\n"
        f"{body}"
    )
    # Defensive ceiling: --stat on a thousand-file change can itself
    # be hundreds of KB, and head+tail of long lines can be ~600 KB
    # apiece; the assembled output can blow past the trigger cap.
    # 2x the trigger cap is the hard ceiling — beyond that we drop the
    # body and keep only the stat (the most compact signal).
    _final_ceiling = 2 * _DIFF_SIZE_CAP_BYTES
    if len(assembled.encode("utf-8", errors="replace")) > _final_ceiling:
        log.debug(
            "_truncate_diff_for_archive: assembled output exceeded final "
            "ceiling (%d > %d); dropping body, keeping --stat only",
            len(assembled), _final_ceiling,
        )
        assembled = (
            f"# Diff exceeded {_DIFF_SIZE_CAP_BYTES // 1024} KB cap "
            f"({full_bytes} bytes); even truncated head/tail exceeded "
            f"{_final_ceiling // 1024} KB ceiling. Keeping --stat only.\n\n"
            "## --stat\n"
            f"{stat_text}"
        )
    return assembled


# --- Helpers ---

def _stage_paths(project_dir: Path, paths: list[str]) -> None:
    """Stage files/dirs that exist, batched into a single git add call."""
    existing = [p for p in paths if (project_dir / p).exists()]
    if existing:
        run(["git", "-C", str(project_dir), "add", *existing])
