#!/usr/bin/env bash
# scripts/lib/scenarios.sh — Scenario file detection and spec validation
#
# Provides: has_scenario_files, detect_scenario_formats, validate_scenario_files,
#           validate_spec_result

[[ -n "${_DF_LIB_SCENARIOS_LOADED:-}" ]] && return 0
_DF_LIB_SCENARIOS_LOADED=1

# ==============================================================================
# Format pattern constants
#
# STRUCTURAL CONSTRAINT: Each array must contain only flat -name/-iname predicates
# joined by -o.  They are spliced bare into `find \( ... \)` groups; any grouping
# operators (\(, \), -prune, etc.) inside an array will break the outer expression.
# ==============================================================================

# Executable test files (pytest / jest / go / rust).
# NOTE: *.rs is intentionally broad — Rust tests live in the same file as source.
# Callers MUST pass the scenarios/ subdirectory, never the full source tree.
_DF_FORMAT_PATTERNS_EXECUTABLE=( -name '*.test.*' -o -name '*.spec.*' -o -name 'test_*.py' -o -name '*_test.py' -o -name '*_test.go' -o -name '*.rs' )
# HTTP behavioral scenarios (Format A)
_DF_FORMAT_PATTERNS_HTTP=( -name '*.scenarios.md' )
# Browser journey scenarios (Format B)
_DF_FORMAT_PATTERNS_JOURNEY=( -name '*.journey.md' )

# Sentinel filename constant (must match darkfactory/scenarios/detection.py).
_DF_SENTINEL_FILENAME="SCENARIOS-NOT-APPLICABLE.md"

# ==============================================================================
# Detection helpers
# ==============================================================================

# has_sentinel_file <dir>
#   Returns 0 if the directory contains a SCENARIOS-NOT-APPLICABLE.md sentinel.
has_sentinel_file() {
    [[ -f "${1}/${_DF_SENTINEL_FILENAME}" ]]
}

# has_scenario_files <dir>
#   Returns 0 if the directory contains any recognized test or scenario files.
has_scenario_files() {
    local dir="$1"
    [[ -n "$(find "$dir" -type f \( \
        "${_DF_FORMAT_PATTERNS_EXECUTABLE[@]}" -o \
        "${_DF_FORMAT_PATTERNS_HTTP[@]}" -o \
        "${_DF_FORMAT_PATTERNS_JOURNEY[@]}" \
    \) -print -quit 2>/dev/null)" ]]
}

# detect_scenario_formats <dir>
#   Sets globals: _DF_HAS_EXECUTABLE, _DF_HAS_HTTP, _DF_HAS_JOURNEY (true/false)
detect_scenario_formats() {
    local dir="$1"
    _DF_HAS_EXECUTABLE=false
    _DF_HAS_HTTP=false
    _DF_HAS_JOURNEY=false
    [[ -n "$(find "$dir" -type f \( "${_DF_FORMAT_PATTERNS_EXECUTABLE[@]}" \) -print -quit 2>/dev/null)" ]] && _DF_HAS_EXECUTABLE=true
    [[ -n "$(find "$dir" -type f \( "${_DF_FORMAT_PATTERNS_HTTP[@]}" \) -print -quit 2>/dev/null)" ]] && _DF_HAS_HTTP=true
    [[ -n "$(find "$dir" -type f \( "${_DF_FORMAT_PATTERNS_JOURNEY[@]}" \) -print -quit 2>/dev/null)" ]] && _DF_HAS_JOURNEY=true
}

# validate_scenario_files <dir>
#   Validates markdown scenario files only (Format A: *.scenarios.md,
#   Format B: *.journey.md).  Executable test files (Format C/D) are NOT
#   checked — section headers are meaningless in pytest/jest files.
#   Warns if a file has no ## section headers; hard-errors on empty files.
#   Returns 0 on success, 1 if any empty files are found.
validate_scenario_files() {
    local dir="$1"
    local rc=0

    while IFS= read -r f; do
        if [[ ! -s "$f" ]]; then
            echo "ERROR: Scenario file is empty: $f"
            rc=1
            continue
        fi
        # Warn if no section headers found (## lines)
        if ! grep -q '^##' "$f" 2>/dev/null; then
            echo "WARNING: Scenario file has no section headers (## ...): $f"
        fi
    done < <(find "$dir" -type f \( \
        "${_DF_FORMAT_PATTERNS_HTTP[@]}" -o \
        "${_DF_FORMAT_PATTERNS_JOURNEY[@]}" \
    \) 2>/dev/null | sort)

    return $rc
}

# validate_spec_result
#   Checks the spec validation status/report for the current SPEC_NAME.
#   Requires global: SPEC_NAME
#   Returns 0 on PASS with score 100, 1 otherwise.
validate_spec_result() {
    local status_file=".dark-factory/reviews/${SPEC_NAME}-validation.status"
    local report_file=".dark-factory/reviews/${SPEC_NAME}-validation.md"
    local verdict="" score="" divergence=""

    # Primary: parse .status file (key=value)
    if [[ -f "$status_file" ]]; then
        verdict=$(grep '^verdict=' "$status_file" | cut -d= -f2-)
        score=$(grep '^score=' "$status_file" | cut -d= -f2-)
        divergence=$(grep '^divergence=' "$status_file" | cut -d= -f2-)
    fi

    # Fallback: parse markdown report for "Score: XX/100" pattern
    if [[ -z "$score" ]] && [[ -f "$report_file" ]]; then
        score=$(grep -oE '[0-9]+/100' "$report_file" | head -1 | cut -d/ -f1) || score=""
        if grep -q 'REJECT\|REVISE' "$report_file"; then
            verdict="REJECT"
        else
            verdict="PASS"
        fi
        echo "WARNING: No .status file found. Parsed score from markdown: ${score:-unknown}"
    fi

    # Fallback: parse markdown report for divergence rating
    if [[ -z "$divergence" ]] && [[ -f "$report_file" ]]; then
        divergence=$(grep -i 'Divergence Rating:' "$report_file" | grep -oE '(LOW|MEDIUM|HIGH)' | head -1) || divergence=""
    fi

    # Enforce hard gate: score
    if [[ -z "$score" ]]; then
        echo "ERROR: Could not determine validation score. Re-run spec validation."
        return 1
    fi
    if [[ "$verdict" != "PASS" ]]; then
        echo "SPEC VALIDATION FAILED: Verdict is $verdict."
        return 1
    fi
    if [[ "$score" -lt 100 ]]; then
        echo "SPEC VALIDATION FAILED: Score is ${score}/100 (must be 100)."
        return 1
    fi

    # Enforce hard gate: divergence
    if [[ -z "$divergence" ]]; then
        echo "SPEC VALIDATION FAILED: Missing divergence rating. Re-run spec validation with the current spec-validator agent definition."
        return 1
    fi
    divergence=$(echo "$divergence" | tr '[:lower:]' '[:upper:]')
    if [[ "$divergence" == "HIGH" ]]; then
        echo "SPEC VALIDATION FAILED: Divergence is HIGH — spec permits contradictory implementations. Revise the spec to eliminate ambiguity."
        return 1
    fi

    echo "Spec validated: PASS (${score}/100, divergence=${divergence})"
}
