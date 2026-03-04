#!/usr/bin/env bash
set -euo pipefail

# tests/test-docker-args.sh — Tests for scripts/lib/docker.sh arg building (no actual Docker)

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"

# Source dependencies
source "${TESTS_DIR}/../scripts/lib/config.sh"
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
RUN_ID="test-run"
AGENT_RETRIES=1

# Parse SOURCE_DIRS and configure barriers
_df_parse_source_dirs
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
# Item 24: I/O limits
# ==============================================================================

test_begin "I/O limits present when configured"

AGENT_IO_DEVICE="/dev/sda"
AGENT_IO_READ_BPS="100mb"
AGENT_IO_WRITE_BPS="50mb"
_build_docker_base_args "io-test" "ro"
args=$(_args_str)
assert_contains "$args" "--device-read-bps /dev/sda:100mb" "has device-read-bps"
assert_contains "$args" "--device-write-bps /dev/sda:50mb" "has device-write-bps"

test_begin "I/O limits absent when not configured"

AGENT_IO_DEVICE=""
AGENT_IO_READ_BPS=""
AGENT_IO_WRITE_BPS=""
_build_docker_base_args "no-io-test" "ro"
args=$(_args_str)
assert_not_contains "$args" "--device-read-bps" "no device-read-bps without config"
assert_not_contains "$args" "--device-write-bps" "no device-write-bps without config"

# ==============================================================================
# run_agent — role-specific args (via DRY_RUN)
# ==============================================================================

test_begin "implementer role"

# Create required directories so symlink check doesn't fail
mkdir -p "$TMPDIR_TEST/project/.dark-factory/scenarios"
mkdir -p "$TMPDIR_TEST/project/.dark-factory/reports"
mkdir -p "$TMPDIR_TEST/project/.dark-factory/reviews"
mkdir -p "$TMPDIR_TEST/project/.dark-factory/audit"
mkdir -p "$TMPDIR_TEST/project/.dark-factory/state"
cd "$TMPDIR_TEST/project"

run_agent "implementer" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)

assert_contains "$args" "--tmpfs /workspace/.dark-factory/scenarios:ro,size=1k" "implementer masks scenarios"
assert_contains "$args" "--tmpfs /workspace/.dark-factory/reports:ro,size=1k" "implementer masks reports"
assert_contains "$args" "--tmpfs /workspace/.dark-factory/audit:ro,size=1k" "implementer masks audit"
assert_contains "$args" "--tmpfs /workspace/.dark-factory/state:ro,size=1k" "implementer masks state"
assert_contains "$args" "--tmpfs /workspace/.git:ro,size=1k" "implementer masks .git"
assert_contains "$args" "--network df-test-run-execution" "implementer on execution network"
assert_contains "$args" "disallowedTools" "implementer has disallowedTools"
assert_contains "$args" "append-system-prompt" "implementer has barrier prompt"

test_begin "scenario-architect role"

mkdir -p "$TMPDIR_TEST/project/src"
mkdir -p "$TMPDIR_TEST/project/.dark-factory/scenarios/test-spec"
run_agent "scenario-architect" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)

assert_contains "$args" "--tmpfs /workspace/src:ro,size=1k" "scenario-arch masks source dir"
assert_contains "$args" "--tmpfs /workspace/.git:ro,size=1k" "scenario-arch masks .git"
assert_contains "$args" "--network df-test-run-analysis" "scenario-arch on analysis network"
assert_contains "$args" "disallowedTools" "scenario-arch has disallowedTools"

# ==============================================================================
# Item 22: colon-delimited multi-dir masks
# ==============================================================================

test_begin "scenario-architect multi-dir masks (colon format)"

SOURCE_DIRS="src:lib:pkg"
_df_parse_source_dirs
configure_barriers
mkdir -p "$TMPDIR_TEST/project/src" "$TMPDIR_TEST/project/lib" "$TMPDIR_TEST/project/pkg"
run_agent "scenario-architect" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)

