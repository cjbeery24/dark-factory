#!/usr/bin/env bash
# scripts/lib/metrics.sh — Pipeline metrics recording and turn budget
#
# Provides:
#   metrics_record_start  — append in-progress record to metrics.jsonl
#   metrics_record_end    — append final record with wall time and outcome
#   _df_check_turn_budget — coarse cost proxy via cumulative turn allocation
#
# Requires globals: SPEC_NAME, RUN_ID (set before first call)

[[ -n "${_DF_LIB_METRICS_LOADED:-}" ]] && return 0
_DF_LIB_METRICS_LOADED=1

_METRICS_FILE=".dark-factory/metrics.jsonl"
_METRICS_START_TS=""

# metrics_record_start <spec> <run_id> <max_iters> <autonomy>
metrics_record_start() {
    local spec="$1" run_id="$2" max_iters="$3" autonomy="$4"
    mkdir -p "$(dirname "$_METRICS_FILE")"
    _METRICS_START_TS=$(date +%s)
    local ts_start
    ts_start=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    printf '{"spec":"%s","run_id":"%s","ts_start":"%s","status":"in_progress","max_iters":%s,"autonomy":%s}\n' \
        "$spec" "$run_id" "$ts_start" "$max_iters" "$autonomy" >> "$_METRICS_FILE"
}

# metrics_record_end <spec> <run_id> <outcome> <iterations> <agent_failures> [pass_rates_pipe] [satisfaction_scores_pipe]
#   pass_rates_pipe: pipe-delimited pass rates per iteration (e.g., "80|100")
#   satisfaction_scores_pipe: pipe-delimited weighted satisfaction scores (e.g., "75|90")
metrics_record_end() {
    local spec="$1" run_id="$2" outcome="$3" iterations="$4" agent_failures="$5"
    local pass_rates="${6:-}" satisfaction_scores="${7:-}"
    local ts_end wall_s
    ts_end=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    if [[ -n "$_METRICS_START_TS" ]]; then
        wall_s=$(( $(date +%s) - _METRICS_START_TS ))
    else
        wall_s=0
    fi
    printf '{"spec":"%s","run_id":"%s","ts_start":"","ts_end":"%s","wall_s":%s,"iters":%s,"outcome":"%s","autonomy":%s,"agent_failures":%s,"pass_rates":"%s","satisfaction_scores":"%s"}\n' \
        "$spec" "$run_id" "$ts_end" "$wall_s" "$iterations" "$outcome" \
        "${AUTONOMY_LEVEL:-3}" "$agent_failures" "$pass_rates" "$satisfaction_scores" >> "$_METRICS_FILE"
}

# _df_check_turn_budget <role>
#   Tracks cumulative turn allocation and aborts if budget exceeded.
#   Set MAX_TOTAL_AGENT_TURNS=0 (default) to disable.
_DF_TOTAL_TURNS_ALLOCATED=0

_df_check_turn_budget() {
    local role="$1"
    local cap="${MAX_TOTAL_AGENT_TURNS:-0}"
    [[ "$cap" -eq 0 ]] && return 0  # disabled

    local per_agent="${AGENT_MAX_TURNS:-200}"
    _DF_TOTAL_TURNS_ALLOCATED=$((_DF_TOTAL_TURNS_ALLOCATED + per_agent))

    if [[ "$_DF_TOTAL_TURNS_ALLOCATED" -gt "$cap" ]]; then
        echo "ERROR: Turn budget exceeded (${_DF_TOTAL_TURNS_ALLOCATED}/${cap}) before ${role}."
        audit_log "cost-control" "BUDGET_EXCEEDED" "cap=${cap} allocated=${_DF_TOTAL_TURNS_ALLOCATED} role=${role}"
        write_state "status" "failed"
        write_state "failure_reason" "turn budget exceeded before ${role}"
        metrics_record_end "${SPEC_NAME}" "${RUN_ID}" "budget_exceeded" "${ITERATION:-0}" "${_DF_AGENT_FAILURES:-0}" "${_DF_PASS_RATES:-}" "${_DF_SATISFACTION_SCORES:-}"
        exit 1
    fi
}
