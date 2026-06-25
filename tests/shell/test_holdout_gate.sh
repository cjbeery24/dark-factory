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

# --- Stub `pnpm`: emulate a vitest run. Output + exit are env-driven (DF_STUB_OUT /
# DF_STUB_EXIT) so each test can supply a failing / passing / no-summary run. The
# default is a FAILING run whose output contains the sentinel (test name +
# assertion + the leaked literal); the runner must discard all of it.
STUB_BIN="$TMPDIR_BASE/bin"
mkdir -p "$STUB_BIN"
cat > "$STUB_BIN/pnpm" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "${DF_STUB_OUT}"
exit "${DF_STUB_EXIT:-0}"
STUB
chmod +x "$STUB_BIN/pnpm"
export PATH="$STUB_BIN:$PATH"
# Default stub output: a FAILING run leaking the sentinel.
export DF_STUB_OUT=" FAIL  ../../.dark-factory/holdouts/myspec/sentinel.test.ts > [adversarial] ${SENTINEL}
AssertionError: expected 200 not to be 200
 Test Files  1 failed (1)
 Tests  1 failed | 0 passed (1)"
export DF_STUB_EXIT=1

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

# ---------------------------------------------------------------------------
# Test 4 (REGRESSION — set -e): an ALL-PASS run has no "Tests N failed" line, so
# the `failed=$(... grep ...)` substitution exits 1. Under `set -euo pipefail`
# (quality-gate.sh's mode) that aborts the WHOLE script before the sidecar write
# — the exact run10 bug (no sidecar, no row, gate exit 1). The fn MUST survive
# set -e and record a clean PASS. Runs in a `set -euo pipefail` SUBSHELL to
# mirror the real gate. (The harness itself can't be set -e: ((PASS++)) returns
# the pre-increment value → 1st success would abort.)
echo "--- Test 4 (set -e): all-pass holdout run must not abort, records PASS ---"
T4="$TMPDIR_BASE/proj4"
mkdir -p "$T4/.dark-factory/architecture" "$T4/.dark-factory/reports" \
         "$T4/.dark-factory/holdouts/myspec"
cat > "$T4/vitest.config.ts" <<'EOF'
import { defineConfig } from 'vitest/config'
export default defineConfig({ test: { environment: 'node' } })
EOF
cat > "$T4/.dark-factory/architecture/myspec-manifest.yaml" <<'EOF'
version: 1
spec: myspec
language: node
test_integration:
  vitest_config: vitest.config.ts
EOF
echo "it('ok', () => { expect(1).toBe(1) })" > "$T4/.dark-factory/holdouts/myspec/ok.test.ts"
# All-pass vitest output: NO "failed" token (the set -e trap).
set4_rc=0
( set -euo pipefail
  cd "$T4"
  export DF_STUB_OUT=" Test Files  1 passed (1)
 Tests  3 passed (3)"
  export DF_STUB_EXIT=0
  REPORT_FILE="$T4/r.md"; SUMMARY_FILE="$T4/s.md"; : > "$REPORT_FILE"; : > "$SUMMARY_FILE"
  GATE_FAILED=false; TIMEOUT_CMD=""; SCENARIO_TIMEOUT=60
  _DF_VITEST_CONFIG_RESOLVED=""; _DF_VITEST_PROJECT_CONFIG=""
  _DF_WORKSPACE_DIR=""; _DF_WORKSPACE_UP=""; _DF_SETUP_INJECT=""
  _df_run_holdout_gate "myspec"
) > /dev/null 2>&1 || set4_rc=$?
assert_eq       "all-pass run does NOT abort under set -e"  "0" "$set4_rc"
assert_contains "all-pass writes PASS sidecar (FAILED=0)"   "HOLDOUT_FAILED=0" \
                "$(cat "$T4/.dark-factory/reports/myspec-holdout-result.env" 2>/dev/null)"
assert_contains "all-pass writes a Blind Holdouts PASS row" "Blind Holdouts | PASS" \
                "$(cat "$T4/r.md" 2>/dev/null)"

# ---------------------------------------------------------------------------
# Test 5 (REGRESSION — set -e): a run that errors with NO summary line at all
# (e.g. server/config failure) → both count greps miss → must NOT abort; the
# can't-run path records a FAIL sidecar (fail-closed), not a silent skip.
# ---------------------------------------------------------------------------
echo "--- Test 5 (set -e): no-summary error run → cant-run FAIL, no abort ---"
T5="$TMPDIR_BASE/proj5"
mkdir -p "$T5/.dark-factory/architecture" "$T5/.dark-factory/reports" \
         "$T5/.dark-factory/holdouts/myspec"
