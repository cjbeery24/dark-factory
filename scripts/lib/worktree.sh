#!/usr/bin/env bash
# scripts/lib/worktree.sh — Git worktree lifecycle management for concurrent spec execution
#
# Provides: worktree_create, worktree_remove, worktree_list, worktree_cleanup_all,
#           conflict_detect
# Requires: DF_PROJECT_ROOT (set by common.sh)

[[ -n "${_DF_LIB_WORKTREE_LOADED:-}" ]] && return 0
_DF_LIB_WORKTREE_LOADED=1

# Worktree base directory (under project root, gitignored)
_DF_WORKTREE_BASE="${DF_PROJECT_ROOT}/.dark-factory/worktrees"

# worktree_create <spec_name> <run_id>
#   Creates an isolated git worktree for a spec.
#   Sets: _DF_WORKTREE_PATH, _DF_WORKTREE_BRANCH
#   Outputs: worktree path to stdout
worktree_create() {
    local spec="$1" run_id="$2"
    local branch="dark-factory/${spec}-${run_id:0:16}"
    local worktree_path="${_DF_WORKTREE_BASE}/${spec}-${run_id:0:16}"

    mkdir -p "$_DF_WORKTREE_BASE"

    local base="${GIT_BASE_BRANCH:-main}"
    local _wt_err=""
    _wt_err=$(git -C "$DF_PROJECT_ROOT" worktree add \
        "$worktree_path" \
        -b "$branch" \
        "${GIT_REMOTE:-origin}/${base}" 2>&1) \
    || {
        # Branch may already exist (resume case) — reuse it
        _wt_err=$(git -C "$DF_PROJECT_ROOT" worktree add \
            "$worktree_path" \
            "$branch" 2>&1) \
        || { echo "ERROR: Failed to create worktree for ${spec}: ${_wt_err}" >&2; return 1; }
    }

    _DF_WORKTREE_PATH="$worktree_path"
    _DF_WORKTREE_BRANCH="$branch"

    # Create .dark-factory runtime dirs in worktree
    mkdir -p "${worktree_path}/.dark-factory/"{specs,scenarios,reviews,reports,audit,state}

    # Copy config into worktree (snapshot — avoids symlink crossing isolation
    # boundary and prevents mid-execution config drift across concurrent specs)
    if [[ -f "${DF_PROJECT_ROOT}/.dark-factory/config.sh" ]]; then
        cp "${DF_PROJECT_ROOT}/.dark-factory/config.sh" \
           "${worktree_path}/.dark-factory/config.sh" \
            || { echo "ERROR: Failed to copy config.sh into worktree for ${spec}" >&2; return 1; }
    fi

    # Copy the spec file into the worktree
    if [[ -f "${DF_PROJECT_ROOT}/.dark-factory/specs/${spec}.md" ]]; then
        cp "${DF_PROJECT_ROOT}/.dark-factory/specs/${spec}.md" \
           "${worktree_path}/.dark-factory/specs/${spec}.md" \
            || { echo "ERROR: Failed to copy spec file into worktree for ${spec}" >&2; return 1; }
    else
        echo "ERROR: Spec file not found: ${DF_PROJECT_ROOT}/.dark-factory/specs/${spec}.md" >&2
        return 1
    fi

    echo "$worktree_path"
}

# worktree_remove <spec_name> <run_id>
#   Removes a worktree after spec completes. Refuses if unpushed commits
#   or uncommitted changes exist.
worktree_remove() {
    local spec="$1" run_id="$2"
    local worktree_path="${_DF_WORKTREE_BASE}/${spec}-${run_id:0:16}"
    local branch="dark-factory/${spec}-${run_id:0:16}"

    if [[ -d "$worktree_path" ]]; then
        # Safety: verify branch was pushed before removing
        if ! git -C "$worktree_path" rev-parse --abbrev-ref '@{upstream}' &>/dev/null; then
            echo "WARNING: Worktree ${spec} has no upstream (never pushed) — skipping removal" >&2
            return 1
        fi
        local unpushed
        unpushed=$(git -C "$worktree_path" log --oneline "@{upstream}..HEAD" 2>/dev/null | wc -l | tr -d ' ')
        if [[ "$unpushed" -gt 0 ]]; then
            echo "WARNING: Worktree ${spec} has ${unpushed} unpushed commit(s) — skipping removal" >&2
            return 1
        fi
        # Check for uncommitted changes
        if ! git -C "$worktree_path" diff --quiet 2>/dev/null || \
           ! git -C "$worktree_path" diff --cached --quiet 2>/dev/null; then
            echo "WARNING: Worktree ${spec} has uncommitted changes — skipping removal" >&2
            return 1
        fi
        git -C "$DF_PROJECT_ROOT" worktree remove "$worktree_path" || {
            echo "WARNING: Failed to remove worktree at $worktree_path" >&2
            return 1
        }
    fi

    # Clean up branch if it was merged
    git -C "$DF_PROJECT_ROOT" branch -d "$branch" 2>/dev/null || true
}

