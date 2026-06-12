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
CLI_GATE_TIMEOUT=""
CLI_SCENARIO_TIMEOUT=""
CLI_TEST_BASE_URL=""
CLI_TEST_SERVER_CMD=""
CLI_SERVER_STARTUP_TIMEOUT=""
CLI_SERVER_HEALTH_PATH=""
CLI_BROWSER_BASE_URL=""
CLI_BROWSER_SERVER_CMD=""
CLI_BROWSER_STARTUP_TIMEOUT=""
CLI_INSTALL_CMD=""
CLI_SETUP_CMD=""
CLI_SETUP_TIMEOUT=""
# pre_setup commands (architect-emitted env preconditions) run BEFORE setup_cmd.
# Collected as a bash array so each command is a distinct argv element — no
# encoding or separator gymnastics. Python side forwards repeated
# --pre-setup-cmd flags, one per entry.
CLI_PRE_SETUP_CMDS=()
_DF_ITERATION=1
PRE_GATE=false
SMOKE_TEST=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

shift || true
while [[ $# -gt 0 ]]; do
    case $1 in
        --pre-gate)               PRE_GATE=true;                    shift 1 ;;
        --smoke)                  SMOKE_TEST=true;                  shift 1 ;;
        --test-cmd)               CLI_TEST_CMD="$2";               shift 2 ;;
        --build-cmd)              CLI_BUILD_CMD="$2";               shift 2 ;;
        --lint-cmd)               CLI_LINT_CMD="$2";                shift 2 ;;
        --typecheck-cmd)          CLI_TYPECHECK_CMD="$2";           shift 2 ;;
        --language)               CLI_LANGUAGE="$2";                shift 2 ;;
        --source-dir)             CLI_SOURCE_DIR="$2";              shift 2 ;;
        --test-dir)               CLI_TEST_DIR="$2";                shift 2 ;;
        --scenario-test-cmd)      CLI_SCENARIO_TEST_CMD="$2";       shift 2 ;;
        --scenario-validation)    CLI_SCENARIO_VALIDATION="$2";     shift 2 ;;
        --gate-timeout)           CLI_GATE_TIMEOUT="$2";            shift 2 ;;
        --scenario-timeout)       CLI_SCENARIO_TIMEOUT="$2";        shift 2 ;;
        --iteration)              _DF_ITERATION="$2";               shift 2 ;;
        --test-base-url)          CLI_TEST_BASE_URL="$2";           shift 2 ;;
        --test-server-cmd)        CLI_TEST_SERVER_CMD="$2";         shift 2 ;;
        --server-startup-timeout) CLI_SERVER_STARTUP_TIMEOUT="$2";  shift 2 ;;
        --server-health-path)     CLI_SERVER_HEALTH_PATH="$2";      shift 2 ;;
        --browser-base-url)       CLI_BROWSER_BASE_URL="$2";        shift 2 ;;
        --browser-server-cmd)     CLI_BROWSER_SERVER_CMD="$2";      shift 2 ;;
        --browser-startup-timeout) CLI_BROWSER_STARTUP_TIMEOUT="$2"; shift 2 ;;
        --install-cmd)            CLI_INSTALL_CMD="$2";             shift 2 ;;
        --setup-cmd)             CLI_SETUP_CMD="$2";              shift 2 ;;
        --setup-timeout)          CLI_SETUP_TIMEOUT="$2";           shift 2 ;;
        --pre-setup-cmd)          CLI_PRE_SETUP_CMDS+=("$2");      shift 2 ;;
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
[[ -n "$CLI_GATE_TIMEOUT" ]]           && GATE_TIMEOUT="$CLI_GATE_TIMEOUT"
[[ -n "$CLI_SCENARIO_TIMEOUT" ]]       && SCENARIO_TIMEOUT="$CLI_SCENARIO_TIMEOUT"
# Default: GATE_TIMEOUT defaults to 600 if not set by CLI or config
GATE_TIMEOUT="${GATE_TIMEOUT:-600}"
# Default: use GATE_TIMEOUT if SCENARIO_TIMEOUT is 0 or unset
if [[ "${SCENARIO_TIMEOUT:-0}" -eq 0 ]]; then
    SCENARIO_TIMEOUT="$GATE_TIMEOUT"
fi
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

# Override report paths for pre-gate mode BEFORE any writes.
# This prevents partial writes to the full-gate report paths.
if [[ "$PRE_GATE" == "true" ]]; then
    REPORT_FILE=".dark-factory/reports/${SPEC_NAME}-pre-gate.md"
    SUMMARY_FILE=".dark-factory/reports/${SPEC_NAME}-pre-gate-summary.md"
fi

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
# Clean up the workspace wrapper config generated inside the workspace dir
# by _df_resolve_vitest_config. Must run on EXIT/INT/TERM so the file does
# not leak into the workspace on unexpected failures or signals — the file
# lives in a :rw mount and would otherwise persist into the next iteration,
# polluting project state and leaking scenario-directory metadata into the
# Implementer container.
_df_cleanup_wrapper() {
    if [[ -n "${_DF_WRAPPER_FILE:-}" && -f "${_DF_WRAPPER_FILE}" ]]; then
        rm -f "${_DF_WRAPPER_FILE}"
    fi
}
trap '_df_cleanup_servers; _df_cleanup_wrapper' EXIT INT TERM

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
    local total_skipped=0
    local total_registered=0
    local counts_file=".dark-factory/reports/${SPEC_NAME}-scenario-counts.env"

    # Sum across all format-specific counts files
    local fmt_file
    for fmt_file in ".dark-factory/reports/${SPEC_NAME}"-scenario-counts-*.env; do
        [[ -f "$fmt_file" ]] || continue
        local p f t sk reg
        p=$(grep '^SCENARIO_PASSED=' "$fmt_file" | cut -d= -f2) || p=0
        f=$(grep '^SCENARIO_FAILED=' "$fmt_file" | cut -d= -f2) || f=0
        t=$(grep '^SCENARIO_TOTAL=' "$fmt_file" | cut -d= -f2) || t=0
        sk=$(grep '^SCENARIO_SKIPPED=' "$fmt_file" | cut -d= -f2) || sk=0
        reg=$(grep '^SCENARIO_REGISTERED=' "$fmt_file" | cut -d= -f2) || reg=0
        total_passed=$((total_passed + ${p:-0}))
        total_failed=$((total_failed + ${f:-0}))
        total_total=$((total_total + ${t:-0}))
        total_skipped=$((total_skipped + ${sk:-0}))
        total_registered=$((total_registered + ${reg:-0}))
    done

    local rate=0
    if [[ $total_total -gt 0 ]]; then
        rate=$((total_passed * 100 / total_total))
    fi

    if [[ $total_total -eq 0 ]]; then
        # No format fragments found or all fragments had zero counts.
        # Do NOT overwrite the canonical file — _parse_scenario_counts already
        # wrote correct values there. Overwriting with zeros is the root cause
        # of the "0% pass rate" bug when fragments are missing.
        echo "  WARNING: _df_accumulate_format_counts found zero scenarios — format runner(s) may not have written counts files." >&2
        echo "  Preserving existing counts file (not overwriting with zeros)."
    else
        cat > "$counts_file" <<EOF
SCENARIO_PASSED=${total_passed}
SCENARIO_FAILED=${total_failed}
SCENARIO_TOTAL=${total_total}
SCENARIO_RATE=${rate}
SCENARIO_SKIPPED=${total_skipped}
SCENARIO_REGISTERED=${total_registered}
EOF
    fi
    echo "  Accumulated scenario counts: ${total_passed}/${total_total} passed (${rate}%), ${total_skipped} skipped (${total_registered} registered)"

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
# Accumulator for sanitized error details that are safe to include in the
# summary file (infrastructure gates only — never blind scenario output).
# Written to SUMMARY_FILE after all gate table rows, separated by a sentinel.
SUMMARY_DETAILS_FILE="${SUMMARY_FILE}.details"
: > "$SUMMARY_DETAILS_FILE"

# _normalize_output: reshape captured tool output so it's readable inside a
# fenced code block in the gate report.
#
# Motivated by the 2026-04-15 budget-brain-v2 wet run (run 7ff4438d539a4ed5):
# drizzle-kit migrate renders progress with an in-place spinner using ANSI
# cursor-control (CSI 1G = "cursor to col 1"; CSI 2K = "erase line"). Under
# non-interactive capture, those bytes remain in the stream without
# newlines, so one long "visual" line contains dozens of rewrites of the
# same frame. The prior code stripped only SGR color codes (`\x1b[...m`);
# everything else survived and made the failure-details block actively
# misleading — iter 2 implementer read a 4-line "hung spinner" report,
# concluded the process was hanging, never wrote the df-setup.sh escape
# hatch, and iter 3 would have looped identically.
#
# Pass order matters:
#   1. Cursor-rewind (\x1b[1G and bare \r) → \n, so rewrite-in-place frames
#      become separate lines that later stages can collapse.
#   2. Erase-line (\x1b[2K) → empty, so frames that were "erase + rewrite"
#      don't leave ghosts before collapse.
#   3. Broad CSI strip: any \x1b[ parameter-bytes final-byte — covers SGR
#      colors AND cursor moves, no exemptions.
#   4. awk dedupe: collapse consecutive identical lines (uniq -c would also
#      work but adds "N times" prefixes we don't want).
#
# LC_ALL=C on both sed and awk forces byte-exact processing. BSD awk on
# macOS mis-compares multi-byte UTF-8 sequences in UTF-8 locales: braille
# spinner glyphs like ⣷/⣾/⣽ (which differ only in their final byte) compare
# as equal, which would collapse distinct spinner frames into one. Byte-
# exact comparison is exactly what dedupe wants — we're collapsing stream
# artifacts, not doing locale-aware text equality.
#
# Returns cleaned text on stdout.
_normalize_output() {
    LC_ALL=C sed -e 's/\x1b\[1G/\n/g' \
                 -e 's/\r/\n/g' \
                 -e 's/\x1b\[2K//g' \
                 -e 's/\x1b\[[0-9;?]*[A-Za-z]//g' \
        | LC_ALL=C awk 'NR==1 || $0 != prev { print; prev=$0 }'
}

