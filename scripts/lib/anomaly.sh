#!/usr/bin/env bash
# scripts/lib/anomaly.sh — Anomaly detection engine (Phase 5)
#
# Provides:
#   anomaly_check_agent     — post-agent threshold check
#   anomaly_check_spec      — post-iteration spec-level check
#   anomaly_compute_baseline — rolling baseline from events.jsonl (requires jq)
#   anomaly_check_baseline  — compare current value against baseline
#   anomaly_composite_score — combine multiple weak signals
#
# Hard thresholds work without jq. Baseline detection requires jq + history.

[[ -n "${_DF_LIB_ANOMALY_LOADED:-}" ]] && return 0
_DF_LIB_ANOMALY_LOADED=1

# anomaly_check_agent <role> <turns_allocated> <wall_s>
#   Checks agent-level anomalies after agent completes.
#   Returns 1 if anomaly detected (severity=block).
anomaly_check_agent() {
    local role="$1" turns_allocated="$2" wall_s="${3:-0}"
    local max_turns="${ANOMALY_MAX_AGENT_TURNS:-150}"

    if (( turns_allocated > max_turns )); then
        if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
            emit_anomaly_detected "type=agent_turns_exceeded" "role=$role" \
                "current=$turns_allocated" "limit=$max_turns" "severity=block" || true
        fi
        return 1
    fi
    return 0
}

# anomaly_check_spec <total_turns> <total_wall_s>
#   Checks spec-level anomalies after each iteration.
#   Returns 1 if anomaly detected (severity=block).
anomaly_check_spec() {
    local total_turns="$1" total_wall_s="$2"
    local max_turns="${ANOMALY_MAX_SPEC_TURNS:-800}"
    local max_wall="${ANOMALY_MAX_SPEC_WALL_S:-7200}"
    local blocked=0

    if (( total_turns > max_turns )); then
        if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
            emit_anomaly_detected "type=spec_turns_exceeded" \
                "current=$total_turns" "limit=$max_turns" "severity=block" || true
        fi
        blocked=1
    fi
    if (( total_wall_s > max_wall )); then
        if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
            emit_anomaly_detected "type=spec_wall_exceeded" \
                "current=$total_wall_s" "limit=$max_wall" "severity=block" || true
        fi
        blocked=1
    fi
    return $blocked
}

# anomaly_compute_baseline <metric> [window]
#   Computes rolling baseline from last N spec runs in events.jsonl.
#   Prints "mean stddev" as space-separated integers.
#   Requires jq and >= 5 completed runs. Returns 1 if insufficient data.
anomaly_compute_baseline() {
    local metric="$1" window="${2:-${ANOMALY_BASELINE_WINDOW:-20}}"
    command -v jq >/dev/null 2>&1 || return 1
    [[ -n "${_DF_EVENTS_FILE:-}" ]] || return 1
    [[ -f "$_DF_EVENTS_FILE" ]] || return 1

    local result
    result=$(jq -n -r --arg metric "$metric" --argjson window "$window" '
        [inputs | select(.event == "SPEC_SHIPPED" or .event == "SPEC_FAILED")]
        | .[-$window:]
        | [.[].data[$metric] | select(. != null) | tonumber]
        | if length < 5 then "insufficient" else
            (add / length) as $mean |
            (map(. - $mean | . * .) | add / length | sqrt) as $stddev |
            "\($mean|round) \($stddev|round)"
          end
    ' "$_DF_EVENTS_FILE" 2>/dev/null)

    [[ "$result" == "insufficient" ]] && return 1
    [[ -z "$result" ]] && return 1
    echo "$result"
}

# anomaly_check_baseline <metric> <current_value>
#   Checks if current value is anomalous (> 2 stddev from mean).
#   Returns 1 if anomalous. Returns 0 (no anomaly) if baseline unavailable.
anomaly_check_baseline() {
    local metric="$1" current_value="$2"
    local baseline
    baseline=$(anomaly_compute_baseline "$metric") || return 0
    local mean="${baseline%% *}" stddev="${baseline##* }"
    local threshold=$(( mean + 2 * stddev ))

    if (( current_value > threshold )); then
        if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
            emit_anomaly_detected "type=baseline_deviation" "metric=$metric" \
                "current=$current_value" "mean=$mean" "stddev=$stddev" "severity=warn" || true
        fi
        return 1
    fi
    return 0
}

# anomaly_composite_score <turns_flag> <wall_flag> <trajectory_flag> <files_flag> <barrier_flag>
#   Combines multiple weak signals into a composite score.
#   Each flag is 0 or 1. Returns 1 if severity=block (score >= 70).
anomaly_composite_score() {
    local turns_flag="${1:-0}" wall_flag="${2:-0}" trajectory_flag="${3:-0}"
    local files_flag="${4:-0}" barrier_flag="${5:-0}"

    local score=$(( turns_flag * 30 + wall_flag * 20 + trajectory_flag * 25 + files_flag * 15 + barrier_flag * 40 ))

    if (( score >= 70 )); then
        if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
            emit_anomaly_detected "type=composite" "score=$score" "severity=block" || true
        fi
        return 1
    elif (( score >= 40 )); then
        if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
            emit_anomaly_detected "type=composite" "score=$score" "severity=escalate" || true
        fi
        if type notify_anomaly >/dev/null 2>&1; then
            notify_anomaly "${_DF_CURRENT_SPEC:-unknown}" "composite" "escalate" "score=$score" 2>/dev/null || true
        fi
    elif (( score >= 20 )); then
        if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
            emit_anomaly_detected "type=composite" "score=$score" "severity=warn" || true
        fi
    fi
    return 0
}
