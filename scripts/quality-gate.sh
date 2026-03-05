#!/usr/bin/env bash
set -euo pipefail

# Quality Gate Runner
# Usage: ./scripts/quality-gate.sh <spec-name> [--test-cmd "command"]
# Exit codes: 0=all pass, 1=non-build failure, 2=build failure

SPEC_NAME="${1:?Usage: quality-gate.sh <spec-name> [--test-cmd \"command\"] [--iteration N]}"
CLI_TEST_CMD=""
_DF_ITERATION=1
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

shift || true
while [[ $# -gt 0 ]]; do
    case $1 in
        --test-cmd)
            CLI_TEST_CMD="$2"
            shift 2
            ;;
        --iteration)
            _DF_ITERATION="$2"
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

# --- P0-2: Input validation ---
validate_spec_name "$SPEC_NAME" || exit 1

# --- Config validation and sourcing ---
load_config ".dark-factory/config.sh" "LANGUAGE SOURCE_DIR TEST_DIR DARK_FACTORY_HOME" "GATE_TIMEOUT"

# --- Timeout detection ---
detect_timeout_cmd

SCENARIOS_DIR=".dark-factory/scenarios/${SPEC_NAME}"
REPORT_FILE=".dark-factory/reports/${SPEC_NAME}-quality-gate.md"
SUMMARY_FILE=".dark-factory/reports/${SPEC_NAME}-quality-gate-summary.md"
mkdir -p ".dark-factory/reports"

# Use LANGUAGE from config if set, otherwise auto-detect
TOOLCHAIN="${LANGUAGE:-$(detect_toolchain)}"

# Initialize report
cat > "$REPORT_FILE" <<EOF
# Quality Gate Report

**Spec:** ${SPEC_NAME}
**Date:** $(date -u +"%Y-%m-%dT%H:%M:%SZ")
**Toolchain:** ${TOOLCHAIN}

| Gate | Status | Details |
|------|--------|---------|
EOF

# Initialize summary file (sanitized — no raw output, no stack traces)
cat > "$SUMMARY_FILE" <<EOF
# Quality Gate Summary: ${SPEC_NAME}
Date: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

| Gate | Status |
|------|--------|
EOF

GATE_FAILED=false
BUILD_FAILED=false
_LAST_GATE_OUTPUT=""
run_gate() {
    local gate_name="$1"
    local gate_cmd="$2"

    echo "  Running gate: ${gate_name}..."

    if [[ -z "$gate_cmd" ]]; then
        echo "| ${gate_name} | SKIP | No command configured |" >> "$REPORT_FILE"
        echo "| ${gate_name} | SKIP |" >> "$SUMMARY_FILE"
        echo "    SKIP (no command configured)"
        _LAST_GATE_OUTPUT=""
        return 0
    fi

    local output
    local exit_code=0

    # P0-2: Subprocess isolation + timeout
    if [[ -n "$TIMEOUT_CMD" ]] && [[ "${GATE_TIMEOUT:-0}" -gt 0 ]]; then
        output=$($TIMEOUT_CMD "${GATE_TIMEOUT}s" bash -c "$gate_cmd" 2>&1) || exit_code=$?
    else
        output=$(bash -c "$gate_cmd" 2>&1) || exit_code=$?
    fi

    # Save output for post-processing (e.g., pass-rate extraction)
    _LAST_GATE_OUTPUT="$output"

    # Handle timeout (exit 124)
    if [[ $exit_code -eq 124 ]]; then
        echo "| ${gate_name} | TIMEOUT | Exceeded ${GATE_TIMEOUT}s limit |" >> "$REPORT_FILE"
        echo "| ${gate_name} | TIMEOUT |" >> "$SUMMARY_FILE"
        echo "" >> "$REPORT_FILE"
        echo "### ${gate_name} Timeout" >> "$REPORT_FILE"
        echo "Command exceeded the ${GATE_TIMEOUT}s time limit." >> "$REPORT_FILE"
        echo "" >> "$REPORT_FILE"
        echo "    TIMEOUT (exceeded ${GATE_TIMEOUT}s)"
        GATE_FAILED=true
        return 0
    fi

    if [[ $exit_code -eq 0 ]]; then
        echo "| ${gate_name} | PASS | Exit code 0 |" >> "$REPORT_FILE"
        echo "| ${gate_name} | PASS |" >> "$SUMMARY_FILE"
        echo "    PASS"
    else
        echo "| ${gate_name} | FAIL | Exit code ${exit_code} |" >> "$REPORT_FILE"
        echo "| ${gate_name} | FAIL |" >> "$SUMMARY_FILE"
        echo "" >> "$REPORT_FILE"
        echo "### ${gate_name} Failure Details" >> "$REPORT_FILE"
        echo '```' >> "$REPORT_FILE"
        echo "$output" | tail -50 >> "$REPORT_FILE"
        echo '```' >> "$REPORT_FILE"
        echo "" >> "$REPORT_FILE"
        echo "    FAIL"
        GATE_FAILED=true
    fi
}

