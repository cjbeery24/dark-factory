#!/usr/bin/env bash
# scripts/lib/git-ops.sh — Git operations for CI/CD closed loop
#
# Provides: gitops_preflight, gitops_create_branch, gitops_commit_changes,
#           gitops_push_branch, gitops_create_pr, gitops_merge_queue_add,
#           gitops_commit_in_worktree, gitops_push_worktree, gitops_create_pr_from_worktree
# Requires: git, gh CLI (for PR creation)

[[ -n "${_DF_LIB_GITOPS_LOADED:-}" ]] && return 0
_DF_LIB_GITOPS_LOADED=1

# gitops_preflight
#   Validates: inside git repo, gh CLI available (if AUTO_PR enabled),
#   clean working tree (warn on dirty), remote reachable.
#   Returns 1 on hard failure, 0 on success.
gitops_preflight() {
    if ! git rev-parse --is-inside-work-tree &>/dev/null; then
        echo "ERROR: Not inside a git repository." >&2
        return 1
    fi

    if ! command -v gh &>/dev/null; then
        echo "ERROR: gh CLI not found. Install it: https://cli.github.com/" >&2
        return 1
    fi

    if ! gh auth status &>/dev/null 2>&1; then
        echo "ERROR: gh CLI not authenticated. Run 'gh auth login' first." >&2
        return 1
    fi

    local _remote="${GIT_REMOTE:-origin}"
    if ! git ls-remote "$_remote" HEAD &>/dev/null 2>&1; then
        echo "ERROR: Cannot reach git remote '${_remote}'." >&2
        return 1
    fi

    if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
        echo "WARNING: Working tree has uncommitted changes." >&2
    fi

    return 0
}

# gitops_create_branch <spec_name> <run_id>
#   Creates branch: dark-factory/<spec_name>-<short_run_id>
#   Based on: GIT_BASE_BRANCH (default: main)
#   Idempotent: checks out existing branch if it already exists.
gitops_create_branch() {
    local spec_name="${1:?gitops_create_branch requires spec_name}"
    local run_id="${2:?gitops_create_branch requires run_id}"
    local short_id="${run_id:0:12}"
    local branch="dark-factory/${spec_name}-${short_id}"
    local base="${GIT_BASE_BRANCH:-main}"

    if git rev-parse --verify "$branch" &>/dev/null; then
        git checkout "$branch" 2>/dev/null
    else
        git checkout -b "$branch" "$base" 2>/dev/null
    fi
}

# gitops_commit_changes <spec_name> <run_id>
#   Stages: SOURCE_DIRS contents, TEST_DIR, common project files.
#   Excludes: .dark-factory/ internals (except per-spec state and scenarios for regression).
#   Commit message: "dark-factory: implement <spec_name> [run:<run_id>]"
#   Idempotent: skips commit if nothing staged.
gitops_commit_changes() {
    local spec_name="${1:?gitops_commit_changes requires spec_name}"
    local run_id="${2:?gitops_commit_changes requires run_id}"

    _gitops_stage -- "$spec_name" "."

    # Skip commit if nothing to commit
    if git diff --cached --quiet 2>/dev/null; then
        echo "  No changes to commit." >&2
        return 0
    fi

    if [[ "${GITOPS_SKIP_HOOKS:-false}" == "true" ]]; then
        git commit --no-verify -m "dark-factory: implement ${spec_name} [run:${run_id}]"
    else
        git commit -m "dark-factory: implement ${spec_name} [run:${run_id}]"
    fi
}

# gitops_push_branch <branch_name>
#   Pushes to GIT_REMOTE (default: origin) with --set-upstream.
#   Returns 1 on auth failures or remote rejection.
gitops_push_branch() {
    local branch="${1:?gitops_push_branch requires branch_name}"
    local remote="${GIT_REMOTE:-origin}"

    if ! git push --set-upstream "$remote" "$branch"; then
        echo "ERROR: Failed to push branch '${branch}' to '${remote}'." >&2
        return 1
    fi
}

