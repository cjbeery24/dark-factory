#!/usr/bin/env bash
# tests/shell/test_holdout_gate.sh — Tests for _df_run_holdout_gate (binary blind
# holdout gate). The CRITICAL invariant: the runner discards the raw vitest output
# so a holdout SENTINEL literal (test names / assertions / adversarial-input PII)
# NEVER reaches the Reviewer/Oracle-readable REPORT/SUMMARY — only a count. This is
# the deterministic Surface-(d) barrier proof at the runner level (Docker-free:
# stubs pnpm so no real vitest/server/postgres is needed).
#
# Usage: bash tests/shell/test_holdout_gate.sh
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
        echo "  PASS: $label"; ((PASS++))
    else
        echo "  FAIL: $label (expected='$expected', got='$actual')"; ((FAIL++))
    fi
}
assert_contains() {
    local label="$1" needle="$2" haystack="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        echo "  PASS: $label"; ((PASS++))
    else
        echo "  FAIL: $label (expected to contain '$needle')"; ((FAIL++))
    fi
}
assert_not_contains() {
    local label="$1" needle="$2" haystack="$3"
    if [[ "$haystack" != *"$needle"* ]]; then
        echo "  PASS: $label"; ((PASS++))
    else
        echo "  FAIL: $label (LEAK — should NOT contain '$needle')"; ((FAIL++))
    fi
}

# shellcheck source=/dev/null
source "${PROJECT_ROOT}/scripts/lib/scenarios.sh"

SENTINEL="WHITESPACE_PADDED_BEARER_SENTINEL_xyz"

# --- Stub `pnpm`: emulate a FAILING vitest run whose output contains the sentinel
# (test name + assertion + the leaked literal). The runner must discard all of it.
STUB_BIN="$TMPDIR_BASE/bin"
mkdir -p "$STUB_BIN"
cat > "$STUB_BIN/pnpm" <<STUB
#!/usr/bin/env bash
cat <<OUT
 FAIL  ../../.dark-factory/holdouts/myspec/sentinel.test.ts > [adversarial] ${SENTINEL}
AssertionError: expected 200 not to be 200
 Test Files  1 failed (1)
 Tests  1 failed | 0 passed (1)
OUT
exit 1
STUB
chmod +x "$STUB_BIN/pnpm"
export PATH="$STUB_BIN:$PATH"

# ---------------------------------------------------------------------------
# Test 1: holdout failure → GATE_FAILED + count-only sidecar/row, ZERO leak.
# ---------------------------------------------------------------------------
echo "--- Test 1: holdout failure is count-only, sentinel never leaks ---"
T="$TMPDIR_BASE/proj"
mkdir -p "$T/.dark-factory/architecture" "$T/.dark-factory/reports" \
         "$T/.dark-factory/holdouts/myspec"
cat > "$T/vitest.config.ts" <<'EOF'
import { defineConfig } from 'vitest/config'
export default defineConfig({ test: { environment: 'node' } })
EOF
cat > "$T/.dark-factory/architecture/myspec-manifest.yaml" <<'EOF'
version: 1
spec: myspec
language: node
test_integration:
  vitest_config: vitest.config.ts
EOF
cat > "$T/.dark-factory/holdouts/myspec/sentinel.test.ts" <<EOF
// ${SENTINEL}
it('adversarial', () => { expect(1).toBe(2) })
EOF

pushd "$T" > /dev/null
REPORT_FILE="$T/report.md"; SUMMARY_FILE="$T/summary.md"
: > "$REPORT_FILE"; : > "$SUMMARY_FILE"
GATE_FAILED=false; TIMEOUT_CMD=""; SCENARIO_TIMEOUT=60
_DF_VITEST_CONFIG_RESOLVED=""; _DF_VITEST_PROJECT_CONFIG=""
_DF_WORKSPACE_DIR=""; _DF_WORKSPACE_UP=""; _DF_SETUP_INJECT=""
_df_run_holdout_gate "myspec" > /dev/null 2>&1
popd > /dev/null

report="$(cat "$T/report.md")"
summary="$(cat "$T/summary.md")"
side="$(cat "$T/.dark-factory/reports/myspec-holdout-result.env" 2>/dev/null)"

assert_eq        "holdout failure sets GATE_FAILED"        "true" "$GATE_FAILED"
assert_contains  "sidecar records HOLDOUT_FAILED=1"        "HOLDOUT_FAILED=1" "$side"
assert_contains  "sidecar marks HOLDOUT_RAN=1"             "HOLDOUT_RAN=1" "$side"
assert_contains  "report has a Blind Holdouts FAIL row"   "Blind Holdouts | FAIL" "$report"
assert_not_contains "SENTINEL never reaches REPORT"        "$SENTINEL" "$report"
assert_not_contains "SENTINEL never reaches SUMMARY"       "$SENTINEL" "$summary"
assert_not_contains "assertion text never reaches REPORT"  "AssertionError" "$report"
assert_not_contains "holdout filename never reaches REPORT" "sentinel.test.ts" "$report"
assert_eq        "holdout wrapper cleaned up"             "" \
                 "$(ls "$T/.df-holdout-wrapper.vitest.config.ts" 2>/dev/null || true)"

# ---------------------------------------------------------------------------
# Test 2 (Arm C): no holdout dir → no row, no GATE_FAILED, no false coverage hole.
# ---------------------------------------------------------------------------
echo "--- Test 2: no holdouts → unaffected (no row, no GATE_FAILED) ---"
T2="$TMPDIR_BASE/proj2"
mkdir -p "$T2/.dark-factory/reports"
pushd "$T2" > /dev/null
REPORT_FILE="$T2/r.md"; SUMMARY_FILE="$T2/s.md"
: > "$REPORT_FILE"; : > "$SUMMARY_FILE"; GATE_FAILED=false
_DF_VITEST_CONFIG_RESOLVED=""
_df_run_holdout_gate "myspec" > /dev/null 2>&1
popd > /dev/null
assert_eq "no holdouts → GATE_FAILED stays false" "false" "$GATE_FAILED"
assert_eq "no holdouts → no Blind Holdouts row"   "" \
          "$(grep 'Blind Holdouts' "$T2/r.md" 2>/dev/null || true)"

# ---------------------------------------------------------------------------
# Test 3 (Arm C): sentinel-on-holdouts → no row (no false hole).
# ---------------------------------------------------------------------------
echo "--- Test 3: SCENARIOS-NOT-APPLICABLE sentinel → skipped ---"
T3="$TMPDIR_BASE/proj3"
mkdir -p "$T3/.dark-factory/reports" "$T3/.dark-factory/holdouts/myspec"
echo "n/a" > "$T3/.dark-factory/holdouts/myspec/SCENARIOS-NOT-APPLICABLE.md"
pushd "$T3" > /dev/null
REPORT_FILE="$T3/r.md"; SUMMARY_FILE="$T3/s.md"
: > "$REPORT_FILE"; : > "$SUMMARY_FILE"; GATE_FAILED=false
_DF_VITEST_CONFIG_RESOLVED=""
_df_run_holdout_gate "myspec" > /dev/null 2>&1
popd > /dev/null
assert_eq "sentinel → GATE_FAILED stays false" "false" "$GATE_FAILED"
assert_eq "sentinel → no Blind Holdouts row"   "" \
          "$(grep 'Blind Holdouts' "$T3/r.md" 2>/dev/null || true)"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
[[ $FAIL -eq 0 ]]