# --- Parse scenario pass/fail counts from test runner output ---
_parse_scenario_counts() {
    local raw_output="$1"
    local counts_file=".dark-factory/reports/${SPEC_NAME}-scenario-counts.env"
    local passed=0 failed=0 total=0 rate=0

    # Strip ANSI color codes
    local clean
    clean=$(echo "$raw_output" | sed 's/\x1b\[[0-9;]*m//g')

    local parsed=false

    # pytest: "X passed" and optionally "Y failed"
    if echo "$clean" | grep -qE '[0-9]+ passed'; then
        passed=$(echo "$clean" | grep -oE '[0-9]+ passed' | tail -1 | grep -oE '[0-9]+')
        failed=$(echo "$clean" | grep -oE '[0-9]+ failed' | tail -1 | grep -oE '[0-9]+' || echo 0)
        [[ -z "$failed" ]] && failed=0
        parsed=true
    fi

    # jest/vitest: "Tests:  X passed" and "Y failed"
    if [[ "$parsed" != "true" ]]; then
        if echo "$clean" | grep -qiE 'Tests:?\s+[0-9]+ passed'; then
            passed=$(echo "$clean" | grep -oiE 'Tests:?\s+[0-9]+ passed' | grep -oE '[0-9]+' | head -1)
            failed=$(echo "$clean" | grep -oiE '[0-9]+ failed' | tail -1 | grep -oE '[0-9]+' || echo 0)
            [[ -z "$failed" ]] && failed=0
            parsed=true
        fi
    fi

    # go test: count "--- PASS:" and "--- FAIL:" lines
    if [[ "$parsed" != "true" ]]; then
        if echo "$clean" | grep -qE '--- (PASS|FAIL):'; then
            passed=$(echo "$clean" | grep -cE '--- PASS:' || echo 0)
            failed=$(echo "$clean" | grep -cE '--- FAIL:' || echo 0)
            parsed=true
        fi
    fi

    # Fallback: binary based on gate exit code
    if [[ "$parsed" != "true" ]] || [[ $((passed + failed)) -eq 0 ]]; then
        if [[ "$GATE_FAILED" != "true" ]]; then
            passed=1; failed=0
        else
            passed=0; failed=1
        fi
    fi

    total=$((passed + failed))
    if [[ $total -gt 0 ]]; then
        rate=$((passed * 100 / total))
    else
        rate=0
    fi

    cat > "$counts_file" <<EOF
SCENARIO_PASSED=$passed
SCENARIO_FAILED=$failed
SCENARIO_TOTAL=$total
SCENARIO_RATE=$rate
EOF
    echo "  Scenario counts: ${passed}/${total} passed (${rate}%)"
}

echo "Running quality gates for: ${SPEC_NAME}"
echo "Toolchain detected: ${TOOLCHAIN}"
echo ""

# Gate 1: Build
# Use config BUILD_CMD if set, otherwise auto-detect
if [[ -z "${BUILD_CMD:-}" ]]; then
    case $TOOLCHAIN in
        node)   BUILD_CMD="npm run build 2>&1 || npx tsc --noEmit 2>&1" ;;
        python) BUILD_CMD="find ${SOURCE_DIR:-src} -name '*.py' -exec python -m py_compile {} + 2>&1" ;;
        go)     BUILD_CMD="go build ./... 2>&1" ;;
        rust)   BUILD_CMD="cargo build 2>&1" ;;
        *)      BUILD_CMD="" ;;
    esac