cp "$T4/vitest.config.ts" "$T5/vitest.config.ts"
sed 's/spec: myspec/spec: myspec/' "$T4/.dark-factory/architecture/myspec-manifest.yaml" \
   > "$T5/.dark-factory/architecture/myspec-manifest.yaml"
echo "it('ok', () => {})" > "$T5/.dark-factory/holdouts/myspec/ok.test.ts"
set5_rc=0
( set -euo pipefail
  cd "$T5"
  export DF_STUB_OUT="Error: connect ECONNREFUSED 127.0.0.1:5432
No test files found, exiting with code 1"
  export DF_STUB_EXIT=1
  REPORT_FILE="$T5/r.md"; SUMMARY_FILE="$T5/s.md"; : > "$REPORT_FILE"; : > "$SUMMARY_FILE"
  GATE_FAILED=false; TIMEOUT_CMD=""; SCENARIO_TIMEOUT=60
  _DF_VITEST_CONFIG_RESOLVED=""; _DF_VITEST_PROJECT_CONFIG=""
  _DF_WORKSPACE_DIR=""; _DF_WORKSPACE_UP=""; _DF_SETUP_INJECT=""
  _df_run_holdout_gate "myspec"
) > /dev/null 2>&1 || set5_rc=$?
assert_eq       "no-summary error does NOT abort under set -e" "0" "$set5_rc"
assert_contains "cant-run records FAIL sidecar (FAILED=-1)"    "HOLDOUT_FAILED=-1" \
                "$(cat "$T5/.dark-factory/reports/myspec-holdout-result.env" 2>/dev/null)"
assert_contains "cant-run writes a Blind Holdouts FAIL row"    "Blind Holdouts | FAIL" \
                "$(cat "$T5/r.md" 2>/dev/null)"

# ---------------------------------------------------------------------------
# Test 6 (REGRESSION — ANSI color): vitest emits color escapes even to a pipe,
# so the summary byte stream is "Tests <ESC>…11 failed". The count regex needs
# contiguous whitespace between "Tests" and the number, so without an ANSI strip
# a real "11 failed" parses as 0 → false PASS → ship-the-bug (the spec-5 mis-ship:
# holdouts caught 11/64, sidecar said 0). The runner MUST strip ANSI and record 11.
echo "--- Test 6 (ANSI): colored '11 failed' summary parses to FAILED=11 ---"
T6="$TMPDIR_BASE/proj6"
mkdir -p "$T6/.dark-factory/architecture" "$T6/.dark-factory/reports" \
         "$T6/.dark-factory/holdouts/myspec"
cp "$T4/vitest.config.ts" "$T6/vitest.config.ts"
cp "$T4/.dark-factory/architecture/myspec-manifest.yaml" \
   "$T6/.dark-factory/architecture/myspec-manifest.yaml"
