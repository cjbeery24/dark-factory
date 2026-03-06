"""OTEL trace and metric export from events.jsonl.

Replaces scripts/df-export-otel.sh (391 lines). Transforms events
into OTLP JSON format for traces and metrics.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from darkfactory.models.events import EventType
from darkfactory.utils.json_helpers import read_jsonl

log = logging.getLogger(__name__)

# OTEL semantic convention attributes.
_SERVICE_NAME = "dark-factory"
_SERVICE_VERSION = "1.0.0"


def export_traces(events_file: Path) -> dict[str, Any]:
    """Export events as OTLP JSON traces.

    Creates one trace per spec run (run_id = trace ID).
    Each agent invocation becomes a child span.
    """
    records = read_jsonl(events_file)

    # Group events by run_id.
    runs: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        run_id = rec.get("run_id", "")
        if run_id:
            runs.setdefault(run_id, []).append(rec)

    resource_spans: list[dict[str, Any]] = []

    for run_id, events in runs.items():
        spans: list[dict[str, Any]] = []
        trace_id = _to_hex_id(run_id, 32)

        # Root span from SPEC_START to SPEC_SHIPPED/FAILED/ESCALATED.
        root = _build_root_span(trace_id, run_id, events)
        if root:
            spans.append(root)

        # Agent spans.
        agent_spans = _build_agent_spans(trace_id, run_id, events)
        spans.extend(agent_spans)

        if spans:
            resource_spans.append({
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": _SERVICE_NAME}},
                        {"key": "service.version", "value": {"stringValue": _SERVICE_VERSION}},
                    ],
                },
                "scopeSpans": [{
                    "scope": {"name": "darkfactory.pipeline"},
                    "spans": spans,
                }],
            })

    return {"resourceSpans": resource_spans}


def export_metrics(events_file: Path) -> dict[str, Any]:
    """Export events as OTLP JSON metrics.

    Exports: spec duration, iterations, cost, trust scores, agent metrics.
    """
    records = read_jsonl(events_file)

    # Collect spec-level metrics.
    spec_iterations: list[dict[str, Any]] = []
    cost_points: list[dict[str, Any]] = []
    trust_points: list[dict[str, Any]] = []
    agent_turns: list[dict[str, Any]] = []

    for rec in records:
        event = rec.get("event", "")
        ts = rec.get("ts", "")
        event_data = rec.get("data", {})
        spec = rec.get("spec", "")

        if event == EventType.SPEC_SHIPPED.value:
            spec_iterations.append({
                "attributes": [
                    _attr("spec", spec),
                    _attr("status", "shipped"),
                ],
                "asInt": str(event_data.get("iterations", 0)),
                "timeUnixNano": _ts_to_nano(ts),
            })

        elif event == EventType.COST_SUMMARY.value:
            cost_points.append({
                "attributes": [_attr("spec", spec)],
                "asDouble": event_data.get("estimated_usd", 0.0),
                "timeUnixNano": _ts_to_nano(ts),
            })

        elif event == EventType.TRUST_SCORED.value:
            trust_points.append({
                "attributes": [_attr("spec", spec)],
                "asInt": str(event_data.get("trust", 0)),
                "timeUnixNano": _ts_to_nano(ts),
            })

        elif event == EventType.AGENT_COMPLETED.value:
            agent_turns.append({
                "attributes": [
                    _attr("role", event_data.get("role", "")),
                    _attr("spec", spec),
                ],
                "asInt": str(event_data.get("turns", 0)),
                "timeUnixNano": _ts_to_nano(ts),
            })

    metrics: list[dict[str, Any]] = []
    if spec_iterations:
        metrics.append(_gauge("dark_factory.spec.iterations", "Iterations per spec", spec_iterations))
    if cost_points:
        metrics.append(_gauge("dark_factory.spec.cost_usd", "Estimated cost per spec (USD)", cost_points))
    if trust_points:
        metrics.append(_gauge("dark_factory.trust.score", "Trust score", trust_points))
    if agent_turns:
        metrics.append(_gauge("dark_factory.agent.turns", "Agent turns per invocation", agent_turns))

    return {
        "resourceMetrics": [{
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": _SERVICE_NAME}},
                ],
            },
            "scopeMetrics": [{
                "scope": {"name": "darkfactory.pipeline"},
                "metrics": metrics,
            }],
        }] if metrics else [],
    }


def export_to_file(events_file: Path, output: Path, *, traces: bool = True, metrics: bool = True) -> None:
    """Export traces and/or metrics to a file."""
    result: dict[str, Any] = {}
    if traces:
        result["traces"] = export_traces(events_file)
    if metrics:
        result["metrics"] = export_metrics(events_file)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2))
    log.info("exported OTEL data to %s", output)


# --- Helpers ---

def _build_root_span(
    trace_id: str, run_id: str, events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Build the root span for a spec run."""
    start_ts = ""
    end_ts = ""
    spec = ""
    status = "unset"

    for rec in events:
        event = rec.get("event", "")
        if event == EventType.SPEC_START.value:
            start_ts = rec.get("ts", "")
            spec = rec.get("spec", "")
        elif event in (
            EventType.SPEC_SHIPPED.value,
            EventType.SPEC_FAILED.value,
            EventType.SPEC_ESCALATED.value,
        ):
            end_ts = rec.get("ts", "")
            if event == EventType.SPEC_SHIPPED.value:
                status = "ok"
            else:
                status = "error"

    if not start_ts:
        return None

    span_id = _to_hex_id(f"{run_id}-root", 16)
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "name": f"spec/{spec}" if spec else "spec/unknown",
        "kind": 1,  # INTERNAL
        "startTimeUnixNano": _ts_to_nano(start_ts),
        "endTimeUnixNano": _ts_to_nano(end_ts) if end_ts else _ts_to_nano(start_ts),
        "status": {"code": 1 if status == "ok" else 2},
        "attributes": [
            _attr("spec.name", spec),
            _attr("run.id", run_id),
        ],
    }