# worktree_list
#   Lists all active dark-factory worktree paths (one per line).
worktree_list() {
    git -C "$DF_PROJECT_ROOT" worktree list --porcelain \
        | { grep "^worktree ${_DF_WORKTREE_BASE}" || true; } \
        | sed 's/^worktree //'
}

# worktree_cleanup_all
#   Force-removes all dark-factory worktrees and prunes stale references.
worktree_cleanup_all() {
    local wt
    while IFS= read -r wt; do
        [[ -n "$wt" ]] && git -C "$DF_PROJECT_ROOT" worktree remove "$wt" --force 2>/dev/null
    done < <(worktree_list)
    # Prune stale worktree references
    git -C "$DF_PROJECT_ROOT" worktree prune
    # Clean up stale dark-factory/ branches (merged only, -d not -D)
    git -C "$DF_PROJECT_ROOT" branch --list 'dark-factory/*' | while IFS= read -r branch; do
        branch="$(echo "$branch" | sed 's/^[[:space:]]*//')"  # trim leading whitespace
        git -C "$DF_PROJECT_ROOT" branch -d "$branch" 2>/dev/null || true
    done
}

# _parse_target_files <spec_file>
#   Extracts target-files from a spec's YAML metadata block.
#   Outputs one file path per line to stdout.
_parse_target_files() {
    local spec_file="$1"
    local in_target_files=false
    while IFS= read -r line; do
        if [[ "$line" =~ ^target-files: ]]; then
            in_target_files=true
            continue
        fi
        if [[ "$in_target_files" == "true" ]]; then
            if [[ "$line" =~ ^[[:space:]]*-[[:space:]]+(.*) ]]; then
                local file="${BASH_REMATCH[1]}"
                file="${file## }"  # trim leading
                file="${file%% }"  # trim trailing
                # Validate: must look like a file path (contains / or .)
                [[ "$file" == *[/.]* ]] && echo "$file"
            else
                break
            fi
        fi
    done < "$spec_file"
}

# conflict_detect <spec1> [spec2] ...
#   Detects file overlap between specs based on target-files: metadata.
#   Returns: 0 if no conflicts, 1 if overlaps found (with report to stderr).
conflict_detect() {
    local -A file_owners  # file → space-delimited spec list

    for spec in "$@"; do
        local spec_file="${DF_PROJECT_ROOT}/.dark-factory/specs/${spec}.md"
        [[ ! -f "$spec_file" ]] && continue

        local files
        files=$(_parse_target_files "$spec_file")

        if [[ -z "$files" ]]; then
            echo "WARNING: Spec ${spec} has no target-files metadata — skipping conflict analysis" >&2
            continue
        fi

        while IFS= read -r file; do
            [[ -z "$file" ]] && continue
            if [[ -n "${file_owners[$file]:-}" ]]; then
                file_owners[$file]="${file_owners[$file]} ${spec}"
            else
                file_owners[$file]="$spec"
            fi
        done <<< "$files"
    done

    # Report conflicts
    local has_conflicts=false
    for file in "${!file_owners[@]}"; do
        local owners="${file_owners[$file]}"
        local owner_count
        owner_count=$(echo "$owners" | wc -w | tr -d ' ')
        if [[ "$owner_count" -gt 1 ]]; then
            echo "CONFLICT: ${file} targeted by: ${owners}" >&2
            has_conflicts=true
        fi
    done

    [[ "$has_conflicts" == "true" ]] && return 1
    return 0
}
