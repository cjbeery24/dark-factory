#!/usr/bin/env bash
set -euo pipefail

# scripts/df-dashboard.sh — CLI summary dashboard (Phase 5, F1)
#
# Reads events.jsonl and prints a formatted summary.
# Requires jq.
#
# Usage: bash scripts/df-dashboard.sh [--last N] [--spec SPEC] [--plan PLAN]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ==============================================================================
# Argument parsing
# ==============================================================================

LAST_N=50
FILTER_SPEC=""
FILTER_PLAN=""
EVENTS_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --last)   LAST_N="$2"; shift 2 ;;
        --spec)   FILTER_SPEC="$2"; shift 2 ;;
        --plan)   FILTER_PLAN="$2"; shift 2 ;;
        --events) EVENTS_FILE="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: df-dashboard.sh [--last N] [--spec SPEC] [--plan PLAN] [--events FILE]"
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# Locate events file
if [[ -z "$EVENTS_FILE" ]]; then
    EVENTS_FILE="${_DF_EVENTS_FILE:-}"
    [[ -z "$EVENTS_FILE" ]] && EVENTS_FILE=".dark-factory/events.jsonl"
fi

if ! command -v jq >/dev/null 2>&1; then
    echo "ERROR: jq is required for the dashboard. Install it: https://jqlang.github.io/jq/" >&2
    exit 1
fi

if [[ ! -f "$EVENTS_FILE" ]]; then
    echo "ERROR: Events file not found: $EVENTS_FILE" >&2
    echo "  Run the dark factory pipeline first to generate events." >&2
    exit 1
fi

# ==============================================================================
# Color support (respects NO_COLOR)
# ==============================================================================

if [[ -z "${NO_COLOR:-}" ]] && [[ -t 1 ]]; then
    BOLD="\033[1m"
    DIM="\033[2m"
    GREEN="\033[32m"
    YELLOW="\033[33m"
    RED="\033[31m"
    CYAN="\033[36m"
    RESET="\033[0m"
else
    BOLD="" DIM="" GREEN="" YELLOW="" RED="" CYAN="" RESET=""
fi

# ==============================================================================
# Data extraction
# ==============================================================================

