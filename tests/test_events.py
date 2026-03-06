"""Tests for events/emitter.py and events/types.py."""

import json
import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase
from darkfactory.events.emitter import emit_event
from darkfactory.events.types import (
    emit_agent_completed,
    emit_budget_alert,
    emit_barrier_violation,
    emit_files_changed,
    emit_gate_completed,
    emit_iter_cost,
    emit_plan_complete,
    emit_spec_dispatched,
    emit_spec_start,
    emit_spec_shipped,
    emit_agent_start,
)
from darkfactory.models.events import EventType
from darkfactory.utils.json_helpers import read_jsonl


class TestEmitter(TempDirTestCase):
    def test_emit_event_creates_file(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        event = emit_event(
            ef, EventType.SPEC_START,
            spec_name="my-spec", run_id="abc123",
        )
        self.assertTrue(ef.exists())
        self.assertEqual(event.event_type, EventType.SPEC_START)
        self.assertEqual(event.spec_name, "my-spec")

    def test_emit_event_jsonl_format(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_event(ef, EventType.SPEC_START, spec_name="s", run_id="r")
        emit_event(ef, EventType.SPEC_SHIPPED, spec_name="s", run_id="r",
                    data={"iterations": 2})

        records = read_jsonl(ef)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["event"], "SPEC_START")
        self.assertEqual(records[1]["event"], "SPEC_SHIPPED")
        self.assertEqual(records[1]["data"]["iterations"], 2)

    def test_optional_fields_omitted(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_event(ef, EventType.PLAN_START)
        records = read_jsonl(ef)
        self.assertNotIn("spec", records[0])
        self.assertNotIn("run_id", records[0])
        self.assertNotIn("iter", records[0])


class TestEventHelpers(TempDirTestCase):
    def test_emit_spec_start(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        event = emit_spec_start(ef, spec="my-spec", run_id="r1")
        self.assertEqual(event.event_type, EventType.SPEC_START)

    def test_emit_spec_shipped(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_spec_shipped(ef, spec="s", run_id="r", iterations=2, satisfaction=85)
        records = read_jsonl(ef)
        self.assertEqual(records[0]["data"]["iterations"], 2)
        self.assertEqual(records[0]["data"]["satisfaction"], 85)

    def test_emit_agent_start(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_agent_start(ef, spec="s", run_id="r", iteration=1, role="implementer")
        records = read_jsonl(ef)
        self.assertEqual(records[0]["data"]["role"], "implementer")
        self.assertEqual(records[0]["iter"], 1)

    def test_emit_gate_completed(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_gate_completed(ef, spec="s", run_id="r", iteration=1,
                            exit_code=0, pass_rate=100, satisfaction=95)
        records = read_jsonl(ef)
        self.assertEqual(records[0]["data"]["exit_code"], 0)

    def test_emit_budget_alert(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_budget_alert(ef, spec="s", run_id="r", percent=80.0,
                          spent=4.0, budget=5.0)
        records = read_jsonl(ef)
        self.assertEqual(records[0]["data"]["percent"], 80.0)

    def test_emit_barrier_violation(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_barrier_violation(ef, spec="s", run_id="r",
                                role="implementer", path=".dark-factory/scenarios")
        records = read_jsonl(ef)
        self.assertEqual(records[0]["data"]["role"], "implementer")

    def test_emit_plan_complete(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_plan_complete(ef, plan_run_id="p1", shipped=3, failed=1, skipped=0)
        records = read_jsonl(ef)
        self.assertEqual(records[0]["event"], "PLAN_COMPLETE")
        self.assertEqual(records[0]["data"]["shipped"], 3)

    def test_emit_spec_dispatched(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_spec_dispatched(ef, spec="s", run_id="r", plan_run_id="p1", worktree="/tmp/wt")
        records = read_jsonl(ef)
        self.assertEqual(records[0]["event"], "SPEC_DISPATCHED")
        self.assertEqual(records[0]["data"]["worktree"], "/tmp/wt")

    def test_emit_agent_completed_with_model(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_agent_completed(
            ef, spec="s", run_id="r", iteration=1, role="implementer",
            turns=100, exit_code=0,
            model_id="claude-sonnet-4-20250514",
            est_input_tokens=400000, est_output_tokens=200000,
        )
        records = read_jsonl(ef)
        self.assertEqual(records[0]["data"]["model_id"], "claude-sonnet-4-20250514")
        self.assertEqual(records[0]["data"]["est_input_tokens"], 400000)
        self.assertEqual(records[0]["data"]["est_output_tokens"], 200000)

    def test_emit_iter_cost(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_iter_cost(ef, spec="s", run_id="r", iteration=1, turns=200, estimated_usd=42.0)
        records = read_jsonl(ef)
        self.assertEqual(records[0]["event"], "ITER_COST")
        self.assertEqual(records[0]["data"]["turns"], 200)
        self.assertAlmostEqual(records[0]["data"]["estimated_usd"], 42.0)

    def test_emit_files_changed(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_files_changed(ef, spec="s", run_id="r", iteration=1, count=15)
        records = read_jsonl(ef)
        self.assertEqual(records[0]["event"], "FILES_CHANGED")
        self.assertEqual(records[0]["data"]["count"], 15)

    def test_emit_budget_alert_data(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_budget_alert(ef, spec="s", run_id="r", percent=80.5, spent=4.025, budget=5.0)
        records = read_jsonl(ef)
        self.assertAlmostEqual(records[0]["data"]["spent"], 4.025)


if __name__ == "__main__":
    unittest.main()
