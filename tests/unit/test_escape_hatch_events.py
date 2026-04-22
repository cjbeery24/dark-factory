"""Tests for ``_emit_escape_hatch_artifacts`` — verifies the
ITER_COST / ESCAPE_HATCH_FEEDBACK emission semantics at the four
iteration-loop escape hatches.

The plan (Task 2) requires ITER_COST to fire at the two
``continue``-path sites (Priority 1.5, R1) because those paths
short-circuit *before* the normal ITER_COST emission at
``iteration.py:2484``. The two ``return False``-path sites (B1,
trajectory) fire *after* :2484, so re-emitting there would produce
a duplicate.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import IterationState, SpecRun
from darkfactory.pipeline.iteration import (
    escape_hatch_continue,
    escape_hatch_return_false,
)
from darkfactory.pipeline.escape_hatch_feedback import (
    REASON_B1_EARLY_ESCALATION,
    REASON_GATE_REGRESSION,
    REASON_SELF_TEST_REGRESSION,
    REASON_TRAJECTORY_PLATEAU,
)


def _read_events(events_file: Path) -> list[dict]:
    if not events_file.exists():
        return []
    out = []
    with events_file.open() as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _scaffold(tmp: str):
    project = Path(tmp)
    (project / ".dark-factory" / "reviews").mkdir(parents=True)
    events = project / ".dark-factory" / "events.jsonl"

    sr = SpecRun(spec_name="myspec")
    sr.iteration = 2
    sr.total_turns = 150
    prior = IterationState(number=1)
    curr = IterationState(number=2)
    prior.gate_exit_code = 0
    prior.pass_rate = 70
    curr.gate_exit_code = 1
    curr.pass_rate = 50
    sr.iterations = [prior, curr]

    config = PipelineConfig()

    return project, events, sr, curr, prior, config


class TestEscapeHatchEvents(unittest.TestCase):
    def test_continue_path_emits_iter_cost_and_feedback_event(self):
        with TemporaryDirectory() as tmp:
            project, events, sr, curr, prior, config = _scaffold(tmp)
            escape_hatch_continue(
                sr, curr, config, project,
                reason=REASON_SELF_TEST_REGRESSION,
                events_file=events,
                turns_at_iter_start=50,  # total 150 - 50 = 100 this iter
                prev_iter_state=prior,
                rollback_ref="refs/abc",
                rolled_back=True,
            )
            records = _read_events(events)
            types = [e["event"] for e in records]
            self.assertEqual(
                types, ["ITER_COST", "ESCAPE_HATCH_FEEDBACK"],
                msg="continue-path sites must emit ITER_COST before the "
                "feedback event to close the cost-telemetry gap",
            )
            iter_cost = records[0]
            self.assertEqual(iter_cost["data"]["turns"], 100)
            feedback = records[1]
            self.assertEqual(feedback["data"]["reason"], "SELF_TEST_REGRESSION")
            self.assertTrue(feedback["data"]["prior_feedback_inlined"] is False)

    def test_return_false_path_does_not_emit_iter_cost(self):
        with TemporaryDirectory() as tmp:
            project, events, sr, curr, prior, config = _scaffold(tmp)
            escape_hatch_return_false(
                sr, curr, project,
                reason=REASON_B1_EARLY_ESCALATION,
                events_file=events,
            )
            records = _read_events(events)
            types = [e["event"] for e in records]
            self.assertEqual(types, ["ESCAPE_HATCH_FEEDBACK"])

    def test_feedback_event_carries_reason_and_inline_flag(self):
        with TemporaryDirectory() as tmp:
            project, events, sr, curr, prior, config = _scaffold(tmp)
            # Seed prior feedback so the inlined flag flips to True.
            prior_feedback = (
                project / ".dark-factory" / "reviews"
                / f"{sr.spec_name}-feedback-iter1.md"
            )
            prior_feedback.write_text("Prior feedback content. AC-2 and FR-1.")

            escape_hatch_continue(
                sr, curr, config, project,
                reason=REASON_GATE_REGRESSION,
                events_file=events,
                turns_at_iter_start=sr.total_turns,
                prev_iter_state=prior,
                rollback_ref="refs/abc",
                rolled_back=True,
            )
            records = _read_events(events)
            feedback = [e for e in records if e["event"] == "ESCAPE_HATCH_FEEDBACK"]
            self.assertEqual(len(feedback), 1)
            self.assertEqual(feedback[0]["data"]["reason"], "GATE_REGRESSION")
            self.assertTrue(feedback[0]["data"]["prior_feedback_inlined"])

    def test_no_events_file_still_writes_feedback(self):
        """The file invariant does not depend on events emission."""
        with TemporaryDirectory() as tmp:
            project, _events, sr, curr, prior, config = _scaffold(tmp)
            escape_hatch_return_false(
                sr, curr, project,
                reason=REASON_TRAJECTORY_PLATEAU,
                events_file=None,
            )
            feedback_path = (
                project / ".dark-factory" / "reviews"
                / f"{sr.spec_name}-feedback-iter2.md"
            )
            self.assertTrue(feedback_path.exists())
            self.assertIn("TRAJECTORY_PLATEAU", feedback_path.read_text())

    def test_osError_on_write_propagates(self):
        """Failing to write synthetic feedback MUST fail the pipeline.

        Silent logging + ``continue`` is exactly the failure mode this
        task fixes — the next iteration's implementer would then read
        a stale or missing file. Linus review §2 hard requirement.
        """
        from unittest.mock import patch

        with TemporaryDirectory() as tmp:
            project, events, sr, curr, prior, config = _scaffold(tmp)

            def _boom(*_args, **_kwargs):
                raise OSError(28, "No space left on device")

            with patch(
                "darkfactory.pipeline.iteration.write_escape_hatch_feedback",
                side_effect=_boom,
            ), self.assertRaises(OSError):
                escape_hatch_return_false(
                    sr, curr, project,
                    reason=REASON_TRAJECTORY_PLATEAU,
                    events_file=events,
                )


if __name__ == "__main__":
    unittest.main()
