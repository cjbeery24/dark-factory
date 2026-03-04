#!/usr/bin/env bash
set -euo pipefail

# scripts/metrics-summary.sh — Print tabular summary of pipeline metrics
#
# Reads .dark-factory/metrics.jsonl and prints a human-readable table.
# Pure bash — no jq dependency. For production analysis, pipe metrics.jsonl
# through jq for richer queries.
#
# Usage: bash scripts/metrics-summary.sh [metrics-file]

METRICS_FILE="${1:-.dark-factory/metrics.jsonl}"

if [[ ! -f "$METRICS_FILE" ]]; then
    echo "No metrics file found at: $METRICS_FILE"
    echo "Run the pipeline to generate metrics."
    exit 0
fi

# Extract a JSON string value by key (simple pattern — works for flat objects)
_json_val() {
    local line="$1" key="$2"
    local val
    # Match "key":"value" or "key":number
    val=$(echo "$line" | sed -n "s/.*\"${key}\":\"\{0,1\}\([^\",$}]*\)\"\{0,1\}.*/\1/p")
    echo "$val"
}

# Print header
printf '\n%-30s %-20s %-10s %-6s %-8s %-10s %-12s\n' \
    "SPEC" "RUN_ID" "OUTCOME" "ITERS" "WALL(s)" "FAILURES" "PASS_RATES"
printf '%s\n' "$(printf '%.0s-' {1..100})"

# Counters
total=0
shipped=0
escalated=0
failed=0
total_iters=0
total_wall=0

while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue

    spec=$(_json_val "$line" "spec")
    run_id=$(_json_val "$line" "run_id")
    outcome=$(_json_val "$line" "outcome")
    iters=$(_json_val "$line" "iters")
    wall_s=$(_json_val "$line" "wall_s")
    agent_failures=$(_json_val "$line" "agent_failures")
    pass_rates=$(_json_val "$line" "pass_rates")

    # Skip in-progress records (no outcome)
    [[ "$outcome" == "in_progress" ]] && continue
    [[ -z "$outcome" ]] && continue

    printf '%-30s %-20s %-10s %-6s %-8s %-10s %-12s\n' \
        "${spec:0:30}" "${run_id:0:20}" "$outcome" "${iters:-?}" \
        "${wall_s:-?}" "${agent_failures:-0}" "${pass_rates:--}"

    total=$((total + 1))
    case "$outcome" in
        shipped)   shipped=$((shipped + 1)) ;;
        escalated) escalated=$((escalated + 1)) ;;
        *)         failed=$((failed + 1)) ;;
    esac
    if [[ -n "$iters" ]] && [[ "$iters" =~ ^[0-9]+$ ]]; then
        total_iters=$((total_iters + iters))
    fi
    if [[ -n "$wall_s" ]] && [[ "$wall_s" =~ ^[0-9]+$ ]]; then
        total_wall=$((total_wall + wall_s))
    fi
done < "$METRICS_FILE"

# Summary
printf '%s\n' "$(printf '%.0s-' {1..100})"
echo ""
echo "Summary:"
echo "  Total runs:      $total"
echo "  Shipped:         $shipped"
echo "  Escalated:       $escalated"
echo "  Failed/Other:    $failed"
if [[ $total -gt 0 ]]; then
    avg_iters=$((total_iters / total))
    avg_wall=$((total_wall / total))
    ship_pct=$((shipped * 100 / total))
    echo "  Ship rate:       ${ship_pct}%"
    echo "  Avg iterations:  ${avg_iters}"
    echo "  Avg wall time:   ${avg_wall}s"
    if [[ $shipped -gt 0 ]]; then
        first_pass=0
        # Re-scan for first-pass rate
        while IFS= read -r line || [[ -n "$line" ]]; do
            outcome=$(_json_val "$line" "outcome")
            iters=$(_json_val "$line" "iters")
            [[ "$outcome" == "shipped" ]] && [[ "$iters" == "1" ]] && first_pass=$((first_pass + 1))
        done < "$METRICS_FILE"
        fp_pct=$((first_pass * 100 / shipped))
        echo "  First-pass rate: ${fp_pct}% (${first_pass}/${shipped})"
    fi
fi
echo ""
