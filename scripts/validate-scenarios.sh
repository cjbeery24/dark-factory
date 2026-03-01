#!/usr/bin/env bash
set -euo pipefail

# Scenario Quality Validator
# Usage: ./scripts/validate-scenarios.sh <spec-name>
#
# Validates that generated scenario files contain real, non-trivial tests.
# Runs after scenario generation, before implementation begins.
#
# Exit codes:
#   0 = pass (or warn mode with failures)
#   1 = validation failure (strict mode)
#   2 = scenarios directory missing or empty

SPEC_NAME="${1:?Usage: validate-scenarios.sh <spec-name>}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Input validation ---
if [[ ! "$SPEC_NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]]; then
    echo "ERROR: Invalid spec name: '$SPEC_NAME'"
    exit 1
fi

# --- Config validation and sourcing ---
validate_config() {
    local config="$1"
    [[ ! -f "$config" ]] && return 0
    if grep -vE '^\s*#|^\s*$|^\s*(export\s+)?[a-zA-Z_][a-zA-Z0-9_]*=' "$config" | grep -qE '.'; then
        echo "ERROR: Config contains non-assignment lines. Only variable assignments allowed."
        echo "Review: $config"
        exit 1
    fi
}

CONFIG_FILE=".dark-factory/config.sh"
if [[ -f "$CONFIG_FILE" ]]; then
    validate_config "$CONFIG_FILE"
    # shellcheck source=/dev/null
    source "$CONFIG_FILE"
fi

# --- Config defaults ---
SCENARIO_VALIDATION="${SCENARIO_VALIDATION:-strict}"
SCENARIO_MIN_FILES="${SCENARIO_MIN_FILES:-3}"
SCENARIO_MIN_ASSERTIONS_PER_FILE="${SCENARIO_MIN_ASSERTIONS_PER_FILE:-1}"

# --- Skip if validation is off ---
if [[ "$SCENARIO_VALIDATION" == "off" ]]; then
    echo "Scenario validation: OFF (skipped)"
    exit 0
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

# --- Paths ---
SCENARIOS_DIR=".dark-factory/scenarios/${SPEC_NAME}"
REPORT_FILE=".dark-factory/reports/${SPEC_NAME}-scenario-validation.md"
mkdir -p ".dark-factory/reports"

# --- Scenarios directory check ---
if [[ ! -d "$SCENARIOS_DIR" ]]; then
    echo "ERROR: Scenarios directory not found: $SCENARIOS_DIR"
    exit 2
fi

if ! has_scenario_files "$SCENARIOS_DIR"; then
    echo "ERROR: No scenario test files found in: $SCENARIOS_DIR"
    exit 2
fi

# --- Toolchain detection (copy from quality-gate.sh) ---
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

TOOLCHAIN="${LANGUAGE:-$(detect_toolchain)}"

# --- File discovery per language ---
discover_scenario_files() {
    local dir="$1"
    case "$TOOLCHAIN" in
        node)
            find "$dir" -type f \( \
                -name '*.test.ts' -o -name '*.test.js' -o \
                -name '*.test.mts' -o -name '*.test.mjs' -o \
                -name '*.spec.ts' -o -name '*.spec.js' \
            \) 2>/dev/null
            ;;
        python)
            find "$dir" -type f \( -name 'test_*.py' -o -name '*_test.py' \) 2>/dev/null
            ;;
        go)
            # Warn if *.test.go found (Go ignores this naming convention)
            local bad_files
            bad_files=$(find "$dir" -type f -name '*.test.go' 2>/dev/null)
            if [[ -n "$bad_files" ]]; then
                echo "WARNING: Found *.test.go files (Go requires *_test.go naming):" >&2
                echo "$bad_files" >&2
            fi
            find "$dir" -type f -name '*_test.go' 2>/dev/null
            ;;
        rust)
            find "$dir" -type f -name '*.rs' 2>/dev/null
            ;;
        *)
            # Fallback: any common test file patterns
            find "$dir" -type f \( \
                -name '*.test.*' -o -name '*.spec.*' -o \
                -name 'test_*' -o -name '*_test.*' \
            \) 2>/dev/null
            ;;
    esac
}