fi
run_gate "Build" "$BUILD_CMD"
if [[ "$GATE_FAILED" == "true" ]]; then
    BUILD_FAILED=true
fi

# Gate 2: Lint
# Note: lint output is included in iteration feedback and consumed by agents.
# Configure linter messages to be instruction-style for better agent convergence.
# See docs/BEST-PRACTICES.md "Linter Messages as Agent Instructions".
# Use config LINT_CMD if set, otherwise auto-detect
if [[ -z "${LINT_CMD:-}" ]]; then
    case $TOOLCHAIN in
        node)   LINT_CMD="npm run lint 2>&1" ;;
        python) LINT_CMD="ruff check . 2>&1 || python -m flake8 . 2>&1" ;;
        go)     LINT_CMD="golangci-lint run 2>&1" ;;
        rust)   LINT_CMD="cargo clippy -- -D warnings 2>&1" ;;
        *)      LINT_CMD="" ;;
    esac
fi
run_gate "Lint" "$LINT_CMD"

# Gate 3: Type Check
# Use config TYPECHECK_CMD if set, otherwise auto-detect
if [[ -z "${TYPECHECK_CMD:-}" ]]; then
    case $TOOLCHAIN in
        node)   TYPECHECK_CMD="npx tsc --noEmit 2>&1" ;;
        python) TYPECHECK_CMD="mypy . 2>&1 || pyright . 2>&1" ;;
        go)     TYPECHECK_CMD="" ;; # Covered by build
        rust)   TYPECHECK_CMD="" ;; # Covered by build
        *)      TYPECHECK_CMD="" ;;
    esac
fi
run_gate "Type Check" "$TYPECHECK_CMD"

# Gate 4: Self-Tests (implementation's own tests)
# CLI --test-cmd flag takes highest priority, then config TEST_CMD, then auto-detect
if [[ -n "$CLI_TEST_CMD" ]]; then
    SELF_TEST_CMD="$CLI_TEST_CMD"
elif [[ -n "${TEST_CMD:-}" ]]; then
    SELF_TEST_CMD="$TEST_CMD"
else
    case $TOOLCHAIN in
        node)   SELF_TEST_CMD="npm test 2>&1" ;;
        python) SELF_TEST_CMD="pytest tests/ -v 2>&1" ;;
        go)     SELF_TEST_CMD="go test ./... -v 2>&1" ;;
        rust)   SELF_TEST_CMD="cargo test 2>&1" ;;
        *)      SELF_TEST_CMD="" ;;
    esac
fi
run_gate "Self-Tests" "$SELF_TEST_CMD"

