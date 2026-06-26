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


def commit_ship_ledger(spec_name: str, project_dir: Path) -> bool:
    """Commit the durable ship record (ledger + scenario archive) on SHIP.

    Runs INDEPENDENT of ``auto_pr``: ``shipped.jsonl`` is the durable record of
    what shipped and MUST survive a working-copy reset, which wipes the
    gitignored runtime state — the exact way the database-foundation ship row
    was lost. Stages ONLY the ship-record paths (never impl) and commits with a
    pathspec so other staged/working changes are untouched; it composes with a
    separate (manual or auto) post-ship commit of the source. Best-effort:
    returns False (logged) on any git failure or no-op — it must never unship.
    """
    paths = [
        ".dark-factory/plan/shipped.jsonl",
        f".dark-factory/shipped-scenarios/{spec_name}",
    ]
    existing = [p for p in paths if (project_dir / p).exists()]
    if not existing:
        return False
    # add (picks up an untracked scenario archive), then commit WITH the pathspec
    # so only these paths land in the commit regardless of the rest of the index.
    _stage_paths(project_dir, existing)
    result = run([
        "git", "-C", str(project_dir), "commit", "--no-verify",
        "-m", f"chore(df): record {spec_name} ship (durable ledger + scenario archive)",
        "--", *existing,
    ])
    if result.exit_code == 0:
        return True
    # Non-zero: distinguish a BENIGN no-op (ledger already committed) from a
    # GENUINE failure (gitignored path, misconfigured git, mid-merge) that leaves
    # the record non-durable — the exact loss class this exists to kill. Ground
    # truth: is the ledger STILL uncommitted/ignored on disk after the attempt?
    ledger = ".dark-factory/plan/shipped.jsonl"
    status = run([
        "git", "-C", str(project_dir), "status", "--porcelain", "--ignored",
        "--", ledger,
    ])
    if status.stdout.strip():
        log.warning(
            "ship-ledger NOT committed for %s — record is NOT durable; is %s "
            "gitignored or git misconfigured? git: %s",
            spec_name, ledger, (result.stderr or result.stdout or "").strip()[:160],
        )
    else:
        log.info("ship-ledger: nothing to commit for %s (already recorded)", spec_name)
    return False


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


def changed_paths(project_dir: Path, source_dirs: list[str]) -> set[str]:
    """Project-relative paths changed (staged/modified + untracked) in source dirs.

    Set-valued companion to count_files_changed — used to scope review
    findings to the surface the run actually touched (the diff half of the
    review-prepass "owned surface"). Works on iteration 1: ``git diff HEAD``
    + untracked captures the implementer's uncommitted work, unlike
    build_iter_diff which is empty for iteration<=1.

    FAILS CLOSED: the diff half is the load-bearing guarantee that a vuln the
    implementer introduces in a file OUTSIDE the architect's file_map is still
    ship-gating. A *partial* result would silently demote such a finding to
    advisory (a false-clean), so any git failure (not a work tree, a command
    erroring) RAISES rather than returning a short set — the caller then fails
    safe to "no scoping / everything gating". The legitimate unborn-HEAD case
    (fresh repo, no commits yet) is NOT a failure: there are no committed
    changes, so the diff-vs-HEAD step is skipped and untracked files still
    count. Paths are git's repo-root-relative form (forward slashes, no leading
    ``./``); the consumer normalizes before comparison.
    """
    if run(["git", "-C", str(project_dir), "rev-parse",
            "--is-inside-work-tree"]).exit_code != 0:
        raise DarkFactoryError(f"changed_paths: {project_dir} is not a git work tree")
    has_head = run(["git", "-C", str(project_dir), "rev-parse",
                    "--verify", "--quiet", "HEAD"]).exit_code == 0

    paths: set[str] = set()
    for src in source_dirs:
        cmds = [["git", "-C", str(project_dir), "ls-files", "--others",
                 "--exclude-standard", "--", src]]
        if has_head:  # diff-vs-HEAD only meaningful once a commit exists
            cmds.insert(0, ["git", "-C", str(project_dir),
                            "diff", "--name-only", "HEAD", "--", src])
        for args in cmds:
            result = run(args)
            if result.exit_code != 0:
                raise DarkFactoryError(
                    f"changed_paths: git failed ({' '.join(args[3:])}): "
                    f"{(result.stderr or '').strip()[:200]}"
                )
            for line in result.stdout.strip().splitlines():
                p = line.strip()
                if p:
                    paths.add(p)
    return paths


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

