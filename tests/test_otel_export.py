"""Tests for events/export_otel.py."""

import json
import unittest
from pathlib import Path
from typing import Any

from tests.helpers import TempDirTestCase
from darkfactory.events.export_otel import (
    export_metrics,
    export_to_file,
    export_traces,
    _ts_to_nano,
)
from darkfactory.utils.json_helpers import append_jsonl


# --- Helpers to reduce fragile OTLP structure traversal ---

def _get_spans(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract all spans from OTLP trace export."""
    spans: list[dict[str, Any]] = []
    for rs in result.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            spans.extend(ss.get("spans", []))
    return spans


def _get_attr(span: dict[str, Any], key: str) -> Any:
    """Get attribute value dict from span by key, or None."""
    for attr in span.get("attributes", []):
        if attr["key"] == key:
            return attr["value"]
    return None


def _get_attr_str(span: dict[str, Any], key: str) -> str:
    """Get string attribute value from span by key."""
    val = _get_attr(span, key)
    return val["stringValue"] if val else ""


class TestExportTraces(TempDirTestCase):
    def _make_events(self) -> Path:
        ef = self.tmp_dir / "events.jsonl"
        append_jsonl(ef, {
            "event": "SPEC_START", "ts": "2025-01-01T00:00:00Z",
            "spec": "my-spec", "run_id": "run1",
        })
        append_jsonl(ef, {
            "event": "AGENT_START", "ts": "2025-01-01T00:00:01Z",
            "spec": "my-spec", "run_id": "run1", "iter": 1,
            "data": {"role": "implementer", "attempt": 1},
        })
        append_jsonl(ef, {
            "event": "AGENT_COMPLETED", "ts": "2025-01-01T00:00:30Z",
            "spec": "my-spec", "run_id": "run1", "iter": 1,
            "data": {
                "role": "implementer", "attempt": 1, "turns": 100,
                "exit_code": 0, "model_id": "claude-sonnet-4-20250514",
                "est_input_tokens": 400000, "est_output_tokens": 200000,
            },
        })
        append_jsonl(ef, {
            "event": "SPEC_SHIPPED", "ts": "2025-01-01T00:01:00Z",
            "spec": "my-spec", "run_id": "run1",
            "data": {"iterations": 1, "satisfaction": 95},
        })
        return ef

    def test_resource_spans_structure(self) -> None:
        ef = self._make_events()
        result = export_traces(ef)
        self.assertIn("resourceSpans", result)
        self.assertGreater(len(result["resourceSpans"]), 0)

    def test_root_span_has_spec_and_run_id(self) -> None:
        ef = self._make_events()
        spans = _get_spans(export_traces(ef))
        root = [s for s in spans if "parentSpanId" not in s][0]
        self.assertEqual(_get_attr_str(root, "spec.name"), "my-spec")
        self.assertEqual(_get_attr_str(root, "run.id"), "run1")

    def test_agent_spans_have_gen_ai_attrs(self) -> None:
        ef = self._make_events()
        spans = _get_spans(export_traces(ef))
        agent_spans = [s for s in spans if "parentSpanId" in s]
        self.assertGreater(len(agent_spans), 0)
        span = agent_spans[0]
        for key in ("gen_ai.system", "gen_ai.operation.name",
                     "gen_ai.agent.role", "gen_ai.request.model", "spec.name"):
            self.assertIsNotNone(_get_attr(span, key), f"missing attr: {key}")

    def test_gen_ai_system_is_anthropic(self) -> None:
        ef = self._make_events()
        spans = _get_spans(export_traces(ef))
        agent_span = [s for s in spans if "parentSpanId" in s][0]
        self.assertEqual(_get_attr_str(agent_span, "gen_ai.system"), "anthropic")

    def test_model_fallback_when_missing(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        append_jsonl(ef, {
            "event": "SPEC_START", "ts": "2025-01-01T00:00:00Z",
            "spec": "s", "run_id": "r1",
        })
        append_jsonl(ef, {
            "event": "AGENT_START", "ts": "2025-01-01T00:00:01Z",
            "spec": "s", "run_id": "r1", "iter": 1,
            "data": {"role": "oracle", "attempt": 1},
        })
        append_jsonl(ef, {
            "event": "AGENT_COMPLETED", "ts": "2025-01-01T00:00:10Z",
            "spec": "s", "run_id": "r1", "iter": 1,
            "data": {"role": "oracle", "attempt": 1, "turns": 50, "exit_code": 0},
        })
        spans = _get_spans(export_traces(ef))
        agent_span = [s for s in spans if "parentSpanId" in s][0]
        self.assertEqual(_get_attr_str(agent_span, "gen_ai.request.model"), "unknown")

    def test_token_values_as_int(self) -> None:
        ef = self._make_events()
        spans = _get_spans(export_traces(ef))
        agent_span = [s for s in spans if "parentSpanId" in s][0]
        input_val = _get_attr(agent_span, "gen_ai.usage.input_tokens")
        self.assertEqual(input_val["intValue"], "400000")
        output_val = _get_attr(agent_span, "gen_ai.usage.output_tokens")
        self.assertEqual(output_val["intValue"], "200000")

    def test_failed_span_gets_gen_ai_attrs(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        append_jsonl(ef, {
            "event": "SPEC_START", "ts": "2025-01-01T00:00:00Z",
            "spec": "s", "run_id": "r1",
        })
        append_jsonl(ef, {
            "event": "AGENT_START", "ts": "2025-01-01T00:00:01Z",
            "spec": "s", "run_id": "r1", "iter": 1,
            "data": {"role": "implementer", "attempt": 1},
        })
        append_jsonl(ef, {
            "event": "AGENT_FAILED", "ts": "2025-01-01T00:00:30Z",
            "spec": "s", "run_id": "r1", "iter": 1,
            "data": {
                "role": "implementer", "attempt": 1, "exit_code": 1,
                "reason": "timeout", "model_id": "claude-sonnet-4-20250514",
                "est_input_tokens": 800000, "est_output_tokens": 400000,
            },
        })
        spans = _get_spans(export_traces(ef))
        agent_span = [s for s in spans if "parentSpanId" in s][0]
        self.assertEqual(_get_attr_str(agent_span, "gen_ai.system"), "anthropic")
        self.assertIsNotNone(_get_attr(agent_span, "gen_ai.usage.input_tokens"))


class TestExportMetrics(TempDirTestCase):
    def test_resource_metrics_structure(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        append_jsonl(ef, {
            "event": "TRUST_SCORED", "ts": "2025-01-01T00:00:00Z",
            "spec": "s", "run_id": "r1",
            "data": {"trust": 75, "autonomy": 3},
        })
        result = export_metrics(ef)
        self.assertIn("resourceMetrics", result)
        self.assertGreater(len(result["resourceMetrics"]), 0)

    def test_empty_events_empty_metrics(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        ef.write_text("")
        result = export_metrics(ef)
        self.assertEqual(result["resourceMetrics"], [])


class TestTsToNano(unittest.TestCase):
    def test_empty_string(self) -> None:
        self.assertEqual(_ts_to_nano(""), "0")

    def test_fractional_seconds(self) -> None:
        result = _ts_to_nano("2025-01-01T00:00:00.500Z")
        self.assertNotEqual(result, "0")


class TestExportToFile(TempDirTestCase):
    def test_writes_valid_json(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        append_jsonl(ef, {
            "event": "SPEC_START", "ts": "2025-01-01T00:00:00Z",
            "spec": "s", "run_id": "r1",
        })
        output = self.tmp_dir / "otel.json"
        export_to_file(ef, output)
        data = json.loads(output.read_text())
        self.assertIn("traces", data)
        self.assertIn("metrics", data)


if __name__ == "__main__":
    unittest.main()
