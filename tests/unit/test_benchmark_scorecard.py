"""Tests for darkfactory/benchmark/scorecard.py (pure logic, no Docker)."""

from __future__ import annotations

import json
import unittest

from tests.helpers import TempDirTestCase
from darkfactory.benchmark.scorecard import (
    Outcome,
    RunMetrics,
    RunUsage,
    ScorecardRow,
    append_scorecard,
    classify_outcome,
    is_ship,
    parse_run_metrics,
    parse_run_usage,
    row_to_json,
)
from darkfactory.intelligence.cost import cost_from_tokens


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

    def test_last_stage_is_last_agent_completed(self) -> None:
        # last_stage tracks WHERE the run got to — the final AGENT_COMPLETED role.
        p = self._write_events("e.jsonl", [
            {"event": "AGENT_COMPLETED", "run_id": "r5", "data": {"role": "spec-validator"}},
            {"event": "AGENT_COMPLETED", "run_id": "r5", "data": {"role": "architect"}},
            {"event": "AGENT_COMPLETED", "run_id": "r5", "data": {"role": "test-engineer"}},
            {"event": "SPEC_FAILED", "run_id": "r5", "data": {"reason": "TE scenarios failed validation after 3 attempts:\n# Scenario Validation\n..."}},
        ])
        m = parse_run_metrics(p)
        self.assertEqual(m.last_stage, "test-engineer")
        # abort_reason = first non-empty line only (full detail in the archive).
        self.assertEqual(m.abort_reason, "TE scenarios failed validation after 3 attempts:")

    def test_abort_reason_truncated_to_200(self) -> None:
        p = self._write_events("e.jsonl", [
            {"event": "SPEC_FAILED", "run_id": "r6", "data": {"reason": "x" * 500}},
        ])
        self.assertEqual(len(parse_run_metrics(p).abort_reason), 200)

    def test_whitespace_reason_degrades_to_none(self) -> None:
        # A reason of only blank lines/whitespace must yield None, not "".
        p = self._write_events("e.jsonl", [
            {"event": "SPEC_FAILED", "run_id": "rw", "data": {"reason": "   \n \t \n"}},
        ])
        self.assertIsNone(parse_run_metrics(p).abort_reason)

    def test_non_str_reason_does_not_crash(self) -> None:
        # Defensive: a non-str reason (emitter bug) must stringify, not raise.
        p = self._write_events("e.jsonl", [
            {"event": "SPEC_FAILED", "run_id": "rn", "data": {"reason": {"k": "v"}}},
        ])
        m = parse_run_metrics(p)
        self.assertIsInstance(m.abort_reason, str)

    def test_ship_has_no_abort_reason(self) -> None:
        # SPEC_SHIPPED carries no reason → abort_reason stays None; last_stage
        # still reflects the last agent that ran.
        p = self._write_events("e.jsonl", [
            {"event": "AGENT_COMPLETED", "run_id": "r7", "data": {"role": "oracle"}},
            {"event": "SPEC_SHIPPED", "run_id": "r7", "data": {"iterations": 1}},
        ])
        m = parse_run_metrics(p)
        self.assertEqual(m.last_stage, "oracle")
        self.assertIsNone(m.abort_reason)

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