assert_contains "$args" "--tmpfs /workspace/src:ro,size=1k" "multi: masks src"
assert_contains "$args" "--tmpfs /workspace/lib:ro,size=1k" "multi: masks lib"
assert_contains "$args" "--tmpfs /workspace/pkg:ro,size=1k" "multi: masks pkg"

# Reset to single dir for remaining tests
SOURCE_DIRS="src"
_df_parse_source_dirs
configure_barriers

test_begin "reviewer role"

run_agent "reviewer" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)

assert_contains "$args" "--tmpfs /workspace/.dark-factory/scenarios:ro,size=1k" "reviewer masks scenarios"
assert_contains "$args" "--tmpfs /workspace/.dark-factory/reports:ro,size=1k" "reviewer masks reports"
assert_contains "$args" "--tmpfs /workspace/.git:ro,size=1k" "reviewer masks .git"
assert_contains "$args" "--network df-test-run-analysis" "reviewer on analysis network"

test_begin "oracle role"

run_agent "oracle" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)

assert_contains "$args" "--network df-test-run-analysis" "oracle on analysis network"
assert_contains "$args" "disallowedTools" "oracle has disallowedTools"
assert_not_contains "$args" "tmpfs /workspace/.dark-factory" "oracle has no scenario mask"

test_begin "spec-validator role"

run_agent "spec-validator" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)

assert_contains "$args" "--network df-test-run-analysis" "spec-validator on analysis network"
assert_contains "$args" "disallowedTools" "spec-validator has disallowedTools"

test_begin "evaluator role"

DEPLOY_CMD="echo deploy"
DEPLOY_BASE_URL="https://app.example.com:8443"
run_agent "evaluator" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)

assert_contains "$args" "--network df-test-run-evaluation" "evaluator on evaluation network"
assert_contains "$args" "HTTPS_PROXY=http://df-test-run-evaluation-proxy:8080" "evaluator has HTTPS_PROXY"
assert_contains "$args" "HTTP_PROXY=http://df-test-run-evaluation-proxy:8080" "evaluator has HTTP_PROXY"
assert_contains "$args" "--tmpfs /workspace/src:ro,size=1k" "evaluator masks source dir"
assert_contains "$args" "--tmpfs /workspace/.git:ro,size=1k" "evaluator masks .git"
assert_not_contains "$args" "host.docker.internal" "evaluator no host.docker.internal for remote host"

test_begin "evaluator localhost mapping"

DEPLOY_BASE_URL="http://localhost:3000"
run_agent "evaluator" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)

assert_contains "$args" "--add-host=host.docker.internal:host-gateway" "evaluator adds host.docker.internal for localhost"

# Reset deploy vars
DEPLOY_CMD=""
DEPLOY_BASE_URL=""

# ==============================================================================
# run_quality_gate
# ==============================================================================

test_begin "quality gate"

run_quality_gate "test-spec" >/dev/null 2>&1 || true
args=$(_args_str)

assert_contains "$args" "--entrypoint" "quality-gate uses entrypoint override"
assert_contains "$args" "bash" "quality-gate entrypoint is bash"
assert_contains "$args" "--network df-test-run-execution" "quality-gate on execution network"
assert_contains "$args" "/workspace:rw" "quality-gate has rw project mount"

test_begin "quality gate TEST_CMD with spaces"

TEST_CMD="npm test -- --coverage"
run_quality_gate "test-spec" >/dev/null 2>&1 || true
# Check that --test-cmd and its value are separate, properly quoted args
found_test_cmd=false
next_is_value=false
for arg in "${_DOCKER_ARGS[@]}"; do
    if [[ "$next_is_value" == "true" ]]; then
        assert_equals "npm test -- --coverage" "$arg" "TEST_CMD value preserved as single arg"
        found_test_cmd=true
        break
    fi
    [[ "$arg" == "--test-cmd" ]] && next_is_value=true
done
assert_equals "true" "$found_test_cmd" "found --test-cmd flag"

test_begin "quality gate empty TEST_CMD"

TEST_CMD=""
run_quality_gate "test-spec" >/dev/null 2>&1 || true
args=$(_args_str)
assert_not_contains "$args" "--test-cmd" "no --test-cmd when TEST_CMD empty"

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

