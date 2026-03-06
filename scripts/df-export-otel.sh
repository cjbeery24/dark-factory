#!/usr/bin/env bash
set -euo pipefail

# scripts/df-export-otel.sh — OTEL-compatible export (Phase 5, F2)
#
# Transforms events.jsonl into OTLP JSON for ingestion by Grafana Tempo (traces)
# and Prometheus (metrics via OTEL Collector).
#
# This is a convenience script, not a runtime dependency.
#
# Usage:
#   bash scripts/df-export-otel.sh [--events FILE] [--format traces|metrics|all] [--output FILE]
#
# Requires jq.

EVENTS_FILE=""
FORMAT="all"
OUTPUT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --events) EVENTS_FILE="$2"; shift 2 ;;
        --format) FORMAT="$2"; shift 2 ;;
        --output) OUTPUT="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: df-export-otel.sh [--events FILE] [--format traces|metrics|all] [--output FILE]"
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$EVENTS_FILE" ]]; then
    EVENTS_FILE="${_DF_EVENTS_FILE:-.dark-factory/events.jsonl}"
fi

if ! command -v jq >/dev/null 2>&1; then
    echo "ERROR: jq is required for OTEL export." >&2
    exit 1
fi

if [[ ! -f "$EVENTS_FILE" ]]; then
    echo "ERROR: Events file not found: $EVENTS_FILE" >&2
    exit 1
fi

# ==============================================================================
# Trace export: one trace per spec run (run_id = trace ID)
# ==============================================================================

