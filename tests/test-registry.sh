#!/usr/bin/env bash
set -euo pipefail

# tests/test-registry.sh — Tests for scripts/lib/registry.sh (Phase 4)

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"

setup_tmpdir

# ==============================================================================
# Setup: Source common.sh, override paths to temp directory
# ==============================================================================

_test_root="${TMPDIR_TEST}/project"
mkdir -p "$_test_root/.dark-factory/scenarios"
mkdir -p "$_test_root/.dark-factory/specs"
mkdir -p "$_test_root/.dark-factory/state"
mkdir -p "$_test_root/.dark-factory/reports"

source "${TESTS_DIR}/../scripts/lib/common.sh"
DF_PROJECT_ROOT="$_test_root"
export DF_PROJECT_ROOT
_DF_REGISTRY_DIR="${DF_PROJECT_ROOT}/.dark-factory/shipped-scenarios"
_DF_SCRIPTS_DIR="${TMPDIR_TEST}/mock-scripts"

# Create mock run-scenarios.sh
mkdir -p "$_DF_SCRIPTS_DIR"
cat > "${_DF_SCRIPTS_DIR}/run-scenarios.sh" <<'MOCK'
#!/usr/bin/env bash
# Mock run-scenarios.sh: fails if .fail marker exists for the spec
spec="$1"
fail_marker="${DF_PROJECT_ROOT}/.dark-factory/scenarios/${spec}/.fail"
if [[ -f "$fail_marker" ]]; then
    echo "FAIL: ${spec}" >&2
    exit 1
fi
echo "PASS: ${spec}"
exit 0
MOCK
chmod +x "${_DF_SCRIPTS_DIR}/run-scenarios.sh"

# Helper: create dummy scenario files for a spec
_create_scenarios() {
    local spec="$1"
    local dir="${_test_root}/.dark-factory/scenarios/${spec}"
    mkdir -p "$dir"
    echo "test('basic', () => {})" > "${dir}/test_basic.test.js"
    echo "test('edge', () => {})" > "${dir}/test_edge.test.js"
}

# Helper: create a spec file (for source_hash)
_create_spec_file() {
    local spec="$1"
    printf -- '---\nspec-name: %s\n---\n# %s\n' "$spec" "$spec" > "${_test_root}/.dark-factory/specs/${spec}.md"
}

# ==============================================================================
# registry_register
# ==============================================================================

test_begin "registry_register: copies scenarios and creates metadata"

_create_scenarios "auth-login"
_create_spec_file "auth-login"

rc=0
output=$(registry_register "auth-login" 2>&1) || rc=$?
assert_equals "0" "$rc" "register returns 0"

if [[ -d "${_DF_REGISTRY_DIR}/auth-login" ]]; then
    _test_pass "registry directory created"
else
    _test_fail "registry directory not created"
fi

assert_file_exists "${_DF_REGISTRY_DIR}/auth-login/test_basic.test.js" "scenario file copied"
assert_file_exists "${_DF_REGISTRY_DIR}/auth-login/.registry-meta" "metadata file created"

# Check metadata contents
meta=$(cat "${_DF_REGISTRY_DIR}/auth-login/.registry-meta")
assert_contains "$meta" "spec=auth-login" "metadata has spec name"
assert_contains "$meta" "registered=" "metadata has timestamp"
assert_contains "$meta" "scenario_count=" "metadata has count"
assert_contains "$meta" "source_hash=" "metadata has hash"

test_begin "registry_register: returns 1 for missing source dir"

rc=0
registry_register "nonexistent-spec" 2>/dev/null || rc=$?
assert_equals "1" "$rc" "returns 1 for missing source"

test_begin "registry_register: returns 1 for empty source dir"

mkdir -p "${_test_root}/.dark-factory/scenarios/empty-spec"
rc=0
registry_register "empty-spec" 2>/dev/null || rc=$?
assert_equals "1" "$rc" "returns 1 for empty source"

test_begin "registry_register: idempotent re-register"

# Modify a scenario file, re-register, verify update
echo "test('updated', () => {})" > "${_test_root}/.dark-factory/scenarios/auth-login/test_basic.test.js"
rc=0
registry_register "auth-login" 2>/dev/null || rc=$?
assert_equals "0" "$rc" "re-register succeeds"

content=$(cat "${_DF_REGISTRY_DIR}/auth-login/test_basic.test.js")
assert_contains "$content" "updated" "re-register overwrites with new content"

