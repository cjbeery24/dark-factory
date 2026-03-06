#!/usr/bin/env bash
set -euo pipefail

# Dark Factory Plan Executor — Parallel multi-spec pipeline with dependency ordering
#
# Usage: ./scripts/dark-factory-plan.sh <plan-name> [options]
#
# Options:
#   --max-concurrent N    Max parallel specs (default: config or 3)
#   --dry-run             Show dispatch order without executing
#   --resume              Resume a previously interrupted plan run
#   --ci                  CI mode: auto-resume, no interactive prompts
#
# Requires: bash 4+ (associative arrays), jq (optional, for token budget)

# ==============================================================================
# Globals
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PLAN_NAME=""
MAX_CONCURRENT=""
DRY_RUN=false
RESUME_MODE=""
CI_MODE=false
_CLI_EXTRA_ARGS=()

# ==============================================================================
# Library bootstrap
# ==============================================================================

source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/state.sh"
source "${SCRIPT_DIR}/lib/audit.sh"
source "${SCRIPT_DIR}/lib/metrics.sh"
source "${SCRIPT_DIR}/lib/notify.sh"

# Load config (non-fatal if missing)
load_config ".dark-factory/config.sh" "" "LANGUAGE" 2>/dev/null || true

# ==============================================================================
# Bash version check
# ==============================================================================

if [[ "${BASH_VERSINFO[0]}" -lt 4 ]]; then
    echo "ERROR: dark-factory-plan.sh requires bash 4+ (associative arrays)."
    echo "Current: bash ${BASH_VERSION}"
    echo "macOS ships bash 3.2 — install bash 4+ via Homebrew: brew install bash"
    exit 1
fi

# ==============================================================================
# Plan Parsing
# ==============================================================================

PLAN_SPECS=()
declare -A PLAN_DEPS=()
declare -A PLAN_PRIORITY=()