# --- Assertion patterns per language ---
get_real_assertion_pattern() {
    case "$TOOLCHAIN" in
        node)   echo 'expect(|assert(|assert\.' ;;
        python) echo 'assert |self\.assert|pytest\.raises' ;;
        go)     echo 't\.Error|t\.Fatal|t\.Errorf|t\.Fatalf|require\.|assert\.' ;;
        rust)   echo 'assert!|assert_eq!|assert_ne!' ;;
        *)      echo 'assert|expect' ;;
    esac
}

get_trivial_assertion_pattern() {
    case "$TOOLCHAIN" in
        node)   echo 'expect\(true\)|expect\(1\)\.toBe\(1\)|assert\.ok\(true\)|assert\(true\)' ;;
        python) echo 'assert True$|assert 1$|self\.assertTrue\(True\)|self\.assertEqual\(1,\s*1\)' ;;
        go)     echo '' ;;  # Trivial assertions rare in Go; skip
        rust)   echo 'assert!\(true\)|assert_eq!\(1,\s*1\)' ;;
        *)      echo 'assert\(true\)|expect\(true\)' ;;
    esac
}

# --- Empty body detection (awk-based, multiline) ---
count_empty_bodies() {
    local file="$1"
    case "$TOOLCHAIN" in
        node)
            # Match: it( or test( followed by { then } with only whitespace between
            awk '
                /\b(it|test)\s*\(/ { in_test=1; next }
                in_test && /\{/ { in_body=1; body_empty=1; next }
                in_body && /\}/ {
                    if (body_empty) count++
                    in_test=0; in_body=0; body_empty=0; next
                }
                in_body && /[^ \t\n\r]/ { body_empty=0 }
                END { print count+0 }
            ' "$file"
            ;;
        python)
            # Match: def test_ followed by only pass/.../blank lines before next def/EOF
            awk '
                /^[[:space:]]*def test_/ { in_test=1; body_empty=1; has_body=0; next }
                in_test && /^[[:space:]]*def / {
                    if (body_empty && has_body) count++
                    in_test=1; body_empty=1; has_body=0; next
                }
                in_test && /^[[:space:]]*$/ { next }
                in_test && /^[[:space:]]*(pass|\.\.\.)[[:space:]]*$/ { has_body=1; next }
                in_test && /[^ \t\n\r]/ { body_empty=0; has_body=1 }
                END {
                    if (in_test && body_empty && has_body) count++
                    print count+0
                }
            ' "$file"
            ;;
        go)
            # Match: func Test followed by { then } with only whitespace between
            awk '
                /^func Test[A-Z]/ { in_test=1; next }
                in_test && /\{/ { in_body=1; body_empty=1; next }
                in_body && /^\}/ {
                    if (body_empty) count++
                    in_test=0; in_body=0; body_empty=0; next
                }
                in_body && /[^ \t\n\r]/ { body_empty=0 }
                END { print count+0 }
            ' "$file"
            ;;
        rust)
            # Match: fn test_ followed by { then } with only whitespace between
            awk '
                /fn test_/ { in_test=1; next }
                in_test && /\{/ { in_body=1; body_empty=1; next }
                in_body && /^\s*\}/ {
                    if (body_empty) count++
                    in_test=0; in_body=0; body_empty=0; next
                }
                in_body && /[^ \t\n\r]/ { body_empty=0 }
                END { print count+0 }
            ' "$file"
            ;;
        *)
            echo "0"
            ;;
    esac
}

# ==============================================================================
# Run validation checks
# ==============================================================================

echo "Validating scenario quality for: ${SPEC_NAME}"
echo "Toolchain: ${TOOLCHAIN}"
echo "Mode: ${SCENARIO_VALIDATION}"
echo ""

