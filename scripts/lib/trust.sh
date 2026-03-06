#!/usr/bin/env bash
# scripts/lib/trust.sh — Trust scoring & dynamic autonomy (Phase 5)
#
# Provides:
#   trust_calculate          — compute trust score 0-100 from historical data
#   trust_to_autonomy        — map trust score to autonomy level
#   trust_predict_iterations — predict iterations needed for a spec
#
# Requires jq for historical data queries. Returns cold-start defaults without jq.

[[ -n "${_DF_LIB_TRUST_LOADED:-}" ]] && return 0
_DF_LIB_TRUST_LOADED=1

# trust_calculate <spec>
#   Returns trust score as integer 0-100.
#   Returns 50 (cold start) without jq or with insufficient history.
trust_calculate() {
    local spec="$1"
    local min_history="${TRUST_MIN_HISTORY:-5}"

    command -v jq >/dev/null 2>&1 || { echo "50"; return 0; }
    [[ -n "${_DF_EVENTS_FILE:-}" ]] || { echo "50"; return 0; }
    [[ -f "$_DF_EVENTS_FILE" ]] || { echo "50"; return 0; }

    local shipped_count
    shipped_count=$(jq -s '[.[] | select(.event == "SPEC_SHIPPED")] | length' "$_DF_EVENTS_FILE" 2>/dev/null || echo "0")
    if (( shipped_count < min_history )); then
        echo "50"
        return 0
    fi

    local first_pass_rate avg_iters regression_fail_rate complexity familiarity

    first_pass_rate=$(_trust_first_pass_rate)
    avg_iters=$(_trust_avg_iterations)
    regression_fail_rate=$(_trust_regression_fail_rate)
    complexity=$(_trust_spec_complexity "$spec")
    familiarity=$(_trust_module_familiarity "$spec")

    # Weighted combination (integer arithmetic, all inputs clamped 0-100)
    local score=$(( first_pass_rate * 30 / 100 + avg_iters * 20 / 100 + regression_fail_rate * 20 / 100 + complexity * 15 / 100 + familiarity * 15 / 100 ))

    (( score < 0 )) && score=0
    (( score > 100 )) && score=100

    echo "$score"
}

# _trust_first_pass_rate — integer 0-100
_trust_first_pass_rate() {
    jq -s '
        [.[] | select(.event == "SPEC_SHIPPED")] |
        if length == 0 then 50
        else ([.[] | select((.data.iters | tonumber) == 1)] | length) * 100 / length
        end
    ' "$_DF_EVENTS_FILE" 2>/dev/null || echo "50"
}

# _trust_avg_iterations — inverted to 0-100 (1 iter → 100, 5+ → 0)
_trust_avg_iterations() {
    local avg
    avg=$(jq -s '
        [.[] | select(.event == "SPEC_SHIPPED") | .data.iters | tonumber] |
        if length == 0 then 2
        else add / length | round
        end
    ' "$_DF_EVENTS_FILE" 2>/dev/null || echo "2")

    (( avg < 1 )) && avg=1
    (( avg > 5 )) && avg=5
    echo $(( (5 - avg) * 25 ))
}

# _trust_regression_fail_rate — inverted 0-100 (more failures = lower)
#   Returns integer via jq's round (which always produces whole numbers).
_trust_regression_fail_rate() {
    jq -s '
        [.[] | select(.event == "REGRESSION_RUN")] |
        if length == 0 then 80
        else (1 - ([.[] | select(.data.failed | tonumber > 0)] | length) / length) * 100 | round
        end
    ' "$_DF_EVENTS_FILE" 2>/dev/null || echo "80"
}

# _trust_spec_complexity <spec> — inverted 0-100 (simpler = higher)
_trust_spec_complexity() {
    local spec="$1"
    local spec_file="${DF_PROJECT_ROOT:-.}/.dark-factory/specs/${spec}.md"
    [[ -f "$spec_file" ]] || { echo "50"; return 0; }

    local target_files deps
    # Extract YAML list items between a key and the next key (or ---)
    # Uses two sed passes: first extracts the section, second stops at --- delimiter
    target_files=$(sed -n '/^target-files:/,/^[a-zA-Z_]/p' "$spec_file" 2>/dev/null \
        | sed '/^---/,$d' 2>/dev/null | grep -c '^- ' 2>/dev/null) || target_files=0
    deps=$(sed -n '/^depends-on:/,/^[a-zA-Z_]/p' "$spec_file" 2>/dev/null \
        | sed '/^---/,$d' 2>/dev/null | grep -c '^- ' 2>/dev/null) || deps=0

    local raw_complexity=$(( target_files * 10 + deps * 20 ))
    (( raw_complexity > 100 )) && raw_complexity=100
    echo $(( 100 - raw_complexity ))
}

# _trust_module_familiarity <spec> — 0-100 based on shipped history
_trust_module_familiarity() {
    local spec="$1"
    local spec_file="${DF_PROJECT_ROOT:-.}/.dark-factory/specs/${spec}.md"
    [[ -f "$spec_file" ]] || { echo "50"; return 0; }

    # Project maturity proxy: shipped spec count capped at 10
    local familiar_count
    familiar_count=$(jq -s '
        [.[] | select(.event == "SPEC_SHIPPED")] | length |
        if . == 0 then 0 elif . > 10 then 10 else . end
    ' "$_DF_EVENTS_FILE" 2>/dev/null || echo "0")

    echo $(( familiar_count * 10 ))
}

# trust_to_autonomy <score>
#   Maps trust score to effective autonomy level.
#   Note: 80+ is autonomy 3 same as 60-79; the 80 threshold is used
#   separately by gitops_auto_merge_high_trust for auto-merge decisions.
trust_to_autonomy() {
    local score="$1"
    if (( score >= 60 )); then echo "3"
    elif (( score >= 30 )); then echo "2"
    else echo "1"
    fi
}

# trust_predict_iterations <spec>
#   Predicts iterations needed based on historical data and complexity.
trust_predict_iterations() {
    local spec="$1"

    command -v jq >/dev/null 2>&1 || { echo "2"; return 0; }
    [[ -n "${_DF_EVENTS_FILE:-}" ]] || { echo "2"; return 0; }
    [[ -f "$_DF_EVENTS_FILE" ]] || { echo "2"; return 0; }

    local complexity
    complexity=$(_trust_spec_complexity "$spec")

    local predicted
    predicted=$(jq -s '
        [.[] | select(.event == "SPEC_SHIPPED") | .data.iters | tonumber] |
        if length < 3 then null
        else add / length | round | if . < 1 then 1 elif . > 5 then 5 else . end
        end
    ' "$_DF_EVENTS_FILE" 2>/dev/null)

    if [[ -z "$predicted" || "$predicted" == "null" ]]; then
        # complexity is INVERTED: 0=most complex, 100=simplest
        # More complex specs need more iterations
        if (( complexity < 30 )); then echo "3"      # complex → 3 iters
        elif (( complexity < 60 )); then echo "2"     # moderate → 2 iters
        else echo "1"                                  # simple → 1 iter
        fi
        return
    fi

    echo "$predicted"
}
