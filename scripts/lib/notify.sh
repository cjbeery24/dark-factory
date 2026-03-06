#!/usr/bin/env bash
# scripts/lib/notify.sh — Pipeline event notifications
#
# Provides: notify_shipped, notify_escalated, notify_failed,
#           notify_pr_created, notify_regression_blocked
# Requires: curl
#
# All functions are fire-and-forget (never fail the pipeline).
# Uses: NOTIFY_SLACK_WEBHOOK_URL, NOTIFY_WEBHOOK_URL from config.

[[ -n "${_DF_LIB_NOTIFY_LOADED:-}" ]] && return 0
_DF_LIB_NOTIFY_LOADED=1

# _notify_json_escape <string>
#   Escapes a string for safe inclusion in JSON values.
_notify_json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"     # backslashes first
    s="${s//\"/\\\"}"     # double quotes
    s="${s//$'\n'/\\n}"   # newlines
    s="${s//$'\r'/\\r}"   # carriage returns
    s="${s//$'\t'/\\t}"   # tabs
    printf '%s' "$s"
}

# _notify_slack <color> <title> <fields_json>
#   Sends a Slack incoming webhook message. Silent on failure.
_notify_slack() {
    local color="$1" title="$2" fields="$3"
    [[ -z "${NOTIFY_SLACK_WEBHOOK_URL:-}" ]] && return 0

    local payload
    payload=$(cat <<EOF
{"attachments":[{"color":"${color}","title":"$(_notify_json_escape "$title")","fields":${fields}}]}
EOF
    )

    curl -sf --max-time 10 --connect-timeout 5 -X POST -H 'Content-Type: application/json' \
        -d "$payload" "$NOTIFY_SLACK_WEBHOOK_URL" \
        >/dev/null 2>&1 || true
}

# _notify_webhook <event> <data_json>
#   Sends a generic JSON POST to NOTIFY_WEBHOOK_URL. Silent on failure.
_notify_webhook() {
    local event="$1" data="$2"
    [[ -z "${NOTIFY_WEBHOOK_URL:-}" ]] && return 0

    local payload
    payload="{\"event\":\"$(_notify_json_escape "$event")\",\"data\":${data}}"

    curl -sf --max-time 10 --connect-timeout 5 -X POST -H 'Content-Type: application/json' \
        -d "$payload" "$NOTIFY_WEBHOOK_URL" \
        >/dev/null 2>&1 || true
}

# notify_shipped <spec_name> <run_id> <iterations> [pr_url]
notify_shipped() {
    local spec="$1" run_id="$2" iters="$3" pr_url="${4:-}"

    local _pr_field=""
    [[ -n "$pr_url" ]] && _pr_field=",{\"title\":\"PR\",\"value\":\"$(_notify_json_escape "$pr_url")\",\"short\":false}"

    _notify_slack "good" "SHIPPED: ${spec}" \
        "[{\"title\":\"Spec\",\"value\":\"$(_notify_json_escape "$spec")\",\"short\":true},{\"title\":\"Iterations\",\"value\":\"${iters}\",\"short\":true}${_pr_field}]"

    local _data
    _data="{\"spec\":\"$(_notify_json_escape "$spec")\",\"run_id\":\"$(_notify_json_escape "$run_id")\",\"iterations\":${iters}"
    [[ -n "$pr_url" ]] && _data+=",\"pr_url\":\"$(_notify_json_escape "$pr_url")\""
    _data+="}"
    _notify_webhook "shipped" "$_data"
}

# notify_escalated <spec_name> <run_id> <iterations> <reason>
notify_escalated() {
    local spec="$1" run_id="$2" iters="$3" reason="$4"

    _notify_slack "danger" "ESCALATED: ${spec}" \
        "[{\"title\":\"Spec\",\"value\":\"$(_notify_json_escape "$spec")\",\"short\":true},{\"title\":\"Iterations\",\"value\":\"${iters}\",\"short\":true},{\"title\":\"Reason\",\"value\":\"$(_notify_json_escape "$reason")\",\"short\":false}]"

    _notify_webhook "escalated" \
        "{\"spec\":\"$(_notify_json_escape "$spec")\",\"run_id\":\"$(_notify_json_escape "$run_id")\",\"iterations\":${iters},\"reason\":\"$(_notify_json_escape "$reason")\"}"
}