# Per-file byte cap on untracked file content appended as synthetic
# diff blocks (Phase 1a follow-up to the original Gap 3 fix). Untracked
# files are read from the working tree directly; a single oversized new
# file (e.g. a generated migration baseline, a vendored asset) shouldn't
# blow past the global diff cap on its own. 200 KB per file leaves room
# for ~5 large new files before the global 1 MB cap kicks in.
_UNTRACKED_PER_FILE_CAP_BYTES = 200_000

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

    # Phase 1a (Gap 3 follow-up, 2026-04-29): `git diff <a>..<b>` only
    # captures TRACKED-file changes. The implementer's NEW files are
    # untracked at the time `git stash create -u` runs and — on git
    # 2.50.1 specifically — are silently dropped from the stash tree.
    # For modification-heavy iters that's a known acceptable skew; for
    # bootstrap-heavy iters (where the implementer's whole work IS new
    # files), the captured patch is misleading. Append synthetic diff
    # blocks for untracked files to close the gap.
    #
    # Reads from the LIVE working tree (not from `end_sha`'s tree) on
    # purpose — `end_sha` is missing the files we want by definition.
    # The archive-time vs iteration-end-time tree state is essentially
    # the same: nothing mutates the working tree between iter end and
    # the archive snapshot at the top of the next iter (or at terminal
    # handlers).
    diff_text = _append_untracked_files_to_diff(
        project_dir, pathspecs, diff_text,
    )

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


def _append_untracked_files_to_diff(
    project_dir: Path,
    pathspecs: list[str],
    diff_text: str,
) -> str:
    """Append synthetic ``diff --git /dev/null b/<path>`` blocks for
    untracked files in scope.

    Closes the new-file evidence gap on git 2.50.1 stash-create-u skew
    (Gap 3 follow-up). ``git ls-files --others --exclude-standard``
    accepts the same pathspec magic as ``git diff``, so the include +
    exclude filters compose naturally.

    Per-file content is bounded at ``_UNTRACKED_PER_FILE_CAP_BYTES``;
    over-cap files emit a `[truncated; N bytes total]` marker after
    the first chunk. Binary files emit `Binary files differ` only
    (matching git's default diff output for untracked binaries
    against /dev/null).

    Best-effort: ls-files failures or per-file read errors are logged
    and skipped; the rest of the patch still ships. Returns the
    original ``diff_text`` plus appended blocks. Emits one summary
    log line at info level recording the appended-vs-skipped split
    so post-mortem can recognize when files were dropped.
    """
    ls_cmd = [
        "git", "-C", str(project_dir),
        "ls-files", "--others", "--exclude-standard",
        "--",
        *pathspecs,
    ]
    ls_result = run(ls_cmd, timeout=15)
    if ls_result.exit_code != 0:
        log.debug(
            "_append_untracked_files_to_diff: ls-files exit %d; stderr=%s",
            ls_result.exit_code, ls_result.stderr.strip()[:200],
        )
        return diff_text

    untracked_paths = [p for p in ls_result.stdout.splitlines() if p.strip()]
    if not untracked_paths:
        return diff_text

    appended_blocks: list[str] = [diff_text]
    appended_count = 0
    skipped_count = 0
    for rel_path in untracked_paths:
        block = _synthesize_untracked_diff_block(project_dir, rel_path)
        if block:
            appended_blocks.append(block)
            appended_count += 1
        else:
            skipped_count += 1

    # S4: aggregate observability — operators reading default-level logs
    # see when files were dropped. Without this, a permission flip or
    # symlink in scope produces a silently-incomplete patch.
    if skipped_count:
        log.info(
            "untracked capture: %d appended, %d skipped (read failure / "
            "symlink / non-regular)",
            appended_count, skipped_count,
        )
    elif appended_count:
        log.debug(
            "untracked capture: %d untracked file(s) appended",
            appended_count,
        )
    return "".join(appended_blocks)


