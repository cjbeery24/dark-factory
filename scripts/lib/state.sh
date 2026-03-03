#!/usr/bin/env bash
# scripts/lib/state.sh — Pipeline state management
#
# Provides: write_state, read_state
# Requires global: STATE_FILE (must be set before calling)

[[ -n "${_DF_LIB_STATE_LOADED:-}" ]] && return 0
_DF_LIB_STATE_LOADED=1

# write_state <key> <value>
#   Writes/overwrites a key=value pair in STATE_FILE.
#   Creates the directory and file if needed. Uses best-effort file locking.
write_state() {
    local key="$1" value="$2"
    mkdir -p "$(dirname "$STATE_FILE")"
    local tmp="${STATE_FILE}.tmp"
    (
        flock -x 200 2>/dev/null || true  # Best-effort lock
        if [[ -f "$STATE_FILE" ]]; then
            grep -v "^${key}=" "$STATE_FILE" > "$tmp" 2>/dev/null || true
        else
            : > "$tmp"
        fi
        echo "${key}=${value}" >> "$tmp"
        mv "$tmp" "$STATE_FILE"
    ) 200>"${STATE_FILE}.lock"
}

# read_state <key> [default]
#   Reads the last value for a key from STATE_FILE.
#   Returns default (or empty) if not found.
read_state() {
    local key="$1" default="${2:-}"
    grep "^${key}=" "$STATE_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || echo "$default"
}
