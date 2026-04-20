"""Priority 4 (2026-04-20 plan revision): implementer temperature=0
on iter >= 2.

Tests the pure gating function and the event emitter in isolation so
we don't have to stand up a full invoke_agent path to verify the rule.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from darkfactory.events.types import emit_implementer_temp_zero
from darkfactory.models.agent import AgentRole
from darkfactory.models.events import EventType
from darkfactory.pipeline.agents import should_force_temperature_zero
from tests.helpers import TempDirTestCase


class TestTemperatureGating(unittest.TestCase):
    def test_implementer_iter1_is_default_temperature(self) -> None:
        """Iter 1 explores — no temperature pin."""
        self.assertFalse(should_force_temperature_zero(AgentRole.IMPLEMENTER, 1))

    def test_implementer_iter2_is_temperature_zero(self) -> None:
        """Iter 2 exploits against iter-1 feedback — pin to 0."""
        self.assertTrue(should_force_temperature_zero(AgentRole.IMPLEMENTER, 2))

    def test_implementer_iter3_is_temperature_zero(self) -> None:
        self.assertTrue(should_force_temperature_zero(AgentRole.IMPLEMENTER, 3))

    def test_implementer_iter0_is_default(self) -> None:
        """Iter 0 is a pre-gate or initialization path; keep default."""
        self.assertFalse(should_force_temperature_zero(AgentRole.IMPLEMENTER, 0))

    def test_defect_classifier_always_temperature_zero(self) -> None:
        """Pre-existing Phase 5.6 B4 rule must still hold: classifier is
        always temp=0 regardless of iteration (it's a measurement instrument)."""
        self.assertTrue(should_force_temperature_zero(AgentRole.DEFECT_CLASSIFIER, 1))
        self.assertTrue(should_force_temperature_zero(AgentRole.DEFECT_CLASSIFIER, 2))
        self.assertTrue(should_force_temperature_zero(AgentRole.DEFECT_CLASSIFIER, 0))

    def test_other_roles_remain_default_temperature(self) -> None:
        """Roles not in the two gating rules must be left at Claude Code's
        default temperature — agentic tool use benefits from non-zero."""
        for role in (
            AgentRole.ARCHITECT,
            AgentRole.TEST_ENGINEER,
            AgentRole.REVIEWER,
            AgentRole.ORACLE,
        ):
            for it in (1, 2, 3):
                self.assertFalse(
                    should_force_temperature_zero(role, it),
                    f"{role.value} iter {it} unexpectedly pinned to temp=0",
                )


class TestImplementerTempZeroEvent(TempDirTestCase):
    def test_emits_expected_event_type_and_envelope(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_implementer_temp_zero(
            ef, spec="demo", run_id="abc123", iteration=2,
        )
        self.assertTrue(ef.exists())
        line = ef.read_text(encoding="utf-8").strip().splitlines()[-1]
        rec = json.loads(line)
        # JSONL record uses short keys — "event", "spec", "iter" — matching
        # the existing on-disk schema (see events/emitter.py).
        self.assertEqual(rec["event"], EventType.IMPLEMENTER_TEMP_ZERO.value)
        self.assertEqual(rec["spec"], "demo")
        self.assertEqual(rec["run_id"], "abc123")
        self.assertEqual(rec["iter"], 2)


if __name__ == "__main__":
    unittest.main()