# Discover scenario files
mapfile -t SCENARIO_FILES < <(discover_scenario_files "$SCENARIOS_DIR")
FILE_COUNT=${#SCENARIO_FILES[@]}

FAILURES=0
WARNINGS=0

# Initialize report
cat > "$REPORT_FILE" <<EOF
# Scenario Quality Validation Report

**Spec:** ${SPEC_NAME}
**Date:** $(date -u +"%Y-%m-%dT%H:%M:%SZ")
**Toolchain:** ${TOOLCHAIN}
**Mode:** ${SCENARIO_VALIDATION}

## Checks

| Check | Status | Details |
|-------|--------|---------|
EOF

# --- Check 1: File count ---
echo "  [1/4] File count..."
if [[ $FILE_COUNT -lt $SCENARIO_MIN_FILES ]]; then
    echo "| File Count | FAIL | Found ${FILE_COUNT} files, minimum is ${SCENARIO_MIN_FILES} |" >> "$REPORT_FILE"
    echo "    FAIL: Found ${FILE_COUNT} scenario files (minimum: ${SCENARIO_MIN_FILES})"
    FAILURES=$((FAILURES + 1))
else
    echo "| File Count | PASS | Found ${FILE_COUNT} files (minimum: ${SCENARIO_MIN_FILES}) |" >> "$REPORT_FILE"
    echo "    PASS: ${FILE_COUNT} scenario files found"
fi

# --- Check 2: Assertion presence ---
echo "  [2/4] Assertion presence..."
ASSERTION_PATTERN=$(get_real_assertion_pattern)
FILES_WITHOUT_ASSERTIONS=()

for file in "${SCENARIO_FILES[@]}"; do
    if [[ -z "$ASSERTION_PATTERN" ]]; then
        break
    fi
    count=$(grep -cE "$ASSERTION_PATTERN" "$file" 2>/dev/null) || count=0
    if [[ $count -lt $SCENARIO_MIN_ASSERTIONS_PER_FILE ]]; then
        FILES_WITHOUT_ASSERTIONS+=("$(basename "$file")")
    fi
done

if [[ ${#FILES_WITHOUT_ASSERTIONS[@]} -gt 0 ]]; then
    echo "| Assertion Presence | FAIL | ${#FILES_WITHOUT_ASSERTIONS[@]} file(s) lack real assertions |" >> "$REPORT_FILE"
    echo "    FAIL: ${#FILES_WITHOUT_ASSERTIONS[@]} file(s) have fewer than ${SCENARIO_MIN_ASSERTIONS_PER_FILE} assertion(s):"
    for f in "${FILES_WITHOUT_ASSERTIONS[@]}"; do
        echo "      - $f"
    done
    FAILURES=$((FAILURES + 1))
else
    echo "| Assertion Presence | PASS | All files contain real assertions |" >> "$REPORT_FILE"
    echo "    PASS: All files contain real assertions"
fi

# --- Check 3: Trivial assertions ---
echo "  [3/4] Trivial assertion detection..."
TRIVIAL_PATTERN=$(get_trivial_assertion_pattern)
FILES_ALL_TRIVIAL=()

if [[ -n "$TRIVIAL_PATTERN" ]]; then
    for file in "${SCENARIO_FILES[@]}"; do
        real_count=$(grep -cE "$ASSERTION_PATTERN" "$file" 2>/dev/null) || real_count=0
        trivial_count=$(grep -cE "$TRIVIAL_PATTERN" "$file" 2>/dev/null) || trivial_count=0

        # If all assertions in the file are trivial, flag it
        if [[ $real_count -gt 0 ]] && [[ $trivial_count -ge $real_count ]]; then
            FILES_ALL_TRIVIAL+=("$(basename "$file")")
        fi
    done
fi

if [[ ${#FILES_ALL_TRIVIAL[@]} -gt 0 ]]; then
    echo "| Trivial Assertions | FAIL | ${#FILES_ALL_TRIVIAL[@]} file(s) contain only trivial assertions |" >> "$REPORT_FILE"
    echo "    FAIL: ${#FILES_ALL_TRIVIAL[@]} file(s) contain ONLY trivial assertions:"
    for f in "${FILES_ALL_TRIVIAL[@]}"; do
        echo "      - $f"
    done
    FAILURES=$((FAILURES + 1))
else
    if [[ -z "$TRIVIAL_PATTERN" ]]; then
        echo "| Trivial Assertions | SKIP | No trivial patterns defined for ${TOOLCHAIN} |" >> "$REPORT_FILE"
        echo "    SKIP: No trivial assertion patterns for ${TOOLCHAIN}"
    else
        echo "| Trivial Assertions | PASS | No files with only trivial assertions |" >> "$REPORT_FILE"
        echo "    PASS: No files with only trivial assertions"
    fi
fi

# --- Check 4: Empty test bodies ---
echo "  [4/4] Empty test body detection..."
FILES_WITH_EMPTY_BODIES=()

for file in "${SCENARIO_FILES[@]}"; do
    empty_count=$(count_empty_bodies "$file")
    if [[ $empty_count -gt 0 ]]; then
        FILES_WITH_EMPTY_BODIES+=("$(basename "$file") (${empty_count} empty)")
    fi
done

if [[ ${#FILES_WITH_EMPTY_BODIES[@]} -gt 0 ]]; then
    echo "| Empty Test Bodies | FAIL | ${#FILES_WITH_EMPTY_BODIES[@]} file(s) have empty test bodies |" >> "$REPORT_FILE"
    echo "    FAIL: ${#FILES_WITH_EMPTY_BODIES[@]} file(s) have empty test bodies:"
    for f in "${FILES_WITH_EMPTY_BODIES[@]}"; do
        echo "      - $f"
    done
    FAILURES=$((FAILURES + 1))
else
    echo "| Empty Test Bodies | PASS | No empty test bodies found |" >> "$REPORT_FILE"
    echo "    PASS: No empty test bodies found"
fi

# ==============================================================================
# Summary
# ==============================================================================

echo "" >> "$REPORT_FILE"

if [[ $FAILURES -gt 0 ]]; then
    echo "## Result: FAIL" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
    echo "${FAILURES} check(s) failed." >> "$REPORT_FILE"

    # Detail sections for failures
    if [[ ${#FILES_WITHOUT_ASSERTIONS[@]} -gt 0 ]]; then
        echo "" >> "$REPORT_FILE"
        echo "### Files Missing Assertions" >> "$REPORT_FILE"
        for f in "${FILES_WITHOUT_ASSERTIONS[@]}"; do
            echo "- ${f}" >> "$REPORT_FILE"
        done
    fi

    if [[ ${#FILES_ALL_TRIVIAL[@]} -gt 0 ]]; then
        echo "" >> "$REPORT_FILE"
        echo "### Files With Only Trivial Assertions" >> "$REPORT_FILE"
        for f in "${FILES_ALL_TRIVIAL[@]}"; do
            echo "- ${f}" >> "$REPORT_FILE"
        done
    fi

    if [[ ${#FILES_WITH_EMPTY_BODIES[@]} -gt 0 ]]; then
        echo "" >> "$REPORT_FILE"
        echo "### Files With Empty Test Bodies" >> "$REPORT_FILE"
        for f in "${FILES_WITH_EMPTY_BODIES[@]}"; do
            echo "- ${f}" >> "$REPORT_FILE"
        done
    fi

    echo ""
    echo "SCENARIO VALIDATION: FAIL (${FAILURES} check(s) failed)"
    echo "See report: ${REPORT_FILE}"

    if [[ "$SCENARIO_VALIDATION" == "strict" ]]; then
        exit 1
    else
        echo "Mode is '${SCENARIO_VALIDATION}' — continuing with warning."
        exit 0
    fi
else
    echo "## Result: PASS" >> "$REPORT_FILE"
    echo ""
    echo "SCENARIO VALIDATION: PASS"
    echo "See report: ${REPORT_FILE}"
    exit 0
fi