test_begin "registry_register: with explicit source_root"

_alt_root="${TMPDIR_TEST}/alt-root"
mkdir -p "${_alt_root}/.dark-factory/scenarios/alt-spec"
echo "test('alt', () => {})" > "${_alt_root}/.dark-factory/scenarios/alt-spec/test_alt.test.js"
_create_spec_file "alt-spec"

rc=0
registry_register "alt-spec" "$_alt_root" 2>/dev/null || rc=$?
assert_equals "0" "$rc" "register with source_root succeeds"
assert_file_exists "${_DF_REGISTRY_DIR}/alt-spec/test_alt.test.js" "file from alt source_root copied"

# ==============================================================================
# registry_deregister
# ==============================================================================

test_begin "registry_deregister: removes spec from registry"

registry_deregister "alt-spec" >/dev/null 2>&1
if [[ -d "${_DF_REGISTRY_DIR}/alt-spec" ]]; then
    _test_fail "alt-spec directory still exists"
else
    _test_pass "alt-spec directory removed"
fi

test_begin "registry_deregister: returns 1 on empty spec name"

rc=0
registry_deregister "" 2>/dev/null || rc=$?
assert_equals "1" "$rc" "returns 1 for empty spec name"

# ==============================================================================
# registry_list
# ==============================================================================

test_begin "registry_list: lists registered specs"

# auth-login should still be registered
listed=$(registry_list)
assert_contains "$listed" "auth-login" "lists auth-login"

test_begin "registry_list: lists multiple specs"

_create_scenarios "user-profile"
_create_spec_file "user-profile"
registry_register "user-profile" >/dev/null 2>&1

listed=$(registry_list)
assert_contains "$listed" "auth-login" "lists auth-login"
assert_contains "$listed" "user-profile" "lists user-profile"

test_begin "registry_list: returns 0 with no output if dir missing"

# Temporarily rename registry dir
mv "$_DF_REGISTRY_DIR" "${_DF_REGISTRY_DIR}.bak"
rc=0
result=$(registry_list) || rc=$?
assert_equals "0" "$rc" "returns 0"
mv "${_DF_REGISTRY_DIR}.bak" "$_DF_REGISTRY_DIR"

# ==============================================================================
# registry_scenario_dir
# ==============================================================================

test_begin "registry_scenario_dir: returns path for registered spec"

rc=0
dir=$(registry_scenario_dir "auth-login") || rc=$?
assert_equals "0" "$rc" "returns 0 for registered spec"
assert_contains "$dir" "auth-login" "path contains spec name"

test_begin "registry_scenario_dir: returns 1 for unregistered spec"

rc=0
registry_scenario_dir "nonexistent" 2>/dev/null || rc=$?
assert_equals "1" "$rc" "returns 1 for unregistered spec"

# ==============================================================================
# registry_run_regression
# ==============================================================================

test_begin "registry_run_regression: happy path all pass"

rc=0
output=$(registry_run_regression 2>&1) || rc=$?
assert_equals "0" "$rc" "all pass returns 0"
assert_contains "$output" "passed" "output mentions passed"

test_begin "registry_run_regression: failing spec"

# Create a fail marker for user-profile
touch "${_test_root}/.dark-factory/scenarios/user-profile/.fail"

rc=0
output=$(registry_run_regression 2>&1) || rc=$?
assert_equals "1" "$rc" "failing spec returns 1"
assert_contains "$output" "FAIL" "output contains FAIL"

rm -f "${_test_root}/.dark-factory/scenarios/user-profile/.fail"

test_begin "registry_run_regression: --exclude flag"

# Put back the fail marker but exclude that spec
touch "${_test_root}/.dark-factory/scenarios/user-profile/.fail"

rc=0
output=$(registry_run_regression --exclude "user-profile" 2>&1) || rc=$?
assert_equals "0" "$rc" "passes when failing spec is excluded"

rm -f "${_test_root}/.dark-factory/scenarios/user-profile/.fail"

test_begin "registry_run_regression: symlink cleanup on failure"

# Register a third spec that will fail, verify symlinks cleaned up
_create_scenarios "cleanup-test"
_create_spec_file "cleanup-test"
registry_register "cleanup-test" >/dev/null 2>&1

# Remove the scenarios dir (so symlink will be created), add fail marker
rm -rf "${_test_root}/.dark-factory/scenarios/cleanup-test"
# Create fail marker at registry level so mock can find it via symlink
touch "${_DF_REGISTRY_DIR}/cleanup-test/.fail"

