#!/usr/bin/env bash
set -euo pipefail

# scripts/run-regression.sh — Run all shipped-spec scenarios against current codebase
# Usage: ./scripts/run-regression.sh [--exclude spec1,spec2] [--timeout N]
#
# Exit codes: 0=all pass, 1=failures found, 2=no shipped specs

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXCLUDE_SPECS=""
REGRESSION_TIMEOUT="${REGRESSION_TIMEOUT:-300}"

while [[ $# -gt 0 ]]; do
    case $1 in
        --exclude)
            [[ $# -lt 2 ]] && { echo "ERROR: --exclude requires a value"; exit 1; }
            EXCLUDE_SPECS="$2"
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

# --- Find shipped specs ---
_shipped=()
_state_dir=".dark-factory/state"
_scenarios_dir=".dark-factory/scenarios"

if [[ -d "$_state_dir" ]]; then
    for _state_file in "$_state_dir"/*.state; do
        [[ -f "$_state_file" ]] || continue
        _spec_name="$(basename "$_state_file" .state)"

        # Check if shipped
        if ! grep -q '^status=shipped' "$_state_file" 2>/dev/null; then
            echo "  SKIP  ${_spec_name} (not shipped)"
            continue
        fi

        # Check exclusion list
        if [[ -n "$EXCLUDE_SPECS" ]] && echo "$EXCLUDE_SPECS" | tr ',' '\n' | grep -qx "$_spec_name"; then
            echo "  Skipping excluded spec: $_spec_name"
            continue
        fi

        # Check scenarios exist
        if [[ -d "${_scenarios_dir}/${_spec_name}" ]]; then
            _shipped+=("$_spec_name")
        fi
    done
fi

if [[ ${#_shipped[@]} -eq 0 ]]; then
    echo "No shipped specs with scenarios found for regression testing."
    exit 2
fi

echo "============================================"
echo "  Regression Suite"
echo "  Specs: ${#_shipped[@]}"
echo "============================================"
echo ""

_passed=0
_failed=0
_failed_specs=()

for _spec in "${_shipped[@]}"; do
    echo "--- Running scenarios for: ${_spec} ---"
    _rc=0
    if [[ -n "${TIMEOUT_CMD:-}" ]] && [[ "$REGRESSION_TIMEOUT" -gt 0 ]]; then
        $TIMEOUT_CMD "${REGRESSION_TIMEOUT}s" bash "${SCRIPT_DIR}/run-scenarios.sh" "$_spec" || _rc=$?
    else
        bash "${SCRIPT_DIR}/run-scenarios.sh" "$_spec" || _rc=$?
    fi

    if [[ $_rc -eq 0 ]]; then
        echo "  PASS: ${_spec}"
        _passed=$((_passed + 1))
    else
        echo "  FAIL: ${_spec} (exit ${_rc})"
        _failed=$((_failed + 1))
        _failed_specs+=("$_spec")
    fi
    echo ""
done

# --- Summary ---
echo "============================================"
echo "  Regression Results"
echo "  Total: ${#_shipped[@]}  Passed: ${_passed}  Failed: ${_failed}"
if [[ ${#_failed_specs[@]} -gt 0 ]]; then
    echo "  Failed: ${_failed_specs[*]}"
fi
echo "============================================"

if [[ $_failed -gt 0 ]]; then
    notify_regression_blocked "regression-suite" "${_failed_specs[*]}"
    exit 1
fi

exit 0
