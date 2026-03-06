#!/usr/bin/env bash
# scripts/lib/registry.sh — Shipped scenario registry
#
# Provides: registry_register, registry_deregister, registry_list,
#           registry_scenario_dir, registry_run_regression, registry_migrate_from_state
# Requires: DF_PROJECT_ROOT, _DF_SCRIPTS_DIR (set by common.sh)
#           portable_sha256 (from common.sh)

[[ -n "${_DF_LIB_REGISTRY_LOADED:-}" ]] && return 0
_DF_LIB_REGISTRY_LOADED=1

# Dependency: state.sh provides _df_acquire_lock/_df_release_lock for
# concurrency-safe register/deregister. state.sh has its own include guard
# (_DF_LIB_STATE_LOADED), so this is safe even when sourced transitively.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/state.sh"

_DF_REGISTRY_DIR="${DF_PROJECT_ROOT}/.dark-factory/shipped-scenarios"

# registry_register <spec_name> [source_root]
#   Copies scenarios from spec's scenario directory to the persistent registry.
#   Overwrites previous entry if re-shipped.
registry_register() {
    local spec="$1"
    [[ "$spec" == */* || "$spec" == *..* ]] && { echo "ERROR: Invalid spec name: $spec" >&2; return 1; }
    local source_root="${2:-$DF_PROJECT_ROOT}"
    local source_dir="${source_root}/.dark-factory/scenarios/${spec}"
    local target_dir="${_DF_REGISTRY_DIR}/${spec}"

    if [[ ! -d "$source_dir" ]] || [[ -z "$(ls -A "$source_dir" 2>/dev/null)" ]]; then
        echo "WARNING: No scenarios found for ${spec} to register" >&2
        return 1
    fi

    # Create registry entry (overwrite if re-shipped)
    # Defensive: never rm -rf a path outside the registry directory
    [[ "$target_dir" == "${_DF_REGISTRY_DIR}/"* ]] || { echo "BUG: target_dir outside registry: $target_dir" >&2; return 1; }

    # Ensure registry directory exists before acquiring lock
    mkdir -p "${_DF_REGISTRY_DIR}"

    # Acquire lock to prevent interleaved writes from parallel specs.
    # Use trap RETURN to guarantee release on all exit paths (error or success).
    if ! _df_acquire_lock "${_DF_REGISTRY_DIR}/.registry-lockdir" 30; then
        echo "ERROR: Could not acquire registry lock for ${spec}" >&2
        return 1
    fi
    trap '_df_release_lock "${_DF_REGISTRY_DIR}/.registry-lockdir"' RETURN

    rm -rf "$target_dir"
    mkdir -p "$target_dir"
    cp -r "${source_dir}/." "$target_dir/"

    # Write metadata
    local scenario_count
    scenario_count=$(find "$target_dir" -type f \( -name '*.test.*' -o -name '*.spec.*' -o -name 'test_*' \) | wc -l | tr -d ' ')
    local source_hash
    source_hash=$(portable_sha256 "${DF_PROJECT_ROOT}/.dark-factory/specs/${spec}.md" 2>/dev/null || echo "unknown")

    cat > "${target_dir}/.registry-meta" <<EOF
spec=${spec}
registered=$(date -u +%Y-%m-%dT%H:%M:%SZ)
scenario_count=${scenario_count}
source_hash=${source_hash}
EOF

    echo "Registered ${spec} scenarios to shipped registry"
}

# registry_deregister <spec_name>
#   Removes a spec from the shipped registry.
registry_deregister() {
    local spec="$1"
    [[ -z "$spec" ]] && { echo "BUG: registry_deregister called with empty spec" >&2; return 1; }
    [[ "$spec" == */* || "$spec" == *..* ]] && { echo "ERROR: Invalid spec name: $spec" >&2; return 1; }
    local target_dir="${_DF_REGISTRY_DIR}/${spec}"
    [[ "$target_dir" == "${_DF_REGISTRY_DIR}/"* ]] || { echo "BUG: target_dir outside registry: $target_dir" >&2; return 1; }

    if ! _df_acquire_lock "${_DF_REGISTRY_DIR}/.registry-lockdir" 30; then
        echo "ERROR: Could not acquire registry lock for deregister ${spec}" >&2
        return 1
    fi
    trap '_df_release_lock "${_DF_REGISTRY_DIR}/.registry-lockdir"' RETURN

    rm -rf "$target_dir"

    echo "Deregistered ${spec} from shipped registry"
}

