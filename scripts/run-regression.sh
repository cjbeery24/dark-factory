#!/usr/bin/env bash
set -euo pipefail

# scripts/run-regression.sh — Run all shipped-spec scenarios against current codebase
# Usage: ./scripts/run-regression.sh [--exclude spec1,spec2] [--timeout N]
#
# Uses the shipped scenario registry (Phase 4). Falls back to state-file migration
# on first run if registry is empty.
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

# --- Migrate legacy state-file entries if registry is empty ---
registry_migrate_from_state

# --- Find registered specs ---
_shipped=()
while IFS= read -r _spec_name; do
    [[ -z "$_spec_name" ]] && continue

    # Check exclusion list
    if [[ -n "$EXCLUDE_SPECS" ]] && echo "$EXCLUDE_SPECS" | tr ',' '\n' | grep -qx "$_spec_name"; then
        echo "  Skipping excluded spec: $_spec_name"
        continue
    fi

    _shipped+=("$_spec_name")
done < <(registry_list)

if [[ ${#_shipped[@]} -eq 0 ]]; then
    echo "No shipped specs with scenarios found for regression testing."
    exit 2
fi

echo "============================================"
echo "  Regression Suite (registry-based)"
echo "  Specs: ${#_shipped[@]}"
echo "============================================"
echo ""

_passed=0
_failed=0
_failed_specs=()

for _spec in "${_shipped[@]}"; do
    echo "--- Running scenarios for: ${_spec} ---"

    # Symlink registry scenarios into the location run-scenarios.sh expects
    _expected_dir="${DF_PROJECT_ROOT}/.dark-factory/scenarios/${_spec}"
    _registry_dir=$(registry_scenario_dir "$_spec") || continue
    _needs_cleanup=false
    if [[ ! -d "$_expected_dir" ]]; then
        ln -sf "$_registry_dir" "$_expected_dir"
        _needs_cleanup=true
    fi

    _rc=0
    if [[ -n "${TIMEOUT_CMD:-}" ]] && [[ "$REGRESSION_TIMEOUT" -gt 0 ]]; then
        $TIMEOUT_CMD "${REGRESSION_TIMEOUT}s" bash "${SCRIPT_DIR}/run-scenarios.sh" "$_spec" || _rc=$?
    else
        bash "${SCRIPT_DIR}/run-scenarios.sh" "$_spec" || _rc=$?
    fi

    # Clean up symlink if we created it
    [[ "$_needs_cleanup" == "true" ]] && rm -f "$_expected_dir"

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
