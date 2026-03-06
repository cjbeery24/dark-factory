#!/usr/bin/env bash
# scripts/lib/cost.sh — Cost estimation, attribution & budget enforcement (Phase 5)
#
# Provides:
#   cost_estimate_from_turns  — estimate USD from turn count
#   cost_check_budget         — check per-spec USD budget, emit alerts
#   cost_spec_summary         — emit COST_SUMMARY event at spec completion
#
# Uses awk for all float arithmetic (POSIX-mandated, available everywhere).
# Does NOT require bc.

[[ -n "${_DF_LIB_COST_LOADED:-}" ]] && return 0
_DF_LIB_COST_LOADED=1

# cost_estimate_from_turns <turns>
#   Returns estimated USD as a decimal string (e.g., "3.00").
cost_estimate_from_turns() {
    local turns="$1"
    local input_rate="${COST_PER_INPUT_TOKEN:-0.000015}"
    local output_rate="${COST_PER_OUTPUT_TOKEN:-0.000075}"
    local est_input="${COST_EST_INPUT_TOKENS_PER_TURN:-4000}"
    local est_output="${COST_EST_OUTPUT_TOKENS_PER_TURN:-2000}"

    awk -v t="$turns" -v ir="$input_rate" -v or="$output_rate" \
        -v ei="$est_input" -v eo="$est_output" \
        'BEGIN { printf "%.2f", t * ei * ir + t * eo * or }'
}

# _cost_float_gt <a> <b>
#   Returns 0 if a > b, 1 otherwise. Uses awk for float comparison.
_cost_float_gt() {
    awk -v a="$1" -v b="$2" 'BEGIN { exit (a > b) ? 0 : 1 }'
}

# Per-threshold alert tracking (prevents duplicate alerts)
declare -gA _DF_COST_ALERTED=()

# cost_check_budget
#   Checks per-spec USD budget. Emits BUDGET_ALERT at thresholds.
#   Returns 1 if budget exceeded (caller should abort spec).
cost_check_budget() {
    local budget="${COST_BUDGET_USD:-0}"
    ! _cost_float_gt "$budget" "0" && return 0  # unlimited (budget <= 0)

    local estimated
    estimated=$(cost_estimate_from_turns "${_DF_SPEC_TOTAL_TURNS:-0}")

    # Check alert thresholds
    local _saved_ifs="$IFS"
    IFS=','
    for threshold in ${COST_ALERT_THRESHOLDS:-50,80,100}; do
        local limit_at_threshold
        limit_at_threshold=$(awk -v b="$budget" -v t="$threshold" 'BEGIN { printf "%.2f", b * t / 100 }')
        if _cost_float_gt "$estimated" "$limit_at_threshold"; then
            # Only alert once per threshold
            if [[ -z "${_DF_COST_ALERTED[$threshold]:-}" ]]; then
                _DF_COST_ALERTED[$threshold]=1
                if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
                    emit_budget_alert "percent=$threshold" "estimated_usd=$estimated" "limit_usd=$budget" || true
                fi
                if type notify_budget_alert >/dev/null 2>&1; then
                    notify_budget_alert "${_DF_CURRENT_SPEC:-unknown}" "$threshold" "$estimated" "$budget" 2>/dev/null || true
                fi
            fi
        fi
    done
    IFS="$_saved_ifs"

    # Hard cap: abort if over budget
    if _cost_float_gt "$estimated" "$budget"; then
        if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
            emit_budget_exceeded "allocated_turns=${_DF_SPEC_TOTAL_TURNS:-0}" \
                "estimated_usd=$estimated" "limit_usd=$budget" "spec=${_DF_CURRENT_SPEC:-unknown}" || true
        fi
        return 1
    fi
    return 0
}

# cost_spec_summary
#   Emits a COST_SUMMARY event at spec completion.
#   With jq: includes by-agent breakdown from events.jsonl.
#   Without jq: uses in-memory cumulative counter.
cost_spec_summary() {
    [[ -n "${_DF_EVENTS_FILE:-}" ]] || return 0

    local total_turns="${_DF_SPEC_TOTAL_TURNS:-0}"
    local wall_s=0
    [[ -n "${_METRICS_START_TS:-}" ]] && wall_s=$(( $(date +%s) - _METRICS_START_TS ))
    local estimated_usd
    estimated_usd=$(cost_estimate_from_turns "$total_turns")

    emit_cost_summary "total_turns=$total_turns" "total_wall_s=$wall_s" \
        "estimated_usd=$estimated_usd" || true
}