# _sanitize_output: normalize + strip barrier-protected paths + tail so the
# summary file (which flows to Reviewer/Oracle prompts) can't leak scenario
# or holdout path references. Report-file callers should pipe raw output
# through _normalize_output directly and skip the barrier filter — the
# report is an implementer-visible surface, not an analysis-agent surface.
_sanitize_output() {
    _normalize_output \
        | grep -vF '.dark-factory/scenarios/' \
        | grep -vF '.dark-factory/holdouts/' \
        | tail -30 \
        || true   # grep -v exits 1 when all lines are filtered; swallow under pipefail
}

run_gate() {
    local gate_name="$1"
    local gate_cmd="$2"
    local include_in_summary="${3:-false}"

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
        echo "$output" | _normalize_output | tail -50 >> "$REPORT_FILE"
        echo '```' >> "$REPORT_FILE"
        echo "" >> "$REPORT_FILE"
        # Detect vitest "no test files" — implementation gap, not a test failure
        if echo "$output" | grep -qF "No test files found"; then
            echo "" >> "$REPORT_FILE"
            echo "> **Tip:** One or more workspace packages has a \`test\` script but no test files." >> "$REPORT_FILE"
            echo "> Add a placeholder test file (e.g., \`src/server.test.ts\`) or remove the \`test\` script." >> "$REPORT_FILE"
        fi
        # Accumulate sanitized error details for the summary file (safe gates only).
        # Barrier-protected paths (.dark-factory/scenarios/, .dark-factory/holdouts/)
        # are stripped so the Reviewer/Oracle never see blind test references.
        if [[ "$include_in_summary" == "true" ]]; then
            local sanitized
            sanitized=$(echo "$output" | _sanitize_output)
            if [[ -n "$sanitized" ]]; then
                {
                    echo ""
                    echo "### ${gate_name} Failure Details"
                    echo '```'
                    echo "$sanitized"
                    echo '```'
                } >> "$SUMMARY_DETAILS_FILE"
            fi
        fi
        echo "    FAIL"
        GATE_FAILED=true
    fi
}

