#!/usr/bin/env bash
# tests/shell/test_resolve_node_runner.sh — Regression test for _df_resolve_node_runner
#
# Verifies that vitest detection works for pnpm monorepo layouts where the
# vitest binary only exists inside node_modules/.pnpm/node_modules/.bin/.
#
# Usage: bash tests/shell/test_resolve_node_runner.sh
# Exit:  0 on all pass, 1 on any failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TMPDIR_BASE="$(mktemp -d)"
PASS=0
FAIL=0

cleanup() { rm -rf "$TMPDIR_BASE"; }
trap cleanup EXIT

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        echo "  PASS: $label"
        ((PASS++))
    else
        echo "  FAIL: $label (expected='$expected', got='$actual')"
        ((FAIL++))
    fi
}

# Override pnpm/npx to always fail so we isolate the find-based detection path.
pnpm() { return 1; }
npx() { return 1; }

# Source the library once (guard variable prevents re-sourcing).
_DF_LIB_SCENARIOS_LOADED=""
source "$PROJECT_ROOT/scripts/lib/scenarios.sh"

# ---------------------------------------------------------------------------
# Test 1: pnpm monorepo — vitest only in .pnpm/node_modules/.bin/
# ---------------------------------------------------------------------------
echo "--- Test 1: pnpm monorepo layout (vitest in .pnpm hoisted bin) ---"

T1="$TMPDIR_BASE/t1"
mkdir -p "$T1/node_modules/.pnpm/node_modules/.bin"
printf '#!/bin/sh\necho "vitest v2.1.8"\n' > "$T1/node_modules/.pnpm/node_modules/.bin/vitest"
chmod +x "$T1/node_modules/.pnpm/node_modules/.bin/vitest"

pushd "$T1" > /dev/null
_DF_NODE_SCENARIO_RUNNER=""
_DF_VITEST_BIN=""
_df_resolve_node_runner
assert_eq "runner=workspace_vitest" "workspace_vitest" "$_DF_NODE_SCENARIO_RUNNER"
assert_eq "bin points to .pnpm vitest" \
    "./node_modules/.pnpm/node_modules/.bin/vitest" "$_DF_VITEST_BIN"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 2: workspace package layout — vitest in apps/web/node_modules/.bin/
# ---------------------------------------------------------------------------
echo "--- Test 2: workspace package layout (vitest in sub-package) ---"

T2="$TMPDIR_BASE/t2"
mkdir -p "$T2/apps/web/node_modules/.bin"
printf '#!/bin/sh\necho "vitest v2.1.8"\n' > "$T2/apps/web/node_modules/.bin/vitest"
chmod +x "$T2/apps/web/node_modules/.bin/vitest"

pushd "$T2" > /dev/null
_DF_NODE_SCENARIO_RUNNER=""
_DF_VITEST_BIN=""
_df_resolve_node_runner
assert_eq "runner=workspace_vitest" "workspace_vitest" "$_DF_NODE_SCENARIO_RUNNER"
assert_eq "bin points to workspace vitest" \
    "./apps/web/node_modules/.bin/vitest" "$_DF_VITEST_BIN"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 3: .dark-factory vitest should be excluded
# ---------------------------------------------------------------------------
echo "--- Test 3: .dark-factory vitest excluded ---"

T3="$TMPDIR_BASE/t3"
mkdir -p "$T3/.dark-factory/node_modules/.bin"
printf '#!/bin/sh\necho "vitest v2.1.8"\n' > "$T3/.dark-factory/node_modules/.bin/vitest"
chmod +x "$T3/.dark-factory/node_modules/.bin/vitest"

pushd "$T3" > /dev/null
_DF_NODE_SCENARIO_RUNNER=""
_DF_VITEST_BIN=""
_df_resolve_node_runner
assert_eq "runner=none (dark-factory excluded)" "none" "$_DF_NODE_SCENARIO_RUNNER"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 4: no vitest at all — should get none
# ---------------------------------------------------------------------------
echo "--- Test 4: empty project (no vitest) ---"

T4="$TMPDIR_BASE/t4"
mkdir -p "$T4"

pushd "$T4" > /dev/null
_DF_NODE_SCENARIO_RUNNER=""
_DF_VITEST_BIN=""
_df_resolve_node_runner
assert_eq "runner=none (empty project)" "none" "$_DF_NODE_SCENARIO_RUNNER"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 5: SIGPIPE regression — multiple vitest binaries under set -eo pipefail
#   The old `find | head -1` pattern caused SIGPIPE (exit 141) when find
#   produced multiple results. `-print -quit` eliminates the pipe entirely.
# ---------------------------------------------------------------------------
echo "--- Test 5: multiple vitest binaries (SIGPIPE regression) ---"

T5="$TMPDIR_BASE/t5"
mkdir -p "$T5/apps/web/node_modules/.bin"
mkdir -p "$T5/apps/api/node_modules/.bin"
mkdir -p "$T5/node_modules/.pnpm/node_modules/.bin"
for bin in "$T5/apps/web/node_modules/.bin/vitest" \
           "$T5/apps/api/node_modules/.bin/vitest" \
           "$T5/node_modules/.pnpm/node_modules/.bin/vitest"; do
    printf '#!/bin/sh\necho "vitest v2.1.8"\n' > "$bin"
    chmod +x "$bin"
done

pushd "$T5" > /dev/null
_DF_NODE_SCENARIO_RUNNER=""
_DF_VITEST_BIN=""
# Enable errexit+pipefail — the conditions that triggered the original SIGPIPE.
(
    set -eo pipefail
    _df_resolve_node_runner
) && {
    echo "  PASS: no SIGPIPE under set -eo pipefail"
    ((PASS++))
} || {
    echo "  FAIL: subshell exited $? (likely SIGPIPE)"
    ((FAIL++))
}
# Re-resolve in the outer shell to check the chosen binary.
_df_resolve_node_runner
assert_eq "runner=workspace_vitest" "workspace_vitest" "$_DF_NODE_SCENARIO_RUNNER"
popd > /dev/null

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed"
if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
