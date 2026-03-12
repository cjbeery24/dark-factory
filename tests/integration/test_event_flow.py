"""Integration tests for the event lifecycle — emission, JSONL persistence, dashboard, OTEL export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from darkfactory.events.emitter import emit_event
from darkfactory.events.dashboard import build_dashboard, format_dashboard
from darkfactory.events.export_otel import export_traces, export_metrics, export_to_file
from darkfactory.events.rotate import rotate
from darkfactory.models.events import EventType
from darkfactory.utils.json_helpers import read_jsonl, append_jsonl


@pytest.mark.integration
class TestEventEmission:
    """Event emission to JSONL files."""

    def test_emit_writes_valid_jsonl(self, events_file: Path):
        emit_event(events_file, EventType.SPEC_START, spec_name="test-spec", run_id="abc123")
        records = read_jsonl(events_file)
        assert len(records) == 1
        assert records[0]["event"] == "SPEC_START"
        assert records[0]["spec"] == "test-spec"
        assert records[0]["run_id"] == "abc123"
        assert "ts" in records[0]

    def test_emit_multiple_events_ordered(self, events_file: Path):
        for i, etype in enumerate([EventType.SPEC_START, EventType.AGENT_START,
                                   EventType.AGENT_COMPLETED, EventType.GATE_COMPLETED]):
            emit_event(events_file, etype, spec_name="s", run_id="r", iteration=i)
        records = read_jsonl(events_file)
        assert len(records) == 4
        types = [r["event"] for r in records]
        assert types == ["SPEC_START", "AGENT_START", "AGENT_COMPLETED", "GATE_COMPLETED"]

    def test_emit_omits_empty_fields(self, events_file: Path):
        emit_event(events_file, EventType.SPEC_START)
        records = read_jsonl(events_file)
        rec = records[0]
        # Empty string fields should not be present
        assert "spec" not in rec
        assert "run_id" not in rec
        assert "iter" not in rec

    def test_emit_includes_data_dict(self, events_file: Path):
        emit_event(events_file, EventType.GATE_COMPLETED, spec_name="s",
                   data={"passed": 10, "failed": 2})
        records = read_jsonl(events_file)
        assert records[0]["data"]["passed"] == 10
        assert records[0]["data"]["failed"] == 2

    def test_emit_returns_event_object(self, events_file: Path):
        event = emit_event(events_file, EventType.SPEC_SHIPPED, spec_name="s", run_id="r")
        assert event.event_type == EventType.SPEC_SHIPPED
        assert event.spec_name == "s"
        assert event.run_id == "r"

    def test_all_event_types_serializable(self, events_file: Path):
        for etype in EventType:
            emit_event(events_file, etype, spec_name="s")
        records = read_jsonl(events_file)
        assert len(records) == len(EventType)
        for rec in records:
            assert rec["event"] in [e.value for e in EventType]


@pytest.mark.integration
class TestJsonHelpers:
    """JSONL append and read utilities."""

    def test_append_creates_file(self, tmp_path: Path):
        path = tmp_path / "sub" / "test.jsonl"
        append_jsonl(path, {"key": "value"})
        assert path.exists()
        records = read_jsonl(path)
        assert len(records) == 1

    def test_read_empty_file(self, tmp_path: Path):
        path = tmp_path / "empty.jsonl"
        path.touch()
        assert read_jsonl(path) == []

    def test_read_nonexistent_file(self, tmp_path: Path):
        path = tmp_path / "nonexistent.jsonl"
        assert read_jsonl(path) == []


@pytest.mark.integration
class TestDashboardIntegration:
    """Dashboard builds from events and renders correctly."""

    def test_dashboard_from_populated_events(self, populated_events: Path):
        data = build_dashboard(populated_events)
        assert data.shipped == 1
        assert data.failed == 0
        assert data.total_iterations == 1
        assert len(data.recent_runs) == 1

    def test_dashboard_format_includes_sections(self, populated_events: Path):
        data = build_dashboard(populated_events)
        output = format_dashboard(data)
        assert "Dark Factory Dashboard" in output
        assert "Shipped:" in output
        assert "Recent Runs" in output

    def test_dashboard_last_n_filter(self, events_file: Path):
        # Emit events for 3 different runs
        for i in range(3):
            emit_event(events_file, EventType.SPEC_START,
                       spec_name=f"spec-{i}", run_id=f"run-{i}")
            emit_event(events_file, EventType.SPEC_SHIPPED,
                       spec_name=f"spec-{i}", run_id=f"run-{i}")
        data = build_dashboard(events_file, last_n=2)
        assert len(data.recent_runs) == 2

    def test_dashboard_spec_filter(self, events_file: Path):
        emit_event(events_file, EventType.SPEC_START, spec_name="a", run_id="r1")
        emit_event(events_file, EventType.SPEC_SHIPPED, spec_name="a", run_id="r1")
        emit_event(events_file, EventType.SPEC_START, spec_name="b", run_id="r2")
        emit_event(events_file, EventType.SPEC_SHIPPED, spec_name="b", run_id="r2")
        data = build_dashboard(events_file, spec_filter="a")
        assert data.shipped == 1


@pytest.mark.integration
class TestOtelExport:
    """OTEL trace and metric export."""

    def test_export_traces_has_spans(self, populated_events: Path):
        result = export_traces(populated_events)
        assert "resourceSpans" in result
        spans = result["resourceSpans"]
        assert len(spans) > 0, "populated events should produce at least one resourceSpan"

    def test_export_traces_timestamps_nonzero(self, populated_events: Path):
        result = export_traces(populated_events)
        for rs in result["resourceSpans"]:
            for scope_span in rs.get("scopeSpans", []):
                for span in scope_span.get("spans", []):
                    start = span.get("startTimeUnixNano", "0")
                    assert start != "0", f"span has zero start time: {span.get('name', '?')}"

    def test_export_metrics_has_data(self, populated_events: Path):
        result = export_metrics(populated_events)
        assert "resourceMetrics" in result
        metrics = result["resourceMetrics"]
        assert len(metrics) > 0, "populated events should produce at least one resourceMetric"
        # Verify expected metric names are present
        metric_names: set[str] = set()
        for rm in metrics:
            for scope_metric in rm.get("scopeMetrics", []):
                for m in scope_metric.get("metrics", []):
                    metric_names.add(m["name"])
        assert "dark_factory.spec.iterations" in metric_names
        assert "dark_factory.agent.turns" in metric_names

    def test_export_to_file(self, populated_events: Path, tmp_path: Path):
        output = tmp_path / "otel.json"
        export_to_file(populated_events, output)
        assert output.exists()
        data = json.loads(output.read_text())
        assert "traces" in data
        assert "metrics" in data
        assert len(data["traces"].get("resourceSpans", [])) > 0


@pytest.mark.integration
class TestEventRotation:
    """Event file rotation."""

    def test_rotation_below_threshold(self, populated_events: Path):
        assert not rotate(populated_events)

    def test_rotation_above_threshold(self, events_file: Path):
        # Write enough data to exceed a small threshold
        for i in range(1000):
            append_jsonl(events_file, {"event": "SPEC_START", "spec": f"spec-{i}",
                                       "run_id": f"r-{i}", "ts": "2026-01-01T00:00:00Z"})
        size = events_file.stat().st_size
        # Rotate with threshold well below current size
        rotated = rotate(events_file, max_size=size // 2)
        assert rotated
        # Backup file should exist
        assert (events_file.parent / "events.jsonl.0").exists()

    def test_double_rotation_keeps_backups(self, events_file: Path):
        # First rotation
        for i in range(500):
            append_jsonl(events_file, {"event": "SPEC_START", "ts": "2026-01-01T00:00:00Z"})
        size = events_file.stat().st_size
        rotate(events_file, max_size=size // 2)
        assert (events_file.parent / "events.jsonl.0").exists()

        # Write more and rotate again
        for i in range(500):
            append_jsonl(events_file, {"event": "SPEC_SHIPPED", "ts": "2026-01-02T00:00:00Z"})
        rotate(events_file, max_size=size // 2)
        # .0 should now be the second rotation, .1 should be the first
        assert (events_file.parent / "events.jsonl.0").exists()
        assert (events_file.parent / "events.jsonl.1").exists()

    def test_rotation_nonexistent_file(self, tmp_path: Path):
        assert not rotate(tmp_path / "nonexistent.jsonl")
