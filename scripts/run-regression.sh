#!/usr/bin/env bash
set -euo pipefail

# scripts/run-regression.sh — Run all shipped-spec scenarios against current codebase
# Usage: ./scripts/run-regression.sh [--exclude spec1,spec2] [--timeout N]
#
# Thin wrapper around registry_run_regression (scripts/lib/registry.sh).
# Handles: CLI parsing, library bootstrap, config loading, timeout detection,
# legacy migration, comma-separated --exclude splitting, and failure notification.
#
# Exit codes: 0=all pass, 1=failures found, 2=no shipped specs

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Parse CLI args (before library bootstrap) ---
_EXCLUDE_ARGS=()
_EXCLUDE_NAMES=()
REGRESSION_TIMEOUT="${REGRESSION_TIMEOUT:-300}"

while [[ $# -gt 0 ]]; do
    case $1 in
        --exclude)
            [[ $# -lt 2 ]] && { echo "ERROR: --exclude requires a value"; exit 1; }
            # Split comma-separated values into separate --exclude flags
            IFS=',' read -ra _parts <<< "$2"
            for _part in "${_parts[@]}"; do
                if [[ -n "$_part" ]]; then
                    _EXCLUDE_ARGS+=(--exclude "$_part")
                    _EXCLUDE_NAMES+=("$_part")
                fi
            done
            shift 2
            ;;
        --timeout)
            [[ $# -lt 2 ]] && { echo "ERROR: --timeout requires a value"; exit 1; }
            REGRESSION_TIMEOUT="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# --- Library bootstrap ---
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/notify.sh"

# --- Config ---
load_config ".dark-factory/config.sh" "SOURCE_DIR TEST_DIR DARK_FACTORY_HOME" "LANGUAGE"

# --- Timeout detection ---
detect_timeout_cmd

# --- Migrate legacy state-file entries if registry is empty ---
registry_migrate_from_state

# --- Count registered specs (excluding excluded ones) for header ---
_spec_count=0
while IFS= read -r _s; do
    [[ -z "$_s" ]] && continue
    _is_excluded=false
    for _ex in "${_EXCLUDE_NAMES[@]+"${_EXCLUDE_NAMES[@]}"}"; do
        [[ "$_s" == "$_ex" ]] && { _is_excluded=true; break; }
    done
    [[ "$_is_excluded" == "true" ]] && continue
    _spec_count=$((_spec_count + 1))
done < <(registry_list)

if [[ $_spec_count -eq 0 ]]; then
    echo "No shipped specs with scenarios found for regression testing."
    exit 2
fi

echo "============================================"
echo "  Regression Suite (registry-based)"
echo "  Specs: ${_spec_count}"
echo "============================================"
echo ""

# --- Run via library function, capture output to extract failed spec names ---
_output_file="$(mktemp)"
trap 'rm -f "$_output_file"' EXIT

_rc=0
registry_run_regression \
    ${_EXCLUDE_ARGS[@]+"${_EXCLUDE_ARGS[@]}"} \
    --timeout "$REGRESSION_TIMEOUT" \
    2>&1 | tee "$_output_file" || _rc=$?

# --- Notification on failure (library function does not notify) ---
if [[ $_rc -ne 0 ]]; then
    # Extract failed spec names from library output (format: "FAILED: spec1 spec2")
    _failed_names=""
    _failed_line=$(grep "^FAILED:" "$_output_file" 2>/dev/null || true)
    if [[ -n "$_failed_line" ]]; then
        _failed_names="${_failed_line#FAILED:}"
    fi
    notify_regression_blocked "regression-suite" "${_failed_names:-unknown}"
    exit 1
fi

exit 0
