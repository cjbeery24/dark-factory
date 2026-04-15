#!/usr/bin/env bash
# tests/shell/test_normalize_output_spinner.sh
#
# Motivated by the 2026-04-15 budget-brain-v2 wet run (run 7ff4438d539a4ed5):
# drizzle-kit migrate renders an in-place spinner using ANSI CSI cursor-control
# (2K = erase line, 1G = cursor to col 1). Under non-interactive capture those
# bytes stay in the stream without newlines — so the gate report's fenced
# "Setup Failure Details" block rendered as:
#
#     [⣷] applying migrations...[2K[1G[⣷] applying migrations...
#
# which told the implementer "looks like it hung", not "here's what failed."
# The old _sanitize_output only stripped SGR color codes; cursor moves
# survived.
#
# This test pins the behavior of the fix: _normalize_output must convert
# cursor-rewind frames into newlines, erase-line into nothing, strip all CSI
# escapes broadly, and collapse consecutive duplicates. It must NOT mangle
# plain text that has no escapes, and it must leave legitimate newlines
# alone.
#
# Usage: bash tests/shell/test_normalize_output_spinner.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
QG_SCRIPT="$PROJECT_ROOT/scripts/quality-gate.sh"
PASS=0
FAIL=0

# Source the helper without running the rest of quality-gate.sh. The script
# does real work on load (mkdir, file writes), so we extract just the
# function definition via a subshell that sources it with tripwires.
# Simpler: copy the function definition verbatim here and verify it matches
# the source. If they drift, the grep will fail the test loudly.
assert_source_matches() {
    # Pin: the fix lives in quality-gate.sh and this test shadows it. If the
    # fix moves or the pipeline drifts, the test should SCREAM.
    if ! grep -q '_normalize_output()' "$QG_SCRIPT"; then
        echo "FAIL: _normalize_output not found in $QG_SCRIPT"
        exit 1
    fi
    # Pin the four sed rules — order and pattern both matter.
    grep -Fq 's/\x1b\[1G/\n/g'              "$QG_SCRIPT" || { echo "FAIL: missing 1G→\\n rule in $QG_SCRIPT"; exit 1; }
    grep -Fq 's/\r/\n/g'                    "$QG_SCRIPT" || { echo "FAIL: missing \\r→\\n rule in $QG_SCRIPT"; exit 1; }
    grep -Fq 's/\x1b\[2K//g'                "$QG_SCRIPT" || { echo "FAIL: missing 2K→'' rule in $QG_SCRIPT"; exit 1; }
    grep -Fq 's/\x1b\[[0-9;?]*[A-Za-z]//g'  "$QG_SCRIPT" || { echo "FAIL: missing broad-CSI rule in $QG_SCRIPT"; exit 1; }
    # LC_ALL=C is required on BOTH sed and awk — macOS awk mis-compares
    # multi-byte UTF-8 glyphs (braille spinner chars) without it.
    grep -Fq 'LC_ALL=C sed'                 "$QG_SCRIPT" || { echo "FAIL: missing LC_ALL=C on sed in $QG_SCRIPT"; exit 1; }
    grep -Fq 'LC_ALL=C awk'                 "$QG_SCRIPT" || { echo "FAIL: missing LC_ALL=C on awk in $QG_SCRIPT"; exit 1; }
}

_normalize_output() {
    LC_ALL=C sed -e 's/\x1b\[1G/\n/g' \
                 -e 's/\r/\n/g' \
                 -e 's/\x1b\[2K//g' \
                 -e 's/\x1b\[[0-9;?]*[A-Za-z]//g' \
        | LC_ALL=C awk 'NR==1 || $0 != prev { print; prev=$0 }'
}

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        echo "  PASS: $label"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label"
        printf '    expected: %q\n' "$expected"
        printf '    actual:   %q\n' "$actual"
        FAIL=$((FAIL + 1))
    fi
}

assert_contains() {
    local label="$1" needle="$2" haystack="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        echo "  PASS: $label"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label"
        printf '    needle:   %q\n' "$needle"
        printf '    haystack: %q\n' "$haystack"
        FAIL=$((FAIL + 1))
    fi
}

