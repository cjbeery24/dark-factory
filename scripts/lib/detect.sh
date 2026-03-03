#!/usr/bin/env bash
# scripts/lib/detect.sh — Toolchain and timeout detection
#
# Provides: detect_toolchain, detect_timeout_cmd

[[ -n "${_DF_LIB_DETECT_LOADED:-}" ]] && return 0
_DF_LIB_DETECT_LOADED=1

# detect_toolchain
#   Prints the detected language based on project files in the current directory.
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

# detect_timeout_cmd
#   Sets global TIMEOUT_CMD to "timeout", "gtimeout", or "" (with warning).
detect_timeout_cmd() {
    if command -v timeout &>/dev/null; then
        TIMEOUT_CMD="timeout"
    elif command -v gtimeout &>/dev/null; then
        TIMEOUT_CMD="gtimeout"
    else
        TIMEOUT_CMD=""
        echo "WARN: timeout command not found. No execution time limits." >&2
    fi
}