_dashboard_data=$(jq -n --argjson last "$LAST_N" \
    --arg spec "$FILTER_SPEC" --arg plan "$FILTER_PLAN" '
    [inputs] |
    (if $spec != "" then [.[] | select(.spec == $spec)] else . end) |
    (if $plan != "" then [.[] | select(.plan_run_id == $plan)] else . end) |
    .[-$last:] as $events |

    # Pipeline health
    [$events[] | select(.event == "SPEC_SHIPPED")] as $shipped |
    [$events[] | select(.event == "SPEC_FAILED" or .event == "SPEC_ESCALATED")] as $failed |
    ($shipped | length) as $shipped_n |
    ($failed | length) as $failed_n |
    ($shipped_n + $failed_n) as $total |

    # Avg iterations for shipped
    (if ($shipped | length) > 0
     then [$shipped[].data.iters | tonumber] | add / length | (. * 10 | round) / 10
     else 0 end) as $avg_iters |

    # First-pass rate
    (if ($shipped | length) > 0
     then ([$shipped[] | select((.data.iters | tonumber) == 1)] | length) * 100 / ($shipped | length)
     else 0 end) as $first_pass |

    # Avg wall-time
    (if ($shipped | length) > 0
     then [$shipped[].data.wall_s // "0" | tonumber] | add / length | round
     else 0 end) as $avg_wall |

    # Cost
    [$events[] | select(.event == "COST_SUMMARY")] as $costs |
    (if ($costs | length) > 0
     then [$costs[].data.estimated_usd // "0" | tonumber] | add | (. * 100 | round) / 100
     else 0 end) as $total_usd |
    (if ($costs | length) > 0
     then [$costs[].data.total_turns // "0" | tonumber] | add
     else 0 end) as $total_turns |
    (if ($costs | length) > 0
     then $total_usd / ($costs | length) | (. * 100 | round) / 100
     else 0 end) as $avg_usd |

    # Trust
    [$events[] | select(.event == "TRUST_SCORED")] | last // null |
    (if . then .data.score // "50" | tonumber else 50 end) as $trust |

    # Anomalies
    [$events[] | select(.event == "ANOMALY_DETECTED")] as $anomalies |

    # Recent runs (last 10)
    [$events[] | select(.event == "SPEC_SHIPPED" or .event == "SPEC_FAILED" or .event == "SPEC_ESCALATED")] |
    .[-10:] as $recent |

    {
        shipped: $shipped_n,
        failed: $failed_n,
        total: $total,
        avg_iters: $avg_iters,
        first_pass: $first_pass,
        avg_wall: $avg_wall,
        total_usd: $total_usd,
        total_turns: $total_turns,
        avg_usd: $avg_usd,
        trust: $trust,
        anomalies: [$anomalies[-5:][] | {
            type: .data.type,
            severity: .data.severity,
            spec: .spec,
            detail: (.data.metric // .data.score // "")
        }],
        recent: [$recent[] | {
            spec: .spec,
            event: .event,
            iters: (.data.iters // "?"),
            satisfaction: (.data.satisfaction_score // .data.pass_rate // "?"),
            usd: (.data.estimated_usd // "?"),
            ts: .ts
        }]
    }
' "$EVENTS_FILE" 2>/dev/null) || {
    echo "ERROR: Failed to parse events file." >&2
    exit 1
}

# ==============================================================================
# Render dashboard
# ==============================================================================

IFS=$'\t' read -r shipped failed total avg_iters first_pass avg_wall total_usd total_turns avg_usd trust < <(
    echo "$_dashboard_data" | jq -r '[.shipped, .failed, .total, .avg_iters, .first_pass, .avg_wall, .total_usd, .total_turns, .avg_usd, .trust] | @tsv' 2>/dev/null
)

# Format wall time
if (( avg_wall >= 60 )); then
    wall_fmt="$((avg_wall / 60))m $((avg_wall % 60))s"
else
    wall_fmt="${avg_wall}s"
fi

# Ship rate
if (( total > 0 )); then
    ship_pct=$(awk -v s="$shipped" -v t="$total" 'BEGIN { printf "%.1f", s * 100 / t }')
else
    ship_pct="0.0"
fi

echo ""
printf "${BOLD}═══ Dark Factory Dashboard ═══════════════════════════════════════${RESET}\n"
echo ""
printf "  ${BOLD}Pipeline Health${RESET}\n"
printf "  ─────────────────────────────\n"
printf "  Specs shipped:     ${GREEN}%s${RESET}/%s (%s%%)\n" "$shipped" "$total" "$ship_pct"
printf "  Avg iterations:    %s\n" "$avg_iters"
printf "  Avg wall-time:     %s\n" "$wall_fmt"
printf "  First-pass rate:   %s%%\n" "$first_pass"
echo ""
printf "  ${BOLD}Cost Summary${RESET}\n"
printf "  ─────────────\n"
printf "  Total turns:       %s\n" "$total_turns"
printf "  Est. USD:          \$%s\n" "$total_usd"
printf "  Avg per spec:      \$%s\n" "$avg_usd"
echo ""
printf "  ${BOLD}Trust Score${RESET}\n"
printf "  ───────────\n"
if (( trust >= 80 )); then
    printf "  Global:            ${GREEN}%s${RESET} / 100\n" "$trust"
elif (( trust >= 50 )); then
    printf "  Global:            ${YELLOW}%s${RESET} / 100\n" "$trust"
else
    printf "  Global:            ${RED}%s${RESET} / 100\n" "$trust"
fi

# Anomalies
anomaly_count=$(echo "$_dashboard_data" | jq '.anomalies | length')
if (( anomaly_count > 0 )); then
    echo ""
    printf "  ${BOLD}Recent Anomalies${RESET}\n"
    printf "  ────────────────\n"
    echo "$_dashboard_data" | jq -r '.anomalies[] |
        "  [\(.severity | ascii_upcase)] \(.spec // "?"): \(.type) \(.detail)"' 2>/dev/null
fi

# Recent runs
recent_count=$(echo "$_dashboard_data" | jq '.recent | length')
if (( recent_count > 0 )); then
    echo ""
    printf "  ${BOLD}Recent Runs${RESET}\n"
    printf "  ───────────\n"
    echo "$_dashboard_data" | jq -r '.recent[] |
        "  \(.spec // "?")\t\(.event | gsub("SPEC_";""))\titer=\(.iters)\tsat=\(.satisfaction)\t$\(.usd)"' 2>/dev/null |
        column -t -s $'\t' 2>/dev/null || echo "$_dashboard_data" | jq -r '.recent[] |
        "  \(.spec // "?")  \(.event | gsub("SPEC_";""))  iter=\(.iters)  sat=\(.satisfaction)  $\(.usd)"' 2>/dev/null
fi

printf "${BOLD}═══════════════════════════════════════════════════════════════════${RESET}\n"
echo ""
