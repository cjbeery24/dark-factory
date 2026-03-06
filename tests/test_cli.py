"""Tests for cli.py — CLI entry point and subcommands."""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.helpers import TempDirTestCase
from darkfactory.cli import main
from darkfactory.events.dashboard import build_dashboard, format_dashboard, DashboardData
from darkfactory.events.export_otel import export_traces, export_metrics
from darkfactory.events.rotate import rotate, migrate_metrics
from darkfactory.tui.selector import select_spec


class TestCLIParsing(unittest.TestCase):
    """Test CLI argument parsing and dispatch."""

    def test_no_command_exits(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main([])
        self.assertEqual(ctx.exception.code, 1)

    def test_dashboard_dispatch(self) -> None:
        mock_cmd = MagicMock()
        with patch.dict("darkfactory.cli._COMMANDS", {"dashboard": mock_cmd}):
            try:
                main(["dashboard"])
            except SystemExit:
                pass
        mock_cmd.assert_called_once()

    def test_metrics_dispatch(self) -> None:
        mock_cmd = MagicMock()
        with patch.dict("darkfactory.cli._COMMANDS", {"metrics": mock_cmd}):
            try:
                main(["metrics"])
            except SystemExit:
                pass
        mock_cmd.assert_called_once()


class TestDashboard(TempDirTestCase):
    def _write_events(self, records: list[dict]) -> Path:
        ef = self.tmp_dir / "events.jsonl"
        ef.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return ef

    def test_empty_events(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        ef.write_text("")
        data = build_dashboard(ef)
        self.assertEqual(data.total_specs, 0)

    def test_shipped_counted(self) -> None:
        ef = self._write_events([
            {"event": "SPEC_START", "spec": "a", "run_id": "r1", "ts": "2026-01-01T00:00:00Z"},
            {"event": "SPEC_SHIPPED", "spec": "a", "run_id": "r1", "ts": "2026-01-01T00:01:00Z", "data": {"iterations": 2}},
        ])
        data = build_dashboard(ef)
        self.assertEqual(data.shipped, 1)
        self.assertEqual(data.total_specs, 1)

    def test_format_dashboard(self) -> None:
        data = DashboardData(total_specs=3, shipped=2, failed=1)
        output = format_dashboard(data)
        self.assertIn("Dark Factory Dashboard", output)
        self.assertIn("Shipped:", output)

    def test_spec_filter(self) -> None:
        ef = self._write_events([
            {"event": "SPEC_SHIPPED", "spec": "a", "run_id": "r1", "ts": "T1", "data": {}},
            {"event": "SPEC_SHIPPED", "spec": "b", "run_id": "r2", "ts": "T2", "data": {}},
        ])
        data = build_dashboard(ef, spec_filter="a")
        self.assertEqual(data.shipped, 1)


class TestOTELExport(TempDirTestCase):
    def _write_events(self, records: list[dict]) -> Path:
        ef = self.tmp_dir / "events.jsonl"
        ef.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return ef

    def test_empty_events_traces(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        ef.write_text("")
        result = export_traces(ef)
        self.assertEqual(result["resourceSpans"], [])

    def test_basic_traces(self) -> None:
        ef = self._write_events([
            {"event": "SPEC_START", "spec": "a", "run_id": "r1", "ts": "2026-01-01T00:00:00Z"},
            {"event": "AGENT_START", "spec": "a", "run_id": "r1", "iter": 1, "ts": "2026-01-01T00:00:01Z", "data": {"role": "implementer", "attempt": 1}},
            {"event": "AGENT_COMPLETED", "spec": "a", "run_id": "r1", "iter": 1, "ts": "2026-01-01T00:01:00Z", "data": {"role": "implementer", "attempt": 1, "turns": 50, "exit_code": 0}},
            {"event": "SPEC_SHIPPED", "spec": "a", "run_id": "r1", "ts": "2026-01-01T00:02:00Z", "data": {"iterations": 1}},
        ])
        result = export_traces(ef)
        self.assertEqual(len(result["resourceSpans"]), 1)
        spans = result["resourceSpans"][0]["scopeSpans"][0]["spans"]
        # Root span + 1 agent span.
        self.assertEqual(len(spans), 2)

    def test_basic_metrics(self) -> None:
        ef = self._write_events([
            {"event": "SPEC_SHIPPED", "spec": "a", "run_id": "r1", "ts": "2026-01-01T00:00:00Z", "data": {"iterations": 1}},
            {"event": "COST_SUMMARY", "spec": "a", "run_id": "r1", "ts": "2026-01-01T00:00:00Z", "data": {"estimated_usd": 0.5, "total_turns": 100}},
        ])
        result = export_metrics(ef)
        self.assertTrue(len(result["resourceMetrics"]) > 0)

    def test_export_to_file(self) -> None:
        from darkfactory.events.export_otel import export_to_file
        ef = self._write_events([
            {"event": "SPEC_START", "spec": "a", "run_id": "r1", "ts": "2026-01-01T00:00:00Z"},
        ])
        output = self.tmp_dir / "otel.json"
        export_to_file(ef, output)
        self.assertTrue(output.exists())
        data = json.loads(output.read_text())
        self.assertIn("traces", data)


class TestRotation(TempDirTestCase):
    def test_no_rotation_small_file(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        ef.write_text("small\n")
        self.assertFalse(rotate(ef, max_size=1000))

    def test_rotation_large_file(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        ef.write_text("x" * 2000)
        self.assertTrue(rotate(ef, max_size=1000))
        self.assertTrue(Path(f"{ef}.0").exists())
        self.assertFalse(ef.exists())

    def test_rotation_cascade(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        # Create .0 and .1 backups.
        Path(f"{ef}.0").write_text("backup0")
        Path(f"{ef}.1").write_text("backup1")
        ef.write_text("x" * 2000)
        rotate(ef, max_size=1000, keep_count=3)
        # .0 → .1, .1 → .2, current → .0
        self.assertEqual(Path(f"{ef}.1").read_text(), "backup0")
        self.assertEqual(Path(f"{ef}.2").read_text(), "backup1")

    def test_migration(self) -> None:
        metrics = self.tmp_dir / "metrics.jsonl"
        events = self.tmp_dir / "events.jsonl"
        metrics.write_text(
            json.dumps({"spec": "a", "outcome": "shipped", "run_id": "r1", "iters": 1}) + "\n"
            + json.dumps({"spec": "b", "outcome": "failed", "run_id": "r2", "iters": 3}) + "\n"
        )
        count = migrate_metrics(metrics, events)
        self.assertEqual(count, 2)
        from darkfactory.utils.json_helpers import read_jsonl
        records = read_jsonl(events)
        self.assertEqual(records[0]["event"], "SPEC_SHIPPED")
        self.assertEqual(records[1]["event"], "SPEC_FAILED")

    def test_migration_skips_existing(self) -> None:
        metrics = self.tmp_dir / "metrics.jsonl"
        events = self.tmp_dir / "events.jsonl"
        metrics.write_text(json.dumps({"spec": "a", "outcome": "shipped"}) + "\n")
        events.write_text("existing\n")
        count = migrate_metrics(metrics, events)
        self.assertEqual(count, 0)

    def test_missing_file(self) -> None:
        self.assertFalse(rotate(self.tmp_dir / "nonexistent.jsonl"))


class TestTUISelector(TempDirTestCase):
    def test_no_specs_dir(self) -> None:
        result = select_spec(self.tmp_dir)
        self.assertEqual(result, "")

    def test_single_spec_auto_selects(self) -> None:
        spec_dir = self.tmp_dir / ".dark-factory" / "specs"
        spec_dir.mkdir(parents=True)
        (spec_dir / "my-spec.md").write_text("# Spec\n")
        result = select_spec(self.tmp_dir)
        self.assertEqual(result, "my-spec")

    def test_ci_mode_multiple_specs(self) -> None:
        spec_dir = self.tmp_dir / ".dark-factory" / "specs"
        spec_dir.mkdir(parents=True)
        (spec_dir / "a.md").write_text("# A\n")
        (spec_dir / "b.md").write_text("# B\n")
        result = select_spec(self.tmp_dir, ci_mode=True)
        self.assertEqual(result, "")


class TestIntegrator(TempDirTestCase):
    def test_create_directories(self) -> None:
        from darkfactory.integration.integrator import _create_directories
        _create_directories(self.tmp_dir, dry_run=False)
        self.assertTrue((self.tmp_dir / ".dark-factory" / "specs").is_dir())
        self.assertTrue((self.tmp_dir / ".dark-factory" / "scenarios").is_dir())

    def test_generate_config(self) -> None:
        from darkfactory.integration.integrator import _generate_config
        (self.tmp_dir / ".dark-factory").mkdir(parents=True)
        _generate_config(self.tmp_dir, "node", dry_run=False)
        config = self.tmp_dir / ".dark-factory" / "config.toml"
        self.assertTrue(config.exists())
        content = config.read_text()
        self.assertIn('language = "node"', content)

    def test_update_claude_md(self) -> None:
        from darkfactory.integration.integrator import _update_claude_md
        _update_claude_md(self.tmp_dir, dry_run=False)
        claude_md = self.tmp_dir / "CLAUDE.md"
        self.assertTrue(claude_md.exists())
        self.assertIn("Dark Factory", claude_md.read_text())

    def test_skip_existing_claude_md(self) -> None:
        from darkfactory.integration.integrator import _update_claude_md
        claude_md = self.tmp_dir / "CLAUDE.md"
        claude_md.write_text("# My Project\n## Dark Factory\nAlready here.\n")
        original = claude_md.read_text()
        _update_claude_md(self.tmp_dir, dry_run=False)
        self.assertEqual(claude_md.read_text(), original)


if __name__ == "__main__":
    unittest.main()
