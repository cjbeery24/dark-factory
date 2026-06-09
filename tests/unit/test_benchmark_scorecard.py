"""Tests for darkfactory/benchmark/scorecard.py (pure logic, no Docker)."""

from __future__ import annotations

import json
import unittest

from tests.helpers import TempDirTestCase
from darkfactory.benchmark.scorecard import (
    Outcome,
    RunMetrics,
    ScorecardRow,
    append_scorecard,
    classify_outcome,
    is_ship,
    parse_run_metrics,
    row_to_json,
)


class TestClassifyOutcome(unittest.TestCase):
    def test_exit0_no_registry_is_clean_ship(self) -> None:
        self.assertEqual(classify_outcome(0, False), Outcome.CLEAN_SHIP)

    def test_exit0_with_registry_is_ship_with_gaps(self) -> None:
        self.assertEqual(classify_outcome(0, True), Outcome.SHIP_WITH_GAPS)

    def test_exit_codes_map(self) -> None:
        self.assertEqual(classify_outcome(1, False), Outcome.FAILED)
        self.assertEqual(classify_outcome(2, False), Outcome.ESCALATED)
        self.assertEqual(classify_outcome(3, False), Outcome.ABORTED)
        self.assertEqual(classify_outcome(4, False), Outcome.HOLD)
        self.assertEqual(classify_outcome(5, False), Outcome.STABLE_FAILURE)

    def test_registry_ignored_for_nonzero_exit(self) -> None:
        # A STABLE_FAILURE writes a defect registry too, but exit 5 wins.
        self.assertEqual(classify_outcome(5, True), Outcome.STABLE_FAILURE)

    def test_unknown_exit_is_error(self) -> None:
        self.assertEqual(classify_outcome(-1, False), Outcome.ERROR)
        self.assertEqual(classify_outcome(137, False), Outcome.ERROR)

    def test_is_ship_only_clean_ship(self) -> None:
        self.assertTrue(is_ship(Outcome.CLEAN_SHIP))
        # The whole point of Sprint 1: these are NOT ships.
        self.assertFalse(is_ship(Outcome.SHIP_WITH_GAPS))
        self.assertFalse(is_ship(Outcome.STABLE_FAILURE))
        self.assertFalse(is_ship(Outcome.ESCALATED))


class TestParseRunMetrics(TempDirTestCase):
    def _write_events(self, name: str, lines: list[dict]) -> None:
        p = self.tmp_dir / name
        p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        return p

    def test_shipped_metrics(self) -> None:
        p = self._write_events("e.jsonl", [
            {"event": "SPEC_START", "run_id": "r1"},
            {"event": "COST_SUMMARY", "run_id": "r1", "data": {"estimated_usd": 42.5, "total_turns": 200}},
            {"event": "SPEC_SHIPPED", "run_id": "r1", "data": {"iterations": 1}},
        ])
        m = parse_run_metrics(p)
        self.assertEqual(m, RunMetrics(cost_usd=42.5, iterations=1, run_id="r1"))

    def test_stable_failure_metrics(self) -> None:
        p = self._write_events("e.jsonl", [
            {"event": "COST_SUMMARY", "run_id": "r9", "data": {"estimated_usd": 84.0}},
            {"event": "SPEC_STABLE_FAILURE", "run_id": "r9", "data": {"iterations": 2, "stable_failures": 4}},
        ])
        m = parse_run_metrics(p)
        self.assertEqual(m.iterations, 2)
        self.assertEqual(m.cost_usd, 84.0)
        self.assertEqual(m.run_id, "r9")

    def test_escalated_metrics(self) -> None:
        p = self._write_events("e.jsonl", [
            {"event": "SPEC_ESCALATED", "run_id": "r2", "data": {"iterations": 3}},
        ])
        self.assertEqual(parse_run_metrics(p).iterations, 3)

    def test_failed_has_no_iterations(self) -> None:
        # SPEC_FAILED carries 'reason', not 'iterations' — and may have no data.
        p = self._write_events("e.jsonl", [
            {"event": "SPEC_FAILED", "run_id": "r3", "data": {"reason": "boom"}},
        ])
        m = parse_run_metrics(p)
        self.assertIsNone(m.iterations)
        self.assertEqual(m.run_id, "r3")

    def test_event_without_data_key(self) -> None:
        # emitter omits empty 'data' — parser must tolerate its absence.
        p = self._write_events("e.jsonl", [
            {"event": "SPEC_FAILED", "run_id": "r4"},
        ])
        m = parse_run_metrics(p)
        self.assertIsNone(m.iterations)
        self.assertIsNone(m.cost_usd)
        self.assertEqual(m.run_id, "r4")

    def test_missing_file_all_none(self) -> None:
        self.assertEqual(parse_run_metrics(self.tmp_dir / "nope.jsonl"), RunMetrics())

    def test_malformed_line_tolerated(self) -> None:
        p = self.tmp_dir / "e.jsonl"
        p.write_text(
            '{"event":"COST_SUMMARY","run_id":"r5","data":{"estimated_usd":10.0}}\n'
            "{ this is not json\n"
            '{"event":"SPEC_SHIPPED","run_id":"r5","data":{"iterations":1}}\n'
        )
        m = parse_run_metrics(p)
        self.assertEqual(m.cost_usd, 10.0)
        self.assertEqual(m.iterations, 1)


class TestScorecardRow(TempDirTestCase):
    def test_row_to_json_roundtrip(self) -> None:
        row = ScorecardRow(
            spec="s", stack="ts", target="/t", run=1, outcome="clean_ship",
            exit_code=0, cost_usd=42.0, iterations=1, run_id="r1",
            wall_s=12.3, ts=1700000000.0,
        )
        d = json.loads(row_to_json(row))
        self.assertEqual(d["spec"], "s")
        self.assertEqual(d["outcome"], "clean_ship")
        self.assertEqual(d["exit_code"], 0)
        self.assertIsNone(json.loads(row_to_json(
            ScorecardRow("s", "ts", "/t", 1, "failed", 1, None, None, None, 0.0, 1.0)
        ))["cost_usd"])

    def test_append_scorecard_one_line_per_row(self) -> None:
        out = self.tmp_dir / "sub" / "scorecard.jsonl"
        for i in range(3):
            append_scorecard(out, ScorecardRow(
                spec="s", stack="ts", target="/t", run=i, outcome="clean_ship",
                exit_code=0, cost_usd=None, iterations=None, run_id=None,
                wall_s=0.0, ts=1.0,
            ))
        lines = out.read_text().strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(json.loads(lines[1])["run"], 1)


if __name__ == "__main__":
    unittest.main()