echo "it('x', () => {})" > "$T6/.dark-factory/holdouts/myspec/x.test.ts"
ESC=$(printf '\033')
set6_rc=0
( set -euo pipefail
  cd "$T6"
  # Real vitest colorized summary: SGR codes between "Tests" and "11 failed".
  export DF_STUB_OUT="${ESC}[2m Test Files ${ESC}[22m ${ESC}[1m${ESC}[31m4 failed${ESC}[39m${ESC}[22m${ESC}[90m (4)${ESC}[39m
${ESC}[2m      Tests ${ESC}[22m ${ESC}[1m${ESC}[31m11 failed${ESC}[39m${ESC}[22m${ESC}[2m | ${ESC}[22m${ESC}[1m${ESC}[32m53 passed${ESC}[39m${ESC}[22m${ESC}[90m (64)${ESC}[39m"
  export DF_STUB_EXIT=1
  REPORT_FILE="$T6/r.md"; SUMMARY_FILE="$T6/s.md"; : > "$REPORT_FILE"; : > "$SUMMARY_FILE"
  GATE_FAILED=false; TIMEOUT_CMD=""; SCENARIO_TIMEOUT=60
  _DF_VITEST_CONFIG_RESOLVED=""; _DF_VITEST_PROJECT_CONFIG=""
  _DF_WORKSPACE_DIR=""; _DF_WORKSPACE_UP=""; _DF_SETUP_INJECT=""
  _df_run_holdout_gate "myspec"
) > /dev/null 2>&1 || set6_rc=$?
side6="$(cat "$T6/.dark-factory/reports/myspec-holdout-result.env" 2>/dev/null)"
assert_eq       "ANSI summary does not abort under set -e"  "0" "$set6_rc"
assert_contains "ANSI '11 failed' → sidecar FAILED=11"      "HOLDOUT_FAILED=11" "$side6"
assert_contains "ANSI summary → sidecar TOTAL=64"           "HOLDOUT_TOTAL=64"  "$side6"
assert_contains "ANSI summary → report row counts 11/64"    "11/64" \
                "$(cat "$T6/r.md" 2>/dev/null)"

# ---------------------------------------------------------------------------
# Test 7 (REGRESSION — Residual 1 / Neo MF-1, Linus C1): a holdout file that
# THROWS at module-load registers ZERO tests. vitest prints "Test Files 1 failed
# (1)" + "Tests no tests", exit 1. The OLD parser grepped the last "(N)" ANYWHERE
# → total=1 (from the Test-Files line) → disarmed the can't-run guard → a suite
# that asserted NOTHING recorded PASS (ship-the-bug). Counts must anchor to the
# "Tests" summary line; zero registered tests → can't-run FAIL.
echo "--- Test 7 (zero-tests/import-throw): Test-Files paren must NOT count as a pass ---"
T7="$TMPDIR_BASE/proj7"
mkdir -p "$T7/.dark-factory/architecture" "$T7/.dark-factory/reports" \
         "$T7/.dark-factory/holdouts/myspec"
cp "$T4/vitest.config.ts" "$T7/vitest.config.ts"
cp "$T4/.dark-factory/architecture/myspec-manifest.yaml" \
   "$T7/.dark-factory/architecture/myspec-manifest.yaml"
echo "import './missing.js'" > "$T7/.dark-factory/holdouts/myspec/throws.test.ts"
set7_rc=0
( set -euo pipefail
  cd "$T7"
  export DF_STUB_OUT=" FAIL  holdouts/myspec/throws.test.ts [ holdouts/myspec/throws.test.ts ]
Error: Cannot find module './missing.js'
 Test Files  1 failed (1)
      Tests  no tests
   Start at  00:00:00"
  export DF_STUB_EXIT=1
  REPORT_FILE="$T7/r.md"; SUMMARY_FILE="$T7/s.md"; : > "$REPORT_FILE"; : > "$SUMMARY_FILE"
  GATE_FAILED=false; TIMEOUT_CMD=""; SCENARIO_TIMEOUT=60
  _DF_VITEST_CONFIG_RESOLVED=""; _DF_VITEST_PROJECT_CONFIG=""
  _DF_WORKSPACE_DIR=""; _DF_WORKSPACE_UP=""; _DF_SETUP_INJECT=""
  _df_run_holdout_gate "myspec"
) > /dev/null 2>&1 || set7_rc=$?
side7="$(cat "$T7/.dark-factory/reports/myspec-holdout-result.env" 2>/dev/null)"
assert_eq       "import-throw does not abort under set -e"      "0" "$set7_rc"
assert_contains "import-throw → can't-run FAIL (not pass)"      "HOLDOUT_FAILED=-1" "$side7"
assert_contains "import-throw writes a Blind Holdouts FAIL row" "Blind Holdouts | FAIL" \
                "$(cat "$T7/r.md" 2>/dev/null)"

# ---------------------------------------------------------------------------
# Test 8 (REGRESSION — Linus C2): a cursor/private-mode escape (ESC[?25l, which
# NO_COLOR does NOT suppress) interleaved between "Tests" and the count. The
# SGR-only strip left it and broke the count → false PASS. The full-CSI strip
# must remove it and recover FAILED=11.
echo "--- Test 8 (cursor escape ESC[?25l): full-CSI strip recovers the count ---"
T8="$TMPDIR_BASE/proj8"
mkdir -p "$T8/.dark-factory/architecture" "$T8/.dark-factory/reports" \
         "$T8/.dark-factory/holdouts/myspec"
cp "$T4/vitest.config.ts" "$T8/vitest.config.ts"
cp "$T4/.dark-factory/architecture/myspec-manifest.yaml" \
   "$T8/.dark-factory/architecture/myspec-manifest.yaml"
echo "it('x', () => {})" > "$T8/.dark-factory/holdouts/myspec/x.test.ts"
ESC=$(printf '\033')
set8_rc=0
( set -euo pipefail
  cd "$T8"
  # ESC[?25l (cursor hide) lands between "Tests" and "11"; ESC[31m is SGR color.
  export DF_STUB_OUT=" Test Files  4 failed (4)
      Tests ${ESC}[?25l ${ESC}[31m11 failed${ESC}[39m | 53 passed (64)"
  export DF_STUB_EXIT=1
  REPORT_FILE="$T8/r.md"; SUMMARY_FILE="$T8/s.md"; : > "$REPORT_FILE"; : > "$SUMMARY_FILE"
  GATE_FAILED=false; TIMEOUT_CMD=""; SCENARIO_TIMEOUT=60
  _DF_VITEST_CONFIG_RESOLVED=""; _DF_VITEST_PROJECT_CONFIG=""
  _DF_WORKSPACE_DIR=""; _DF_WORKSPACE_UP=""; _DF_SETUP_INJECT=""
  _df_run_holdout_gate "myspec"
) > /dev/null 2>&1 || set8_rc=$?
side8="$(cat "$T8/.dark-factory/reports/myspec-holdout-result.env" 2>/dev/null)"
assert_eq       "cursor-escape run does not abort under set -e"  "0" "$set8_rc"
assert_contains "cursor escape stripped → FAILED=11"             "HOLDOUT_FAILED=11" "$side8"
assert_contains "cursor escape stripped → TOTAL=64"              "HOLDOUT_TOTAL=64"  "$side8"

# ---------------------------------------------------------------------------
# Test 9 (sidecar written on EVERY path — Neo MF-2): the legitimate-skip paths
# (no holdout dir / sentinel) must now write an explicit RAN=0/FAILED=0 sidecar
# so downstream ABSENCE is unambiguously an aborted run (fail-closed), never a
# silent pass.
echo "--- Test 9 (MF-2): skip paths write an explicit RAN=0/FAILED=0 sidecar ---"
assert_contains "no-holdouts skip writes RAN=0 sidecar" "HOLDOUT_RAN=0" \
                "$(cat "$T2/.dark-factory/reports/myspec-holdout-result.env" 2>/dev/null)"
assert_contains "no-holdouts skip writes FAILED=0 (not -1)" "HOLDOUT_FAILED=0" \
                "$(cat "$T2/.dark-factory/reports/myspec-holdout-result.env" 2>/dev/null)"
assert_contains "sentinel skip writes RAN=0 sidecar" "HOLDOUT_RAN=0" \
                "$(cat "$T3/.dark-factory/reports/myspec-holdout-result.env" 2>/dev/null)"

# ---------------------------------------------------------------------------
# Helper for the round-3 arms: run the gate in a set -e subshell with a given
# stub output+exit, echo the resulting sidecar.
_run_arm() {  # <dir> <stub_out> <stub_exit>
    local d="$1" out="$2" rc="$3"
    mkdir -p "$d/.dark-factory/architecture" "$d/.dark-factory/reports" \
             "$d/.dark-factory/holdouts/myspec"
    cp "$T4/vitest.config.ts" "$d/vitest.config.ts"
    cp "$T4/.dark-factory/architecture/myspec-manifest.yaml" \
       "$d/.dark-factory/architecture/myspec-manifest.yaml"
    echo "it('x', () => {})" > "$d/.dark-factory/holdouts/myspec/x.test.ts"
    ( set -euo pipefail
      cd "$d"
      export DF_STUB_OUT="$out"; export DF_STUB_EXIT="$rc"
      REPORT_FILE="$d/r.md"; SUMMARY_FILE="$d/s.md"; : > "$REPORT_FILE"; : > "$SUMMARY_FILE"
      GATE_FAILED=false; TIMEOUT_CMD=""; SCENARIO_TIMEOUT=60
      _DF_VITEST_CONFIG_RESOLVED=""; _DF_VITEST_PROJECT_CONFIG=""
      _DF_WORKSPACE_DIR=""; _DF_WORKSPACE_UP=""; _DF_SETUP_INJECT=""
      _df_run_holdout_gate "myspec"
    ) > /dev/null 2>&1
    cat "$d/.dark-factory/reports/myspec-holdout-result.env" 2>/dev/null
}

# Test 10 (Neo C-3): unhandled error/rejection — vitest exits NONZERO but prints
# a populated "N passed (N)" summary. The exit signal must NOT be laundered into
# a pass just because the summary line exists.
echo "--- Test 10 (Neo C-3): nonzero exit + all-passed summary → can't-run FAIL ---"
side10="$(_run_arm "$TMPDIR_BASE/proj10" " Test Files  1 passed (1)
      Tests  64 passed (64)
 Unhandled Errors  1" 1)"
assert_contains "nonzero-exit + 64 passed → FAILED=-1 (not 0)" "HOLDOUT_FAILED=-1" "$side10"

# Test 11 (Neo C-3): all tests skipped — asserted nothing.
echo "--- Test 11 (Neo C-3): all-skipped summary → can't-run FAIL ---"
side11="$(_run_arm "$TMPDIR_BASE/proj11" " Test Files  1 passed (1)
      Tests  64 skipped (64)" 0)"
assert_contains "all-skipped → FAILED=-1 (asserted nothing)" "HOLDOUT_FAILED=-1" "$side11"

# Test 12 (Neo C-3): "0 passed (64)" — collected tests, ran none.
echo "--- Test 12 (Neo C-3): 0-passed summary → can't-run FAIL ---"
side12="$(_run_arm "$TMPDIR_BASE/proj12" " Test Files  1 failed (1)
      Tests  0 passed (64)" 0)"
assert_contains "0 passed → FAILED=-1 (asserted nothing)" "HOLDOUT_FAILED=-1" "$side12"

# Test 13 (Linus C3): a SHADOW "Tests … (N)" log line printed AFTER the real
# summary (adversary-influenced holdout stdout, captured 2>&1) must not displace
# the real count. The grammar anchor (count token + trailing (N)) picks the real
# summary; the shadow (no pass/fail token, no trailing paren) is excluded.
echo "--- Test 13 (Linus C3): trailing shadow 'Tests …' line does not mask the real count ---"
side13="$(_run_arm "$TMPDIR_BASE/proj13" " Test Files  1 failed (1)
      Tests  2 failed | 1 passed (3)
Tests for the auth (99) module completed successfully" 1)"
assert_contains "shadow line ignored → real FAILED=2"   "HOLDOUT_FAILED=2" "$side13"
assert_contains "shadow line ignored → real TOTAL=3"    "HOLDOUT_TOTAL=3"  "$side13"

# Test 13b (Neo C-4): PARTIAL skip — most blind probes never executed. passed>0
# and total>0 satisfy the earlier walls, but passed+failed<total → can't-run.
echo "--- Test 13b (Neo C-4): partial-skip (2 passed | 62 skipped) → can't-run FAIL ---"
side13b="$(_run_arm "$TMPDIR_BASE/proj13b" " Test Files  1 passed (1)
      Tests  2 passed | 62 skipped (64)" 0)"
assert_contains "partial-skip → FAILED=-1 (most probes never ran)" "HOLDOUT_FAILED=-1" "$side13b"

# Test 14 (sanity): a genuine clean all-pass still records PASS (no over-strict
# false NO-SHIP from the new guards).
echo "--- Test 14 (sanity): genuine clean all-pass still PASSES ---"
side14="$(_run_arm "$TMPDIR_BASE/proj14" " Test Files  1 passed (1)
      Tests  64 passed (64)" 0)"
assert_contains "clean all-pass → RAN=1"        "HOLDOUT_RAN=1"     "$side14"
assert_contains "clean all-pass → FAILED=0"     "HOLDOUT_FAILED=0"  "$side14"
assert_contains "clean all-pass → PASSED=64"    "HOLDOUT_PASSED=64" "$side14"

# Test 15 (Codex P2 extraction safety): hostile vitest output carrying titles, SQL,
# {anchor}, expected/actual values, and [hb:..] tokens on BOTH fail and pass lines.
# HOLDOUT_FAILED_IDS must carry ONLY the opaque FAILING tokens (f1,f2) — never the
# passing token (p1), and never any title/assertion/input text.
echo "--- Test 15 (P2 extraction): hostile output → opaque failing ids only, no leak ---"
hb_out=" FAIL  x > [adversarial] sneaky DROP TABLE users; title [hb:f1] expected 401 got 200
 × another failing {anchor} payload=<script> [hb:f2]
 ✓ a passing test bearer-token-trim [hb:p1]
 Test Files  1 failed (1)
      Tests  2 failed | 1 passed (3)"
side15="$(_run_arm "$TMPDIR_BASE/proj15" "$hb_out" 1)"
ids15="$(printf '%s\n' "$side15" | grep '^HOLDOUT_FAILED_IDS=' | cut -d= -f2-)"
assert_eq           "extraction: only opaque FAILING ids (f1,f2)" "f1,f2"  "$ids15"
assert_not_contains "no passing token leaks"                       "p1"     "$ids15"
assert_not_contains "no title text leaks"                          "sneaky" "$ids15"
assert_not_contains "no SQL leaks"                                 "DROP"   "$ids15"
assert_not_contains "no input/payload leaks"                       "script" "$ids15"
assert_not_contains "no expected/actual value leaks"               "401"    "$ids15"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
[[ $FAIL -eq 0 ]]