assert_not_contains() {
    local label="$1" needle="$2" haystack="$3"
    if [[ "$haystack" != *"$needle"* ]]; then
        echo "  PASS: $label"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label"
        printf '    must-not-contain: %q\n' "$needle"
        printf '    haystack:         %q\n' "$haystack"
        FAIL=$((FAIL + 1))
    fi
}

echo "Assert source matches pinned rules:"
assert_source_matches
echo "  PASS: quality-gate.sh contains the four sed rules"
PASS=$((PASS + 1))

ESC=$'\x1b'
CR=$'\r'

echo ""
echo "Drizzle-kit spinner fixture (the exact shape that failed the wet run):"
# Three spinner frames, rewrite-in-place via erase-line + cursor-to-col-1.
# No newlines between frames in raw capture.
DRIZZLE_FIXTURE="No config path provided, using default 'drizzle.config.ts'
Reading config file '/project/apps/api/drizzle.config.ts'
Using 'postgres' driver for database querying
[⣷] applying migrations...${ESC}[2K${ESC}[1G[⣾] applying migrations...${ESC}[2K${ESC}[1G[⣽] applying migrations..."

out=$(printf '%s' "$DRIZZLE_FIXTURE" | _normalize_output)

assert_not_contains "no literal CSI byte survives" "$ESC" "$out"
assert_not_contains "no literal '[2K' token in output" "[2K" "$out"
assert_not_contains "no literal '[1G' token in output" "[1G" "$out"
assert_contains "preamble line 1 preserved" "No config path provided" "$out"
assert_contains "preamble line 2 preserved" "Reading config file" "$out"
assert_contains "preamble line 3 preserved" "Using 'postgres' driver" "$out"
assert_contains "at least one spinner frame preserved" "applying migrations..." "$out"

# Different spinner characters are DIFFERENT lines, so awk dedupe does not
# collapse them. All three should survive.
frame_count=$(printf '%s\n' "$out" | grep -c 'applying migrations\.\.\.')
assert_eq "all three distinct spinner frames preserved" "3" "$frame_count"

echo ""
echo "Consecutive identical frames collapse (the 'hung spinner' anti-pattern):"
# Same glyph twice in a row — awk dedupe should collapse to one line.
REPEAT_FIXTURE="[⣷] applying migrations...${ESC}[2K${ESC}[1G[⣷] applying migrations...${ESC}[2K${ESC}[1G[⣷] applying migrations..."
repeat_out=$(printf '%s' "$REPEAT_FIXTURE" | _normalize_output)
repeat_count=$(printf '%s\n' "$repeat_out" | grep -c 'applying migrations\.\.\.')
assert_eq "3 identical frames collapse to 1 line" "1" "$repeat_count"

echo ""
echo "Carriage-return rewrites (node TTY emulation):"
# Some tools use plain \r for cursor-rewind instead of \x1b[1G.
CR_FIXTURE="progress: 10%${CR}progress: 20%${CR}progress: 30%"
cr_out=$(printf '%s' "$CR_FIXTURE" | _normalize_output)
assert_contains "10% frame preserved" "progress: 10%" "$cr_out"
assert_contains "20% frame preserved" "progress: 20%" "$cr_out"
assert_contains "30% frame preserved" "progress: 30%" "$cr_out"
assert_not_contains "no CR survives" "$CR" "$cr_out"

echo ""
echo "SGR color codes still stripped (regression guard for prior behavior):"
# Previous _sanitize_output only stripped SGR; make sure the broad-CSI rule
# still removes them — we must not regress on the narrow case while fixing
# the wide one.
SGR_FIXTURE="${ESC}[32mPASS${ESC}[0m: health check${ESC}[31mFAIL${ESC}[0m: db check"
sgr_out=$(printf '%s' "$SGR_FIXTURE" | _normalize_output)
assert_eq "SGR colors stripped, text preserved" "PASS: health checkFAIL: db check" "$sgr_out"

echo ""
echo "Non-escape input passes through unchanged:"
PLAIN_FIXTURE="line one
line two
line three"
plain_out=$(printf '%s' "$PLAIN_FIXTURE" | _normalize_output)
assert_eq "plain multiline preserved verbatim" "$PLAIN_FIXTURE" "$plain_out"

echo ""
echo "Empty input produces empty output (no crash, no phantom output):"
empty_out=$(printf '' | _normalize_output)
assert_eq "empty in, empty out" "" "$empty_out"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