# --- Parse scenario pass/fail counts from test runner output ---
_parse_scenario_counts() {
    local raw_output="$1"
    local counts_file
    if [[ "${SMOKE_TEST:-false}" == "true" ]]; then
        counts_file=".dark-factory/reports/${SPEC_NAME}-smoke-test.env"
    else
        # Always write to format-specific fragment; the orchestrator (Python or
        # bash fallback) accumulates fragments into the canonical file.
        counts_file=".dark-factory/reports/${SPEC_NAME}-scenario-counts-executable.env"
    fi
    local passed=0 failed=0 total=0 rate=0

    # Strip ANSI escape sequences — all CSI sequences (color, cursor, erase),
    # not just SGR color codes ending in 'm'.  Vitest emits cursor-control
    # sequences (\x1b[2K, \x1b[1A) that survive SGR-only stripping; leftover
    # escape bytes at the start of a line prevent crash_pat (^\s*TypeError:)
    # from matching, causing SCENARIO_CRASHED=0 for genuine TypeErrors.
    local clean
    clean=$(echo "$raw_output" | sed 's/\x1b\[[0-9;]*[a-zA-Z]//g')

    local parsed=false

    # pytest: "X passed" and optionally "Y failed"
    if echo "$clean" | grep -qE '[0-9]+ passed'; then
        passed=$(echo "$clean" | grep -oE '[0-9]+ passed' | tail -1 | grep -oE '[0-9]+')
        failed=$(echo "$clean" | grep -oE '[0-9]+ failed' | tail -1 | grep -oE '[0-9]+' || echo 0)
        [[ -z "$failed" ]] && failed=0
        parsed=true
    fi

    # jest/vitest summary line — handles every ordering of failed/passed/skipped
    # and failure-only runs.  The old implementation gated on the substring
    # "passed", which silently dropped failure-only runs into the binary
    # fallback below and collapsed every failing iter to total=1,failed=1
    # (Run 9 Phase 4.6b.1d Finding 1).
    #
    # Real shapes seen in the wild (after ANSI strip):
    #   Tests  6 failed | 35 skipped (41)
    #   Tests  12 passed (12)
    #   Tests  3 failed | 9 passed (12)
    #   Tests  2 failed | 1 skipped | 7 passed (10)
    # The grand total in parens is authoritative when present.
    local skipped=0
    local paren_total=0
    if [[ "$parsed" != "true" ]]; then
        local tests_line
        tests_line=$(echo "$clean" | grep -iE '^\s*Tests\s+[0-9]+\s+(passed|failed|skipped)' | tail -1 || true)
        if [[ -n "$tests_line" ]]; then
            passed=$(echo "$tests_line" | grep -oiE '[0-9]+ passed' | grep -oE '[0-9]+' || echo 0)
            failed=$(echo "$tests_line" | grep -oiE '[0-9]+ failed' | grep -oE '[0-9]+' || echo 0)
            skipped=$(echo "$tests_line" | grep -oiE '[0-9]+ skipped' | grep -oE '[0-9]+' || echo 0)
            paren_total=$(echo "$tests_line" | grep -oE '\([0-9]+\)' | tr -d '()' || echo 0)
            [[ -z "$passed" ]] && passed=0
            [[ -z "$failed" ]] && failed=0
            [[ -z "$skipped" ]] && skipped=0
            [[ -z "$paren_total" ]] && paren_total=0
            parsed=true
        fi
    fi

    # go test: count "--- PASS:" and "--- FAIL:" lines
    if [[ "$parsed" != "true" ]]; then
        if echo "$clean" | grep -qE '--- (PASS|FAIL):'; then
            passed=$(echo "$clean" | grep -cE '--- PASS:' || true)
            failed=$(echo "$clean" | grep -cE '--- FAIL:' || true)
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

    # Phase 7: registered = total including skipped tests.
    # SCENARIO_TOTAL stays as passed+failed (all 14 downstream consumers depend
    # on this denominator). SCENARIO_REGISTERED is the vitest parenthesized total
    # (or computed sum) — used only by the skip threshold check.
    local registered=$((passed + failed + skipped))
    if [[ "$paren_total" -gt 0 ]]; then
        registered=$paren_total
    fi

    # Crash classification: count crash-class errors that start a line (after
    # optional whitespace) followed by a colon.  This distinguishes genuine
    # crashes like "TypeError: expect(...).toEndWith is not a function" from
    # assertion messages that mention crash types like "expected to throw
    # TypeError".
    #
    # Uses python3 (available in all Dark Factory containers) to avoid the
    # SIGPIPE (exit 141) that killed the previous sed|head bash pipeline under
    # set -eo pipefail.
    #
    # Known limitation: a single crash can produce multiple matching lines in
    # vitest output (error line + "Serialized Error" section + summary).  The
    # count is capped at $failed but may still overcount the number of unique
    # crashed tests.  The tsc --noEmit pre-compilation check (scenario
    # validator Check 9) prevents most crashes from reaching runtime.
    local crashed=0
    local assert_failed="$failed"
    if [[ "$failed" -gt 0 ]]; then
        crashed=$(echo "$clean" | DF_FAILED="$failed" python3 -c "
import os, sys, re
failed = int(os.environ['DF_FAILED'])
pat = re.compile(r'^\s*(?:TypeError|ReferenceError|SyntaxError|Cannot find module|ERR_MODULE_NOT_FOUND|ModuleNotFoundError|ImportError|NameError):')
n = sum(1 for line in sys.stdin if pat.match(line))
print(min(n, failed))
" 2>/dev/null || { echo "  Warning: crash classification failed, defaulting to 0" >&2; echo 0; })
        crashed="${crashed:-0}"
        assert_failed=$(( failed - crashed < 0 ? 0 : failed - crashed ))
    fi

    cat > "$counts_file" <<EOF
SCENARIO_PASSED=$passed
SCENARIO_FAILED=$failed
SCENARIO_TOTAL=$total
SCENARIO_RATE=$rate
SCENARIO_CRASHED=$crashed
SCENARIO_ASSERT_FAILED=$assert_failed
SCENARIO_SKIPPED=$skipped
SCENARIO_REGISTERED=$registered
EOF

    # Phase 7: skip-rate threshold — infrastructure failure detection.
    # When >50% of registered tests are skipped, it means a service dependency
    # (database, cache) is unreachable or uninitialized. Flag this so the
    # orchestrator routes to infra-failure handling instead of treating it as
    # implementation bugs.
    if [[ "$registered" -gt 0 ]] && [[ "$skipped" -gt 0 ]]; then
        local skip_pct=$((skipped * 100 / registered))
        if [[ "$skip_pct" -gt 50 ]]; then
            echo "  INFRASTRUCTURE FAILURE: ${skip_pct}% of tests skipped (${skipped}/${registered})"
            echo "  This typically means a service dependency (database, cache) is unreachable."
            echo "  Check that migrations/schema setup ran before scenarios execute."
            echo "skip_failure=true" >> "$counts_file"
        fi
    fi

    echo "  Scenario counts: ${passed}/${total} passed (${rate}%), ${skipped} skipped (${registered} registered)"
}

# --- Extract per-test names and statuses from test runner output ---
# Writes a JSON array to {spec}-test-results-iter{N}.json for stable
# failure detection (P4). Each entry: {name, status, error_type?}.
_write_per_test_results() {
    local raw_output="$1"
    local iteration="$2"
    local results_file
    if [[ "${SMOKE_TEST:-false}" == "true" ]]; then
        results_file=".dark-factory/reports/${SPEC_NAME}-test-results-iter0.json"
    else
        results_file=".dark-factory/reports/${SPEC_NAME}-test-results-iter${iteration}.json"
    fi

    local clean
    clean=$(echo "$raw_output" | sed 's/\x1b\[[0-9;]*[a-zA-Z]//g')

    # Collect entries as individual JSON lines, then wrap in array.
    local entries_file
    entries_file=$(mktemp)
    trap "rm -f '$entries_file'" RETURN

    # --- vitest/jest: "✓ name (Xms)" / "× name (Xms)" ---
    # Also handles ASCII fallbacks: √ (pass) and x/X (fail)
    while IFS= read -r line; do
        local name status
        # Pass markers: ✓ √
        if [[ "$line" =~ ^[[:space:]]*[✓√][[:space:]]+(.*) ]]; then
            name="${BASH_REMATCH[1]}"
            # Strip trailing duration like "(5 ms)"
            # Strip trailing duration: jest "(5 ms)", vitest "2028ms", either
            # with or without parens/space.  Required for stable test-name
            # identity across iterations (otherwise detect_stable_failures
            # gets a drifting duration suffix and plateau detection fails
            # silently — Run 11 regression).
            name=$(echo "$name" | sed -E 's/[[:space:]]*\(?[0-9]+[[:space:]]*m?s\)?[[:space:]]*$//')
            status="passed"
        # Fail markers: ✗ × ✕
        elif [[ "$line" =~ ^[[:space:]]*[✗×✕][[:space:]]+(.*) ]]; then
            name="${BASH_REMATCH[1]}"
            # Strip trailing duration: jest "(5 ms)", vitest "2028ms", either
            # with or without parens/space.  Required for stable test-name
            # identity across iterations (otherwise detect_stable_failures
            # gets a drifting duration suffix and plateau detection fails
            # silently — Run 11 regression).
            name=$(echo "$name" | sed -E 's/[[:space:]]*\(?[0-9]+[[:space:]]*m?s\)?[[:space:]]*$//')
            status="failed"
        # pytest verbose: "file.py::test_name PASSED" / "FAILED"
        elif [[ "$line" =~ ^[[:space:]]*(.*::.+)[[:space:]]+(PASSED|FAILED) ]]; then
            name="${BASH_REMATCH[1]}"
            [[ "${BASH_REMATCH[2]}" == "PASSED" ]] && status="passed" || status="failed"
        # go test: "--- PASS: TestName (0.01s)" / "--- FAIL: TestName"
        elif [[ "$line" =~ ^---[[:space:]]+(PASS|FAIL):[[:space:]]+(.*) ]]; then
            [[ "${BASH_REMATCH[1]}" == "PASS" ]] && status="passed" || status="failed"
            name="${BASH_REMATCH[2]}"
            name=$(echo "$name" | sed -E 's/[[:space:]]*\([0-9.]+s\)[[:space:]]*$//')
        else
            continue
        fi

        # Escape for JSON: backslash and double-quote.
        name=$(echo "$name" | sed 's/\\/\\\\/g; s/"/\\"/g')

        # error_type set to "assertion" here; upgraded to "crash" below for
        # tests whose failure block contains a crash-class error pattern.
        echo "{\"name\": \"${name}\", \"status\": \"${status}\", \"error_type\": \"assertion\"}" >> "$entries_file"
    done <<< "$clean"

    # --- Per-test crash classification (post-process) ---
    # Scan the FAIL summary blocks in the cleaned output to find tests whose
    # error is a crash-class pattern (TypeError, ReferenceError, etc.) and
    # upgrade their error_type from "assertion" to "crash".  Uses the same
    # crash regex as the aggregate crash count above for consistency.
    if [[ -s "$entries_file" ]]; then
        python3 -c "
import sys, re, json, os

entries_path = sys.argv[1]
clean = sys.stdin.read()

crash_pat = re.compile(
    r'^\s*(?:TypeError|ReferenceError|SyntaxError|Cannot find module'
    r'|ERR_MODULE_NOT_FOUND|ModuleNotFoundError|ImportError|NameError):',
)

# Parse FAIL blocks: ' FAIL  file > describe > test name' followed by error
# lines.  Extract test name suffix (after last '>') and check if error is crash.
fail_re = re.compile(r'^\s*FAIL\s+\S+.*?>\s*(.+?)\s*$')
lines = clean.split('\n')
crash_test_fragments: set[str] = set()
for i, line in enumerate(lines):
    m = fail_re.match(line)
    if not m:
        continue
    test_fragment = m.group(1).strip()
    for j in range(i + 1, min(i + 6, len(lines))):
        if crash_pat.match(lines[j]):
            crash_test_fragments.add(test_fragment)
            break
        # Stop at next FAIL block or test marker
        if fail_re.match(lines[j]) or re.match(r'^\s*[✓√✗×✕]', lines[j]):
            break

if not crash_test_fragments:
    sys.exit(0)

# Read entries, upgrade matching failed tests to crash
with open(entries_path) as f:
    entries = [json.loads(line) for line in f if line.strip()]

changed = False
for entry in entries:
    if entry['status'] != 'failed':
        continue
    for frag in crash_test_fragments:
        if frag in entry['name']:
            entry['error_type'] = 'crash'
            changed = True
            break

if changed:
    with open(entries_path, 'w') as f:
        for entry in entries:
            f.write(json.dumps(entry) + '\n')
" "$entries_file" <<< "$clean" 2>&1 || echo "  Warning: per-test crash classification failed (non-fatal)" >&2
    fi

    # Write JSON array.
    echo "[" > "$results_file"
    if [[ -s "$entries_file" ]]; then
        # Join entries with commas (paste all lines, add comma separator).
        local first=true
        while IFS= read -r entry; do
            if [[ "$first" == "true" ]]; then
                echo "  $entry" >> "$results_file"
                first=false
            else
                echo "  ,$entry" >> "$results_file"
            fi
        done < "$entries_file"
    fi
    echo "]" >> "$results_file"
}

# Phase 4.6b.1d Finding 1: assert the shell scenario counter and the per-test
# JSON writer agree on SCENARIO_FAILED.  The two parsers independently scan
# the same raw vitest output; Run 9 proved they can drift silently.  Writes
# a structured line to .dark-factory/audit/gate-telemetry.jsonl on every
# invocation (success or failure) and exits the gate non-zero with a visible
# error message when they disagree.
_assert_scenario_count_invariant() {
    local iteration="$1"
    # Phase 4.6b.1g scope guard (Run 11 follow-up): the invariant must only
    # run on real iteration gate runs, not on the smoke-test baseline.  In
    # smoke mode _parse_scenario_counts writes to {spec}-smoke-test.env while
    # this check reads {spec}-scenario-counts-executable.env — comparing two
    # unrelated files produces a spurious mismatch (Run 11 iter0 telemetry:
    # reported 0/0, parsed 5 failed rows).  iter0 is also legitimately the
    # pre-implementation baseline; the two-parser drift bug 4.6b.1d guards
    # against cannot occur until there is real impl code to test.
    if [[ "${SMOKE_TEST:-false}" == "true" ]] || [[ "$iteration" == "0" ]]; then
        return 0
    fi
    local counts_file=".dark-factory/reports/${SPEC_NAME}-scenario-counts-executable.env"
    local results_file=".dark-factory/reports/${SPEC_NAME}-test-results-iter${iteration}.json"
    local telemetry_dir=".dark-factory/audit"
    local telemetry_file="${telemetry_dir}/gate-telemetry.jsonl"

    mkdir -p "$telemetry_dir" 2>/dev/null || true

    local reported_failed=0 reported_total=0
    if [[ -f "$counts_file" ]]; then
        reported_failed=$(grep '^SCENARIO_FAILED=' "$counts_file" | cut -d= -f2 || echo 0)
        reported_total=$(grep '^SCENARIO_TOTAL=' "$counts_file" | cut -d= -f2 || echo 0)
        [[ -z "$reported_failed" ]] && reported_failed=0
        [[ -z "$reported_total" ]] && reported_total=0
    fi

    local parsed_failed_rows=0
    if [[ -f "$results_file" ]]; then
        parsed_failed_rows=$(grep -c '"status": "failed"' "$results_file" 2>/dev/null || echo 0)
        parsed_failed_rows=${parsed_failed_rows//[^0-9]/}
        [[ -z "$parsed_failed_rows" ]] && parsed_failed_rows=0
    fi

    local ts
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    printf '{"schema_version":1,"ts":"%s","spec":"%s","iteration":%s,"reported_failed":%s,"reported_total":%s,"parsed_failed_rows":%s}\n' \
        "$ts" "$SPEC_NAME" "$iteration" "$reported_failed" "$reported_total" "$parsed_failed_rows" \
        >> "$telemetry_file" 2>/dev/null || true

    if [[ "$reported_failed" -ne "$parsed_failed_rows" ]]; then
        echo "" >&2
        echo "FATAL: scenario count invariant violated (Phase 4.6b.1d Finding 1 guard)" >&2
        echo "  ${counts_file}: SCENARIO_FAILED=${reported_failed}" >&2
        echo "  ${results_file}: ${parsed_failed_rows} rows with status==failed" >&2
        echo "  These two parsers must agree.  A mismatch means _parse_scenario_counts" >&2
        echo "  and _write_per_test_results drifted again — investigate before shipping." >&2
        GATE_FAILED=true
        return 1
    fi
    return 0
}

echo "Running quality gates for: ${SPEC_NAME}"
echo "Toolchain detected: ${TOOLCHAIN}"
echo ""

# In smoke test mode, override report paths up-front so the install step
# (and any subsequent rows) write to the smoke report files instead of the
# full-gate report. Pre-gate mode already overrode at line ~110.
if [[ "$SMOKE_TEST" == "true" ]]; then
    REPORT_FILE=".dark-factory/reports/${SPEC_NAME}-smoke-test.md"
    SUMMARY_FILE=".dark-factory/reports/${SPEC_NAME}-smoke-test-summary.md"
    cat > "$REPORT_FILE" <<EOF
# Scenario Smoke Test: ${SPEC_NAME}

| Gate | Status | Details |
|------|--------|---------|
EOF
    cat > "$SUMMARY_FILE" <<EOF
# Smoke Test Summary: ${SPEC_NAME}

| Gate | Status |
|------|--------|
EOF
    echo "SMOKE TEST MODE — skipping Gates 1-4, running scenarios only"
    echo ""
fi

# --- Defensive install step (runs before any gate, in all modes) ---
# Bug #6 fix: gate container previously never installed dependencies, so
# vitest/eslint/tsc all failed with "command not found". The architect-supplied
# commands.install (forwarded as --install-cmd) is the canonical source. If
# absent, fall back to lockfile-based detection per language. Explicit failures
# are infrastructure failures (exit 2); fallback failures are warnings.
run_install_step() {
    local install_cmd=""
    local source="explicit"

    if [[ -n "$CLI_INSTALL_CMD" ]]; then
        install_cmd="$CLI_INSTALL_CMD"
    else
        source="fallback"
        # Language-detected fallback ladder. Ordered: lockfile presence wins.
        if [[ -f "package.json" ]]; then
            if [[ -f "pnpm-lock.yaml" ]]; then
                install_cmd="pnpm install --frozen-lockfile"
            elif [[ -f "yarn.lock" ]]; then
                install_cmd="yarn install --frozen-lockfile"
            elif [[ -f "package-lock.json" ]]; then
                install_cmd="npm ci"
            fi
        elif [[ -f "requirements.txt" ]]; then
            install_cmd="pip install -r requirements.txt"
        elif [[ -f "poetry.lock" ]] || [[ -f "uv.lock" ]]; then
            # Poetry/uv projects must set commands.install explicitly — don't
            # guess with `pip install -e .`, it usually fails or produces a
            # broken environment. Skip cleanly.
            echo "  Install: SKIP (poetry/uv project — set commands.install explicitly)"
            echo "| Install | SKIP | poetry/uv project — set commands.install explicitly |" >> "$REPORT_FILE"
            echo "| Install | SKIP |" >> "$SUMMARY_FILE"
            return 0
        elif [[ -f "pyproject.toml" ]] || [[ -f "setup.py" ]]; then
            install_cmd="pip install -e ."
        elif [[ -f "go.mod" ]]; then
            install_cmd="go mod download"
        elif [[ -f "Cargo.toml" ]]; then
            install_cmd="cargo fetch"
        fi
    fi

    if [[ -z "$install_cmd" ]]; then
        echo "  Install: SKIP (no commands.install set and no recognized lockfile)"
        echo "| Install | SKIP | no install command and no recognized lockfile |" >> "$REPORT_FILE"
        echo "| Install | SKIP |" >> "$SUMMARY_FILE"
        return 0
    fi

    echo "  Install (${source}): ${install_cmd}"
    local out rc=0
    out=$(bash -c "$install_cmd" 2>&1) || rc=$?

    if [[ $rc -eq 0 ]]; then
        echo "| Install | PASS | ${source}: ${install_cmd} |" >> "$REPORT_FILE"
        echo "| Install | PASS |" >> "$SUMMARY_FILE"
        echo "    PASS"
        return 0
    fi

    # Failure handling differs by source.
    echo "| Install | FAIL | ${source}: exit ${rc} |" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
    echo "### Install Failure Details" >> "$REPORT_FILE"
    echo '```' >> "$REPORT_FILE"
    echo "$out" | _normalize_output | tail -50 >> "$REPORT_FILE"
    echo '```' >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"

    if [[ "$source" == "explicit" ]]; then
        echo "| Install | FAIL |" >> "$SUMMARY_FILE"
        # Sentinel: gate table ends here — parser must stop before error details.
        # (Early exit — the normal-flow sentinel at end-of-script will not run.)
        echo "<!-- END GATE TABLE -->" >> "$SUMMARY_FILE"
        # Include sanitized error details so the Reviewer/Oracle can diagnose.
        local install_sanitized
        install_sanitized=$(echo "$out" | _sanitize_output)
        if [[ -n "$install_sanitized" ]]; then
            {
                echo ""
                echo "### Install Failure Details"
                echo '```'
                echo "$install_sanitized"
                echo '```'
            } >> "$SUMMARY_FILE"
        fi
        # Sentinel comment (not a table row) so trajectory/Oracle parsers can
        # detect infrastructure failure without corrupting the markdown table.
        echo "<!-- INSTALL_FAILURE=true -->" >> "$SUMMARY_FILE"
        echo "" >> "$SUMMARY_FILE"
        echo "## Result: FAIL (install)" >> "$SUMMARY_FILE"
        echo "    FAIL (explicit commands.install — infrastructure failure)"
        echo "QUALITY GATE: FAIL (install)"
        rm -f "$SUMMARY_DETAILS_FILE"
        # Hard exit immediately — do not run any gates against an unbuilt tree.
        # In smoke mode the Gates 1-4 conditional would otherwise skip the
        # downstream BUILD_FAILED → exit 2 branch entirely.
        exit 2
    else
        echo "| Install | WARN |" >> "$SUMMARY_FILE"
        echo "    WARN (fallback install failed — proceeding best-effort)"
    fi
}

run_install_step

# _pg_diagnostics_snapshot <outfile>
#   If DATABASE_URL is set and psql is available, dump pg_stat_activity +
#   pg_locks to outfile. No-op otherwise. Never fails the caller — diagnostic
#   capture is best-effort. Each psql invocation is itself time-bounded
#   because the usual cause of a setup hang is a wedged database — an
#   un-bounded diagnostic can hang on the very condition we're trying to
#   observe.
_pg_diagnostics_snapshot() {
    local outfile="$1"
    if [[ -z "${DATABASE_URL:-}" ]]; then return 0; fi
    if ! command -v psql &>/dev/null; then return 0; fi
    local psql_timeout=""
    if [[ -n "$TIMEOUT_CMD" ]]; then psql_timeout="$TIMEOUT_CMD 5s"; fi
    {
        echo "### pg_stat_activity"
        echo '```'
        $psql_timeout psql "$DATABASE_URL" -X -c "SELECT pid, state, wait_event_type, wait_event, query_start, left(query, 200) AS query FROM pg_stat_activity WHERE datname IS NOT NULL;" 2>&1 | grep -vi 'password' || echo "(pg_stat_activity query failed or timed out)"
        echo '```'
        echo ""
        echo "### pg_locks (non-granted)"
        echo '```'
        $psql_timeout psql "$DATABASE_URL" -X -c "SELECT locktype, relation::regclass, mode, granted, pid FROM pg_locks WHERE NOT granted;" 2>&1 | grep -vi 'password' || echo "(pg_locks query failed or timed out)"
        echo '```'
    } >> "$outfile"
}

# Phase 8: run commands.setup after install, before gates. Typically used for
# database migrations/schema setup. Skipped in smoke/pre-gate mode (no services).
#
# Setup timeout rationale: migrations that exceed SETUP_TIMEOUT are
# structurally stuck (e.g., blocked on a lock, waiting on a missing
# service) — not slow. Timing out with diagnostics beats burning the full
# Python-level container timeout with no breadcrumbs. SETUP_HANG is
# distinct from SETUP_FAIL so downstream can treat it as ENV_ISSUE.
run_pre_setup() {
    # Run architect-emitted commands.pre_setup entries BEFORE the main
    # setup_cmd. Each entry runs under `bash -c` with a per-entry timeout
    # (same as setup_cmd) to prevent a wedged entry from hanging the gate —
    # especially relevant when the architect emits a scaffolding subcommand
    # like `drizzle-kit generate`, which can hang on a missing DB. Failure of
    # any entry fails the Setup gate with the specific command index and
    # output, consistent with f43970e's pattern of surfacing the actual
    # failing command rather than a generic exit code.
    local n=${#CLI_PRE_SETUP_CMDS[@]}
    if [[ $n -eq 0 ]]; then
        return 0
    fi
    local pre_timeout="${CLI_SETUP_TIMEOUT:-300}"
    echo "  Pre-setup: ${n} command(s) (timeout=${pre_timeout}s per entry)"
    local i=0
    for cmd in "${CLI_PRE_SETUP_CMDS[@]}"; do
        i=$((i + 1))
        echo "    [${i}/${n}] ${cmd}"
        local out rc=0
        if [[ -n "$TIMEOUT_CMD" ]] && [[ "$pre_timeout" -gt 0 ]]; then
            out=$("$TIMEOUT_CMD" --kill-after=5s "${pre_timeout}s" \
                bash -c "$cmd" 2>&1) || rc=$?
        else
            out=$(bash -c "$cmd" 2>&1) || rc=$?
        fi
        if [[ $rc -ne 0 ]]; then
            # Fixed-width cell so `|` inside $cmd can't corrupt the Markdown
            # table that the oracle consumes. Full command + output go in the
            # code block below.
            local hang_note=""
            if [[ $rc -eq 124 ]] || [[ $rc -eq 137 ]] || [[ $rc -eq 143 ]]; then
                hang_note=" (timeout/hang)"
            fi
            echo "| Setup | FAIL | pre_setup[${i}/${n}] exit ${rc}${hang_note} (see Pre-Setup Failure Details) |" >> "$REPORT_FILE"
            echo "| Setup | FAIL |" >> "$SUMMARY_FILE"
            echo "" >> "$REPORT_FILE"
            echo "### Pre-Setup Failure Details" >> "$REPORT_FILE"
            echo '```' >> "$REPORT_FILE"
            echo "pre_setup[${i}/${n}] exited ${rc}${hang_note}:" >> "$REPORT_FILE"
            echo "  cmd: ${cmd}" >> "$REPORT_FILE"
            # Show both head and tail so a catastrophic failure whose signal
            # is at the start (e.g., bash parse error) isn't silently
            # truncated by `tail -30`. Include total-line count so the oracle
            # (or a human) can tell whether the middle was elided.
            local total_lines
            total_lines=$(printf '%s\n' "$out" | wc -l | tr -d ' ')
            if [[ "$total_lines" -le 30 ]]; then
                printf '%s\n' "$out" >> "$REPORT_FILE"
            else
                echo "[output: ${total_lines} lines total; head 15 + tail 15 shown]" >> "$REPORT_FILE"
                printf '%s\n' "$out" | head -15 >> "$REPORT_FILE"
                echo "..." >> "$REPORT_FILE"
                printf '%s\n' "$out" | tail -15 >> "$REPORT_FILE"
            fi
            echo '```' >> "$REPORT_FILE"
            echo "<!-- SETUP_FAILURE=true -->" >> "$REPORT_FILE"
            echo "    FAIL (exit ${rc}${hang_note})"
            return 1
        fi
    done
    return 0
}

run_setup_step() {
    # Convention file override: if the implementer wrote scripts/df-setup.sh,
    # use it instead of the architect's commands.setup guess. The implementer
    # knows which ORM commands actually work in the project.
    local setup_cmd="$CLI_SETUP_CMD"
    if [[ -x "scripts/df-setup.sh" ]]; then
        setup_cmd="bash scripts/df-setup.sh"
    fi

    if [[ -z "$setup_cmd" ]] && [[ ${#CLI_PRE_SETUP_CMDS[@]} -eq 0 ]]; then
        return 0
    fi
    if [[ "$SMOKE_TEST" == "true" ]] || [[ "$PRE_GATE" == "true" ]]; then
        echo "  Setup: SKIP (${SMOKE_TEST:+smoke}${PRE_GATE:+pre-gate} mode — no services)"
        return 0
    fi

    # Run pre_setup commands BEFORE the main setup_cmd. If any fails, the
    # Setup gate fails and we never attempt setup_cmd — that's deliberate:
    # a pre_setup failure means the environment precondition is broken,
    # and setup_cmd would fail obscurely downstream.
    if ! run_pre_setup; then
        return 1
    fi

    # If setup_cmd is empty but pre_setup succeeded, that's a valid state:
    # preconditions were all that was needed. Report Setup PASS and return.
    if [[ -z "$setup_cmd" ]]; then
        echo "| Setup | PASS | (pre_setup only) |" >> "$REPORT_FILE"
        echo "| Setup | PASS |" >> "$SUMMARY_FILE"
        echo "  Setup: PASS (pre_setup only)"
        return 0
    fi

    local setup_timeout="${CLI_SETUP_TIMEOUT:-300}"
    # Validate: anything non-numeric would slip through and become a
    # cryptic `timeout: invalid time interval 'foos'` diagnostic.
    if ! [[ "$setup_timeout" =~ ^[0-9]+$ ]]; then
        echo "ERROR: --setup-timeout must be numeric, got '${setup_timeout}'" >&2
        exit 1
    fi
    echo "  Setup: ${setup_cmd} (timeout=${setup_timeout}s)"
    local out rc=0
    # stdbuf -oL -eL: force line-buffering on stdout/stderr. Under
    # default block-buffering, a child killed by SIGKILL loses any output
    # still in its 4KB libc buffer — which is exactly the output we want
    # to see when diagnosing a hang. Line-buffering ensures spinner
    # frames and progress messages flush to the pipe before kill.
    # stdbuf must wrap a real binary. It fronts bash, which then evaluates
    # $setup_cmd with full shell grammar (builtins, &&, |, subshells).
    local runner_prefix=()
    if command -v stdbuf &>/dev/null; then
        runner_prefix=(stdbuf -oL -eL)
    fi
    if [[ -n "$TIMEOUT_CMD" ]] && [[ "$setup_timeout" -gt 0 ]]; then
        # --kill-after=10s: if TERM doesn't make the child exit within 10s,
        # escalate to KILL. Prevents a wedged migrate process from outliving
        # the timeout boundary.
        out=$("$TIMEOUT_CMD" --kill-after=10s "${setup_timeout}s" \
            "${runner_prefix[@]}" bash -c "$setup_cmd" 2>&1) || rc=$?
    else
        out=$("${runner_prefix[@]}" bash -c "$setup_cmd" 2>&1) || rc=$?
    fi

    if [[ $rc -eq 0 ]]; then
        echo "| Setup | PASS | ${setup_cmd} |" >> "$REPORT_FILE"
        echo "| Setup | PASS |" >> "$SUMMARY_FILE"
        echo "    PASS"
        return 0
    fi

    # Distinguish hang (timeout-signal exit codes) from genuine failure.
    # GNU `timeout` exits 124 when the child died of SIGTERM on expiration,
    # 137 (128+9) when --kill-after escalated to SIGKILL, and 143 (128+15)
    # when the bash wrapper itself received and passed on SIGTERM before
    # the child was reaped. Any of these three means "we had to kill this
    # ourselves — it never finished on its own."
    local status_label="FAIL"
    local result_footer="FAIL (setup)"
    local sentinel="SETUP_FAILURE=true"
    local is_hang=false
    if [[ $rc -eq 124 ]] || [[ $rc -eq 137 ]] || [[ $rc -eq 143 ]]; then
        status_label="HANG"
        result_footer="FAIL (setup-hang)"
        sentinel="SETUP_HANG=true"
        is_hang=true
    fi

    echo "| Setup | ${status_label} | exit ${rc} |" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
    if [[ "$is_hang" == "true" ]]; then
        echo "### Setup Hang Diagnostics" >> "$REPORT_FILE"
        echo "Setup command exceeded ${setup_timeout}s and was killed (exit ${rc})." >> "$REPORT_FILE"
        echo "" >> "$REPORT_FILE"
        # Postgres-side state (pg_stat_activity, pg_locks) survives the
        # client kill, so it's still load-bearing at this point. A client
        # process-tree snapshot would be post-mortem (the hung child is
        # reaped before we run ps), so we don't bother.
        _pg_diagnostics_snapshot "$REPORT_FILE"
        echo "" >> "$REPORT_FILE"
        echo "### Captured output before kill (may be truncated — child was killed mid-stream)" >> "$REPORT_FILE"
    else
        echo "### Setup Failure Details" >> "$REPORT_FILE"
    fi
    echo '```' >> "$REPORT_FILE"
    echo "$out" | _normalize_output | tail -50 >> "$REPORT_FILE"
    echo '```' >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"

    echo "| Setup | ${status_label} |" >> "$SUMMARY_FILE"
    # Sentinel: gate table ends here — parser must stop before error details.
    echo "<!-- END GATE TABLE -->" >> "$SUMMARY_FILE"
    # Include sanitized error details so the Reviewer/Oracle can diagnose.
    local setup_sanitized
    setup_sanitized=$(echo "$out" | _sanitize_output)
    if [[ -n "$setup_sanitized" ]]; then
        {
            echo ""
            if [[ "$is_hang" == "true" ]]; then
                echo "### Setup Hang (timeout after ${setup_timeout}s, exit ${rc})"
            else
                echo "### Setup Failure Details"
            fi
            echo '```'
            echo "$setup_sanitized"
            echo '```'
        } >> "$SUMMARY_FILE"
    fi
    echo "<!-- ${sentinel} -->" >> "$SUMMARY_FILE"
    echo "" >> "$SUMMARY_FILE"
    echo "## Result: ${result_footer}" >> "$SUMMARY_FILE"
    echo "    ${result_footer} — infrastructure failure"
    rm -f "$SUMMARY_DETAILS_FILE"
    echo "QUALITY GATE: ${result_footer}"
    exit 2
}

# Sprint 21: deterministic DB driver<->URL-scheme preflight (python+postgres).
# A postgres DATABASE_URL names exactly one SQLAlchemy driver via its scheme; if
# the project didn't install that driver, the Setup migration (and every DB
# scenario) crashes with an obscure ``ModuleNotFoundError`` the implementer
# can't readily diagnose. Resolve the required module from the URL scheme
# (deterministic — not a guess) and fail Setup early with a precise message.
# Runs AFTER install (drivers from main deps are present) and BEFORE setup (so
# the alembic migration doesn't crash obscurely). python toolchain only — a
# node+postgres project uses a JS pg client, not psycopg.
# Audience: Implementer-readable on the next iteration (deterministic
# driver/scheme env-contract diagnostic; the message names no scenario, holdout,
# or test content — it is the same env-contract surface the Setup gate already
# exposes).
run_db_driver_preflight() {
    [[ "$TOOLCHAIN" == "python" ]] || return 0
    # Mirror run_setup_step: smoke / pre-gate modes start NO services and skip
    # the migration, so a driver check is moot there and could only false-FAIL.
    # The full gate run (where the real crash occurred) still catches the mismatch.
    if [[ "${SMOKE_TEST:-}" == "true" ]] || [[ "${PRE_GATE:-}" == "true" ]]; then
        return 0
    fi
    # DATABASE_URL is injected only for a postgres spec (services.postgres
    # inject_env), and the driver it implies is needed by BOTH the Setup
    # migration AND the DB scenarios — so gate on DATABASE_URL, not on a
    # setup_cmd existing (the real-world crash was at SCENARIO collection).
    [[ -n "${DATABASE_URL:-}" ]] || return 0
    # Defensive: if python3 is somehow absent, skip (fail-open) rather than
    # misreport "python3: command not found" as a driver/scheme mismatch.
    command -v python3 >/dev/null 2>&1 || return 0
    local out rc=0
    out=$(python3 "${SCRIPT_DIR}/lib/db_driver_preflight.py" "$DATABASE_URL" 2>&1) || rc=$?
    [[ $rc -eq 0 ]] && return 0
    echo "  Setup: FAIL (DB driver/scheme preflight)"
    echo "| Setup | FAIL | DB driver/scheme mismatch |" >> "$REPORT_FILE"
    {
        echo ""
        echo "### Setup Failure Details"
        echo '```'
        echo "$out"
        echo '```'
        echo ""
    } >> "$REPORT_FILE"
    echo "| Setup | FAIL |" >> "$SUMMARY_FILE"
    # Sentinel: gate table ends here — parser must stop before error details.
    echo "<!-- END GATE TABLE -->" >> "$SUMMARY_FILE"
    {
        echo ""
        echo "### Setup Failure Details"
        echo '```'
        echo "$out"
        echo '```'
    } >> "$SUMMARY_FILE"
    echo "<!-- SETUP_FAILURE=true -->" >> "$SUMMARY_FILE"
    echo "" >> "$SUMMARY_FILE"
    echo "## Result: FAIL (setup)" >> "$SUMMARY_FILE"
    echo "    FAIL (setup) — DB driver/scheme mismatch"
    rm -f "$SUMMARY_DETAILS_FILE"
    echo "QUALITY GATE: FAIL (setup)"
    exit 2
}

run_db_driver_preflight
run_setup_step

# Note: workspace script consistency is checked by the orchestrator before
# the iteration loop starts (darkfactory/setup/workspace_check.py).
# No container-level Gate 0 check is needed here.

# --- Smoke test mode: skip Gates 1-4, jump to Gate 5 (scenarios) ---
# When SMOKE_TEST=true, we skip all gates and jump directly to scenarios.
# The skip is implemented by wrapping Gates 1-4 in a conditional block.
if [[ "$SMOKE_TEST" != "true" ]]; then

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
run_gate "Build" "$BUILD_CMD" true
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
run_gate "Lint" "$LINT_CMD" true

# Gate 3: Type Check
# Use config TYPECHECK_CMD if set, otherwise auto-detect
if [[ -z "${TYPECHECK_CMD:-}" ]]; then
    case $TOOLCHAIN in
        node)   TYPECHECK_CMD="tsc --noEmit 2>&1" ;;
        # --disable-error-code=import-untyped: don't hard-fail on a third-party
        # lib that ships no type stubs (the image has mypy but no types-* stubs);
        # [import-not-found] + in-project type errors stay enforced. Mirrors the
        # detect.py-generated command for the no-config fallback path.
        python) TYPECHECK_CMD="mypy . --disable-error-code=import-untyped 2>&1 || pyright . 2>&1" ;;
        go)     TYPECHECK_CMD="" ;; # Covered by build
        rust)   TYPECHECK_CMD="" ;; # Covered by build
        *)      TYPECHECK_CMD="" ;;
    esac
fi
run_gate "Type Check" "$TYPECHECK_CMD" true

# Pre-gate early exit: Gates 1-3 complete, skip Gates 4+.
# Do NOT reference $CLI_TEST_CMD or any Gate 4+ variables before this exit —
# with set -euo pipefail, referencing an unset variable is a fatal error.
if [[ "$PRE_GATE" == "true" ]]; then
    rm -f "$SUMMARY_DETAILS_FILE"
    echo "" >> "$REPORT_FILE"
    if [[ "$GATE_FAILED" == "true" ]]; then
        echo "## Result: FAIL" >> "$REPORT_FILE"
        echo "PRE_GATE_PASSED=false" > "$SUMMARY_FILE"
        echo ""
        echo "PRE-GATE: FAIL"
        echo "See report: ${REPORT_FILE}"
        if [[ "$BUILD_FAILED" == "true" ]]; then
            exit 2  # Build failure
        fi
        exit 1      # Non-build failure
    else
        echo "## Result: PASS" >> "$REPORT_FILE"
        echo "PRE_GATE_PASSED=true" > "$SUMMARY_FILE"
        echo ""
        echo "PRE-GATE: PASS"
        echo "See report: ${REPORT_FILE}"
        exit 0
    fi
fi

# --- Substrate Verification phase ---
# Renders a "Substrate Verification" row from a findings JSON file
# written by the orchestrator-side dispatcher
# (darkfactory.pipeline.substrate_phase) BEFORE this container started.
# The verifier ran in a peer container (same execution network, with
# the darkfactory package mounted) — this in-gate step is pure
# rendering with stdlib Python. See scripts/lib/render_substrate_phase.py.
# Position: between PRE_GATE exit and Gate 4 — substrate is not part
# of pre-gate (no services running), and smoke mode is already
# excluded by the enclosing SMOKE_TEST guard.
SUBSTRATE_FINDINGS_FILE=".dark-factory/reports/${SPEC_NAME}-substrate-findings.json"
echo "  Running gate: Substrate Verification..."
python3 /opt/dark-factory/scripts/lib/render_substrate_phase.py \
    "$REPORT_FILE" "$SUMMARY_FILE" "$SUBSTRATE_FINDINGS_FILE" \
    || GATE_FAILED=true

# Import Contract: verify architect manifest entry_points resolve at runtime
# from the scenarios-dir depth. This catches module-system / import-extension
# / specifier-resolution mismatches that would otherwise surface as silent
# SCENARIO_CRASH during blind scenarios. Runs post-substrate so generated
# artifacts (drizzle types, prisma client, etc.) are in place, and
# pre-Self-Tests so the single-import-per-file diagnostic surfaces before
# a full suite masks it.
echo "  Running gate: Import Contract..."
python3 /opt/dark-factory/scripts/lib/render_import_contract_phase.py \
    "$REPORT_FILE" "$SUMMARY_FILE" "$SPEC_NAME" "$TOOLCHAIN" \
    || GATE_FAILED=true

# Gate 4: Self-Tests (implementation's own tests)
# CLI --test-cmd flag takes highest priority, then config TEST_CMD, then auto-detect
if [[ -n "$CLI_TEST_CMD" ]]; then
    SELF_TEST_CMD="$CLI_TEST_CMD"
elif [[ -n "${TEST_CMD:-}" ]]; then
    SELF_TEST_CMD="$TEST_CMD"
else
    case $TOOLCHAIN in
        node)
            # Only default to npm test if package.json actually has a "test" script.
            if node -e "const p=require('./package.json'); if(!p.scripts||!p.scripts.test)process.exit(1)" 2>/dev/null; then
                SELF_TEST_CMD="npm test 2>&1"
            else
                SELF_TEST_CMD=""
            fi
            ;;
        python) SELF_TEST_CMD="pytest tests/ -v 2>&1" ;;
        go)     SELF_TEST_CMD="go test ./... -v 2>&1" ;;
        rust)   SELF_TEST_CMD="cargo test 2>&1" ;;
        *)      SELF_TEST_CMD="" ;;
    esac