# registry_list
#   Outputs one registered spec name per line.
registry_list() {
    [[ ! -d "$_DF_REGISTRY_DIR" ]] && return 0
    local dir
    for dir in "${_DF_REGISTRY_DIR}"/*/; do
        [[ -d "$dir" ]] || continue
        local name; name=$(basename "$dir")
        [[ "$name" == .* ]] && continue
        echo "$name"
    done
}

# registry_scenario_dir <spec_name>
#   Outputs the registry scenario directory path, or returns 1 if not found.
registry_scenario_dir() {
    local dir="${_DF_REGISTRY_DIR}/$1"
    [[ -d "$dir" ]] && echo "$dir" || return 1
}

# registry_run_regression [--exclude <spec>] [--timeout <seconds>]
#   Runs all registered scenarios against current codebase.
#   Returns: 0 if all pass, 1 if any fail.
#
# NOTE: run-scenarios.sh takes a spec NAME and resolves the directory as
# .dark-factory/scenarios/{spec}/. For registry regression, we symlink
# the shipped scenarios into the expected location before running.
registry_run_regression() {
    # Acquire lock to prevent concurrent regression runs (A4)
    mkdir -p "${_DF_REGISTRY_DIR}"
    if ! _df_acquire_lock "${_DF_REGISTRY_DIR}/.regression-lockdir" 60; then
        echo "ERROR: Could not acquire regression lock" >&2
        return 1
    fi
    trap '_df_release_lock "${_DF_REGISTRY_DIR}/.regression-lockdir"' RETURN

    local _regression_start=$SECONDS
    local -a excludes=()
    local timeout="${REGRESSION_TIMEOUT:-300}"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --exclude) [[ $# -lt 2 ]] && { echo "ERROR: --exclude requires a value" >&2; return 1; }; excludes+=("$2"); shift 2 ;;
            --timeout) [[ $# -lt 2 ]] && { echo "ERROR: --timeout requires a value" >&2; return 1; }; timeout="$2"; shift 2 ;;
            *) echo "ERROR: Unknown argument to registry_run_regression: $1" >&2; return 1 ;;
        esac
    done

    local specs
    specs=$(registry_list)
    [[ -z "$specs" ]] && { echo "No shipped scenarios in registry"; return 0; }

    local total=0 passed=0 failed=0 failed_specs=""
    local regression_log_dir="${DF_PROJECT_ROOT}/.dark-factory/reports/regression"
    mkdir -p "$regression_log_dir"

    while IFS= read -r spec; do
        [[ -z "$spec" ]] && continue
        # Check membership in excludes array
        local _excluded=false
        for _ex in "${excludes[@]+"${excludes[@]}"}"; do
            [[ "$spec" == "$_ex" ]] && { _excluded=true; break; }
        done
        [[ "$_excluded" == "true" ]] && continue
        total=$((total + 1))

        # Symlink registry scenarios into the location run-scenarios.sh expects
        local expected_dir="${DF_PROJECT_ROOT}/.dark-factory/scenarios/${spec}"
        local registry_dir
        registry_dir=$(registry_scenario_dir "$spec") || continue
        local needs_cleanup=false
        if [[ ! -d "$expected_dir" ]]; then
            ln -sfn "$registry_dir" "$expected_dir"
            needs_cleanup=true
        fi

        local log_file="${regression_log_dir}/${spec}-regression.log"
        echo "  Running regression: ${spec}..."
        local _rc=0
        if [[ -n "${TIMEOUT_CMD:-}" ]] && [[ "$timeout" -gt 0 ]]; then
            $TIMEOUT_CMD "${timeout}s" bash "${_DF_SCRIPTS_DIR}/run-scenarios.sh" "$spec" \
                >"$log_file" 2>&1 || _rc=$?
        else
            bash "${_DF_SCRIPTS_DIR}/run-scenarios.sh" "$spec" \
                >"$log_file" 2>&1 || _rc=$?
        fi

        if [[ $_rc -eq 0 ]]; then
            echo "    PASS: ${spec}"
            passed=$((passed + 1))
        else
            echo "    FAIL: ${spec} (exit ${_rc})"
            failed=$((failed + 1))
            failed_specs="${failed_specs} ${spec}"
            echo "    (details: ${log_file})"
        fi

        # Clean up symlink if we created it
        [[ "$needs_cleanup" == "true" ]] && rm -f "$expected_dir"
    done <<< "$specs"

    local _regression_wall_s=$(( SECONDS - _regression_start ))
    echo "Regression: ${passed}/${total} passed"
    if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
        emit_regression_run "spec_count=$total" "passed=$passed" "failed=$failed" \
            "wall_s=$_regression_wall_s" 2>/dev/null || true
    fi
    [[ $failed -gt 0 ]] && {
        echo "FAILED:${failed_specs}"
        return 1
    }
    return 0
}

# registry_migrate_from_state
#   One-time migration: populates registry from existing shipped state files.
#   No-op if registry already has entries.
registry_migrate_from_state() {
    [[ -d "$_DF_REGISTRY_DIR" ]] && [[ -n "$(ls -A "$_DF_REGISTRY_DIR" 2>/dev/null)" ]] && return 0

    local migrated=0
    echo "Migrating shipped scenarios to registry..."
    for state_file in "${DF_PROJECT_ROOT}/.dark-factory/state/"*.state; do
        [[ -f "$state_file" ]] || continue
        grep -q "status=shipped" "$state_file" || continue
        local spec
        spec=$(basename "$state_file" .state)
        local scenario_dir="${DF_PROJECT_ROOT}/.dark-factory/scenarios/${spec}"
        if [[ -d "$scenario_dir" ]]; then
            registry_register "$spec" && migrated=$((migrated + 1))
        fi
    done
    echo "Migrated ${migrated} spec(s) to shipped registry"
}
