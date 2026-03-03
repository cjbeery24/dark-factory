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

# audit_summary
#   Prints summary of suspected violations from the audit log.
audit_summary() {
    local v
    v=$(grep -c "VIOLATION_SUSPECTED" "$AUDIT_LOG" 2>/dev/null) || v=0
    echo ""
    echo "--- Audit: ${v} suspected violation(s) ---"
    if [[ "$v" -gt 0 ]]; then
        grep "VIOLATION_SUSPECTED" "$AUDIT_LOG"
    fi
    echo "Full log: $AUDIT_LOG"
}
