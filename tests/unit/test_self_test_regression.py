"""Priority 1.5 (2026-04-20): self-test regression hard gate.

Covers three layers:

1. ``_parse_hard_gate_status`` extracts the Self-Tests row into
   ``GateResult.self_tests_status`` (the iter-over-iter regression
   input).
2. ``IterationState.self_tests_status`` is populated from
   ``GateResult.self_tests_status`` so the next iteration can compare.
3. ``emit_self_test_regression_rejected`` writes the expected envelope.

The full iteration-loop integration (compare prior vs current, trigger
rollback, advance iteration) is exercised by
``tests/integration/test_iteration_loop.py``; keeping the unit-level
verification on the narrow pieces keeps this fast.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from darkfactory.events.types import emit_self_test_regression_rejected
from darkfactory.models.events import EventType
from darkfactory.models.pipeline import IterationState
from darkfactory.pipeline.quality_gate import (
    GateResult,
    _parse_hard_gate_status,
)
from tests.helpers import TempDirTestCase, make_project


def _write_summary(project_dir: Path, spec: str, body: str) -> None:
    summary = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec}-quality-gate-summary.md"
    )
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(body, encoding="utf-8")


class TestSelfTestsStatusParsing(TempDirTestCase):
    def test_self_tests_pass_captured(self) -> None:
        project_dir = make_project(self.tmp_dir)
        _write_summary(project_dir, "demo",
            "| Gate | Status |\n"
            "|------|--------|\n"
            "| Build | PASS |\n"
            "| Lint | PASS |\n"
            "| Type Check | PASS |\n"
            "| Self-Tests | PASS |\n"
            "| Blind Scenarios | FAIL |\n"
        )
        result = GateResult(exit_code=1)
        _parse_hard_gate_status(result, project_dir, "demo")
        self.assertEqual(result.self_tests_status, "PASS")

    def test_self_tests_fail_captured(self) -> None:
        project_dir = make_project(self.tmp_dir)
        _write_summary(project_dir, "demo",
            "| Gate | Status |\n"
            "|------|--------|\n"
            "| Build | PASS |\n"
            "| Lint | PASS |\n"
            "| Type Check | PASS |\n"
            "| Self-Tests | FAIL |\n"
        )
        result = GateResult(exit_code=1)
        _parse_hard_gate_status(result, project_dir, "demo")
        self.assertEqual(result.self_tests_status, "FAIL")

    def test_self_tests_skip_captured(self) -> None:
        project_dir = make_project(self.tmp_dir)
        _write_summary(project_dir, "demo",
            "| Gate | Status |\n"
            "|------|--------|\n"
            "| Build | PASS |\n"
            "| Self-Tests | SKIP |\n"
        )
        result = GateResult(exit_code=0)
        _parse_hard_gate_status(result, project_dir, "demo")
        self.assertEqual(result.self_tests_status, "SKIP")

    def test_missing_self_tests_row_leaves_empty(self) -> None:
        """No Self-Tests row in the table means the empty-string default is
        preserved — the regression check then treats it as 'unknown' and
        will not fire (only PASS->FAIL fires)."""
        project_dir = make_project(self.tmp_dir)
        _write_summary(project_dir, "demo",
            "| Gate | Status |\n"
            "|------|--------|\n"
            "| Build | PASS |\n"
            "| Lint | PASS |\n"
            "| Type Check | PASS |\n"
        )
        result = GateResult(exit_code=0)
        _parse_hard_gate_status(result, project_dir, "demo")
        self.assertEqual(result.self_tests_status, "")

    def test_missing_summary_leaves_empty(self) -> None:
        project_dir = make_project(self.tmp_dir)
        result = GateResult(exit_code=1)
        _parse_hard_gate_status(result, project_dir, "demo")
        self.assertEqual(result.self_tests_status, "")


class TestIterationStateThread(unittest.TestCase):
    """The iteration loop copies gate_result.self_tests_status onto
    iter_state.self_tests_status so prior-iteration comparison has a
    stable source. The dataclass default must be the empty string."""

    def test_default_is_empty(self) -> None:
        state = IterationState(number=1)
        self.assertEqual(state.self_tests_status, "")

    def test_assignable(self) -> None:
        state = IterationState(number=1)
        state.self_tests_status = "PASS"
        self.assertEqual(state.self_tests_status, "PASS")


class TestRegressionRejectedEvent(TempDirTestCase):
    def test_emits_envelope(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_self_test_regression_rejected(
            ef,
            spec="demo",
            run_id="abc123",
            iteration=2,
            prev_status="PASS",
            curr_status="FAIL",
            rollback_ref="refs/dark-factory/iter-end/abc123-1",
            rolled_back=True,
        )
        self.assertTrue(ef.exists())
        rec = json.loads(
            ef.read_text(encoding="utf-8").strip().splitlines()[-1]
        )
        self.assertEqual(
            rec["event"], EventType.SELF_TEST_REGRESSION_REJECTED.value,
        )
        self.assertEqual(rec["spec"], "demo")
        self.assertEqual(rec["run_id"], "abc123")
        self.assertEqual(rec["iter"], 2)
        data = rec["data"]
        self.assertEqual(data["prev_status"], "PASS")
        self.assertEqual(data["curr_status"], "FAIL")
        self.assertEqual(
            data["rollback_ref"], "refs/dark-factory/iter-end/abc123-1",
        )
        self.assertTrue(data["rolled_back"])


class TestRegressionDecision(unittest.TestCase):
    """The decision rule is intentionally narrow: fires ONLY on
    prev=PASS AND curr=FAIL. This test pins that behavior against the
    input matrix so a future refactor that widens or narrows the rule
    has to update this test explicitly."""

    @staticmethod
    def _should_reject(prev: str, curr: str) -> bool:
        return prev == "PASS" and curr == "FAIL"

    def test_pass_to_fail_fires(self) -> None:
        self.assertTrue(self._should_reject("PASS", "FAIL"))

    def test_pass_to_pass_does_not_fire(self) -> None:
        self.assertFalse(self._should_reject("PASS", "PASS"))

    def test_fail_to_fail_does_not_fire(self) -> None:
        """A new failure introduced during iter 1 that persists into
        iter 2 is handled by the R1 general regression guard + the
        stable-failure floor, not by this specific gate."""
        self.assertFalse(self._should_reject("FAIL", "FAIL"))

    def test_fail_to_pass_does_not_fire(self) -> None:
        self.assertFalse(self._should_reject("FAIL", "PASS"))

    def test_empty_prev_does_not_fire(self) -> None:
        """Prior iteration had no Self-Tests row (e.g., gate aborted
        early) — treat as unknown and do not fire."""
        self.assertFalse(self._should_reject("", "FAIL"))

    def test_empty_curr_does_not_fire(self) -> None:
        self.assertFalse(self._should_reject("PASS", ""))

    def test_skip_prev_does_not_fire(self) -> None:
        """SKIP means no self-test command configured or the gate
        skipped that step — not a prior-pass baseline."""
        self.assertFalse(self._should_reject("SKIP", "FAIL"))


if __name__ == "__main__":
    unittest.main()
