#!/usr/bin/env bash
# scripts/lib/config.sh — Config validation and loading
#
# Provides: validate_config, validate_config_values_raw, validate_config_metacharacters,
#           validate_positive_integers, load_config, validate_spec_name
#
# All functions use return (not exit) for composability.

[[ -n "${_DF_LIB_CONFIG_LOADED:-}" ]] && return 0
_DF_LIB_CONFIG_LOADED=1

# validate_config <file>
#   Returns 0 if file is missing or contains only comments, blanks, and assignments.
#   Returns 1 and prints error if file contains non-assignment lines.
validate_config() {
    local config="$1"
    [[ ! -f "$config" ]] && return 0
    # Allow: comments, blank lines, variable assignments (with optional export)
    if grep -vE '^\s*#|^\s*$|^\s*(export\s+)?[a-zA-Z_][a-zA-Z0-9_]*=' "$config" | grep -qE '.'; then
        echo "ERROR: Config contains non-assignment lines. Only variable assignments allowed."
        echo "Review: $config"
        return 1
    fi
    return 0
}

# validate_config_metacharacters <var1> [var2 ...]
#   Checks that the named variables (already sourced) don't contain $, `, or (.
#   Returns 1 on first violation.
validate_config_metacharacters() {
    local var val
    for var in "$@"; do
        val="${!var:-}"
        if [[ "$val" =~ [\$\`\(] ]]; then
            echo "ERROR: Config variable $var contains shell metacharacters: $val"
            return 1
        fi
    done
    return 0
}

# validate_positive_integers <var1> [var2 ...]
#   Checks that each non-empty variable value is a positive integer (>= 1).
#   Returns 1 on first violation.
validate_positive_integers() {
    local var val
    for var in "$@"; do
        val="${!var:-}"
        if [[ -n "$val" ]] && [[ ! "$val" =~ ^[1-9][0-9]*$ ]]; then
            echo "ERROR: Config variable $var must be a positive integer, got: '$val'"
            return 1
        fi
    done
    return 0
}

# validate_config_values_raw <file>
#   Reads config file as raw text and checks each assignment's RHS for shell
#   metacharacters ($, `, () BEFORE the file is sourced.  This catches injection
#   attempts that validate_config (structural check) misses because backticks and
#   $() are valid inside a quoted RHS.
#   Returns 0 if clean, 1 on first violation.
validate_config_values_raw() {
    local file="$1"
    [[ ! -f "$file" ]] && return 0
    local line_num=0
    while IFS= read -r line || [[ -n "$line" ]]; do
        line_num=$((line_num + 1))
        # Skip comments and blank lines
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" =~ ^[[:space:]]*$ ]] && continue
        # Extract variable name and RHS (everything after the first =)
        if [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([a-zA-Z_][a-zA-Z0-9_]*)=(.*) ]]; then
            local var="${BASH_REMATCH[2]}"
            local rhs="${BASH_REMATCH[3]}"
            if [[ "$rhs" =~ [\$\`] ]] || [[ "$rhs" =~ \$\( ]]; then
                echo "ERROR: Config line ${line_num}: variable ${var} contains shell metacharacters in raw value"
                echo "  Line: $line"
                return 1
            fi
        fi
    done < "$file"
    return 0
}

# load_config <file> <metachar_vars> <integer_vars>
#   Convenience wrapper: validate structure, pre-source raw check, source, check metacharacters, check integers.
#   Exits 1 on any failure (gate behavior).
#   metachar_vars and integer_vars are space-separated variable name lists.
load_config() {
    local file="$1"
    local metachar_vars="$2"
    local integer_vars="${3:-}"

    if [[ -f "$file" ]]; then
        validate_config "$file" || exit 1
        validate_config_values_raw "$file" || exit 1
        # shellcheck source=/dev/null
        source "$file"

        if [[ -n "$metachar_vars" ]]; then
            # shellcheck disable=SC2086
            validate_config_metacharacters $metachar_vars || exit 1
        fi
        if [[ -n "$integer_vars" ]]; then
            # shellcheck disable=SC2086
            validate_positive_integers $integer_vars || exit 1
        fi
    fi
}

# validate_spec_name <name>
#   Returns 0 if valid, 1 if invalid (with error message).
validate_spec_name() {
    local name="$1"
    if [[ ! "$name" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]]; then
        echo "ERROR: Invalid spec name: '$name'"
        echo "Spec names must start with alphanumeric and contain only alphanumerics, dots, hyphens, underscores."
        return 1
    fi
    return 0
}
