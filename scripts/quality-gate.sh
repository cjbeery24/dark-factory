#!/usr/bin/env bash
set -euo pipefail

# Quality Gate Runner
# Usage: ./scripts/quality-gate.sh <spec-name> [--test-cmd "command"]

SPEC_NAME="${1:?Usage: quality-gate.sh <spec-name> [--test-cmd \"command\"]}"
CLI_TEST_CMD=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

shift || true
while [[ $# -gt 0 ]]; do
    case $1 in
        --test-cmd)
            CLI_TEST_CMD="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# --- P0-2: Input validation ---
if [[ ! "$SPEC_NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]]; then
    echo "ERROR: Invalid spec name: '$SPEC_NAME'"
    echo "Spec names must start with alphanumeric and contain only alphanumerics, dots, hyphens, underscores."
    exit 1
fi

# --- P0-2: Config validation ---
validate_config() {
    local config="$1"
    [[ ! -f "$config" ]] && return 0
    if grep -vE '^\s*#|^\s*$|^\s*(export\s+)?[a-zA-Z_][a-zA-Z0-9_]*=' "$config" | grep -qE '.'; then
        echo "ERROR: Config contains non-assignment lines. Only variable assignments allowed."
        echo "Review: $config"
        exit 1
    fi
}

# Source per-project config if it exists (config values used as defaults below)
CONFIG_FILE=".dark-factory/config.sh"
if [[ -f "$CONFIG_FILE" ]]; then
    validate_config "$CONFIG_FILE"
    # shellcheck source=/dev/null
    source "$CONFIG_FILE"

    # Post-source: validate plain-value variables don't contain shell metacharacters
    for var in LANGUAGE SOURCE_DIR TEST_DIR DARK_FACTORY_HOME; do
        val="${!var:-}"
        if [[ "$val" =~ [\$\`\(] ]]; then
            echo "ERROR: Config variable $var contains shell metacharacters: $val"
            exit 1
        fi
    done
fi

# --- P1-4: Timeout detection ---
if command -v timeout &>/dev/null; then
    TIMEOUT_CMD="timeout"
elif command -v gtimeout &>/dev/null; then
    TIMEOUT_CMD="gtimeout"
else
    TIMEOUT_CMD=""
    echo "WARN: timeout command not found. No execution time limits." >&2
fi

# --- Scenario file detection (M7 fix) ---
has_scenario_files() {
    local dir="$1"
    [[ -n "$(find "$dir" -type f \( \
        -name '*.test.*' -o -name '*.spec.*' -o \
        -name 'test_*.py' -o -name '*_test.py' -o \
        -name '*_test.go' -o -name '*.rs' \
    \) -print -quit 2>/dev/null)" ]]
}

SCENARIOS_DIR=".dark-factory/scenarios/${SPEC_NAME}"
REPORT_FILE=".dark-factory/reports/${SPEC_NAME}-quality-gate.md"

# Detect project type and set default commands
detect_toolchain() {
    if [[ -f "package.json" ]]; then
        echo "node"
    elif [[ -f "pyproject.toml" ]] || [[ -f "setup.py" ]] || [[ -f "requirements.txt" ]]; then
        echo "python"
    elif [[ -f "go.mod" ]]; then
        echo "go"
    elif [[ -f "Cargo.toml" ]]; then
        echo "rust"
    else
        echo "unknown"
    fi
}

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

GATE_FAILED=false
run_gate() {
    local gate_name="$1"
    local gate_cmd="$2"

    echo "  Running gate: ${gate_name}..."

    if [[ -z "$gate_cmd" ]]; then
        echo "| ${gate_name} | SKIP | No command configured |" >> "$REPORT_FILE"
        echo "    SKIP (no command configured)"
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

    # Handle timeout (exit 124)
    if [[ $exit_code -eq 124 ]]; then
        echo "| ${gate_name} | TIMEOUT | Exceeded ${GATE_TIMEOUT}s limit |" >> "$REPORT_FILE"
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
        echo "    PASS"
    else
        echo "| ${gate_name} | FAIL | Exit code ${exit_code} |" >> "$REPORT_FILE"
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

echo "Running quality gates for: ${SPEC_NAME}"
echo "Toolchain detected: ${TOOLCHAIN}"
echo ""

# Gate 1: Build
# Use config BUILD_CMD if set, otherwise auto-detect
if [[ -z "${BUILD_CMD:-}" ]]; then
    case $TOOLCHAIN in
        node)   BUILD_CMD="npm run build 2>&1 || npx tsc --noEmit 2>&1" ;;
        python) BUILD_CMD="python -m py_compile \$(find src -name '*.py' 2>/dev/null | head -20) 2>&1" ;;
        go)     BUILD_CMD="go build ./... 2>&1" ;;
        rust)   BUILD_CMD="cargo build 2>&1" ;;
        *)      BUILD_CMD="" ;;
    esac
fi
run_gate "Build" "$BUILD_CMD"

# Gate 2: Lint
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
    # Use config SCENARIO_TEST_CMD if set, otherwise auto-detect
    if [[ -n "${SCENARIO_TEST_CMD:-}" ]]; then
        SCENARIO_CMD="${SCENARIO_TEST_CMD//\{SCENARIOS_DIR\}/${SCENARIOS_DIR}}"
    else
        case $TOOLCHAIN in
            node)   SCENARIO_CMD="npx vitest run ${SCENARIOS_DIR} 2>&1 || npx jest ${SCENARIOS_DIR} 2>&1" ;;
            python) SCENARIO_CMD="pytest ${SCENARIOS_DIR} -v 2>&1" ;;
            go)     SCENARIO_CMD="go test ./${SCENARIOS_DIR}/... -v 2>&1" ;;
            rust)   SCENARIO_CMD="cargo test --test '*' 2>&1" ;;
            *)      SCENARIO_CMD="" ;;
        esac
    fi
    run_gate "Blind Scenarios" "$SCENARIO_CMD"
else
    echo "| Blind Scenarios | SKIP | No scenarios found in ${SCENARIOS_DIR} |" >> "$REPORT_FILE"
    echo "  Blind Scenarios: SKIP (no scenarios found)"
fi

# Gate 6: Mutation Testing (opt-in)
if [[ -n "${MUTATION_CMD:-}" ]]; then
    run_gate "Mutation Testing" "$MUTATION_CMD"
else
    echo "| Mutation Testing | SKIP | MUTATION_CMD not configured |" >> "$REPORT_FILE"
    echo "  Mutation Testing: SKIP (MUTATION_CMD not configured)"
fi

echo "" >> "$REPORT_FILE"

# Summary
if [[ "$GATE_FAILED" == "true" ]]; then
    echo "## Result: FAIL" >> "$REPORT_FILE"
    echo ""
    echo "QUALITY GATE: FAIL"
    echo "See report: ${REPORT_FILE}"
    exit 1
else
    echo "## Result: PASS" >> "$REPORT_FILE"
    echo ""
    echo "QUALITY GATE: PASS"
    echo "See report: ${REPORT_FILE}"
fi