# notify_failed <spec_name> <run_id> <reason>
notify_failed() {
    local spec="$1" run_id="$2" reason="$3"

    _notify_slack "danger" "FAILED: ${spec}" \
        "[{\"title\":\"Spec\",\"value\":\"$(_notify_json_escape "$spec")\",\"short\":true},{\"title\":\"Reason\",\"value\":\"$(_notify_json_escape "$reason")\",\"short\":false}]"

    _notify_webhook "failed" \
        "{\"spec\":\"$(_notify_json_escape "$spec")\",\"run_id\":\"$(_notify_json_escape "$run_id")\",\"reason\":\"$(_notify_json_escape "$reason")\"}"
}

# notify_pr_created <spec_name> <pr_url>
notify_pr_created() {
    local spec="$1" pr_url="$2"

    _notify_slack "#439FE0" "PR Created: ${spec}" \
        "[{\"title\":\"Spec\",\"value\":\"$(_notify_json_escape "$spec")\",\"short\":true},{\"title\":\"PR\",\"value\":\"$(_notify_json_escape "$pr_url")\",\"short\":false}]"

    _notify_webhook "pr_created" \
        "{\"spec\":\"$(_notify_json_escape "$spec")\",\"pr_url\":\"$(_notify_json_escape "$pr_url")\"}"
}

# notify_budget_alert <spec_name> <percent> <estimated_usd> <budget_usd>
notify_budget_alert() {
    local spec="$1" percent="$2" estimated="$3" budget="$4"

    _notify_slack "warning" "BUDGET ALERT: ${spec} (${percent}%)" \
        "[{\"title\":\"Spec\",\"value\":\"$(_notify_json_escape "$spec")\",\"short\":true},{\"title\":\"Estimated\",\"value\":\"\$${estimated} / \$${budget}\",\"short\":true},{\"title\":\"Threshold\",\"value\":\"${percent}%\",\"short\":true}]"

    _notify_webhook "budget_alert" \
        "{\"spec\":\"$(_notify_json_escape "$spec")\",\"percent\":${percent},\"estimated_usd\":\"${estimated}\",\"budget_usd\":\"${budget}\"}"
}

# notify_anomaly <spec_name> <type> <severity> <detail>
notify_anomaly() {
    local spec="$1" type="$2" severity="$3" detail="$4"

    _notify_slack "danger" "ANOMALY (${severity}): ${spec}" \
        "[{\"title\":\"Spec\",\"value\":\"$(_notify_json_escape "$spec")\",\"short\":true},{\"title\":\"Type\",\"value\":\"$(_notify_json_escape "$type")\",\"short\":true},{\"title\":\"Detail\",\"value\":\"$(_notify_json_escape "$detail")\",\"short\":false}]"

    _notify_webhook "anomaly_detected" \
        "{\"spec\":\"$(_notify_json_escape "$spec")\",\"type\":\"$(_notify_json_escape "$type")\",\"severity\":\"$(_notify_json_escape "$severity")\",\"detail\":\"$(_notify_json_escape "$detail")\"}"
}

# notify_trust_change <spec_name> <old_score> <new_score> <reason>
notify_trust_change() {
    local spec="$1" old_score="$2" new_score="$3" reason="$4"

    _notify_slack "#439FE0" "TRUST CHANGE: ${spec}" \
        "[{\"title\":\"Spec\",\"value\":\"$(_notify_json_escape "$spec")\",\"short\":true},{\"title\":\"Score\",\"value\":\"${old_score} → ${new_score}\",\"short\":true},{\"title\":\"Reason\",\"value\":\"$(_notify_json_escape "$reason")\",\"short\":false}]"

    _notify_webhook "trust_changed" \
        "{\"spec\":\"$(_notify_json_escape "$spec")\",\"old_score\":${old_score},\"new_score\":${new_score},\"reason\":\"$(_notify_json_escape "$reason")\"}"
}

# notify_regression_blocked <spec_name> <failed_specs>
notify_regression_blocked() {
    local spec="$1" failed="$2"

    _notify_slack "danger" "REGRESSION BLOCKED: ${spec}" \
        "[{\"title\":\"Spec\",\"value\":\"$(_notify_json_escape "$spec")\",\"short\":true},{\"title\":\"Failed Specs\",\"value\":\"$(_notify_json_escape "$failed")\",\"short\":false}]"

    _notify_webhook "regression_blocked" \
        "{\"spec\":\"$(_notify_json_escape "$spec")\",\"failed_specs\":\"$(_notify_json_escape "$failed")\"}"
}