fi
run_gate "Self-Tests" "$SELF_TEST_CMD" true

fi  # End of SMOKE_TEST != true conditional (Gates 1-4 skipped in smoke mode)

# Note: smoke-mode report header override + smoke-mode dependency install
# both moved earlier (before run_install_step) so the install row writes to
# the correct (smoke) report files. See run_install_step above.

# Scenario Lint (pre-scenario-execution): reports shape-level bugs in
# generated scenario files that would otherwise crash as "ENOENT / cwd
# not found" under execSync. NON-BLOCKING — writes a FAIL row to the
# gate report for observability but does NOT set GATE_FAILED. The right
# architectural home for §4a forbidden_test_shapes enforcement is the
# TE pre-acceptance phase (counted against TE retry budget, never
# surfaced to the Implementer); see ROADMAP.md Phase 2 B1.
#
# ROADMAP.md Phase 2 B2: the FAIL row is a count-only summary. The
# verbose body (rule names, file:line anchors, source excerpts —
# verbatim test-shape information) lands in
# .dark-factory/audit/scenario-lint-<spec>-iter<n>.txt. The audit
# directory is masked from the Implementer container
# (barriers.py:62 — implementer_masked includes _AUDIT), so a leak
# of the lint output stays Reviewer/Oracle/human-readable only.
# Defense-in-depth even after B1 lands.
#
# Skipped when there's no scenarios dir (sentinel case).
if [[ -d "$SCENARIOS_DIR" ]] && ! has_sentinel_file "$SCENARIOS_DIR"; then
    _SCENARIO_LINT_SCRIPT="/opt/dark-factory/scripts/lib/scenario_lint.py"
    if [[ -f "$_SCENARIO_LINT_SCRIPT" ]] && command -v python3 >/dev/null 2>&1; then
        # Capture exit code inline — under ``set -e`` a non-zero return from
        # ``$(…)`` kills the script BEFORE ``_LINT_EXIT=$?`` on the next line
        # runs, truncating the gate report after Self-Tests and skipping
        # every subsequent step (Scenario Lint row, Blind Scenarios,
        # Mutation Testing). ``|| _LINT_EXIT=$?`` keeps set -e happy and
        # preserves the original captured exit code.
        _LINT_EXIT=0
        # --spec-path enables §4a forbidden_test_shapes enforcement
        # (Phase 5 Stage 2d). Pass the spec markdown when present;
        # missing file is harmless — the script returns no spec-driven
        # issues and runs the built-in rules unchanged.
        _SCENARIO_LINT_SPEC="/project/.dark-factory/specs/${SPEC_NAME}.md"
        _SCENARIO_LINT_ARGS=("$SCENARIOS_DIR")
        if [[ -f "$_SCENARIO_LINT_SPEC" ]]; then
            _SCENARIO_LINT_ARGS+=(--spec-path "$_SCENARIO_LINT_SPEC")
        fi
        # B2: route verbose body to the audit dir; stdout is just the
        # issue count. stderr (YAML parse errors, regex compile errors)
        # goes to a sibling .stderr file so the detail file stays
        # parseable as the format scenario_lint.py wrote (e.g.,
        # `--json --detail-out` produces clean JSON; stderr does not
        # corrupt the structure). An unwriteable audit dir is fatal
        # by design — `mkdir -p` failure under `set -e` aborts the
        # whole gate, which is the loudest possible signal.
        _LINT_AUDIT_DIR=".dark-factory/audit"
        mkdir -p "$_LINT_AUDIT_DIR"
        _LINT_DETAIL="${_LINT_AUDIT_DIR}/scenario-lint-${SPEC_NAME}-iter${_DF_ITERATION}.txt"
        _LINT_DETAIL_ERR="${_LINT_DETAIL}.stderr"
        _SCENARIO_LINT_ARGS+=(--detail-out "$_LINT_DETAIL")
        _LINT_OUT=$(python3 "$_SCENARIO_LINT_SCRIPT" "${_SCENARIO_LINT_ARGS[@]}" 2>>"$_LINT_DETAIL_ERR") || _LINT_EXIT=$?
        if [[ $_LINT_EXIT -ne 0 ]]; then
            # _LINT_OUT is the issue count from --detail-out's stdout
            # contract: a single integer on a single line. Take only
            # the last line as defense against a future stray stdout
            # write polluting the count. Default to "?" if the script
            # crashed before printing the count (stderr file may hold
            # a traceback).
            _LINT_COUNT="${_LINT_OUT##*$'\n'}"
            _LINT_COUNT="${_LINT_COUNT:-?}"
            echo "| Scenario Lint | FAIL | [pipeline] ${_LINT_COUNT} shape issue(s); details in audit dir (Implementer-masked) |" >> "$REPORT_FILE"
            echo "| Scenario Lint | FAIL |" >> "$SUMMARY_FILE"
            echo "  Scenario Lint: FAIL (${_LINT_COUNT} issue(s); see ${_LINT_DETAIL})"
        else
            echo "| Scenario Lint | PASS | [pipeline] no shape-level issues |" >> "$REPORT_FILE"
            echo "| Scenario Lint | PASS |" >> "$SUMMARY_FILE"
            echo "  Scenario Lint: PASS"
        fi
    fi
