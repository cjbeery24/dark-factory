#!/usr/bin/env bash
# scripts/lib/common.sh — Dark Factory library bootstrap
#
# Sources core modules used by all scripts.
# Docker-specific modules (docker.sh, barriers.sh, tui.sh) are NOT sourced here;
# they must be sourced explicitly by scripts that need them.
#
# Usage:
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "${SCRIPT_DIR}/lib/common.sh"

[[ -n "${_DF_LIB_LOADED:-}" ]] && return 0
_DF_LIB_LOADED=1

# Resolve paths relative to this file
_DF_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_DF_SCRIPTS_DIR="$(cd "${_DF_LIB_DIR}/.." && pwd)"

# Source modules required by quality-gate.sh (the only remaining consumer).
# All other lib modules have been replaced by Python equivalents in darkfactory/.
source "${_DF_LIB_DIR}/config.sh"
source "${_DF_LIB_DIR}/detect.sh"
source "${_DF_LIB_DIR}/scenarios.sh"
source "${_DF_LIB_DIR}/satisfaction.sh"

# portable_sha256 <file>
#   Prints the SHA-256 hash of a file. Works on macOS (shasum) and Linux (sha256sum).
portable_sha256() {
    local file="$1"
    if command -v sha256sum &>/dev/null; then
        sha256sum "$file" | cut -d' ' -f1
    elif command -v shasum &>/dev/null; then
        shasum -a 256 "$file" | cut -d' ' -f1
    else
        echo "ERROR: No sha256 tool found (need sha256sum or shasum)" >&2
        return 1
    fi
}
