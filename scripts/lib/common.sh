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
DF_PROJECT_ROOT="$(cd "${_DF_SCRIPTS_DIR}/.." && pwd)"

# Source core modules
source "${_DF_LIB_DIR}/config.sh"
source "${_DF_LIB_DIR}/detect.sh"
source "${_DF_LIB_DIR}/scenarios.sh"