class TestParseRunUsage(TempDirTestCase):
    def _write(self, lines: list[dict]) -> object:
        p = self.tmp_dir / "run-telemetry.jsonl"
        p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        return p

    def _line(self, **over) -> dict:
        d = {
            "run_id": "rX", "role": "spec-validator",
            "model_id": "claude-sonnet-4-20250514",
            "input_tokens": 100, "output_tokens": 200,
            "cache_read_tokens": 50, "cache_creation_tokens": 10,
            "wall_clock_ms": 1000,
        }
        d.update(over)
        return d

    def test_sums_across_agents(self) -> None:
        p = self._write([self._line(), self._line(role="implementer", output_tokens=300)])
        u = parse_run_usage(p)
        self.assertEqual(u.input_tokens, 200)
        self.assertEqual(u.output_tokens, 500)
        self.assertEqual(u.cache_read_tokens, 100)
        self.assertEqual(u.agent_invocations, 2)  # = invocations (retries count), not distinct roles
        self.assertEqual(u.model_id, "claude-sonnet-4-20250514")

    def test_retries_counted_as_invocations(self) -> None:
        # Same role twice (a retry) → 2 invocations, tokens summed (paid for both).
        p = self._write([self._line(), self._line()])
        u = parse_run_usage(p)
        self.assertEqual(u.agent_invocations, 2)
        self.assertEqual(u.output_tokens, 400)

    def test_run_id_filter(self) -> None:
        p = self._write([self._line(run_id="rX"), self._line(run_id="OTHER", output_tokens=999)])
        u = parse_run_usage(p, run_id="rX")
        self.assertEqual(u.agent_invocations, 1)
        self.assertEqual(u.output_tokens, 200)  # the OTHER-run line excluded

    def test_missing_file_zero(self) -> None:
        self.assertEqual(parse_run_usage(self.tmp_dir / "nope.jsonl"), RunUsage())

    def test_malformed_line_tolerated(self) -> None:
        p = self.tmp_dir / "run-telemetry.jsonl"
        p.write_text(json.dumps(self._line()) + "\n{ broken\n")
        self.assertEqual(parse_run_usage(p).agent_invocations, 1)

    def test_modal_model_id(self) -> None:
        p = self._write([
            self._line(model_id="claude-sonnet-4-x"),
            self._line(model_id="claude-sonnet-4-x"),
            self._line(model_id="claude-opus-4-x"),
        ])
        self.assertEqual(parse_run_usage(p).model_id, "claude-sonnet-4-x")

    def test_cost_present_for_known_model(self) -> None:
        # one sonnet line: 100*3e-6 + 200*15e-6 + 50*3e-6*0.1 + 10*3e-6*1.25
        p = self._write([self._line()])
        expected = 100 * 3e-6 + 200 * 15e-6 + 50 * 3e-6 * 0.1 + 10 * 3e-6 * 1.25
        self.assertAlmostEqual(parse_run_usage(p).cost_usd, expected, places=9)

    def test_cost_summed_per_line_mixed_models(self) -> None:
        # Mixed models must be priced PER LINE, not by the modal model.
        p = self._write([
            self._line(model_id="claude-sonnet-4-6", input_tokens=0, output_tokens=1000,
                       cache_read_tokens=0, cache_creation_tokens=0),  # 0.015
            self._line(model_id="claude-opus-4-8", input_tokens=0, output_tokens=1000,
                       cache_read_tokens=0, cache_creation_tokens=0),  # 0.025
        ])
        self.assertAlmostEqual(parse_run_usage(p).cost_usd, 0.015 + 0.025, places=9)

    def test_cost_none_when_any_model_unknown(self) -> None:
        # An unrecognized model on a real line → incomplete total → None
        # (don't report a number we know undercounts).
        p = self._write([
            self._line(model_id="claude-sonnet-4-6"),
            self._line(model_id="some-unknown-model"),
        ])
        u = parse_run_usage(p)
        self.assertIsNone(u.cost_usd)
        self.assertEqual(u.agent_invocations, 2)  # tokens still summed

    def test_cost_none_when_no_telemetry(self) -> None:
        self.assertIsNone(parse_run_usage(self.tmp_dir / "nope.jsonl").cost_usd)


class TestCostFromTokens(unittest.TestCase):
    def test_sonnet_with_cache(self) -> None:
        # sonnet: in=3e-6, out=15e-6; cache_read=0.1x in, cache_create=1.25x in.
        c = cost_from_tokens(
            "claude-sonnet-4-20250514",
            input_tokens=1000, output_tokens=2000,
            cache_read_tokens=10000, cache_creation_tokens=4000,
        )
        expected = (1000 * 3e-6 + 2000 * 15e-6
                    + 10000 * 3e-6 * 0.1 + 4000 * 3e-6 * 1.25)
        self.assertAlmostEqual(c, expected, places=9)

    def test_opus_rate_is_current_not_stale(self) -> None:
        # Opus 4.x is $5/$25 per 1M (NOT the stale $15/$75 in config defaults).
        opus = cost_from_tokens("claude-opus-4-8", input_tokens=1000, output_tokens=1000)
        self.assertAlmostEqual(opus, 1000 * 5e-6 + 1000 * 25e-6, places=9)  # 0.030
        sonnet = cost_from_tokens("claude-sonnet-4-6", input_tokens=1000, output_tokens=1000)
        self.assertGreater(opus, sonnet)

    def test_unknown_model_none(self) -> None:
        self.assertIsNone(cost_from_tokens("gpt-4o", input_tokens=1000, output_tokens=1000))
        self.assertIsNone(cost_from_tokens("", input_tokens=1000, output_tokens=1000))

    def test_zero_usage(self) -> None:
        self.assertEqual(cost_from_tokens("claude-sonnet-4-6", input_tokens=0, output_tokens=0), 0.0)


if __name__ == "__main__":
    unittest.main()