# Gate 5: Blind Scenarios
if [[ -d "$SCENARIOS_DIR" ]] && has_scenario_files "$SCENARIOS_DIR"; then
    _multi_n="${SCENARIO_MULTI_RUN_N:-1}"
    if [[ "$_multi_n" -gt 1 ]]; then
        # Multi-run mode: per-file N-of-K with weighted scoring
        echo "  Running gate: Blind Scenarios (multi-run N=${_multi_n}, K=${SCENARIO_MULTI_RUN_K:-1})..."
        _df_run_scenarios_satisfaction "$SCENARIOS_DIR" "$TOOLCHAIN" "$SPEC_NAME" "${_DF_ITERATION}"
        source ".dark-factory/reports/${SPEC_NAME}-scenario-counts.env"
        if [[ "${SCENARIO_FAILED:-0}" -gt 0 ]]; then
            echo "| Blind Scenarios | FAIL | ${SCENARIO_PASSED}/${SCENARIO_TOTAL} (satisfaction: ${SATISFACTION_SCORE}%) |" >> "$REPORT_FILE"
            echo "| Blind Scenarios | FAIL |" >> "$SUMMARY_FILE"
            echo "    FAIL"
            GATE_FAILED=true
        else
            echo "| Blind Scenarios | PASS | ${SCENARIO_PASSED}/${SCENARIO_TOTAL} (satisfaction: ${SATISFACTION_SCORE}%) |" >> "$REPORT_FILE"
            echo "| Blind Scenarios | PASS |" >> "$SUMMARY_FILE"
            echo "    PASS"
        fi
    else
        # Legacy single-run path (unchanged)
        # Use config SCENARIO_TEST_CMD if set, otherwise auto-detect
        if [[ -n "${SCENARIO_TEST_CMD:-}" ]]; then
            SCENARIO_CMD="${SCENARIO_TEST_CMD//\{SCENARIOS_DIR\}/${SCENARIOS_DIR}}"
        else
            case $TOOLCHAIN in
                node)   SCENARIO_CMD="npx vitest run ${SCENARIOS_DIR} 2>&1 || npx jest ${SCENARIOS_DIR} 2>&1" ;;
                python) SCENARIO_CMD="pytest ${SCENARIOS_DIR} -v 2>&1" ;;
                # L5: Go ignores dotted directories — go test cannot reach .dark-factory/scenarios/.
                # Set SCENARIO_TEST_CMD in config.sh to a custom runner that copies/symlinks
                # scenario files into a valid Go package path before running go test.
                go)     SCENARIO_CMD="" ;;
                # L6: cargo test --test runs tests/*, not .dark-factory/scenarios/.
                # Set SCENARIO_TEST_CMD in config.sh to a custom runner for Rust scenarios.
                rust)   SCENARIO_CMD="" ;;
                *)      SCENARIO_CMD="" ;;
            esac
        fi
        run_gate "Blind Scenarios" "$SCENARIO_CMD"
        _parse_scenario_counts "$_LAST_GATE_OUTPUT"
        # Parse per-layer results from raw output (enables per-category tracking in single-run mode)
        _df_parse_per_layer_output "$_LAST_GATE_OUTPUT" "$SPEC_NAME" "${_DF_ITERATION}"
    fi
else
    echo "| Blind Scenarios | SKIP | No scenarios found in ${SCENARIOS_DIR} |" >> "$REPORT_FILE"
    echo "| Blind Scenarios | SKIP |" >> "$SUMMARY_FILE"
    echo "  Blind Scenarios: SKIP (no scenarios found)"
fi

# Gate 6: Mutation Testing (opt-in)
if [[ -n "${MUTATION_CMD:-}" ]]; then
    run_gate "Mutation Testing" "$MUTATION_CMD"
else
    echo "| Mutation Testing | SKIP | MUTATION_CMD not configured |" >> "$REPORT_FILE"
    echo "| Mutation Testing | SKIP |" >> "$SUMMARY_FILE"
    echo "  Mutation Testing: SKIP (MUTATION_CMD not configured)"
fi

echo "" >> "$REPORT_FILE"

# Defensive: ensure summary file never contains raw test output
# (should never happen given current code, guards against future regressions)
if grep -qE 'AssertionError|Expected:.*Received:|at Object\.<anonymous>' "$SUMMARY_FILE" 2>/dev/null; then
    echo "WARNING: Summary file contains raw test output — stripping for safety." >&2
    grep -E '^\|.*\|$|^#|^Date:|^$|^-' "$SUMMARY_FILE" > "${SUMMARY_FILE}.clean" 2>/dev/null || true
    if [[ -s "${SUMMARY_FILE}.clean" ]]; then
        mv "${SUMMARY_FILE}.clean" "$SUMMARY_FILE"
    else
        rm -f "${SUMMARY_FILE}.clean"
        echo "WARNING: Sanitized summary was empty — keeping original." >&2
    fi
fi

# Summary
if [[ "$GATE_FAILED" == "true" ]]; then
    echo "## Result: FAIL" >> "$REPORT_FILE"
    echo "" >> "$SUMMARY_FILE"
    echo "## Result: FAIL" >> "$SUMMARY_FILE"
    echo ""
    echo "QUALITY GATE: FAIL"
    echo "See report: ${REPORT_FILE}"
    if [[ "$BUILD_FAILED" == "true" ]]; then
        exit 2  # Build failed (hard gate trigger)
    fi
    exit 1      # Non-build failures only
else
    echo "## Result: PASS" >> "$REPORT_FILE"
    echo "" >> "$SUMMARY_FILE"
    echo "## Result: PASS" >> "$SUMMARY_FILE"
    echo ""
    echo "QUALITY GATE: PASS"
    echo "See report: ${REPORT_FILE}"
fi
