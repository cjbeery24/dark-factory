#!/usr/bin/env bash
# scripts/lib/events.sh — Structured event emitter (Phase 5)
#
# Provides: emit_event and 23 convenience wrappers (emit_plan_start, etc.)
# Writes append-only JSONL to $_DF_EVENTS_FILE.
#
# jq path: uses jq -n for safe JSON construction.
# No-jq path: printf-based fallback with basic escaping.
#
# Requires: _DF_EVENTS_FILE must be set before calling emit_event.

[[ -n "${_DF_LIB_EVENTS_LOADED:-}" ]] && return 0
_DF_LIB_EVENTS_LOADED=1

# Detect jq availability once at source time
_DF_HAS_JQ=false
command -v jq >/dev/null 2>&1 && _DF_HAS_JQ=true

# Detect timestamp command once at source time
if command -v gdate >/dev/null 2>&1; then
    _DF_TS_CMD=gdate
elif date -u +"%Y-%m-%dT%H:%M:%S.%3NZ" 2>/dev/null | grep -qv '%3N'; then
    _DF_TS_CMD=date_ms
else
    _DF_TS_CMD=date_noms
fi

_df_timestamp() {
    case "$_DF_TS_CMD" in
        gdate)     gdate -u +"%Y-%m-%dT%H:%M:%S.%3NZ" ;;
        date_ms)   date -u +"%Y-%m-%dT%H:%M:%S.%3NZ" ;;
        date_noms) date -u +"%Y-%m-%dT%H:%M:%SZ" ;;
    esac
}

# _df_json_escape <string>
#   Escapes a string for safe interpolation into JSON values.
_df_json_escape() {
    printf '%s' "$1" | awk '{gsub(/\\/, "\\\\"); gsub(/"/, "\\\""); gsub(/\t/, "\\t"); gsub(/\r/, "\\r"); printf "%s", (NR>1 ? "\\n" : "") $0}'
}

# Track which events directory has been created (reset on path change)
_DF_EVENTS_DIR_CREATED=""

# emit_event <EVENT_NAME> [key=val ...]
#   Core emitter — all other emit_* functions call this.
#   Values are treated as strings in JSON output.
emit_event() {
    local event_name="$1"; shift
    local events_file="${_DF_EVENTS_FILE:?_DF_EVENTS_FILE must be set}"
    local events_dir
    events_dir="$(dirname "$events_file")"

    # Create directory once per unique path
    if [[ "$_DF_EVENTS_DIR_CREATED" != "$events_dir" ]]; then
        mkdir -p "$events_dir"
        _DF_EVENTS_DIR_CREATED="$events_dir"
    fi

    local ts
    ts=$(_df_timestamp)

    if $_DF_HAS_JQ; then
        local jq_args=("--arg" "ts" "$ts" "--arg" "event" "$event_name")
        local jq_filter='{ts: $ts, event: $event'

        if [[ -n "${_DF_CURRENT_SPEC:-}" ]]; then
            jq_args+=("--arg" "spec" "$_DF_CURRENT_SPEC")
            jq_filter+=', spec: $spec'
        fi
        if [[ -n "${_DF_RUN_ID:-}" ]]; then
            jq_args+=("--arg" "run_id" "$_DF_RUN_ID")
            jq_filter+=', run_id: $run_id'
        fi
        if [[ -n "${_DF_PLAN_RUN_ID:-}" ]]; then
            jq_args+=("--arg" "plan_run_id" "$_DF_PLAN_RUN_ID")
            jq_filter+=', plan_run_id: $plan_run_id'
        fi
        if [[ -n "${_DF_CURRENT_ITER:-}" ]] && [[ "$_DF_CURRENT_ITER" =~ ^[0-9]+$ ]]; then
            jq_args+=("--argjson" "iter" "$_DF_CURRENT_ITER")
            jq_filter+=', iter: $iter'
        fi

        jq_filter+=', data: {'
        local first=true
        for kv in "$@"; do
            local key="${kv%%=*}" val="${kv#*=}"
            $first || jq_filter+=','
            jq_args+=("--arg" "data_$key" "$val")
            jq_filter+=" \"$key\": \$data_$key"
            first=false
        done
        jq_filter+='}}'

        jq -n -c "${jq_args[@]}" "$jq_filter" >> "$events_file"
    else
        # Fallback: minimal JSON with escaping (no jq)
        local json="{\"ts\":\"$ts\",\"event\":\"$(_df_json_escape "$event_name")\""
        [[ -n "${_DF_CURRENT_SPEC:-}" ]] && json+=",\"spec\":\"$(_df_json_escape "$_DF_CURRENT_SPEC")\""
        [[ -n "${_DF_RUN_ID:-}" ]] && json+=",\"run_id\":\"$(_df_json_escape "$_DF_RUN_ID")\""
        [[ -n "${_DF_PLAN_RUN_ID:-}" ]] && json+=",\"plan_run_id\":\"$(_df_json_escape "$_DF_PLAN_RUN_ID")\""
        if [[ -n "${_DF_CURRENT_ITER:-}" ]] && [[ "$_DF_CURRENT_ITER" =~ ^[0-9]+$ ]]; then
            json+=",\"iter\":$_DF_CURRENT_ITER"
        fi
        json+=",\"data\":{"
        local first=true
        for kv in "$@"; do
            local key="${kv%%=*}" val="${kv#*=}"
            $first || json+=","
            json+="\"$(_df_json_escape "$key")\":\"$(_df_json_escape "$val")\""
            first=false
        done
        json+="}}"
        printf '%s\n' "$json" >> "$events_file"
    fi
}

# Convenience wrappers for all 23 event types
emit_plan_start()            { emit_event "PLAN_START" "$@"; }
emit_plan_complete()         { emit_event "PLAN_COMPLETE" "$@"; }
emit_plan_timeout()          { emit_event "PLAN_TIMEOUT" "$@"; }
emit_plan_resume()           { emit_event "PLAN_RESUME" "$@"; }
emit_spec_start()            { emit_event "SPEC_START" "$@"; }
emit_spec_dispatched()       { emit_event "SPEC_DISPATCHED" "$@"; }
emit_spec_shipped()          { emit_event "SPEC_SHIPPED" "$@"; }
emit_spec_failed()           { emit_event "SPEC_FAILED" "$@"; }
emit_spec_escalated()        { emit_event "SPEC_ESCALATED" "$@"; }
emit_agent_start()           { emit_event "AGENT_START" "$@"; }
emit_agent_completed()       { emit_event "AGENT_COMPLETED" "$@"; }
emit_agent_failed()          { emit_event "AGENT_FAILED" "$@"; }
emit_gate_completed()        { emit_event "GATE_COMPLETED" "$@"; }
emit_satisfaction_scored()   { emit_event "SATISFACTION_SCORED" "$@"; }
emit_trajectory_classified() { emit_event "TRAJECTORY_CLASSIFIED" "$@"; }
emit_regression_run()        { emit_event "REGRESSION_RUN" "$@"; }
emit_budget_exceeded()       { emit_event "BUDGET_EXCEEDED" "$@"; }
emit_budget_alert()          { emit_event "BUDGET_ALERT" "$@"; }
emit_barrier_violation()     { emit_event "BARRIER_VIOLATION" "$@"; }
emit_cost_summary()          { emit_event "COST_SUMMARY" "$@"; }
emit_anomaly_detected()      { emit_event "ANOMALY_DETECTED" "$@"; }
emit_trust_scored()          { emit_event "TRUST_SCORED" "$@"; }
emit_iter_cost()             { emit_event "ITER_COST" "$@"; }
