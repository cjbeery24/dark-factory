#!/usr/bin/env bash
# scripts/lib/audit.sh — Audit logging
#
# Provides: audit_log, audit_check_output, audit_summary
# Requires global: AUDIT_LOG (must be set before calling)

[[ -n "${_DF_LIB_AUDIT_LOADED:-}" ]] && return 0
_DF_LIB_AUDIT_LOADED=1

# audit_log <agent> <event> [details]
audit_log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [$1] [$2] ${3:-}" >> "$AUDIT_LOG"
}

# audit_check_output <agent> <output> <pattern>
#   Checks agent output for restricted path patterns.
audit_check_output() {
    local agent="$1" output="$2" pattern="$3"
    local hits
    hits=$(echo "$output" | grep -c "$pattern" 2>/dev/null) || hits=0
    if [[ "$hits" -gt 0 ]]; then
        audit_log "$agent" "VIOLATION_SUSPECTED" \
            "Output mentions restricted pattern '$pattern' ($hits times). Manual review recommended."
        echo "WARNING: $agent output references '$pattern' ($hits times) — check audit log"
    fi
}

# validate_oracle_feedback <feedback_content>
#   Checks Oracle feedback for semantic leakage indicators.
#   Warns and logs but does NOT block the pipeline (detect-only).
validate_oracle_feedback() {
    local content="$1"
    local leaks=0

    # Scenario filename patterns (low false-positive)
    if printf '%s\n' "$content" | grep -qEi 'scenario[_-][0-9]+|\.test\.(js|ts|py|go|rs)|\.spec\.(js|ts|py|go|rs)|test_[a-z_]+\.py|[a-z_]+_test\.go'; then
        audit_log "oracle-feedback" "SEMANTIC_LEAKAGE_SUSPECTED" "category=scenario_filename"
        leaks=$((leaks + 1))
    fi

    # Test framework function-call patterns (anchored to parens to avoid prose false-positives)
    if printf '%s\n' "$content" | grep -qE 'expect\(|\.toBe\(|\.toEqual\(|\.toThrow\(|assertEqual\(|assertRaises\(|assert_eq!\('; then
        audit_log "oracle-feedback" "SEMANTIC_LEAKAGE_SUSPECTED" "category=test_assertion_call"
        leaks=$((leaks + 1))
    fi

    # Test DSL patterns (anchored to parens/quotes)
    if printf '%s\n' "$content" | grep -qE "it\(['\"]|describe\(['\"]|test\(['\"]"; then
        audit_log "oracle-feedback" "SEMANTIC_LEAKAGE_SUSPECTED" "category=test_dsl_pattern"
        leaks=$((leaks + 1))
    fi

    # Raw test output fragments (specific format strings)
    if printf '%s\n' "$content" | grep -qEi 'Expected:.*Received:|AssertionError|FAILED.*assert|pytest.*FAILED'; then
        audit_log "oracle-feedback" "SEMANTIC_LEAKAGE_SUSPECTED" "category=raw_test_output"
        leaks=$((leaks + 1))
    fi

    # Test runner stack traces
    if printf '%s\n' "$content" | grep -qE 'at Object\.<anonymous>|at Test\.|File ".*test.*\.py", line|_test\.go:[0-9]+'; then
        audit_log "oracle-feedback" "SEMANTIC_LEAKAGE_SUSPECTED" "category=test_stack_trace"
        leaks=$((leaks + 1))
    fi

    # Layer-specific leakage (category names from trajectory analysis)
    # Catches: "Layer 01", "contract layer", "contract scenarios", "edge case suite", etc.
    # Note: "security" is intentionally narrower (layer|scenarios|suite only) to avoid
    # false-positives on implementation-focused feedback like "security check logic".
    if printf '%s\n' "$content" | grep -qEi \
        'Layer.?0[1-9]|(contract|happy.?path|edge.?case|error.?condition|state.?sequence|property.?based|adversarial).?(layer|test|scenario|check|suite|categor)|security.?(layer|scenarios|suite|categor)'; then
        audit_log "oracle-feedback" "SEMANTIC_LEAKAGE_SUSPECTED" "category=layer_reference"
        leaks=$((leaks + 1))
    fi

    if [[ $leaks -gt 0 ]]; then
        echo "WARNING: Oracle feedback contains $leaks possible semantic leakage indicator(s). Review audit log."
    fi
    return 0
}

# audit_summary
#   Prints summary of suspected violations from the audit log.
audit_summary() {
    local v s
    v=$(grep -c "VIOLATION_SUSPECTED" "$AUDIT_LOG" 2>/dev/null) || v=0
    s=$(grep -c "SEMANTIC_LEAKAGE_SUSPECTED" "$AUDIT_LOG" 2>/dev/null) || s=0
    echo ""
    echo "--- Audit: ${v} violation(s), ${s} semantic leakage indicator(s) ---"
    if [[ "$v" -gt 0 ]]; then
        grep "VIOLATION_SUSPECTED" "$AUDIT_LOG"
    fi
    if [[ "$s" -gt 0 ]]; then
        grep "SEMANTIC_LEAKAGE_SUSPECTED" "$AUDIT_LOG"
    fi
    echo "Full log: $AUDIT_LOG"
}
