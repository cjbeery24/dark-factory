#!/usr/bin/env bash
# scripts/lib/git-ops.sh — Git operations for CI/CD closed loop
#
# Provides: gitops_preflight, gitops_create_branch, gitops_commit_changes,
#           gitops_push_branch, gitops_create_pr, gitops_merge_queue_add
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

    # Stage source directories
    local _dirs="${SOURCE_DIRS:-${SOURCE_DIR:-src}}"
    local _old_ifs="${IFS}"
    IFS=':'
    local _dir
    for _dir in $_dirs; do
        if [[ -d "$_dir" ]]; then
            git add "$_dir" 2>/dev/null || true
        fi
    done
    IFS="${_old_ifs}"

    # Stage test directory
    if [[ -n "${TEST_DIR:-}" ]] && [[ -d "$TEST_DIR" ]]; then
        git add "$TEST_DIR" 2>/dev/null || true
    fi

    # Stage common project files (only if they exist and are modified)
    local _f
    for _f in package.json package-lock.json go.mod go.sum Cargo.toml Cargo.lock \
              pyproject.toml requirements.txt setup.py setup.cfg tsconfig.json; do
        if [[ -f "$_f" ]]; then
            git add "$_f" 2>/dev/null || true
        fi
    done

    # Stage dark-factory artifacts needed for cross-spec regression
    if [[ -f ".dark-factory/state/${spec_name}.state" ]]; then
        git add ".dark-factory/state/${spec_name}.state" 2>/dev/null || true
    fi
    if [[ -d ".dark-factory/scenarios/${spec_name}" ]]; then
        git add ".dark-factory/scenarios/${spec_name}" 2>/dev/null || true
    fi

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
