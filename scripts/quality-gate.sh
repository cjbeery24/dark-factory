#!/usr/bin/env bash
set -euo pipefail

# Quality Gate Runner
# Usage: ./scripts/quality-gate.sh <spec-name> [--test-cmd "command"]
# Exit codes: 0=all pass, 1=non-build failure, 2=build failure

SPEC_NAME="${1:?Usage: quality-gate.sh <spec-name> [options]}"
CLI_TEST_CMD=""
CLI_BUILD_CMD=""
CLI_LINT_CMD=""
CLI_TYPECHECK_CMD=""
CLI_LANGUAGE=""
CLI_SOURCE_DIR=""
CLI_TEST_DIR=""
CLI_SCENARIO_TEST_CMD=""
CLI_SCENARIO_VALIDATION=""
CLI_TEST_BASE_URL=""
CLI_TEST_SERVER_CMD=""
CLI_SERVER_STARTUP_TIMEOUT=""
CLI_SERVER_HEALTH_PATH=""
CLI_BROWSER_BASE_URL=""
CLI_BROWSER_SERVER_CMD=""
CLI_BROWSER_STARTUP_TIMEOUT=""
_DF_ITERATION=1
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

shift || true
while [[ $# -gt 0 ]]; do
    case $1 in
        --test-cmd)               CLI_TEST_CMD="$2";               shift 2 ;;
        --build-cmd)              CLI_BUILD_CMD="$2";               shift 2 ;;
        --lint-cmd)               CLI_LINT_CMD="$2";                shift 2 ;;
        --typecheck-cmd)          CLI_TYPECHECK_CMD="$2";           shift 2 ;;
        --language)               CLI_LANGUAGE="$2";                shift 2 ;;
        --source-dir)             CLI_SOURCE_DIR="$2";              shift 2 ;;
        --test-dir)               CLI_TEST_DIR="$2";                shift 2 ;;
        --scenario-test-cmd)      CLI_SCENARIO_TEST_CMD="$2";       shift 2 ;;
        --scenario-validation)    CLI_SCENARIO_VALIDATION="$2";     shift 2 ;;
        --iteration)              _DF_ITERATION="$2";               shift 2 ;;
        --test-base-url)          CLI_TEST_BASE_URL="$2";           shift 2 ;;
        --test-server-cmd)        CLI_TEST_SERVER_CMD="$2";         shift 2 ;;
        --server-startup-timeout) CLI_SERVER_STARTUP_TIMEOUT="$2";  shift 2 ;;
        --server-health-path)     CLI_SERVER_HEALTH_PATH="$2";      shift 2 ;;
        --browser-base-url)       CLI_BROWSER_BASE_URL="$2";        shift 2 ;;
        --browser-server-cmd)     CLI_BROWSER_SERVER_CMD="$2";      shift 2 ;;
        --browser-startup-timeout) CLI_BROWSER_STARTUP_TIMEOUT="$2"; shift 2 ;;
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
# config.sh is optional when the Python CLI forwards all values via CLI flags.
if [[ -f ".dark-factory/config.sh" ]]; then
    load_config ".dark-factory/config.sh" "LANGUAGE SOURCE_DIR TEST_DIR DARK_FACTORY_HOME" "GATE_TIMEOUT"
fi

