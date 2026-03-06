#!/usr/bin/env bash
set -euo pipefail

# scripts/df-events-migrate.sh — Backfill events.jsonl from metrics.jsonl
#
# One-time utility for projects with Phase 1–4 history. Converts each
# metrics.jsonl end record into SPEC_SHIPPED or SPEC_FAILED events.
# Requires jq.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"

if ! command -v jq >/dev/null 2>&1; then
    echo "ERROR: jq is required for migration" >&2
    exit 1
fi

METRICS_FILE="${DF_PROJECT_ROOT}/.dark-factory/metrics.jsonl"
EVENTS_FILE="${DF_PROJECT_ROOT}/.dark-factory/events.jsonl"

if [[ ! -f "$METRICS_FILE" ]]; then
    echo "No metrics.jsonl found — nothing to migrate."
    exit 0
fi

if [[ -f "$EVENTS_FILE" ]] && [[ -s "$EVENTS_FILE" ]]; then
    echo "events.jsonl already exists and is non-empty. Skipping migration."
    echo "To force, remove events.jsonl first."
    exit 0
fi

mkdir -p "$(dirname "$EVENTS_FILE")"

count=0
while IFS= read -r line; do
    # Extract all fields in a single jq call (2 forks instead of 14)
    local_fields=$(echo "$line" | jq -r '
        [.outcome // "", .spec // "", .run_id // "",
         (.ts_end // .ts_start // ""), (.iters // 0),
         (.wall_s // 0), (.pass_rates // "")] | @tsv
    ' 2>/dev/null) || continue

    IFS=$'\t' read -r outcome spec run_id ts iters wall_s pass_rates <<< "$local_fields"
    [[ -z "$outcome" ]] && continue

    case "$outcome" in
        shipped)   event="SPEC_SHIPPED" ;;
        escalated) event="SPEC_ESCALATED" ;;
        *)         event="SPEC_FAILED" ;;
    esac

    jq -n -c \
        --arg ts "$ts" \
        --arg event "$event" \
        --arg spec "$spec" \
        --arg run_id "$run_id" \
        --arg iters "$iters" \
        --arg wall_s "$wall_s" \
        --arg pass_rate "$pass_rates" \
        '{ts: $ts, event: $event, spec: $spec, run_id: $run_id, data: {iters: $iters, wall_s: $wall_s, pass_rate: $pass_rate, migrated: "true"}}' \
        >> "$EVENTS_FILE"

    count=$((count + 1))
done < "$METRICS_FILE"

echo "Migrated $count record(s) from metrics.jsonl to events.jsonl"