def _build_agent_spans(
    trace_id: str, run_id: str, events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build agent spans from AGENT_START/COMPLETED/FAILED events."""
    spans: list[dict[str, Any]] = []
    root_span_id = _to_hex_id(f"{run_id}-root", 16)

    # Pair start and end events.
    pending: dict[str, dict[str, Any]] = {}  # key: "iter-role-attempt"

    for rec in events:
        event = rec.get("event", "")
        data = rec.get("data", {})
        iteration = rec.get("iter", 0)
        role = data.get("role", "")
        attempt = data.get("attempt", 1)
        key = f"{iteration}-{role}-{attempt}"

        if event == EventType.AGENT_START.value:
            pending[key] = rec

        elif event in (EventType.AGENT_COMPLETED.value, EventType.AGENT_FAILED.value):
            start_rec = pending.pop(key, None)
            start_ts = start_rec.get("ts", rec.get("ts", "")) if start_rec else rec.get("ts", "")
            end_ts = rec.get("ts", "")

            span_id = _to_hex_id(f"{run_id}-{key}", 16)
            status_code = 1 if event == EventType.AGENT_COMPLETED.value else 2

            attrs = [
                _attr("gen_ai.system", "anthropic"),
                _attr("gen_ai.operation.name", "agentic_loop"),
                _attr("gen_ai.agent.role", role),
                _attr("gen_ai.request.model", data.get("model_id", "unknown")),
                _attr("spec.name", rec.get("spec", "")),
            ]
            if data.get("est_input_tokens"):
                attrs.append({"key": "gen_ai.usage.input_tokens", "value": {"intValue": str(data["est_input_tokens"])}})
            if data.get("est_output_tokens"):
                attrs.append({"key": "gen_ai.usage.output_tokens", "value": {"intValue": str(data["est_output_tokens"])}})
            if data.get("exit_code") is not None:
                attrs.append({"key": "process.exit_code", "value": {"intValue": str(data["exit_code"])}})

            spans.append({
                "traceId": trace_id,
                "spanId": span_id,
                "parentSpanId": root_span_id,
                "name": f"agent/{role}",
                "kind": 1,
                "startTimeUnixNano": _ts_to_nano(start_ts),
                "endTimeUnixNano": _ts_to_nano(end_ts),
                "status": {"code": status_code},
                "attributes": attrs,
            })

    return spans


def _to_hex_id(s: str, length: int) -> str:
    """Convert a string to a hex ID of specified length."""
    h = hashlib.sha256(s.encode()).hexdigest()
    return h[:length]


def _ts_to_nano(ts: str) -> str:
    """Convert ISO 8601 timestamp to nanoseconds since epoch."""
    if not ts:
        return "0"
    try:
        # Try exact format first (most common).
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return str(int(dt.timestamp() * 1_000_000_000))
    except ValueError:
        pass
    try:
        # Handle fractional seconds.
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return str(int(dt.timestamp() * 1_000_000_000))
    except ValueError:
        log.warning("unparseable timestamp: %s", ts)
        return "0"


def _attr(key: str, value: str) -> dict[str, Any]:
    """Build an OTLP string attribute."""
    return {"key": key, "value": {"stringValue": value}}


def _gauge(name: str, description: str, points: list[dict[str, Any]]) -> dict[str, Any]:
    """Build an OTLP gauge metric."""
    return {
        "name": name,
        "description": description,
        "gauge": {"dataPoints": points},
    }
