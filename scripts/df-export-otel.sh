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
        # Helper: compute histogram bucket counts from values and bounds
        def bucket_counts(bounds):
            . as $vals |
            (bounds + [1e99]) |
            [range(length)] |
            map(. as $i |
                if $i == 0 then [$vals[] | select(. <= bounds[0])] | length
                elif $i >= (bounds | length) then [$vals[] | select(. > bounds[$i - 1])] | length
                else [$vals[] | select(. > bounds[$i - 1] and . <= bounds[$i])] | length
                end
            );

        [inputs] as $events |

        # Spec events grouped by status
        [$events[] | select(.event == "SPEC_SHIPPED")] as $shipped |
        [$events[] | select(.event == "SPEC_FAILED" or .event == "SPEC_ESCALATED")] as $failed |

        # Spec durations by status
        [$shipped[] | .data.wall_s // "0" | tonumber] as $shipped_durations |
        [$failed[] | .data.wall_s // "0" | tonumber] as $failed_durations |
        ($shipped_durations + $failed_durations) as $all_durations |

        # Spec iterations (shipped only)
        [$shipped[] | .data.iters // "0" | tonumber] as $iterations |

        # Agent stats by role (turns + durations)
        ([$events[] | select(.event == "AGENT_COMPLETED")] |
        group_by(.data.agent_role) |
        [.[] | {
            role: .[0].data.agent_role,
            turns: [.[].data.turns_allocated // "0" | tonumber],
            durations: [.[].data.wall_s // "0" | tonumber]
        }]) as $agent_stats |

        # Cost
        [$events[] | select(.event == "COST_SUMMARY") |
            .data.estimated_usd // "0" | tonumber] as $costs |

        # Per-iteration cost
        [$events[] | select(.event == "ITER_COST") |
            .data.estimated_usd // "0" | tonumber] as $iter_costs |

        # Trust (latest event)
        ([$events[] | select(.event == "TRUST_SCORED")] | if length > 0 then last else null end) as $trust_event |
        ($trust_event // {data: {score: "50"}}) as $trust |

        # Histogram bounds
        [60, 300, 900, 1800, 3600] as $duration_bounds |
        [1, 2, 3, 5] as $iter_bounds |

        {
            resourceMetrics: [{
                resource: {
                    attributes: [
                        {key: "service.name", value: {stringValue: "dark-factory"}}
                    ]
                },
                scopeMetrics: [{
                    scope: {name: "dark-factory.pipeline"},
                    metrics: ([
                        # --- Spec duration histogram (all, with status data points) ---
                        {
                            name: "df.spec.duration_s",
                            description: "Spec execution duration in seconds",
                            unit: "s",
                            histogram: {
                                dataPoints: [
                                    if ($shipped_durations | length) > 0 then {
                                        attributes: [{key: "status", value: {stringValue: "shipped"}}],
                                        count: ($shipped_durations | length),
                                        sum: ($shipped_durations | add),
                                        min: ($shipped_durations | min),
                                        max: ($shipped_durations | max),
                                        explicitBounds: $duration_bounds,
                                        bucketCounts: ($shipped_durations | bucket_counts($duration_bounds))
                                    } else empty end,
                                    if ($failed_durations | length) > 0 then {
                                        attributes: [{key: "status", value: {stringValue: "failed"}}],
                                        count: ($failed_durations | length),
                                        sum: ($failed_durations | add),
                                        min: ($failed_durations | min),
                                        max: ($failed_durations | max),
                                        explicitBounds: $duration_bounds,
                                        bucketCounts: ($failed_durations | bucket_counts($duration_bounds))
                                    } else empty end
                                ]
                            }
                        },
                        # --- Spec iterations histogram ---
                        {
                            name: "df.spec.iterations",
                            description: "Iterations to ship",
                            histogram: {
                                dataPoints: [{
                                    count: ($iterations | length),
                                    sum: (if ($iterations | length) > 0 then $iterations | add else 0 end),
                                    min: (if ($iterations | length) > 0 then $iterations | min else 0 end),
                                    max: (if ($iterations | length) > 0 then $iterations | max else 0 end),
                                    explicitBounds: $iter_bounds,
                                    bucketCounts: (if ($iterations | length) > 0 then $iterations | bucket_counts($iter_bounds) else [0, 0, 0, 0, 0] end)
                                }]
                            }
                        },
                        # --- Cost total ---
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
                        # --- Per-iteration cost gauge (latest or average) ---
                        {
                            name: "df.cost.per_iteration_usd",
                            description: "Cost per iteration in USD",
                            unit: "USD",
                            gauge: {
                                dataPoints: [{
                                    asDouble: (if ($iter_costs | length) > 0 then ($iter_costs | add) / ($iter_costs | length) else 0 end)
                                }]
                            }
                        },
                        # --- Trust score ---
                        {
                            name: "df.trust.score",
                            description: "Trust score (0-100)",
                            gauge: {
                                dataPoints: [{
                                    asDouble: ($trust.data.score // "50" | tonumber)
                                }]
                            }
                        },
                        # --- Trust dimension gauges ---
                        {
                            name: "df.trust.effective_autonomy",
                            description: "Effective autonomy level from trust scoring",
                            gauge: {
                                dataPoints: [{
                                    asDouble: ($trust.data.effective_autonomy // "3" | tonumber)
                                }]
                            }
                        },
                        {
                            name: "df.trust.first_pass_rate",
                            description: "First-pass ship rate (0-100)",
                            gauge: {
                                dataPoints: [{
                                    asDouble: ($trust.data.first_pass_rate // "0" | tonumber)
                                }]
                            }
                        },
                        {
                            name: "df.trust.avg_iterations",
                            description: "Average iterations score (inverted, 0-100)",
                            gauge: {
                                dataPoints: [{
                                    asDouble: ($trust.data.avg_iterations // "0" | tonumber)
                                }]
                            }
                        },
                        {
                            name: "df.trust.regression_fail_rate",
                            description: "Regression fail rate score (inverted, 0-100)",
                            gauge: {
                                dataPoints: [{
                                    asDouble: ($trust.data.regression_fail_rate // "0" | tonumber)
                                }]
                            }
                        },
                        {
                            name: "df.trust.complexity",
                            description: "Spec complexity score (inverted, 0-100)",
                            gauge: {
                                dataPoints: [{
                                    asDouble: ($trust.data.complexity // "0" | tonumber)
                                }]
                            }
                        },
                        {
                            name: "df.trust.familiarity",
                            description: "Module familiarity score (0-100)",
                            gauge: {
                                dataPoints: [{
                                    asDouble: ($trust.data.familiarity // "0" | tonumber)
                                }]
                            }
                        }
                    ] + [
                        # --- Agent turns histograms by role ---
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
                    ] + [
                        # --- Agent duration histograms by role (6b) ---
                        $agent_stats[] | {
                            name: "df.agent.duration_s.\(.role)",
                            description: "Agent duration in seconds by role",
                            unit: "s",
                            histogram: {
                                dataPoints: [{
                                    count: (.durations | length),
                                    sum: (if (.durations | length) > 0 then .durations | add else 0 end),
                                    min: (if (.durations | length) > 0 then .durations | min else 0 end),
                                    max: (if (.durations | length) > 0 then .durations | max else 0 end),
                                    explicitBounds: $duration_bounds,
                                    bucketCounts: (if (.durations | length) > 0 then .durations | bucket_counts($duration_bounds) else [0, 0, 0, 0, 0, 0] end)
                                }]
                            }
                        }
                    ])
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