# ==============================================================================
# invoke_agent — retry behavior
# ==============================================================================

test_begin "invoke_agent retry on failure"

# Use a file to track call count across subshells (invoke_agent uses $(...))
_retry_file="$TMPDIR_TEST/retry_count"
echo "0" > "$_retry_file"
run_agent() {
    local count
    count=$(cat "$_retry_file")
    count=$((count + 1))
    echo "$count" > "$_retry_file"
    if [[ $count -le 1 ]]; then
        echo "short failure output"
        return 1
    fi
    printf 'x%.0s' {1..100}
    return 0
}
AGENT_RETRIES=1
invoke_agent "test-role" "test prompt" 50
assert_equals "0" "$AGENT_EXIT" "succeeds after retry"
_final_count=$(cat "$_retry_file")
assert_equals "2" "$_final_count" "ran agent twice (1 failure + 1 retry)"

test_begin "invoke_agent no retry when AGENT_RETRIES=0"

echo "0" > "$_retry_file"
run_agent() {
    local count
    count=$(cat "$_retry_file")
    count=$((count + 1))
    echo "$count" > "$_retry_file"
    echo "fail output that is long enough to pass threshold"
    return 1
}
AGENT_RETRIES=0
invoke_agent "test-role" "test prompt" 5
assert_equals "1" "$AGENT_EXIT" "fails without retry"
_final_count=$(cat "$_retry_file")
assert_equals "1" "$_final_count" "ran agent only once"

# Reset
AGENT_RETRIES=1

# ==============================================================================
# Namespaced NO_PROXY
# ==============================================================================

test_begin "NO_PROXY uses namespaced proxy names"

# Restore real run_agent
source "${TESTS_DIR}/../scripts/lib/docker.sh"
unset -f run_agent  # remove test override to use real run_agent
# Need to re-source to get the real function back
_DF_LIB_DOCKER_LOADED=""
source "${TESTS_DIR}/../scripts/lib/docker.sh"

run_agent "implementer" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)
assert_contains "$args" "NO_PROXY=localhost,127.0.0.1,df-test-run-analysis-proxy,df-test-run-execution-proxy,df-test-run-evaluation-proxy" "NO_PROXY has namespaced proxy names"

# ==============================================================================
# Implementer write scope (IMPLEMENTER_WRITE_DIRS)
# ==============================================================================

test_begin "implementer default write scope (empty IMPLEMENTER_WRITE_DIRS)"

# Re-source docker.sh to get real run_agent
_DF_LIB_DOCKER_LOADED=""
source "${TESTS_DIR}/../scripts/lib/docker.sh"

cd "$TMPDIR_TEST/project"
IMPLEMENTER_WRITE_DIRS=""
run_agent "implementer" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)
assert_contains "$args" "/workspace:rw" "default: full project write access"

test_begin "implementer explicit full write (IMPLEMENTER_WRITE_DIRS='.')"

IMPLEMENTER_WRITE_DIRS="."
run_agent "implementer" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)
assert_contains "$args" "/workspace:rw" "explicit dot: full project write access"

test_begin "implementer scoped write (IMPLEMENTER_WRITE_DIRS='src:tests')"

IMPLEMENTER_WRITE_DIRS="src:tests"
mkdir -p "$TMPDIR_TEST/project/src" "$TMPDIR_TEST/project/tests"
run_agent "implementer" "test prompt" >/dev/null 2>&1 || true
args=$(_args_str)
assert_contains "$args" "/workspace:ro" "scoped: base mount is read-only"
assert_contains "$args" "/workspace/src:rw" "scoped: src gets rw override"
assert_contains "$args" "/workspace/tests:rw" "scoped: tests gets rw override"
assert_contains "$args" "/workspace/.dark-factory/reviews:rw" "scoped: reviews gets rw override"

# Reset
IMPLEMENTER_WRITE_DIRS=""

cd "$TESTS_DIR/.."
cleanup_tmpdir
test_summary
