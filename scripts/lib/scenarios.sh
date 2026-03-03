#!/usr/bin/env bash
# scripts/lib/scenarios.sh — Scenario file detection and spec validation
#
# Provides: has_scenario_files, validate_spec_result

[[ -n "${_DF_LIB_SCENARIOS_LOADED:-}" ]] && return 0
_DF_LIB_SCENARIOS_LOADED=1

# has_scenario_files <dir>
#   Returns 0 if the directory contains recognized test files.
#   Uses find with specific patterns instead of ls -A (which counts dotfiles).
has_scenario_files() {
    local dir="$1"
    [[ -n "$(find "$dir" -type f \( \
        -name '*.test.*' -o -name '*.spec.*' -o \
        -name 'test_*.py' -o -name '*_test.py' -o \
        -name '*_test.go' -o -name '*.rs' \
    \) -print -quit 2>/dev/null)" ]]
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