# parse_plan <plan_file>
#   Populates PLAN_SPECS[], PLAN_DEPS[], PLAN_PRIORITY[] from the plan table.
#   Also reads depends-on from each spec's metadata block as source of truth.
parse_plan() {
    local plan_file="$1"
    local in_table=false

    while IFS= read -r line; do
        # Skip empty lines, headers, and table separator
        [[ -z "$line" ]] && continue
        [[ "$line" =~ ^#\  ]] && continue
        [[ "$line" =~ ^[[:space:]]*\|[[:space:]]*-+ ]] && continue

        # Detect table header row
        if [[ "$line" =~ ^\|[[:space:]]*Spec[[:space:]]*\| ]]; then
            in_table=true
            continue
        fi

        if [[ "$in_table" == "true" ]] && [[ "$line" =~ ^\| ]]; then
            # Parse: | spec | deps | priority |
            local spec deps priority
            spec=$(echo "$line" | awk -F'|' '{print $2}' | tr -d ' ')
            deps=$(echo "$line" | awk -F'|' '{print $3}' | tr -d ' ')
            priority=$(echo "$line" | awk -F'|' '{print $4}' | tr -d ' ')

            [[ -z "$spec" ]] && continue

            # Validate spec name
            validate_spec_name "$spec" || {
                echo "ERROR: Invalid spec name in plan: '${spec}'" >&2
                return 1
            }

            # Reject duplicate spec names
            for _existing in "${PLAN_SPECS[@]}"; do
                if [[ "$_existing" == "$spec" ]]; then
                    echo "ERROR: Duplicate spec in plan: '${spec}'" >&2
                    return 1
                fi
            done

            PLAN_SPECS+=("$spec")

            # Validate priority is numeric (default 99 if empty)
            local _prio="${priority:-99}"
            if ! [[ "$_prio" =~ ^[0-9]+$ ]]; then
                echo "ERROR: Non-numeric priority '${_prio}' for spec '${spec}'" >&2
                return 1
            fi
            PLAN_PRIORITY[$spec]="$_prio"

            # Parse deps (comma-separated)
            if [[ "$deps" == "—" ]] || [[ "$deps" == "-" ]] || [[ -z "$deps" ]]; then
                PLAN_DEPS[$spec]=""
            else
                # Convert commas to spaces
                PLAN_DEPS[$spec]="${deps//,/ }"
            fi
        else
            # Stop table parsing on non-table line after we started
            [[ "$in_table" == "true" ]] && break
        fi
    done < "$plan_file"

    if [[ ${#PLAN_SPECS[@]} -eq 0 ]]; then
        echo "ERROR: No specs found in plan file: ${plan_file}" >&2
        return 1
    fi

    # Override deps from individual spec metadata (source of truth)
    for spec in "${PLAN_SPECS[@]}"; do
        local spec_file="${DF_PROJECT_ROOT}/.dark-factory/specs/${spec}.md"
        if [[ -f "$spec_file" ]]; then
            local meta_deps=""
            while IFS= read -r mline; do
                if [[ "$mline" =~ ^depends-on:[[:space:]]*\[(.*)\] ]]; then
                    meta_deps="${BASH_REMATCH[1]}"
                    meta_deps="${meta_deps// /}"      # strip spaces: "spec-a,spec-b"
                    meta_deps="${meta_deps//,/ }"     # commas to spaces: "spec-a spec-b"
                    break
                fi
            done < "$spec_file"
            if [[ -n "$meta_deps" ]]; then
                PLAN_DEPS[$spec]="$meta_deps"
            fi
        else
            echo "WARNING: Spec file not found: ${spec_file}" >&2
        fi
    done

    # Validate all dependencies reference known specs
    for spec in "${PLAN_SPECS[@]}"; do
        for dep in ${PLAN_DEPS[$spec]:-}; do
            local found=false
            for s in "${PLAN_SPECS[@]}"; do
                [[ "$s" == "$dep" ]] && { found=true; break; }
            done
            if [[ "$found" != "true" ]]; then
                echo "ERROR: Spec '${spec}' depends on unknown spec '${dep}'" >&2
                return 1
            fi
        done
    done
}

# ==============================================================================
# Priority Sort Helper
# ==============================================================================

# _sort_specs_by_priority
#   Reads spec names from stdin (one per line), sorts by PLAN_PRIORITY
#   (ascending, default 99), preserving parse order for equal priorities.
#   Priority only acts as a tie-breaker within a dependency tier — specs
#   becoming ready at different iterations of the topo-sort outer loop are
#   dispatched in the order they become ready, regardless of priority.
_sort_specs_by_priority() {
    while IFS= read -r spec; do
        printf '%d\t%s\n' "${PLAN_PRIORITY[$spec]:-99}" "$spec"
    done | sort -t$'\t' -k1,1n -s | cut -f2
}

# ==============================================================================
# Topological Sort (Kahn's Algorithm)
# O(V^2 * E) — intentionally simple for expected plan sizes (5-20 specs).
# ==============================================================================

DISPATCH_ORDER=()

topological_sort() {
    local -A in_degree
    local -a queue=()
    local -a result=()

    # Initialize in-degrees
    for spec in "${PLAN_SPECS[@]}"; do
        in_degree[$spec]=0
    done
    for spec in "${PLAN_SPECS[@]}"; do
        for dep in ${PLAN_DEPS[$spec]:-}; do
            in_degree[$spec]=$(( ${in_degree[$spec]} + 1 ))
        done
    done

    # Seed queue with zero in-degree specs, sorted by priority
    while IFS= read -r _s; do
        queue+=("$_s")
    done < <(
        for spec in "${PLAN_SPECS[@]}"; do
            [[ ${in_degree[$spec]} -eq 0 ]] && echo "$spec"
        done | _sort_specs_by_priority
    )

    # Process queue
    while [[ ${#queue[@]} -gt 0 ]]; do
        local current="${queue[0]}"
        if [[ ${#queue[@]} -gt 1 ]]; then
            queue=("${queue[@]:1}")
        else
            queue=()
        fi
        result+=("$current")

        # Reduce in-degree for dependents, collect newly-ready specs
        local -a _newly_ready=()
        for spec in "${PLAN_SPECS[@]}"; do
            for dep in ${PLAN_DEPS[$spec]:-}; do
                if [[ "$dep" == "$current" ]]; then
                    in_degree[$spec]=$(( ${in_degree[$spec]} - 1 ))
                    [[ ${in_degree[$spec]} -eq 0 ]] && _newly_ready+=("$spec")
                fi
            done
        done
        # Sort newly-ready specs by priority before appending to queue
        if [[ ${#_newly_ready[@]} -gt 0 ]]; then
            while IFS= read -r _s; do
                queue+=("$_s")
            done < <(printf '%s\n' "${_newly_ready[@]}" | _sort_specs_by_priority)
        fi
    done

    # Cycle detection
    if [[ ${#result[@]} -ne ${#PLAN_SPECS[@]} ]]; then
        echo "ERROR: Circular dependency detected in plan" >&2
        echo "Specs involved in cycle:" >&2
        for spec in "${PLAN_SPECS[@]}"; do
            local found=false
            for s in "${result[@]}"; do
                [[ "$s" == "$spec" ]] && found=true
            done
            [[ "$found" == "false" ]] && echo "  - ${spec}" >&2
        done
        return 1
    fi

    DISPATCH_ORDER=("${result[@]}")
}

# ==============================================================================
# Resource Governor
# ==============================================================================

governor_preflight() {
    local max_concurrent="${MAX_CONCURRENT:-3}"

    # Check memory (warn if < 4GB per concurrent slot)
    local mem_gb mem_label
    if [[ "$(uname)" == "Darwin" ]]; then
        mem_gb=$(( $(sysctl -n hw.memsize) / 1073741824 ))
        mem_label="total"
    else
        mem_gb=$(( $(grep MemAvailable /proc/meminfo | awk '{print $2}') / 1048576 ))
        mem_label="available"
    fi
    local required_mem=$(( max_concurrent * 4 ))
    if [[ $mem_gb -lt $required_mem ]]; then
        echo "WARNING: ${mem_gb}GB ${mem_label} memory, ${required_mem}GB recommended for ${max_concurrent} concurrent specs" >&2
        echo "Consider reducing MAX_CONCURRENT_SPECS" >&2
    fi

    # Validate jq availability (required for token budget tracking)
    if ! command -v jq >/dev/null 2>&1; then
        echo "WARNING: jq not found — token budget tracking disabled" >&2
    fi

    # Check Docker daemon is responsive
    if ! docker info >/dev/null 2>&1; then
        echo "ERROR: Docker daemon not running" >&2
        return 1
    fi
}

# ==============================================================================
# Conflict Detection Serialization
# ==============================================================================

# serialize_conflicting_specs
#   When CONFLICT_STRATEGY=serialize, adds synthetic dependency edges
#   between specs that share target files.
#   NOTE: Uses DISPATCH_ORDER from the first topological sort to determine
#   "first owner". The re-sort after adding edges may produce a different order,
#   but serialization correctness is preserved (just priority may be sub-optimal).
serialize_conflicting_specs() {
    local -A file_first_owner  # file → first spec that claims it

    for spec in "${DISPATCH_ORDER[@]}"; do
        local spec_file="${DF_PROJECT_ROOT}/.dark-factory/specs/${spec}.md"
        [[ ! -f "$spec_file" ]] && continue

        # Uses shared _parse_target_files from worktree.sh
        local files
        files=$(_parse_target_files "$spec_file")
        [[ -z "$files" ]] && continue

        while IFS= read -r file; do
            [[ -z "$file" ]] && continue
            if [[ -n "${file_first_owner[$file]:-}" ]]; then
                local first="${file_first_owner[$file]}"
                if [[ "$first" != "$spec" ]]; then
                    # Add synthetic dependency: spec depends on first owner
                    if [[ -z "${PLAN_DEPS[$spec]:-}" ]]; then
                        PLAN_DEPS[$spec]="$first"
                    elif [[ " ${PLAN_DEPS[$spec]} " != *" ${first} "* ]]; then
                        PLAN_DEPS[$spec]="${PLAN_DEPS[$spec]} ${first}"
                    fi
                    echo "  Serialized: ${spec} now depends on ${first} (shared file: ${file})" >&2
                fi
            else
                file_first_owner[$file]="$spec"
            fi
        done <<< "$files"
    done
}

# ==============================================================================
# Script-scope associative arrays (signal handler safety under set -u)
# ==============================================================================

declare -A spec_status=()
declare -A spec_pid=()
declare -A spec_worktree=()
declare -A spec_run_id=()

# ==============================================================================
# Signal Handler
# ==============================================================================

plan_cleanup() {
    set +e  # Bug 6: cleanup must not abort on individual failures
    echo ""
    echo "Shutting down plan execution..."
    # Guard: if PLAN_SPECS is not yet populated, nothing to clean up
    [[ ${#PLAN_SPECS[@]} -eq 0 ]] && return 0

    # Send SIGTERM to all running child subshells.
    # Each child runs dark-factory.sh which has its own EXIT trap that calls cleanup_all
    # (which stops Docker containers via cleanup_containers). Killing the subshell
    # triggers that trap chain.
    for spec in "${PLAN_SPECS[@]}"; do
        if [[ "${spec_status[$spec]:-}" == "running" ]]; then
            kill "${spec_pid[$spec]}" 2>/dev/null || true
        fi
    done
    # Wait for children to exit and run their cleanup traps
    wait 2>/dev/null || true
    # Cleanup worktrees based on config
    for spec in "${PLAN_SPECS[@]}"; do
        if [[ "${spec_status[$spec]:-}" != "shipped" ]] && [[ -n "${spec_run_id[$spec]:-}" ]]; then
            if [[ "${WORKTREE_CLEANUP_ON_FAIL:-false}" == "true" ]]; then
                worktree_remove "$spec" "${spec_run_id[$spec]}" 2>/dev/null || true
            else
                echo "  Keeping worktree for failed spec: ${spec} (${spec_worktree[$spec]:-})"
            fi
        fi
    done
}

# ==============================================================================
# Dispatch Engine
# ==============================================================================

dispatch_plan() {
    local max_concurrent="${MAX_CONCURRENT:-3}"
    local active_count=0

    # Initialize all as pending
    for spec in "${PLAN_SPECS[@]}"; do
        spec_status[$spec]="pending"
    done

    # Generate a plan-level RUN_ID
    local plan_run_id
    plan_run_id=$(od -An -tx1 -N8 /dev/urandom | tr -d ' \n')
    export _DF_PLAN_RUN_ID="$plan_run_id"

    # Set events file at the real project root (before worktree overrides)
    export _DF_EVENTS_FILE="${DF_PROJECT_ROOT}/.dark-factory/events.jsonl"

    # Bug 2: Pre-dispatch budget enforcement
    local _plan_turns_allocated=0
    local _per_spec_turns="${AGENT_MAX_TURNS:-200}"
    local _plan_budget="${PLAN_TOKEN_BUDGET:-0}"

    echo ""
    echo "============================================"
    echo "  Plan Executor: ${PLAN_NAME}"
    echo "  Specs: ${#PLAN_SPECS[@]}"
    echo "  Max concurrent: ${max_concurrent}"
    echo "  Plan run ID: ${plan_run_id}"
    echo "============================================"
    echo ""
    echo "Dispatch order: ${DISPATCH_ORDER[*]}"
    echo ""

    local plan_start_epoch
    plan_start_epoch=$(date +%s)

    if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
        emit_plan_start "plan_name=$PLAN_NAME" "spec_count=${#PLAN_SPECS[@]}" \
            "max_concurrent=$max_concurrent" || true
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "[dry-run] Would dispatch specs in this order:"
        for spec in "${DISPATCH_ORDER[@]}"; do
            local deps="${PLAN_DEPS[$spec]:-none}"
            echo "  ${spec} (depends on: ${deps:-none})"
        done
        return 0
    fi

    while true; do
        # Check for completed children via exit-code files
        for spec in "${PLAN_SPECS[@]}"; do
            if [[ "${spec_status[$spec]}" == "running" ]]; then
                local exit_file="${spec_worktree[$spec]}/exit_code"
                if [[ -f "$exit_file" ]]; then
                    local exit_code
                    exit_code=$(cat "$exit_file" 2>/dev/null) || continue
                    [[ -z "$exit_code" ]] && continue
                    wait "${spec_pid[$spec]}" 2>/dev/null || true  # clean up zombie
                    active_count=$((active_count - 1))

                    if [[ "$exit_code" -eq 0 ]]; then
                        spec_status[$spec]="shipped"
                        echo "[SHIPPED] ${spec}"
                        plan_checkpoint_write "$PLAN_NAME" "$plan_run_id" "$spec" "shipped" "$exit_code" 2>/dev/null || true
                    else
                        spec_status[$spec]="failed"
                        echo "[FAILED]  ${spec} (exit ${exit_code})"
                        plan_checkpoint_write "$PLAN_NAME" "$plan_run_id" "$spec" "failed" "$exit_code" 2>/dev/null || true
                    fi
                # Bug 1: Detect dead processes (OOM-killed, SIGKILL) that never wrote exit_code
                elif ! kill -0 "${spec_pid[$spec]}" 2>/dev/null; then
                    wait "${spec_pid[$spec]}" 2>/dev/null || true
                    active_count=$((active_count - 1))
                    spec_status[$spec]="failed"
                    echo "[FAILED]  ${spec} (process terminated without exit code — possible OOM)" >&2
                    plan_checkpoint_write "$PLAN_NAME" "$plan_run_id" "$spec" "failed" "137" \
                        "failure_reason=process_terminated" 2>/dev/null || true
                fi
            fi
        done

        # Check token budget post-hoc (if governor enabled)
        if ! governor_check_budget_posthoc "$plan_run_id"; then
            echo "WARNING: Token budget exceeded — not dispatching new specs" >&2
            # Still wait for running specs to finish
            local any_running=false
            for spec in "${PLAN_SPECS[@]}"; do
                [[ "${spec_status[$spec]}" == "running" ]] && any_running=true
            done
            [[ "$any_running" != "true" ]] && break
            sleep "${_DF_DISPATCH_POLL_INTERVAL:-1}"
            continue
        fi

        # Dispatch ready specs (dependencies satisfied, under concurrency cap)
        for spec in "${DISPATCH_ORDER[@]}"; do
            [[ "${spec_status[$spec]}" != "pending" ]] && continue
            [[ $active_count -ge $max_concurrent ]] && break

            # Check for failed dependencies (unrecoverable)
            local deps_blocked=false
            for dep in ${PLAN_DEPS[$spec]:-}; do
                if [[ "${spec_status[$dep]}" == "failed" ]] || [[ "${spec_status[$dep]}" == "escalated" ]] || [[ "${spec_status[$dep]}" == "budget_skipped" ]]; then
                    spec_status[$spec]="failed"
                    echo "[BLOCKED] ${spec} (dependency ${dep} failed)"
                    deps_blocked=true
                    break
                fi
            done
            [[ "$deps_blocked" == "true" ]] && continue

            # Check all dependencies satisfied
            local deps_met=true
            for dep in ${PLAN_DEPS[$spec]:-}; do
                if [[ "${spec_status[$dep]}" != "shipped" ]]; then
                    deps_met=false
                    break
                fi
            done
            [[ "$deps_met" != "true" ]] && continue

            # Bug 2: Pre-dispatch budget check
            if [[ "$_plan_budget" -gt 0 ]] && \
               [[ $((_plan_turns_allocated + _per_spec_turns)) -gt "$_plan_budget" ]]; then
                spec_status[$spec]="budget_skipped"
                echo "[BUDGET]  ${spec} skipped (would exceed plan token budget: $((_plan_turns_allocated + _per_spec_turns))/${_plan_budget})" >&2
                plan_checkpoint_write "$PLAN_NAME" "$plan_run_id" "$spec" "budget_skipped" "0" 2>/dev/null || true
                continue
            fi

            # Dispatch this spec
            local run_id
            run_id="${plan_run_id}-$(od -An -tx1 -N4 /dev/urandom | tr -d ' \n')"
            spec_run_id[$spec]="$run_id"

            # 1D: Graceful handling of worktree_create failure
            local wt_path
            wt_path=$(worktree_create "$spec" "$run_id") || {
                spec_status[$spec]="failed"
                echo "[FAILED]  ${spec} (worktree creation failed)" >&2
                continue
            }
            spec_worktree[$spec]="$wt_path"

            # Ensure audit log directory exists
            mkdir -p "${DF_PROJECT_ROOT}/.dark-factory/audit"

            # Launch dark-factory.sh in background, targeting the worktree
            (
                _ec=0
                "${SCRIPT_DIR}/dark-factory.sh" "$spec" \
                    --worktree-path "$wt_path" \
                    --ci --auto-pr \
                    ${_CLI_EXTRA_ARGS[@]+"${_CLI_EXTRA_ARGS[@]}"} \
                    >>"${DF_PROJECT_ROOT}/.dark-factory/audit/${spec}-plan-${plan_run_id}.log" 2>&1 \
                    || _ec=$?
                # Atomic write: write to temp, then rename (prevents parent reading partial data)
                echo "$_ec" > "${wt_path}/exit_code.tmp"
                mv "${wt_path}/exit_code.tmp" "${wt_path}/exit_code"
            ) &

            spec_pid[$spec]=$!
            spec_status[$spec]="running"
            active_count=$((active_count + 1))
            _plan_turns_allocated=$((_plan_turns_allocated + _per_spec_turns))
            if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
                _DF_CURRENT_SPEC="$spec" emit_spec_dispatched \
                    "worktree_path=$wt_path" "priority=${PLAN_PRIORITY[$spec]:-5}" \
                    "depends_on=${PLAN_DEPS[$spec]:-none}" || true
            fi
            echo "[DISPATCH] ${spec} (worktree: ${wt_path})"
        done

        # Check plan wall-time timeout (A3)
        local _plan_wall_limit="${PLAN_MAX_WALL_TIME_S:-0}"
        if (( _plan_wall_limit > 0 && $(date +%s) - plan_start_epoch > _plan_wall_limit )); then
            echo "PLAN TIMEOUT: Wall-time limit exceeded (${_plan_wall_limit}s)" >&2
            # Send SIGTERM to all running specs
            for spec in "${PLAN_SPECS[@]}"; do
                if [[ "${spec_status[$spec]}" == "running" ]]; then
                    kill -TERM "${spec_pid[$spec]}" 2>/dev/null || true
                fi
            done
            # Wait up to 10s for graceful shutdown
            local _grace_start=$SECONDS
            while (( SECONDS - _grace_start < 10 )); do
                local _any_alive=false
                for spec in "${PLAN_SPECS[@]}"; do
                    if [[ "${spec_status[$spec]}" == "running" ]] && kill -0 "${spec_pid[$spec]}" 2>/dev/null; then
                        _any_alive=true
                    fi
                done
                [[ "$_any_alive" != "true" ]] && break
                sleep 1
            done
            # Capture running specs BEFORE reclassifying them
            local _running_at_timeout=""
            for spec in "${PLAN_SPECS[@]}"; do
                [[ "${spec_status[$spec]}" == "running" ]] && _running_at_timeout+="${spec} "
            done
            # SIGKILL any remaining
            for spec in "${PLAN_SPECS[@]}"; do
                if [[ "${spec_status[$spec]}" == "running" ]] && kill -0 "${spec_pid[$spec]}" 2>/dev/null; then
                    kill -KILL "${spec_pid[$spec]}" 2>/dev/null || true
                fi
            done
            # Collect exit codes and write checkpoints
            for spec in "${PLAN_SPECS[@]}"; do
                if [[ "${spec_status[$spec]}" == "running" ]]; then
                    wait "${spec_pid[$spec]}" 2>/dev/null || true
                    local exit_file="${spec_worktree[$spec]}/exit_code"
                    if [[ -f "$exit_file" ]]; then
                        local ec
                        ec=$(cat "$exit_file" 2>/dev/null) || ec=1
                        if [[ "$ec" -eq 0 ]]; then
                            spec_status[$spec]="shipped"
                            plan_checkpoint_write "$PLAN_NAME" "$plan_run_id" "$spec" "shipped" "$ec" 2>/dev/null || true
                        else
                            spec_status[$spec]="failed"
                            plan_checkpoint_write "$PLAN_NAME" "$plan_run_id" "$spec" "failed" "$ec" 2>/dev/null || true
                        fi
                    else
                        spec_status[$spec]="failed"
                        plan_checkpoint_write "$PLAN_NAME" "$plan_run_id" "$spec" "failed" "1" \
                            "failure_reason=plan_timeout" 2>/dev/null || true
                    fi
                    active_count=$((active_count - 1))
                fi
            done
            if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
                emit_plan_timeout "wall_s=$(( $(date +%s) - plan_start_epoch ))" \
                    "limit_s=$_plan_wall_limit" "running_specs=${_running_at_timeout}" || true
            fi
            break
        fi

        # Check termination condition
        local all_done=true
        for spec in "${PLAN_SPECS[@]}"; do
            if [[ "${spec_status[$spec]}" == "pending" || "${spec_status[$spec]}" == "running" ]]; then
                all_done=false
                break
            fi
        done

        [[ "$all_done" == "true" ]] && break

        # Brief sleep to avoid busy-wait (children take minutes; sub-second polling is wasteful)
        sleep "${_DF_DISPATCH_POLL_INTERVAL:-2}"
    done

    # ── Summary Report ───────────────────────────────────────────
    echo ""
    echo "============================================"
    echo "  Plan Execution Summary"
    echo "============================================"
    local shipped=0 failed=0 skipped=0
    for spec in "${PLAN_SPECS[@]}"; do
        case "${spec_status[$spec]}" in
            shipped)        echo "  PASS  ${spec}"; shipped=$((shipped + 1)) ;;
            failed)         echo "  FAIL  ${spec}"; failed=$((failed + 1)) ;;
            escalated)      echo "  WARN  ${spec} (escalated)"; failed=$((failed + 1)) ;;
            budget_skipped) echo "  SKIP  ${spec} (budget exceeded)"; skipped=$((skipped + 1)) ;;
            *)              echo "  ????  ${spec} (${spec_status[$spec]})" ;;
        esac
    done
    echo "============================================"
    echo "  ${shipped} shipped, ${failed} failed, ${skipped} skipped"
    echo "============================================"

    if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
        emit_plan_complete "shipped_count=$shipped" "failed_count=$failed" "skipped_count=$skipped" \
            "wall_s=$(( $(date +%s) - plan_start_epoch ))" || true
    fi

    # Cleanup worktrees for shipped specs (keep failed for debugging)
    for spec in "${PLAN_SPECS[@]}"; do
        if [[ "${spec_status[$spec]}" == "shipped" ]]; then
            if [[ "${WORKTREE_CLEANUP_ON_SHIP:-true}" == "true" ]]; then
                worktree_remove "$spec" "${spec_run_id[$spec]}" 2>/dev/null || true
            fi
        fi
    done

    [[ $failed -gt 0 ]] && return 1
    return 0
}

# ==============================================================================
# Main (only when executed directly, not when sourced)
# ==============================================================================

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then

    # Argument parsing
    if [[ $# -lt 1 ]] || [[ "${1:0:2}" == "--" ]]; then
        echo "Usage: $0 <plan-name> [--max-concurrent N] [--dry-run] [--resume] [--ci]"
        exit 1
    fi

    PLAN_NAME="$1"
    shift

    while [[ $# -gt 0 ]]; do
        case $1 in
            --max-concurrent)
                [[ $# -lt 2 ]] && { echo "ERROR: --max-concurrent requires a value"; exit 1; }
                MAX_CONCURRENT="$2"
                shift 2
                ;;
            --dry-run)
                DRY_RUN=true
                _CLI_EXTRA_ARGS+=(--dry-run)
                shift
                ;;
            --resume)
                RESUME_MODE="resume"
                _CLI_EXTRA_ARGS+=(--resume)
                shift
                ;;
            --ci)
                CI_MODE=true
                _CLI_EXTRA_ARGS+=(--ci)
                shift
                ;;
            *)
                echo "Unknown option: $1"
                exit 1
                ;;
        esac
    done

    PLAN_FILE="${DF_PROJECT_ROOT}/.dark-factory/plans/${PLAN_NAME}.md"
    if [[ ! -f "$PLAN_FILE" ]]; then
        echo "ERROR: Plan file not found: ${PLAN_FILE}"
        exit 1
    fi

    MAX_CONCURRENT="${MAX_CONCURRENT:-${MAX_CONCURRENT_SPECS:-3}}"

    trap 'plan_cleanup' EXIT INT TERM

    parse_plan "$PLAN_FILE"
    topological_sort

    # Conflict detection
    if ! conflict_detect "${PLAN_SPECS[@]}"; then
        case "${CONFLICT_STRATEGY:-abort}" in
            abort)
                echo "ERROR: File conflicts detected. Resolve by adjusting specs or set CONFLICT_STRATEGY=serialize" >&2
                exit 1
                ;;
            serialize)
                echo "WARNING: Serializing conflicting specs" >&2
                serialize_conflicting_specs
                # Re-sort with new edges — may fail if serialization created a cycle
                if ! topological_sort 2>/dev/null; then
                    echo "ERROR: Conflict serialization created a dependency cycle." >&2
                    echo "This happens when specs with shared files already have opposing dependency edges." >&2
                    echo "Fix: Remove the circular dependency in your specs, or use CONFLICT_STRATEGY=abort to identify the conflicting files." >&2
                    exit 1
                fi
                ;;
            warn)
                echo "WARNING: Proceeding despite file conflicts" >&2
                ;;
        esac
    fi

    # Resource preflight (non-fatal warnings)
    governor_preflight || exit 1

    # Dispatch
    dispatch_plan
    exit $?
fi
