#!/usr/bin/env bash
set -euo pipefail

# Run blind test scenarios against the implementation
# Usage: ./scripts/run-scenarios.sh <spec-name> [--test-cmd "command"]

SPEC_NAME="${1:?Usage: run-scenarios.sh <spec-name> [--test-cmd \"command\"]}"
CLI_TEST_CMD=""

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

# Source per-project config if it exists
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

SCENARIOS_DIR=".dark-factory/scenarios/${SPEC_NAME}"

if [[ ! -d "$SCENARIOS_DIR" ]]; then
    echo "ERROR: Scenarios directory not found: $SCENARIOS_DIR"
    echo "Generate scenarios first using the Scenario Architect agent."
    exit 1
fi

if [[ -z "$(ls -A "$SCENARIOS_DIR" 2>/dev/null)" ]]; then
    echo "ERROR: Scenarios directory is empty: $SCENARIOS_DIR"
    exit 1
fi

echo "Running blind scenarios for: ${SPEC_NAME}"
echo "Scenarios directory: ${SCENARIOS_DIR}"
echo ""

# Helper: run a command with optional timeout + subprocess isolation
run_with_timeout() {
    local cmd="$1"
    if [[ -n "$TIMEOUT_CMD" ]] && [[ "${SCENARIO_TIMEOUT:-0}" -gt 0 ]]; then
        $TIMEOUT_CMD "${SCENARIO_TIMEOUT}s" bash -c "$cmd"
    else
        bash -c "$cmd"
    fi
}

# Priority: CLI --test-cmd flag > config SCENARIO_TEST_CMD > auto-detect
if [[ -n "$CLI_TEST_CMD" ]]; then
    echo "Using custom test command: ${CLI_TEST_CMD}"
    run_with_timeout "$CLI_TEST_CMD"
    exit $?
fi

if [[ -n "${SCENARIO_TEST_CMD:-}" ]]; then
    RESOLVED_CMD="${SCENARIO_TEST_CMD//\{SCENARIOS_DIR\}/${SCENARIOS_DIR}}"
    echo "Using config SCENARIO_TEST_CMD: ${RESOLVED_CMD}"
    run_with_timeout "$RESOLVED_CMD"
    exit $?
fi

# Auto-detect test framework
if [[ -f "package.json" ]]; then
    # Try vitest first, then jest
    if npx vitest --version &>/dev/null; then
        echo "Using: vitest"
        run_with_timeout "npx vitest run '$SCENARIOS_DIR' --reporter=verbose"
    elif npx jest --version &>/dev/null; then
        echo "Using: jest"
        run_with_timeout "npx jest '$SCENARIOS_DIR' --verbose"
    else
        echo "ERROR: No test runner found. Install vitest or jest, or use --test-cmd."
        exit 1
    fi
elif [[ -f "pyproject.toml" ]] || [[ -f "setup.py" ]] || [[ -f "requirements.txt" ]]; then
    echo "Using: pytest"
    run_with_timeout "pytest '$SCENARIOS_DIR' -v"
elif [[ -f "go.mod" ]]; then
    echo "Using: go test"
    run_with_timeout "go test './$SCENARIOS_DIR/...' -v"
elif [[ -f "Cargo.toml" ]]; then
    echo "Using: cargo test"
    run_with_timeout "cargo test --test '*' -- --nocapture"
else
    echo "ERROR: Could not detect test framework. Use --test-cmd to specify."
    exit 1
fi