# Run regression (will fail for cleanup-test)
registry_run_regression 2>/dev/null || true

# Check that symlink was cleaned up
if [[ -L "${_test_root}/.dark-factory/scenarios/cleanup-test" ]]; then
    _test_fail "symlink still exists after regression"
else
    _test_pass "symlink cleaned up after regression"
fi

# ==============================================================================
# registry_migrate_from_state
# ==============================================================================

test_begin "registry_migrate_from_state: migrates shipped state files"

# Clear registry first
rm -rf "$_DF_REGISTRY_DIR"

# Create state files
echo "status=shipped" > "${_test_root}/.dark-factory/state/migrate-spec.state"
_create_scenarios "migrate-spec"
_create_spec_file "migrate-spec"

# Create a non-shipped state file
echo "status=failed" > "${_test_root}/.dark-factory/state/failed-spec.state"
_create_scenarios "failed-spec"

output=$(registry_migrate_from_state 2>&1)
assert_contains "$output" "Migrated" "migration output present"

# Check shipped spec was registered
if [[ -d "${_DF_REGISTRY_DIR}/migrate-spec" ]]; then
    _test_pass "shipped spec migrated"
else
    _test_fail "shipped spec not migrated"
fi

# Check non-shipped spec was NOT registered
if [[ -d "${_DF_REGISTRY_DIR}/failed-spec" ]]; then
    _test_fail "non-shipped spec should not be migrated"
else
    _test_pass "non-shipped spec correctly skipped"
fi

test_begin "registry_migrate_from_state: skips when registry non-empty"

# Registry now has entries from previous test, so migration should be skipped
echo "status=shipped" > "${_test_root}/.dark-factory/state/another-spec.state"
_create_scenarios "another-spec"
_create_spec_file "another-spec"

output=$(registry_migrate_from_state 2>&1)
# Should return immediately without migrating
if [[ -d "${_DF_REGISTRY_DIR}/another-spec" ]]; then
    _test_fail "should not migrate when registry already has entries"
else
    _test_pass "migration skipped when registry non-empty"
fi

test_begin "registry_migrate_from_state: no state files"

rm -rf "$_DF_REGISTRY_DIR"
rm -f "${_test_root}/.dark-factory/state/"*.state

rc=0
output=$(registry_migrate_from_state 2>&1) || rc=$?
assert_equals "0" "$rc" "no error with no state files"
assert_contains "$output" "Migrated 0" "reports 0 migrated"

# ==============================================================================
# Registry locking
# ==============================================================================

test_begin "registry_register: lockdir absent after successful register"

# Re-create scenarios for a fresh spec
_create_scenarios "lock-test-reg"
_create_spec_file "lock-test-reg"
registry_register "lock-test-reg" >/dev/null 2>&1

if [[ -d "${_DF_REGISTRY_DIR}/.registry-lockdir" ]]; then
    _test_fail "lockdir still exists after register"
else
    _test_pass "lockdir cleaned up after register"
fi

test_begin "registry_deregister: lockdir absent after successful deregister"

registry_deregister "lock-test-reg" >/dev/null 2>&1

if [[ -d "${_DF_REGISTRY_DIR}/.registry-lockdir" ]]; then
    _test_fail "lockdir still exists after deregister"
else
    _test_pass "lockdir cleaned up after deregister"
fi

test_begin "registry_register: stale lock recovery (dead PID)"

# Create a stale lockdir with a provably-dead PID
bash -c 'exit 0' &
_dead_pid=$!
wait $_dead_pid
mkdir -p "${_DF_REGISTRY_DIR}/.registry-lockdir"
echo "$_dead_pid" > "${_DF_REGISTRY_DIR}/.registry-lockdir/pid"

_create_scenarios "stale-lock-test"
_create_spec_file "stale-lock-test"

rc=0
registry_register "stale-lock-test" >/dev/null 2>&1 || rc=$?
assert_equals "0" "$rc" "register succeeds despite stale lock"

if [[ -d "${_DF_REGISTRY_DIR}/.registry-lockdir" ]]; then
    _test_fail "stale lockdir still exists after register"
else
    _test_pass "stale lockdir cleaned up"
fi

# ==============================================================================
# Teardown
# ==============================================================================

cleanup_tmpdir
test_summary