fi

# Gate 5: Blind Scenarios
if has_sentinel_file "$SCENARIOS_DIR"; then
    # Sentinel present — Scenario Architect explicitly declared scenarios not applicable.
    # The sentinel's justification was validated before the implementation ran.
    echo "| Blind Scenarios | SKIP | SCENARIOS-NOT-APPLICABLE.md sentinel present |" >> "$REPORT_FILE"
    echo "| Blind Scenarios | SKIP |" >> "$SUMMARY_FILE"
    echo "  Blind Scenarios: SKIP (sentinel present — declared not applicable)"
elif [[ -d "$SCENARIOS_DIR" ]] && has_scenario_files "$SCENARIOS_DIR"; then
    _multi_n="${SCENARIO_MULTI_RUN_N:-1}"
    if [[ "$_multi_n" -gt 1 ]]; then
        # Multi-run mode: per-file N-of-K with weighted scoring
        # detect_scenario_formats is called inside _df_run_scenarios_satisfaction
        # (via the updated find patterns) so all formats are counted.
        echo "  Running gate: Blind Scenarios (multi-run N=${_multi_n}, K=${SCENARIO_MULTI_RUN_K:-1})..."
        _df_run_scenarios_satisfaction "$SCENARIOS_DIR" "$TOOLCHAIN" "$SPEC_NAME" "${_DF_ITERATION}"
        # Source the satisfaction fragment (not the canonical file, which
        # Python creates after this container exits).
        source ".dark-factory/reports/${SPEC_NAME}-scenario-counts-satisfaction.env"
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

        # When the Python orchestrator is driving (DF_PYTHON_ORCHESTRATED=1),
        # A/B format containers are launched after this container exits.
        # Standalone bash runs handle A/B inline below.
        _DF_AB_DELEGATED=false
        if [[ "${DF_PYTHON_ORCHESTRATED:-0}" == "1" ]]; then
            if [[ "$_DF_HAS_HTTP" == "true" ]] || [[ "$_DF_HAS_JOURNEY" == "true" ]]; then
                echo "  Blind Scenarios (HTTP/Journey): delegated to Python orchestrator"
                _DF_AB_DELEGATED=true
            fi
        fi

        # Start HTTP test server if needed (standalone bash only — Python orchestrator
        # launches A/B servers in dedicated containers).
        if [[ "${DF_PYTHON_ORCHESTRATED:-0}" != "1" ]] && \
           [[ "$_DF_HAS_HTTP" == "true" ]] && [[ -n "${TEST_SERVER_CMD:-}" ]]; then
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
        if [[ "${DF_PYTHON_ORCHESTRATED:-0}" != "1" ]] && \
           [[ "$_DF_HAS_JOURNEY" == "true" ]] && [[ -n "${BROWSER_SERVER_CMD:-}" ]]; then
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

        # Run executable scenarios (pytest / jest / vitest / go / rust)
        _DF_ANY_FORMAT_RAN=false
        if [[ "$_DF_HAS_EXECUTABLE" == "true" ]]; then
            _DF_ANY_FORMAT_RAN=true
            # Temporarily use SCENARIO_TIMEOUT for scenario gates
            _SAVED_GATE_TIMEOUT="$GATE_TIMEOUT"
            GATE_TIMEOUT="$SCENARIO_TIMEOUT"
            if [[ -n "${SCENARIO_TEST_CMD:-}" ]]; then
                # Explicit override — run as-is, substituting SCENARIOS_DIR placeholder.
                SCENARIO_CMD="${SCENARIO_TEST_CMD//\{SCENARIOS_DIR\}/${SCENARIOS_DIR}}"
                run_gate "Blind Scenarios (executable)" "$SCENARIO_CMD"
            else
                detect_executable_languages "$SCENARIOS_DIR"

                # Warn when Python scenario files exist in a node project — pytest is not
                # available in the node container. Set scenario_test_cmd to handle them.
                if [[ "$_DF_HAS_PY_TESTS" == "true" ]] && [[ "$TOOLCHAIN" == "node" ]]; then
                    echo "  WARNING: Python scenario files found in a node toolchain project."
                    echo "           pytest is not available in this container."
                    echo "           These scenarios cannot run here. Options:"
                    echo "           1. Regenerate scenarios as TypeScript (*.test.ts)."
                    echo "           2. Add SCENARIOS-NOT-APPLICABLE.md if this spec has no executable scenarios."
                fi

                case $TOOLCHAIN in
                    node)
                        if [[ "$_DF_HAS_TS_TESTS" == "true" ]]; then
                            # Ensure PROJECT_ROOT is set so TypeScript scenario files can
                            # locate the project root via process.env.PROJECT_ROOT.
                            if [[ -z "${PROJECT_ROOT:-}" ]]; then
                                export PROJECT_ROOT=/project
                            fi
                            # Resolve the available runner once (cached). Uses --version
                            # probe to distinguish "not installed" (skip) from "installed
                            # but tests fail" (real failure), avoiding the || chain that
                            # would swallow genuine test failures.
                            _df_resolve_node_runner
                            _df_resolve_vitest_config "${SPEC_NAME}"
                            if [[ -n "${_DF_WORKSPACE_DIR:-}" ]]; then
                                # Monorepo workspace: run vitest from the workspace dir
                                # so workspace-scoped node_modules are in the resolution path.
                                SCENARIO_CMD=$(_df_build_vitest_cmd dir)
                            else
                                # Non-workspace: standard runner dispatch.
                                case "$_DF_NODE_SCENARIO_RUNNER" in
                                    pnpm_vitest)      SCENARIO_CMD="pnpm exec vitest run ${_DF_VITEST_CONFIG_FLAG} ${SCENARIOS_DIR} 2>&1" ;;
                                    workspace_vitest) SCENARIO_CMD="${_DF_VITEST_BIN} run ${_DF_VITEST_CONFIG_FLAG} ${SCENARIOS_DIR} 2>&1" ;;
                                    npx_vitest)       SCENARIO_CMD="npx vitest run ${_DF_VITEST_CONFIG_FLAG} ${SCENARIOS_DIR} 2>&1" ;;
                                    pnpm_jest)        SCENARIO_CMD="pnpm exec jest ${SCENARIOS_DIR} 2>&1" ;;
                                    npx_jest)         SCENARIO_CMD="npx jest ${SCENARIOS_DIR} 2>&1" ;;
                                    *)
                                        SCENARIO_CMD=""
                                        _LAST_GATE_OUTPUT=""
                                        echo "| Blind Scenarios (executable) | FAIL | No vitest/jest runner found — install vitest as a devDependency or set scenario_test_cmd in config |" >> "$REPORT_FILE"
                                        echo "| Blind Scenarios (executable) | FAIL |" >> "$SUMMARY_FILE"
                                        GATE_FAILED=true
                                        ;;
                                esac
                            fi
                        elif [[ "$_DF_HAS_PY_TESTS" == "true" ]]; then
                            # Only Python files present in a node project — cannot run from
                            # this container. Emit FAIL (not SKIP) so the operator knows
                            # the scenarios are incompatible and must be regenerated. Reset
                            # _LAST_GATE_OUTPUT so count-parsing below doesn't process
                            # stale output from Gate 4.
                            SCENARIO_CMD=""
                            _LAST_GATE_OUTPUT=""
                            echo "| Blind Scenarios (executable) | FAIL | Python scenarios cannot run in node container — regenerate as TypeScript or add SCENARIOS-NOT-APPLICABLE.md |" >> "$REPORT_FILE"
                            echo "| Blind Scenarios (executable) | FAIL |" >> "$SUMMARY_FILE"
                            GATE_FAILED=true
                        else
                            SCENARIO_CMD=""
                        fi
                        ;;
                    python) SCENARIO_CMD="pytest ${SCENARIOS_DIR} -v 2>&1" ;;
                    # L5: Go ignores dotted directories — set SCENARIO_TEST_CMD in config
                    go)     SCENARIO_CMD="" ;;
                    # L6: cargo test --test runs tests/*, not .dark-factory/scenarios/
                    rust)   SCENARIO_CMD="" ;;
                    *)      SCENARIO_CMD="" ;;
                esac

                if [[ -n "$SCENARIO_CMD" ]]; then
                    run_gate "Blind Scenarios (executable)" "$SCENARIO_CMD"
                fi
            fi
            # Clean up workspace wrapper config eagerly on the normal path.
            # Also covered by the EXIT trap (_df_cleanup_wrapper) for any
            # signal/failure path between wrapper generation and this point.
            _df_cleanup_wrapper
            GATE_TIMEOUT="$_SAVED_GATE_TIMEOUT"
            # Write per-test results JSON (must run before _parse_scenario_counts so
            # crash classification can use per-test data instead of grep -c).
            _write_per_test_results "$_LAST_GATE_OUTPUT" "${_DF_ITERATION}"
            # Write counts to a format-specific file so accumulation picks it up correctly
            # when mixed with HTTP/Journey formats.
            _parse_scenario_counts "$_LAST_GATE_OUTPUT"
            # _parse_scenario_counts always writes directly to the -executable.env
            # fragment — no copy needed.
            _df_parse_per_layer_output "$_LAST_GATE_OUTPUT" "$SPEC_NAME" "${_DF_ITERATION}"
            # Phase 4.6b.1d Finding 1 fail-loud invariant: the failing-test
            # count reported by the parser must match the failing-test count
            # in the per-test JSON file.  Mismatch = the two parsers drifted
            # (exactly what Run 9 surfaced: parser said 1, JSON had 6).
            _assert_scenario_count_invariant "${_DF_ITERATION}"
        fi

        # Format A (HTTP behavioral scenarios) retired in Phase 4.6b.1a.

        # Run browser journey scenarios (Format B) — standalone bash only.
        # When Python orchestrates, this runs in a dedicated container.
        if [[ "${DF_PYTHON_ORCHESTRATED:-0}" != "1" ]] && \
           [[ "$_DF_HAS_JOURNEY" == "true" ]] && [[ "$_DF_BROWSER_SERVER_OK" == "true" ]]; then
            _DF_ANY_FORMAT_RAN=true
            echo "  Running gate: Blind Scenarios (Journey)..."
            _run_format_runner "${SCRIPT_DIR}/lib/journey-runner.py" \
                "$SCENARIOS_DIR" \
                --base-url "${BROWSER_BASE_URL:-}" \
                --reports-dir ".dark-factory/reports" \
                --spec-name "$SPEC_NAME" \
                --iteration "$_DF_ITERATION"
            if [[ $_FORMAT_RUNNER_EXIT_CODE -ne 0 ]]; then
                echo "| Blind Scenarios (Journey) | FAIL | journey-runner exited ${_FORMAT_RUNNER_EXIT_CODE} |" >> "$REPORT_FILE"
                echo "| Blind Scenarios (Journey) | FAIL |" >> "$SUMMARY_FILE"
                GATE_FAILED=true
            else
                echo "| Blind Scenarios (Journey) | PASS | Exit code 0 |" >> "$REPORT_FILE"
                echo "| Blind Scenarios (Journey) | PASS |" >> "$SUMMARY_FILE"
            fi
        fi

        # Accumulate format-specific fragments into the canonical counts file.
        # Python orchestrator accumulates fragments after container exits.
        # Standalone bash runs (skills workflows) accumulate here.
        if [[ "$_DF_ANY_FORMAT_RAN" == "true" ]] && [[ "${DF_PYTHON_ORCHESTRATED:-0}" != "1" ]]; then
            _df_accumulate_format_counts
        fi

        # Emit a skip row only when no formats ran AND none were delegated to Python.
        if [[ "$_DF_ANY_FORMAT_RAN" != "true" ]] && [[ "$_DF_AB_DELEGATED" != "true" ]]; then
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

