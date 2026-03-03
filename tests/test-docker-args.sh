#!/usr/bin/env bash
set -euo pipefail

# tests/test-docker-args.sh — Tests for scripts/lib/docker.sh arg building (no actual Docker)

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"

# Source dependencies
source "${TESTS_DIR}/../scripts/lib/audit.sh"
source "${TESTS_DIR}/../scripts/lib/barriers.sh"
source "${TESTS_DIR}/../scripts/lib/docker.sh"

setup_tmpdir

# Set up required globals for testing
AUDIT_LOG="$TMPDIR_TEST/audit.log"
touch "$AUDIT_LOG"
DRY_RUN=true
LANGUAGE="node"
SPEC_NAME="test-spec"
DEBUG_MODE=false
TIMEOUT_CMD=""
AGENT_MEMORY="4g"
AGENT_CPUS="4"
AGENT_MAX_TURNS=200
AGENT_TIMEOUT=600
GATE_TIMEOUT=300
DOCKER_NETWORK="df-pipeline"
EGRESS_FILTERING_ENABLED=true
SOURCE_DIRS="src"
SOURCE_DIR="src"
DARK_FACTORY_HOME="dark-factory"
AGENTS_DIR="agents"
DF_AUTH_TYPE="api_key"
TEST_CMD=""

# Configure barriers
configure_barriers

# Helper: join array into a single space-separated string for searching
_args_str() {
    echo "${_DOCKER_ARGS[*]}"
}

# ==============================================================================
# _build_docker_base_args
# ==============================================================================

test_begin "base args"

_build_docker_base_args "test-container" "ro"
args=$(_args_str)

assert_contains "$args" "--cap-drop=ALL" "has cap-drop"
assert_contains "$args" "--security-opt" "has security-opt"
assert_contains "$args" "--pids-limit=512" "has pids-limit"
assert_contains "$args" "--memory=4g" "has memory limit"
assert_contains "$args" "--cpus=4" "has cpu limit"
assert_contains "$args" "--rm" "has --rm when debug off"
assert_contains "$args" "/workspace:ro" "mounts workspace read-only"

test_begin "base args debug mode"

DEBUG_MODE=true
_build_docker_base_args "debug-container" "rw"
args=$(_args_str)
assert_not_contains "$args" "--rm" "no --rm in debug mode"
assert_contains "$args" "/workspace:rw" "mounts workspace read-write"
DEBUG_MODE=false

# ==============================================================================
# run_agent — role-specific args (via DRY_RUN)
# ==============================================================================

test_begin "implementer role"

# Create required directories so symlink check doesn't fail
mkdir -p "$TMPDIR_TEST/project/.dark-factory/scenarios"
mkdir -p "$TMPDIR_TEST/project/.dark-factory/reports"
mkdir -p "$TMPDIR_TEST/project/.dark-factory/reviews"
cd "$TMPDIR_TEST/project"

run_agent "implementer" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)

assert_contains "$args" "--tmpfs /workspace/.dark-factory/scenarios:ro,size=1k" "implementer masks scenarios"
assert_contains "$args" "--tmpfs /workspace/.dark-factory/reports:ro,size=1k" "implementer masks reports"
assert_contains "$args" "--tmpfs /workspace/.git:ro,size=1k" "implementer masks .git"
assert_contains "$args" "--network df-execution" "implementer on execution network"
assert_contains "$args" "disallowedTools" "implementer has disallowedTools"
assert_contains "$args" "append-system-prompt" "implementer has barrier prompt"

test_begin "scenario-architect role"

mkdir -p "$TMPDIR_TEST/project/src"
mkdir -p "$TMPDIR_TEST/project/.dark-factory/scenarios/test-spec"
run_agent "scenario-architect" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)

assert_contains "$args" "--tmpfs /workspace/src:ro,size=1k" "scenario-arch masks source dir"
assert_contains "$args" "--tmpfs /workspace/.git:ro,size=1k" "scenario-arch masks .git"
assert_contains "$args" "--network df-analysis" "scenario-arch on analysis network"
assert_contains "$args" "disallowedTools" "scenario-arch has disallowedTools"

test_begin "reviewer role"

run_agent "reviewer" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)

assert_contains "$args" "--tmpfs /workspace/.dark-factory/scenarios:ro,size=1k" "reviewer masks scenarios"
assert_contains "$args" "--tmpfs /workspace/.git:ro,size=1k" "reviewer masks .git"
assert_contains "$args" "--network df-analysis" "reviewer on analysis network"

test_begin "oracle role"

run_agent "oracle" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)

assert_contains "$args" "--network df-analysis" "oracle on analysis network"
assert_contains "$args" "disallowedTools" "oracle has disallowedTools"
assert_not_contains "$args" "tmpfs /workspace/.dark-factory" "oracle has no scenario mask"

test_begin "spec-validator role"

run_agent "spec-validator" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)

assert_contains "$args" "--network df-analysis" "spec-validator on analysis network"
assert_contains "$args" "disallowedTools" "spec-validator has disallowedTools"

# ==============================================================================
# run_quality_gate
# ==============================================================================

test_begin "quality gate"

run_quality_gate "test-spec" >/dev/null 2>&1 || true
args=$(_args_str)

assert_contains "$args" "--entrypoint" "quality-gate uses entrypoint override"
assert_contains "$args" "bash" "quality-gate entrypoint is bash"
assert_contains "$args" "--network df-execution" "quality-gate on execution network"
assert_contains "$args" "/workspace:rw" "quality-gate has rw project mount"

# ==============================================================================
# invoke_agent — low output detection
# ==============================================================================

test_begin "invoke_agent low output"

# Override run_agent to return short output
run_agent() { echo "OK"; return 0; }
invoke_agent "test-role" "test prompt" 50
assert_equals "99" "$AGENT_EXIT" "sets exit 99 for low output"

# Override run_agent to return sufficient output
run_agent() { printf 'x%.0s' {1..100}; return 0; }
invoke_agent "test-role" "test prompt" 50
assert_equals "0" "$AGENT_EXIT" "exit 0 for sufficient output"

cd "$TESTS_DIR/.."
cleanup_tmpdir
test_summary
