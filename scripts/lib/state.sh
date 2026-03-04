#!/usr/bin/env bash
# scripts/lib/state.sh — Pipeline state management
#
# Provides: write_state, read_state, _df_acquire_lock, _df_release_lock
# Requires global: STATE_FILE (must be set before calling)

[[ -n "${_DF_LIB_STATE_LOADED:-}" ]] && return 0
_DF_LIB_STATE_LOADED=1

# ==============================================================================
# Portable locking (mkdir-based — works on macOS and Linux without flock)
# ==============================================================================

# _df_acquire_lock <lockdir> [timeout_seconds]
#   Acquires an exclusive lock using mkdir (atomic on all POSIX systems).
#   Stale lock detection via PID file + kill -0 check.
#   Returns 0 on success, 1 on timeout (falls back to no-lock).
_df_acquire_lock() {
    local lockdir="$1" timeout="${2:-10}"
    local elapsed=0

    while ! mkdir "$lockdir" 2>/dev/null; do
        # Check for stale lock
        if [[ -f "${lockdir}/pid" ]]; then
            local stale_pid
            stale_pid=$(cat "${lockdir}/pid" 2>/dev/null) || stale_pid=""
            if [[ -n "$stale_pid" ]] && ! kill -0 "$stale_pid" 2>/dev/null; then
                # PID is dead — remove stale lock
                rm -rf "$lockdir"
                continue
            fi
        fi
        if [[ $elapsed -ge $timeout ]]; then
            return 1  # Timeout — caller falls back to no-lock
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done

    echo $$ > "${lockdir}/pid"
    return 0
}

# _df_release_lock <lockdir>
#   Releases the lock by removing the lockdir.
_df_release_lock() {
    local lockdir="$1"
    rm -rf "$lockdir"
}

# write_state <key> <value>
#   Writes/overwrites a key=value pair in STATE_FILE.
#   Creates the directory and file if needed. Uses best-effort locking.
write_state() {
    local key="$1" value="$2"
    mkdir -p "$(dirname "$STATE_FILE")"
    local tmp="${STATE_FILE}.tmp"
    local lockdir="${STATE_FILE}.lockdir"

    _df_acquire_lock "$lockdir" 10 || true  # Best-effort lock

    if [[ -f "$STATE_FILE" ]]; then
        grep -v "^${key}=" "$STATE_FILE" > "$tmp" 2>/dev/null || true
    else
        : > "$tmp"
    fi
    echo "${key}=${value}" >> "$tmp"
    mv "$tmp" "$STATE_FILE"

    _df_release_lock "$lockdir"
}

# read_state <key> [default]
#   Reads the last value for a key from STATE_FILE.
#   Returns default (or empty) if not found.
read_state() {
    local key="$1" default="${2:-}" result
    result=$(grep "^${key}=" "$STATE_FILE" 2>/dev/null | tail -1 | cut -d= -f2-) || result=""
    echo "${result:-$default}"
}
