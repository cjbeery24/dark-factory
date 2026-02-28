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

# Source per-project config if it exists
CONFIG_FILE=".dark-factory/config.sh"
if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$CONFIG_FILE"
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

# Priority: CLI --test-cmd flag > config SCENARIO_TEST_CMD > auto-detect
if [[ -n "$CLI_TEST_CMD" ]]; then
    echo "Using custom test command: ${CLI_TEST_CMD}"
    eval "$CLI_TEST_CMD"
    exit $?
fi

if [[ -n "${SCENARIO_TEST_CMD:-}" ]]; then
    RESOLVED_CMD="${SCENARIO_TEST_CMD//\{SCENARIOS_DIR\}/${SCENARIOS_DIR}}"
    echo "Using config SCENARIO_TEST_CMD: ${RESOLVED_CMD}"
    eval "$RESOLVED_CMD"
    exit $?
fi

# Auto-detect test framework
if [[ -f "package.json" ]]; then
    # Try vitest first, then jest
    if npx vitest --version &>/dev/null; then
        echo "Using: vitest"
        npx vitest run "$SCENARIOS_DIR" --reporter=verbose
    elif npx jest --version &>/dev/null; then
        echo "Using: jest"
        npx jest "$SCENARIOS_DIR" --verbose
    else
        echo "ERROR: No test runner found. Install vitest or jest, or use --test-cmd."
        exit 1
    fi
elif [[ -f "pyproject.toml" ]] || [[ -f "setup.py" ]] || [[ -f "requirements.txt" ]]; then
    echo "Using: pytest"
    pytest "$SCENARIOS_DIR" -v
elif [[ -f "go.mod" ]]; then
    echo "Using: go test"
    go test "./$SCENARIOS_DIR/..." -v
elif [[ -f "Cargo.toml" ]]; then
    echo "Using: cargo test"
    cargo test --test '*' -- --nocapture
else
    echo "ERROR: Could not detect test framework. Use --test-cmd to specify."
    exit 1
fi