# gitops_create_pr <spec_name> <branch_name> <base_branch>
#   Creates a PR using gh CLI with structured body.
#   Idempotent: returns existing PR URL if one already exists for the branch.
#   Outputs: PR URL to stdout.
gitops_create_pr() {
    local spec_name="${1:?gitops_create_pr requires spec_name}"
    local branch="${2:?gitops_create_pr requires branch_name}"
    local base="${3:?gitops_create_pr requires base_branch}"

    # Check for existing PR on this branch
    local _existing
    _existing=$(gh pr list --head "$branch" --json url --jq '.[0].url' 2>/dev/null || true)
    if [[ -n "$_existing" ]]; then
        echo "$_existing"
        return 0
    fi

    # Build PR body
    local _body=""
    _body+="## Summary"$'\n'$'\n'
    _body+="Automated implementation of spec \`${spec_name}\`."$'\n'$'\n'

    # Quality gate results (from report file if available)
    local _report_file=".dark-factory/reports/${spec_name}-report.md"
    if [[ -f "$_report_file" ]]; then
        _body+="## Quality Gate Results"$'\n'$'\n'
        # Extract gate results table (lines with pass/fail/PASS/FAIL)
        local _gates
        _gates=$(grep -Ei '(pass|fail|PASS|FAIL)' "$_report_file" 2>/dev/null | head -20 || true)
        if [[ -n "$_gates" ]]; then
            _body+='```'$'\n'"${_gates}"$'\n''```'$'\n'$'\n'
        fi
    fi

    # Reviewer verdict (confidence only, no excerpts that might leak scenario info)
    local _review_file=".dark-factory/reviews/${spec_name}-review.md"
    if [[ -f "$_review_file" ]]; then
        local _verdict
        _verdict=$(grep -Ei '(verdict|confidence|SHIP|NO.SHIP)' "$_review_file" 2>/dev/null | head -5 || true)
        if [[ -n "$_verdict" ]]; then
            _body+="## Reviewer Verdict"$'\n'$'\n'
            _body+='```'$'\n'"${_verdict}"$'\n''```'$'\n'$'\n'
        fi
    fi

    # Pipeline metadata
    _body+="## Pipeline Metadata"$'\n'$'\n'
    _body+="- **Run ID:** \`${RUN_ID:-unknown}\`"$'\n'
    _body+="- **Iterations:** \`${ITERATION:-unknown}\`"$'\n'
    _body+="- **Autonomy Level:** \`${AUTONOMY_LEVEL:-3}\`"$'\n'$'\n'
    _body+="---"$'\n'
    _body+="*Generated by [Dark Factory](https://github.com/anthropics/dark-factory)*"

    # Truncate body to 60,000 chars (GitHub limit is 65,536)
    if [[ ${#_body} -gt 60000 ]]; then
        _body="${_body:0:59900}"$'\n\n'"*[Body truncated — exceeded 60,000 character limit]*"
    fi

    local _pr_url
    _pr_url=$(gh pr create \
        --title "dark-factory: ${spec_name}" \
        --body "$_body" \
        --base "$base" \
        --head "$branch" \
        --label "dark-factory" \
        --label "auto-generated" \
        2>&1) || {
        echo "ERROR: gh pr create failed: ${_pr_url}" >&2
        return 1
    }

    echo "$_pr_url"
}

# gitops_merge_queue_add <pr_url>
#   If MERGE_QUEUE_ENABLED=true: adds PR to merge queue.
#   Otherwise: logs that manual merge is required.
gitops_merge_queue_add() {
    local pr_url="${1:?gitops_merge_queue_add requires pr_url}"

    if [[ "${MERGE_QUEUE_ENABLED:-false}" == "true" ]]; then
        if gh pr merge --merge-queue "$pr_url" 2>&1; then
            echo "  PR added to merge queue."
        else
            echo "  WARNING: Failed to add PR to merge queue." >&2
        fi
    else
        echo "  Merge queue not enabled. Manual merge required."
    fi
}

# gitops_auto_merge_high_trust <pr_url> <trust_score>
#   Enables auto-merge for PRs from high-trust specs (trust >= 80).
#   Requires: AUTO_MERGE_HIGH_TRUST=true, AUTO_PR=true, trust_score >= 80.
#   Uses gh pr merge --auto --squash to enable GitHub auto-merge.
gitops_auto_merge_high_trust() {
    local pr_url="${1:?gitops_auto_merge_high_trust requires pr_url}"
    local trust_score="${2:-0}"

    [[ "${AUTO_MERGE_HIGH_TRUST:-false}" == "true" ]] || return 0
    (( trust_score >= 80 )) || return 0

    echo "  HIGH-TRUST AUTO-MERGE: trust=$trust_score, enabling auto-merge..." >&2
    if gh pr merge --auto --squash "$pr_url" >/dev/null 2>&1; then
        echo "  Auto-merge enabled. GitHub will merge when checks pass." >&2
    else
        echo "  WARNING: Failed to enable auto-merge." >&2
    fi
}

# ==============================================================================
# Worktree-aware operations (Phase 4: concurrent execution)
# ==============================================================================

# _gitops_stage <git_prefix_args...> -- <spec_name> <root_path>
#   Shared staging logic used by both gitops_commit_changes and gitops_commit_in_worktree.
#   git_prefix_args: empty for cwd, or "-C" "$wt_path"
#   root_path: filesystem path for existence checks AND the git working directory.
#     For cwd: "." (checks ./dir, git adds dir)
#     For worktree: "$wt_path" (checks $wt_path/dir, git -C $wt_path adds dir)
_gitops_stage() {
    local -a git_args=()
    local spec_name="" root_path=""

    # Parse: collect args before --, then spec_name and root_path after
    while [[ $# -gt 0 ]]; do
        if [[ "$1" == "--" ]]; then
            shift
            spec_name="$1"
            root_path="$2"
            break
        fi
        git_args+=("$1")
        shift
    done

    # _stage_add: stage a path, warn on failure (existence already checked by caller)
    _stage_add() {
        local _err
        _err=$(git ${git_args[@]+"${git_args[@]}"} add "$1" 2>&1) || {
            echo "WARNING: git add '$1' failed: ${_err}" >&2
        }
    }

    # Stage source directories
    local _dirs="${SOURCE_DIRS:-${SOURCE_DIR:-src}}"
    local _old_ifs="${IFS}"
    IFS=':'
    local _dir
    for _dir in $_dirs; do
        [[ -d "${root_path}/${_dir}" ]] && _stage_add "$_dir"
    done
    IFS="${_old_ifs}"

    # Stage test directory
    if [[ -n "${TEST_DIR:-}" ]] && [[ -d "${root_path}/${TEST_DIR}" ]]; then
        _stage_add "$TEST_DIR"
    fi

    # Stage common project files
    local _f
    for _f in package.json package-lock.json go.mod go.sum Cargo.toml Cargo.lock \
              pyproject.toml requirements.txt setup.py setup.cfg tsconfig.json; do
        [[ -f "${root_path}/${_f}" ]] && _stage_add "$_f"
    done

    # Stage dark-factory artifacts for cross-spec regression
    if [[ -f "${root_path}/.dark-factory/state/${spec_name}.state" ]]; then
        _stage_add ".dark-factory/state/${spec_name}.state"
    fi
    if [[ -d "${root_path}/.dark-factory/scenarios/${spec_name}" ]]; then
        _stage_add ".dark-factory/scenarios/${spec_name}"
    fi

    unset -f _stage_add
}

# gitops_commit_in_worktree <worktree_path> <spec_name> <run_id>
#   Stages and commits within a worktree. No branch checkout needed.
gitops_commit_in_worktree() {
    local wt_path="${1:?gitops_commit_in_worktree requires worktree_path}"
    local spec_name="${2:?gitops_commit_in_worktree requires spec_name}"
    local run_id="${3:?gitops_commit_in_worktree requires run_id}"

    _gitops_stage -C "$wt_path" -- "$spec_name" "$wt_path"

    # Skip commit if nothing to commit
    if git -C "$wt_path" diff --cached --quiet 2>/dev/null; then
        echo "  No changes to commit." >&2
        return 0
    fi

    if [[ "${GITOPS_SKIP_HOOKS:-false}" == "true" ]]; then
        git -C "$wt_path" commit --no-verify -m "dark-factory: implement ${spec_name} [run:${run_id}]"
    else
        git -C "$wt_path" commit -m "dark-factory: implement ${spec_name} [run:${run_id}]"
    fi
}

# gitops_push_worktree <worktree_path>
#   Pushes the worktree's current branch to the remote.
gitops_push_worktree() {
    local wt_path="${1:?gitops_push_worktree requires worktree_path}"
    local branch
    branch=$(git -C "$wt_path" rev-parse --abbrev-ref HEAD)
    local remote="${GIT_REMOTE:-origin}"

    if ! git -C "$wt_path" push --set-upstream "$remote" "$branch"; then
        echo "ERROR: Failed to push worktree branch '${branch}' to '${remote}'." >&2
        return 1
    fi
}

# gitops_create_pr_from_worktree <worktree_path> <spec_name> <base_branch>
#   Creates a PR from the worktree's branch. Reuses gitops_create_pr logic.
gitops_create_pr_from_worktree() {
    local wt_path="${1:?gitops_create_pr_from_worktree requires worktree_path}"
    local spec_name="${2:?gitops_create_pr_from_worktree requires spec_name}"
    local base="${3:?gitops_create_pr_from_worktree requires base_branch}"

    local branch
    branch=$(git -C "$wt_path" rev-parse --abbrev-ref HEAD)

    # Delegate to existing gitops_create_pr
    gitops_create_pr "$spec_name" "$branch" "$base"
}