# CLI flags take precedence over config.sh values
[[ -n "$CLI_LANGUAGE" ]]               && LANGUAGE="$CLI_LANGUAGE"
[[ -n "$CLI_SOURCE_DIR" ]]             && SOURCE_DIR="$CLI_SOURCE_DIR"
[[ -n "$CLI_TEST_DIR" ]]               && TEST_DIR="$CLI_TEST_DIR"
[[ -n "$CLI_BUILD_CMD" ]]              && BUILD_CMD="$CLI_BUILD_CMD"
[[ -n "$CLI_LINT_CMD" ]]               && LINT_CMD="$CLI_LINT_CMD"
[[ -n "$CLI_TYPECHECK_CMD" ]]          && TYPECHECK_CMD="$CLI_TYPECHECK_CMD"
[[ -n "$CLI_SCENARIO_TEST_CMD" ]]      && SCENARIO_TEST_CMD="$CLI_SCENARIO_TEST_CMD"
[[ -n "$CLI_SCENARIO_VALIDATION" ]]    && SCENARIO_VALIDATION="$CLI_SCENARIO_VALIDATION"
[[ -n "$CLI_TEST_BASE_URL" ]]          && TEST_BASE_URL="$CLI_TEST_BASE_URL"
[[ -n "$CLI_TEST_SERVER_CMD" ]]        && TEST_SERVER_CMD="$CLI_TEST_SERVER_CMD"
[[ -n "$CLI_SERVER_STARTUP_TIMEOUT" ]] && SERVER_STARTUP_TIMEOUT="$CLI_SERVER_STARTUP_TIMEOUT"
[[ -n "$CLI_SERVER_HEALTH_PATH" ]]     && SERVER_HEALTH_PATH="$CLI_SERVER_HEALTH_PATH"
[[ -n "$CLI_BROWSER_BASE_URL" ]]       && BROWSER_BASE_URL="$CLI_BROWSER_BASE_URL"
[[ -n "$CLI_BROWSER_SERVER_CMD" ]]     && BROWSER_SERVER_CMD="$CLI_BROWSER_SERVER_CMD"
[[ -n "$CLI_BROWSER_STARTUP_TIMEOUT" ]] && BROWSER_STARTUP_TIMEOUT="$CLI_BROWSER_STARTUP_TIMEOUT"

# --- Timeout detection ---
detect_timeout_cmd

SCENARIOS_DIR=".dark-factory/scenarios/${SPEC_NAME}"
REPORT_FILE=".dark-factory/reports/${SPEC_NAME}-quality-gate.md"
SUMMARY_FILE=".dark-factory/reports/${SPEC_NAME}-quality-gate-summary.md"
mkdir -p ".dark-factory/reports"

# Use LANGUAGE from config/CLI if set, otherwise auto-detect
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

# ==============================================================================
# Server lifecycle
# ==============================================================================

_DF_SERVER_PIDS=()

_df_cleanup_servers() {
    local pid
    for pid in "${_DF_SERVER_PIDS[@]+"${_DF_SERVER_PIDS[@]}"}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
}
trap '_df_cleanup_servers' EXIT INT TERM

# _df_start_server <cmd> <timeout_secs> <health_path> <base_url>
#   Starts a server process in the background, waits up to <timeout_secs>
#   seconds for it to respond on <base_url><health_path>.
#   Records the PID in _DF_SERVER_PIDS.
#   Returns 0 when ready, 1 on timeout or crash.
_df_start_server() {
    local cmd="$1"
    local timeout_secs="${2:-30}"
    local health_path="${3:-/health}"
    local base_url="$4"

    echo "  Starting server: ${cmd}"
    bash -c "$cmd" &
    local server_pid=$!
    _DF_SERVER_PIDS+=("$server_pid")

    local health_url="${base_url%/}${health_path}"
    local elapsed=0
    while [[ $elapsed -lt $timeout_secs ]]; do
        # Check crash before sleeping so immediate failures are caught quickly.
        if ! kill -0 "$server_pid" 2>/dev/null; then
            echo "  ERROR: Server process (PID ${server_pid}) exited early."
            return 1
        fi
        # Check health endpoint before sleeping to allow sub-second ready servers
        if curl -sf --max-time 2 "$health_url" >/dev/null 2>&1; then
            echo "  Server ready after ${elapsed}s (${health_url})"
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done

    echo "  ERROR: Server did not become ready within ${timeout_secs}s (${health_url})"
    # NOTE: child processes spawned by the server may not be cleaned up by SIGTERM alone.
    # The server command is responsible for reaping its own children on termination.
    return 1
}

# ==============================================================================
# Format runner helpers (for Format A/B)
# ==============================================================================

_FORMAT_RUNNER_EXIT_CODE=0

# _run_format_runner <script_path> [args...]
#   Runs a Python format runner script. Sets _FORMAT_RUNNER_EXIT_CODE.
_run_format_runner() {
    local script="$1"; shift
    local rc=0
    python3 "$script" "$@" || rc=$?
    _FORMAT_RUNNER_EXIT_CODE=$rc
}