# Flush accumulated infrastructure error details into the summary file.
# The sentinel separates the gate table (parsed by _parse_gate_outcomes) from
# freeform error output that could contain false-positive gate name matches.
echo "<!-- END GATE TABLE -->" >> "$SUMMARY_FILE"
if [[ -s "$SUMMARY_DETAILS_FILE" ]]; then
    cat "$SUMMARY_DETAILS_FILE" >> "$SUMMARY_FILE"
fi
rm -f "$SUMMARY_DETAILS_FILE"

# Defensive: ensure summary file never contains raw test output or
# barrier-protected paths (guards against future regressions).
if grep -qE 'AssertionError|Expected:.*Received:|at Object\.<anonymous>|\.dark-factory/scenarios/|\.dark-factory/holdouts/' "$SUMMARY_FILE" 2>/dev/null; then
    echo "WARNING: Summary file contains barrier-sensitive content — stripping." >&2
    # Strip offending lines from the error detail blocks while preserving
    # the table structure and sentinel.
    grep -vF '.dark-factory/scenarios/' "$SUMMARY_FILE" \
        | grep -vF '.dark-factory/holdouts/' \
        | grep -vE 'AssertionError|Expected:.*Received:|at Object\.<anonymous>' \
        > "${SUMMARY_FILE}.clean" 2>/dev/null || true
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