_export_traces() {
    jq -n '
        [inputs] |
        group_by(.run_id) |
        [.[] | select(length > 0 and .[0].run_id != null) |
            .[0].run_id as $run_id |
            .[0].spec as $spec |

            # Root span: SPEC_START → SPEC_SHIPPED/SPEC_FAILED
            ([.[] | select(.event == "SPEC_START")][0]) as $start |
            ([.[] | select(.event == "SPEC_SHIPPED" or .event == "SPEC_FAILED" or .event == "SPEC_ESCALATED")][0]) as $end |

            # Agent spans
            [.[] | select(.event == "AGENT_COMPLETED" or .event == "AGENT_FAILED")] as $agents |

            {
                resourceSpans: [{
                    resource: {
                        attributes: [
                            {key: "service.name", value: {stringValue: "dark-factory"}},
                            {key: "df.spec", value: {stringValue: ($spec // "unknown")}}
                        ]
                    },
                    scopeSpans: [{
                        scope: {name: "dark-factory.pipeline"},
                        spans: [
                            # Root span
                            (if $start then {
                                traceId: $run_id,
                                spanId: ($run_id[:16]),
                                name: ("spec:" + ($spec // "unknown")),
                                kind: 1,
                                startTimeUnixNano: ($start.ts // "" | if . != "" then . else null end),
                                endTimeUnixNano: ($end.ts // $start.ts // null),
                                status: {
                                    code: (if $end.event == "SPEC_SHIPPED" then 1 else 2 end)
                                },
                                attributes: [
                                    {key: "df.iterations", value: {intValue: (($end.data.iters // "0") | tonumber)}},
                                    {key: "df.wall_s", value: {intValue: (($end.data.wall_s // "0") | tonumber)}}
                                ]
                            } else null end),

                            # Agent spans (enumerate for unique spanIds)
                            ([range($agents | length)] as $idxs | $idxs[] | . as $i | $agents[$i] | {
                                traceId: $run_id,
                                spanId: (($run_id + (.data.agent_role // "x") + ($i | tostring)) | gsub("[^a-f0-9]";"") | .[-16:]),
                                parentSpanId: ($run_id[:16]),
                                name: ("agent:" + (.data.agent_role // "unknown")),
                                kind: 1,
                                attributes: [
                                    {key: "df.agent.role", value: {stringValue: (.data.agent_role // "unknown")}},
                                    {key: "df.agent.exit_code", value: {intValue: ((.data.exit_code // "0") | tonumber)}},
                                    {key: "df.agent.wall_s", value: {intValue: ((.data.wall_s // "0") | tonumber)}},
                                    {key: "df.agent.turns", value: {intValue: ((.data.turns_allocated // "0") | tonumber)}},
                                    {key: "gen_ai.system", value: {stringValue: "anthropic"}},
                                    {key: "gen_ai.operation.name", value: {stringValue: "agentic_loop"}},
                                    {key: "gen_ai.request.model", value: {stringValue: (.data.model_id // "claude-sonnet-4-20250514")}},
                                    {key: "gen_ai.usage.input_tokens", value: {intValue: ((.data.est_input_tokens // "0") | tonumber)}},
                                    {key: "gen_ai.usage.output_tokens", value: {intValue: ((.data.est_output_tokens // "0") | tonumber)}},
                                    {key: "df.token_estimation_method", value: {stringValue: "ceiling"}}
                                ]
                            })
                        ] | [.[] | select(. != null)]
                    }]
                }]
            }
        ]
    ' "$EVENTS_FILE" 2>/dev/null
}

# ==============================================================================
# Metrics export: OTLP JSON format
# ==============================================================================

_export_metrics() {
    jq -n '
        [inputs] as $events |

        # Spec durations
        [$events[] | select(.event == "SPEC_SHIPPED" or .event == "SPEC_FAILED") |
            .data.wall_s // "0" | tonumber] as $durations |

        # Spec iterations
        [$events[] | select(.event == "SPEC_SHIPPED") |
            .data.iters // "0" | tonumber] as $iterations |

        # Agent turns by role
        [$events[] | select(.event == "AGENT_COMPLETED")] |
        group_by(.data.agent_role) |
        [.[] | {
            role: .[0].data.agent_role,
            turns: [.[].data.turns_allocated // "0" | tonumber],
            durations: [.[].data.wall_s // "0" | tonumber]
        }] as $agent_stats |

        # Cost
        [$events[] | select(.event == "COST_SUMMARY") |
            .data.estimated_usd // "0" | tonumber] as $costs |

        # Trust
        [$events[] | select(.event == "TRUST_SCORED") |
            .data.score // "50" | tonumber] as $trust_scores |

        {
            resourceMetrics: [{
                resource: {
                    attributes: [
                        {key: "service.name", value: {stringValue: "dark-factory"}}
                    ]
                },
                scopeMetrics: [{
                    scope: {name: "dark-factory.pipeline"},
                    metrics: [
                        {
                            name: "df.spec.duration_s",
                            description: "Spec execution duration in seconds",
                            unit: "s",
                            histogram: {
                                dataPoints: [{
                                    count: ($durations | length),
                                    sum: (if ($durations | length) > 0 then $durations | add else 0 end),
                                    min: (if ($durations | length) > 0 then $durations | min else 0 end),
                                    max: (if ($durations | length) > 0 then $durations | max else 0 end)
                                }]
                            }
                        },
                        {
                            name: "df.spec.iterations",
                            description: "Iterations to ship",
                            histogram: {
                                dataPoints: [{
                                    count: ($iterations | length),
                                    sum: (if ($iterations | length) > 0 then $iterations | add else 0 end),
                                    min: (if ($iterations | length) > 0 then $iterations | min else 0 end),
                                    max: (if ($iterations | length) > 0 then $iterations | max else 0 end)
                                }]
                            }
                        },
                        {
                            name: "df.cost.estimated_usd",
                            description: "Estimated cost in USD",
                            unit: "USD",
                            sum: {
                                dataPoints: [{
                                    asDouble: (if ($costs | length) > 0 then $costs | add else 0 end)
                                }],
                                isMonotonic: true
                            }
                        },
                        {
                            name: "df.trust.score",
                            description: "Trust score (0-100)",
                            gauge: {
                                dataPoints: [{
                                    asDouble: (if ($trust_scores | length) > 0 then $trust_scores | last else 50 end)
                                }]
                            }
                        }
                    ] + [
                        $agent_stats[] | {
                            name: "df.agent.turns.\(.role)",
                            description: "Agent turns by role",
                            histogram: {
                                dataPoints: [{
                                    count: (.turns | length),
                                    sum: (if (.turns | length) > 0 then .turns | add else 0 end),
                                    min: (if (.turns | length) > 0 then .turns | min else 0 end),
                                    max: (if (.turns | length) > 0 then .turns | max else 0 end)
                                }]
                            }
                        }
                    ]
                }]
            }]
        }
    ' "$EVENTS_FILE" 2>/dev/null
}

# ==============================================================================
# Output
# ==============================================================================

_output() {
    if [[ -n "$OUTPUT" ]]; then
        cat > "$OUTPUT"
        echo "Exported to $OUTPUT" >&2
    else
        cat
    fi
}

case "$FORMAT" in
    traces)
        _export_traces | _output
        ;;
    metrics)
        _export_metrics | _output
        ;;
    all)
        {
            echo '{"traces":'
            _export_traces
            echo ',"metrics":'
            _export_metrics
            echo '}'
        } | jq '.' | _output
        ;;
    *)
        echo "ERROR: Unknown format: $FORMAT (use traces, metrics, or all)" >&2
        exit 1
        ;;
esac