# _df_accumulate_format_counts
#   Reads all {SPEC_NAME}-scenario-counts-*.env files and sums PASSED/FAILED/TOTAL
#   into the canonical {SPEC_NAME}-scenario-counts.env.
#   Also writes {SPEC_NAME}-category-breakdown.env by merging all format breakdowns.
_df_accumulate_format_counts() {
    local total_passed=0
    local total_failed=0
    local total_total=0
    local counts_file=".dark-factory/reports/${SPEC_NAME}-scenario-counts.env"

    # Sum across all format-specific counts files
    local fmt_file
    for fmt_file in ".dark-factory/reports/${SPEC_NAME}"-scenario-counts-*.env; do
        [[ -f "$fmt_file" ]] || continue
        local p f t
        p=$(grep '^SCENARIO_PASSED=' "$fmt_file" | cut -d= -f2) || p=0
        f=$(grep '^SCENARIO_FAILED=' "$fmt_file" | cut -d= -f2) || f=0
        t=$(grep '^SCENARIO_TOTAL=' "$fmt_file" | cut -d= -f2) || t=0
        total_passed=$((total_passed + ${p:-0}))
        total_failed=$((total_failed + ${f:-0}))
        total_total=$((total_total + ${t:-0}))
    done

    local rate=0
    if [[ $total_total -gt 0 ]]; then
        rate=$((total_passed * 100 / total_total))
    fi

    cat > "$counts_file" <<EOF
SCENARIO_PASSED=${total_passed}
SCENARIO_FAILED=${total_failed}
SCENARIO_TOTAL=${total_total}
SCENARIO_RATE=${rate}
EOF
    if [[ $total_total -eq 0 ]]; then
        echo "  WARNING: _df_accumulate_format_counts found zero scenarios — format runner(s) may not have written counts files." >&2
    fi
    echo "  Accumulated scenario counts: ${total_passed}/${total_total} passed (${rate}%)"

    # Merge category breakdowns from all format-specific breakdown files
    local cat_file=".dark-factory/reports/${SPEC_NAME}-category-breakdown.env"
    local -A merged_passed=() merged_total=()
    local bd_file
    for bd_file in ".dark-factory/reports/${SPEC_NAME}"-category-breakdown-*.env; do
        [[ -f "$bd_file" ]] || continue
        local line prefix p t
        while IFS= read -r line; do
            if [[ "$line" =~ ^CAT_([0-9]{2})_PASSED=([0-9]+)$ ]]; then
                prefix="${BASH_REMATCH[1]}"
                p="${BASH_REMATCH[2]}"
                merged_passed["$prefix"]=$(( ${merged_passed["$prefix"]:-0} + p ))
            elif [[ "$line" =~ ^CAT_([0-9]{2})_TOTAL=([0-9]+)$ ]]; then
                prefix="${BASH_REMATCH[1]}"
                t="${BASH_REMATCH[2]}"
                merged_total["$prefix"]=$(( ${merged_total["$prefix"]:-0} + t ))
            fi
        done < "$bd_file"
    done

    : > "$cat_file"
    for prefix in $(echo "${!merged_total[@]}" | tr ' ' '\n' | sort); do
        echo "CAT_${prefix}_PASSED=${merged_passed[$prefix]:-0}" >> "$cat_file"
        echo "CAT_${prefix}_TOTAL=${merged_total[$prefix]}" >> "$cat_file"
    done
}

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
        # detect_scenario_formats is called inside _df_run_scenarios_satisfaction
        # (via the updated find patterns) so all formats are counted.
        echo "  Running gate: Blind Scenarios (multi-run N=${_multi_n}, K=${SCENARIO_MULTI_RUN_K:-1})..."
        _df_run_scenarios_satisfaction "$SCENARIOS_DIR" "$TOOLCHAIN" "$SPEC_NAME" "${_DF_ITERATION}"
        source ".dark-factory/reports/${SPEC_NAME}-scenario-counts.env"
        _sat_floor="${MIN_SATISFACTION_SCORE:-0}"
        if [[ "$_sat_floor" -gt 0 ]]; then
            # Probabilistic mode: gate passes if satisfaction >= floor
            if [[ "${SATISFACTION_SCORE:-0}" -ge "$_sat_floor" ]]; then
                echo "| Blind Scenarios | PASS | ${SCENARIO_PASSED}/${SCENARIO_TOTAL} (satisfaction: ${SATISFACTION_SCORE}% >= ${_sat_floor}%) |" >> "$REPORT_FILE"
                echo "| Blind Scenarios | PASS |" >> "$SUMMARY_FILE"
                echo "    PASS (satisfaction ${SATISFACTION_SCORE}% >= floor ${_sat_floor}%)"
            else
                echo "| Blind Scenarios | FAIL | ${SCENARIO_PASSED}/${SCENARIO_TOTAL} (satisfaction: ${SATISFACTION_SCORE}% < ${_sat_floor}%) |" >> "$REPORT_FILE"
                echo "| Blind Scenarios | FAIL |" >> "$SUMMARY_FILE"
                echo "    FAIL (satisfaction ${SATISFACTION_SCORE}% < floor ${_sat_floor}%)"
                GATE_FAILED=true
            fi
        else
            # Legacy binary mode (MIN_SATISFACTION_SCORE=0 or unset): any failure = gate fail
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
        fi
    else
        # Single-run path: detect formats, start servers, dispatch per-format runners
        detect_scenario_formats "$SCENARIOS_DIR"

        # Track whether each server started successfully so runners can be skipped
        # if their server is unavailable. Not declared with 'local' — these are
        # script-level variables (local is a no-op outside function bodies in bash).
        _DF_HTTP_SERVER_OK=true
        _DF_BROWSER_SERVER_OK=true

        # Start HTTP test server if needed
        if [[ "$_DF_HAS_HTTP" == "true" ]] && [[ -n "${TEST_SERVER_CMD:-}" ]]; then
            echo "  Starting HTTP test server..."
            if ! _df_start_server \
                    "${TEST_SERVER_CMD}" \
                    "${SERVER_STARTUP_TIMEOUT:-30}" \
                    "${SERVER_HEALTH_PATH:-/health}" \
                    "${TEST_BASE_URL:-http://localhost:8080}"; then
                echo "| Blind Scenarios (HTTP) | FAIL | HTTP test server failed to start |" >> "$REPORT_FILE"
                echo "| Blind Scenarios (HTTP) | FAIL |" >> "$SUMMARY_FILE"
                GATE_FAILED=true
                _DF_HTTP_SERVER_OK=false
            fi
        fi

        # Start browser server if needed.
        # Deduplication: skip if cmd AND url are identical to the HTTP server
        # (same process is already serving both HTTP and browser traffic).
        if [[ "$_DF_HAS_JOURNEY" == "true" ]] && [[ -n "${BROWSER_SERVER_CMD:-}" ]]; then
            if [[ "${BROWSER_SERVER_CMD}" == "${TEST_SERVER_CMD:-}" ]] && \
               [[ "${BROWSER_BASE_URL:-}" == "${TEST_BASE_URL:-}" ]]; then
                # Reuse HTTP server; inherit its ready/failed state
                _DF_BROWSER_SERVER_OK="$_DF_HTTP_SERVER_OK"
            else
                echo "  Starting browser test server..."
                if ! _df_start_server \
                        "${BROWSER_SERVER_CMD}" \
                        "${BROWSER_STARTUP_TIMEOUT:-60}" \
                        "${SERVER_HEALTH_PATH:-/health}" \
                        "${BROWSER_BASE_URL:-http://localhost:8080}"; then
                    echo "| Blind Scenarios (Journey) | FAIL | Browser test server failed to start |" >> "$REPORT_FILE"
                    echo "| Blind Scenarios (Journey) | FAIL |" >> "$SUMMARY_FILE"
                    GATE_FAILED=true
                    _DF_BROWSER_SERVER_OK=false
                fi
            fi
        fi

        # Run executable scenarios (pytest / jest / go / rust)
        _DF_ANY_FORMAT_RAN=false
        if [[ "$_DF_HAS_EXECUTABLE" == "true" ]]; then
            _DF_ANY_FORMAT_RAN=true
            if [[ -n "${SCENARIO_TEST_CMD:-}" ]]; then
                SCENARIO_CMD="${SCENARIO_TEST_CMD//\{SCENARIOS_DIR\}/${SCENARIOS_DIR}}"
            else
                case $TOOLCHAIN in
                    node)   SCENARIO_CMD="npx vitest run ${SCENARIOS_DIR} 2>&1 || npx jest ${SCENARIOS_DIR} 2>&1" ;;
                    python) SCENARIO_CMD="pytest ${SCENARIOS_DIR} -v 2>&1" ;;
                    # L5: Go ignores dotted directories — set SCENARIO_TEST_CMD in config
                    go)     SCENARIO_CMD="" ;;
                    # L6: cargo test --test runs tests/*, not .dark-factory/scenarios/
                    rust)   SCENARIO_CMD="" ;;
                    *)      SCENARIO_CMD="" ;;
                esac
            fi
            run_gate "Blind Scenarios (executable)" "$SCENARIO_CMD"
            # Write counts to a format-specific file so accumulation picks it up correctly
            # when mixed with HTTP/Journey formats.
            _parse_scenario_counts "$_LAST_GATE_OUTPUT"
            _DF_EXEC_COUNTS_FILE=".dark-factory/reports/${SPEC_NAME}-scenario-counts.env"
            _DF_EXEC_FMT_FILE=".dark-factory/reports/${SPEC_NAME}-scenario-counts-executable.env"
            if [[ -f "$_DF_EXEC_COUNTS_FILE" ]]; then
                cp "$_DF_EXEC_COUNTS_FILE" "$_DF_EXEC_FMT_FILE"
            fi
            _df_parse_per_layer_output "$_LAST_GATE_OUTPUT" "$SPEC_NAME" "${_DF_ITERATION}"
        fi

        # Run HTTP scenarios (Format A) — only if server started successfully (or no server configured)
        if [[ "$_DF_HAS_HTTP" == "true" ]] && [[ "$_DF_HTTP_SERVER_OK" == "true" ]]; then
            _DF_ANY_FORMAT_RAN=true
            echo "  Running gate: Blind Scenarios (HTTP)..."
            _run_format_runner "${SCRIPT_DIR}/lib/http-scenario-runner.py" \
                "$SCENARIOS_DIR" \
                --base-url "${TEST_BASE_URL:-}" \
                --reports-dir ".dark-factory/reports" \
                --spec-name "$SPEC_NAME"
            if [[ $_FORMAT_RUNNER_EXIT_CODE -ne 0 ]]; then
                echo "| Blind Scenarios (HTTP) | FAIL | http-scenario-runner exited ${_FORMAT_RUNNER_EXIT_CODE} |" >> "$REPORT_FILE"
                echo "| Blind Scenarios (HTTP) | FAIL |" >> "$SUMMARY_FILE"
                GATE_FAILED=true
            else
                echo "| Blind Scenarios (HTTP) | PASS | Exit code 0 |" >> "$REPORT_FILE"
                echo "| Blind Scenarios (HTTP) | PASS |" >> "$SUMMARY_FILE"
            fi
        fi

        # Run browser journey scenarios (Format B) — only if server started successfully
        if [[ "$_DF_HAS_JOURNEY" == "true" ]] && [[ "$_DF_BROWSER_SERVER_OK" == "true" ]]; then
            _DF_ANY_FORMAT_RAN=true
            echo "  Running gate: Blind Scenarios (Journey)..."
            _run_format_runner "${SCRIPT_DIR}/lib/journey-runner.py" \
                "$SCENARIOS_DIR" \
                --base-url "${BROWSER_BASE_URL:-}" \
                --reports-dir ".dark-factory/reports" \
                --spec-name "$SPEC_NAME"
            if [[ $_FORMAT_RUNNER_EXIT_CODE -ne 0 ]]; then
                echo "| Blind Scenarios (Journey) | FAIL | journey-runner exited ${_FORMAT_RUNNER_EXIT_CODE} |" >> "$REPORT_FILE"
                echo "| Blind Scenarios (Journey) | FAIL |" >> "$SUMMARY_FILE"
                GATE_FAILED=true
            else
                echo "| Blind Scenarios (Journey) | PASS | Exit code 0 |" >> "$REPORT_FILE"
                echo "| Blind Scenarios (Journey) | PASS |" >> "$SUMMARY_FILE"
            fi
        fi

        # Accumulate counts across ALL format-specific env files into the canonical file.
        # This correctly handles mixed executable+HTTP+Journey scenarios because the
        # executable path above copies its output to scenario-counts-executable.env before
        # this accumulation runs.
        if [[ "$_DF_ANY_FORMAT_RAN" == "true" ]]; then
            _df_accumulate_format_counts
        fi

        # Emit a skip row when no formats were detected at all
        if [[ "$_DF_ANY_FORMAT_RAN" != "true" ]]; then
            echo "| Blind Scenarios | SKIP | No recognized formats in ${SCENARIOS_DIR} |" >> "$REPORT_FILE"
            echo "| Blind Scenarios | SKIP |" >> "$SUMMARY_FILE"
        fi
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
