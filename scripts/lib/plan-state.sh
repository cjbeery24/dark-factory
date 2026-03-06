#!/usr/bin/env bash
# scripts/lib/plan-state.sh — Plan checkpoint/resume (Phase 5)
#
# Provides:
#   plan_checkpoint_write       — append one spec's completion to checkpoint file
#   plan_checkpoint_load        — find most recent checkpoint for a plan
#   plan_checkpoint_completed_specs — list specs that should not be re-run
#
# Checkpoint file: .dark-factory/state/plan-{name}-{run_id}.jsonl
# Each line: {"spec":"...", "status":"shipped|failed|escalated|budget_skipped", ...}

[[ -n "${_DF_LIB_PLAN_STATE_LOADED:-}" ]] && return 0
_DF_LIB_PLAN_STATE_LOADED=1

# _plan_state_dir — resolve dynamically to handle DF_PROJECT_ROOT changes
_plan_state_dir() {
    echo "${DF_PROJECT_ROOT:-.}/.dark-factory/state"
}

# _plan_json_escape <string>
#   Escapes a string for safe interpolation into JSON values.
_plan_json_escape() {
    printf '%s' "$1" | awk '{gsub(/\\/, "\\\\"); gsub(/"/, "\\\""); printf "%s", (NR>1 ? "\\n" : "") $0}'
}

# plan_checkpoint_write <plan_name> <plan_run_id> <spec> <status> <exit_code> [key=val ...]
plan_checkpoint_write() {
    local plan_name="$1" plan_run_id="$2" spec="$3" status="$4" exit_code="$5"
    shift 5

    local checkpoint_dir
    checkpoint_dir=$(_plan_state_dir)
    mkdir -p "$checkpoint_dir"
    local checkpoint_file="${checkpoint_dir}/plan-${plan_name}-${plan_run_id}.jsonl"

    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    # Compute spec hash if spec file exists
    local spec_hash=""
    local spec_file="${DF_PROJECT_ROOT:-.}/.dark-factory/specs/${spec}.md"
    if [[ -f "$spec_file" ]] && type portable_sha256 >/dev/null 2>&1; then
        spec_hash=$(portable_sha256 "$spec_file" 2>/dev/null || echo "")
    fi
    [[ -z "$spec_hash" ]] && echo "WARNING: Could not compute spec hash for $spec (sha256 unavailable or file missing)" >&2

    # Build JSON line with proper escaping
    local json
    json=$(printf '{\"spec\":\"%s\",\"status\":\"%s\",\"exit_code\":%s,\"ts\":\"%s\",\"run_id\":\"%s\",\"spec_hash\":\"%s\"' \
        "$(_plan_json_escape "$spec")" \
        "$(_plan_json_escape "$status")" \
        "$exit_code" \
        "$ts" \
        "$(_plan_json_escape "$plan_run_id")" \
        "$(_plan_json_escape "$spec_hash")")
    for kv in "$@"; do
        local key="${kv%%=*}" val="${kv#*=}"
        json+=",\"$(_plan_json_escape "$key")\":\"$(_plan_json_escape "$val")\""
    done
    json+="}"

    printf '%s\n' "$json" >> "$checkpoint_file"
    # Flush to disk for crash safety
    sync 2>/dev/null || true
}

# plan_checkpoint_load <plan_name>
#   Finds the most recent checkpoint file for a plan.
#   Prints the file path, or returns 1 if none found.
plan_checkpoint_load() {
    local plan_name="$1"
    local checkpoint_dir
    checkpoint_dir=$(_plan_state_dir)
    local pattern="${checkpoint_dir}/plan-${plan_name}-*.jsonl"

    # Collect matching files
    local -a files=()
    for f in $pattern; do
        [[ -f "$f" ]] && files+=("$f")
    done
    [[ ${#files[@]} -eq 0 ]] && return 1

    if command -v jq >/dev/null 2>&1; then
        # Pick the file with the most recent ts in its first line
        local latest="" latest_ts=""
        for f in "${files[@]}"; do
            local ts
            ts=$(head -1 "$f" | jq -r '.ts // ""' 2>/dev/null) || continue
            [[ -z "$ts" ]] && continue
            if [[ -z "$latest_ts" ]] || [[ "$ts" > "$latest_ts" ]]; then
                latest="$f"
                latest_ts="$ts"
            fi
        done
        [[ -n "$latest" ]] && { echo "$latest"; return 0; }
    fi

    # Fallback: lexicographic sort (newest run_id suffix wins)
    printf '%s\n' "${files[@]}" | sort | tail -1
}

# plan_checkpoint_completed_specs <checkpoint_file>
#   Prints spec names with status in {shipped, budget_skipped}.
#   These specs should be skipped on resume. Failed/escalated specs are re-run.
plan_checkpoint_completed_specs() {
    local checkpoint_file="$1"
    [[ -f "$checkpoint_file" ]] || return 0

    if command -v jq >/dev/null 2>&1; then
        jq -r 'select(.status == "shipped" or .status == "budget_skipped") | .spec' \
            "$checkpoint_file" 2>/dev/null
    else
        # Fallback: sed-based extraction (POSIX-compatible, no grep -P)
        grep '"status":"shipped"\|"status":"budget_skipped"' "$checkpoint_file" 2>/dev/null \
            | sed 's/.*"spec":"\([^"]*\)".*/\1/' 2>/dev/null || true
    fi
}

# plan_checkpoint_spec_hash <checkpoint_file> <spec>
#   Returns the spec_hash recorded in the checkpoint for a given spec.
plan_checkpoint_spec_hash() {
    local checkpoint_file="$1" spec="$2"
    [[ -f "$checkpoint_file" ]] || return 1

    if command -v jq >/dev/null 2>&1; then
        jq -r --arg s "$spec" 'select(.spec == $s) | .spec_hash // ""' \
            "$checkpoint_file" 2>/dev/null | tail -1
    else
        # Fallback: sed-based extraction (POSIX-compatible)
        grep "\"spec\":\"${spec}\"" "$checkpoint_file" 2>/dev/null \
            | sed 's/.*"spec_hash":"\([^"]*\)".*/\1/' 2>/dev/null | tail -1 || echo ""
    fi
}