def _synthesize_untracked_diff_block(
    project_dir: Path, rel_path: str,
) -> str:
    """Build a synthetic ``diff --git /dev/null b/<path>`` block for one
    untracked file.

    Returns the block as a single string ending in newline, or empty
    string on read failure (logged at warning — see Linus belated
    review S3). Binary detection is heuristic: if the file's first
    8 KB contains a NUL byte, treat as binary and emit `Binary files
    differ` matching git's default.

    Single-read pattern (Linus B2): file is read once into bytes up
    to ``_UNTRACKED_PER_FILE_CAP_BYTES + 1`` (the +1 detects oversize
    without a separate stat). Sniffing the first 8 KB for NUL bytes
    happens against the same buffer — no separate sniff syscall, no
    stat-vs-read race for truncation accounting.

    Empty files emit a header-only block with no ``@@`` hunk (Linus
    S1) — matches git's actual output for empty new files.

    Symlinks are skipped with a warning (Linus S2). Real git emits
    `mode 120000` for symlinks; doing that correctly requires reading
    the link target, not the file content. For an Implementer-evidence
    archive, skipping with a log line is more honest than misleading
    100644 + target-content output.

    No-newline-at-eof: when the file's last byte is not ``\\n``, emit
    git's canonical ``\\ No newline at end of file`` marker (Linus B1).
    Without this the synthesized hunk's line count silently disagrees
    with the actual content for files that don't end in newline.
    """
    abs_path = project_dir / rel_path

    # S2: skip symlinks. Emitting them as 100644 with target content
    # would lie about the file mode and source.
    try:
        if abs_path.is_symlink():
            log.warning(
                "untracked capture: skipping symlink %s "
                "(mode 120000 not synthesized)", abs_path,
            )
            return ""
        if not abs_path.is_file():
            return ""
    except OSError as e:
        log.warning(
            "_synthesize_untracked_diff_block: stat failed for %s (%s)",
            abs_path, e,
        )
        return ""

    header = (
        f"\ndiff --git a/{rel_path} b/{rel_path}\n"
        f"new file mode 100644\n"
        f"--- /dev/null\n"
        f"+++ b/{rel_path}\n"
    )

    # B2: single-read pattern. Read cap+1 bytes — the extra byte is the
    # truncation oracle (if we got it, the file is bigger than the cap).
    # Same buffer used for both NUL-byte sniff and decode, so binary
    # detection and content can't disagree on a file mutated mid-read.
    try:
        with abs_path.open("rb") as fh:
            raw = fh.read(_UNTRACKED_PER_FILE_CAP_BYTES + 1)
    except OSError as e:
        log.warning(
            "_synthesize_untracked_diff_block: read failed for %s (%s)",
            abs_path, e,
        )
        return ""

    truncated = len(raw) > _UNTRACKED_PER_FILE_CAP_BYTES
    if truncated:
        raw = raw[:_UNTRACKED_PER_FILE_CAP_BYTES]

    # Binary heuristic: NUL byte in the first 8 KB of the buffer we
    # already have.
    if b"\x00" in raw[:8192]:
        return header + f"Binary files /dev/null and b/{rel_path} differ\n"

    if not raw:
        # S1: empty file — git emits header-only, no @@ hunk.
        return header

    content = raw.decode("utf-8", errors="replace")
    # B1: detect no-newline-at-eof BEFORE splitlines (which strips it).
    ends_with_nl = content.endswith("\n")

    lines = content.splitlines()
    if not lines:
        # Buffer was non-empty but splitlines returned []. Pathological
        # case: file is just a newline byte. Emit empty hunk consistent
        # with S1 above.
        return header

    plus_lines = "\n".join(f"+{ln}" for ln in lines)
    body = f"@@ -0,0 +1,{len(lines)} @@\n{plus_lines}\n"
    if not ends_with_nl:
        # Canonical git marker — without this, `git apply --check`
        # round-trips fail on files lacking trailing newline.
        body += "\\ No newline at end of file\n"
    if truncated:
        # Use stat to report the actual file size in the marker. Best-
        # effort: if stat fails we still know the captured size.
        try:
            actual_size = abs_path.stat().st_size
        except OSError:
            actual_size = -1
        size_repr = (
            f"{actual_size} bytes total" if actual_size >= 0
            else "size unknown"
        )
        body += (
            f"\\ [truncated; {size_repr}, "
            f"{_UNTRACKED_PER_FILE_CAP_BYTES} captured]\n"
        )
    return header + body


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
