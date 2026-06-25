"""P1 TE-decomposition: batched Test-Engineer generation.

On heavy specs the monolithic single TE generate turn exceeds the model's max
output tokens (one ~16.5K-token thinking block walls the 30-min budget,
``exit 124 reason=max_output_tokens``). The fix splits the reasoning SCOPE:
invoke the TE once per small BATCH of anchors so each turn reasons over only K
anchors (Option B — sequential, cold per-batch).

These tests cover:
  * the pure ``_batch_anchor_ids`` chunker by execution;
  * the per-batch loop ``_run_batched_te_generation`` by execution with a
    mocked ``invoke_agent`` (cold invocations, batch-scoped prompts, per-batch
    failure isolation, timeout-salvage flag);
  * the wiring inside ``_invoke_test_engineer_generation`` (K=0 falls to the
    monolithic path; K>0 routes to the batched helper) by source inspection —
    the established convention for the orchestrator body (see
    test_te_timeout_salvage.py / test_te_warm_session.py; the full step has no
    real-execution harness).
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import SpecRun
from darkfactory.spec.anchors import AnchorManifest
from darkfactory.pipeline import orchestrator as orch

_SRC = Path(__file__).resolve().parents[2] / "darkfactory" / "pipeline" / "orchestrator.py"


def _scenario_gen_body() -> str:
    src = _SRC.read_text()
    start = src.find("def _invoke_test_engineer_generation(")
    assert start != -1, "_invoke_test_engineer_generation not found"
    nxt = src.find("\ndef ", start + 1)
    return src[start: nxt if nxt != -1 else len(src)]


def _ok(**kw) -> AgentResult:
    return AgentResult(role=AgentRole.TEST_ENGINEER, exit_code=0, **kw)


# ---------------------------------------------------------------------------
# _batch_anchor_ids (pure chunker)
# ---------------------------------------------------------------------------
class TestBatchAnchorIds(unittest.TestCase):
    def test_off_when_batch_size_zero(self) -> None:
        m = AnchorManifest(ac_ids=["AC-1", "AC-2"], ec_ids=["EC-1"], fr_ids=["FR-1"])
        self.assertEqual(orch._batch_anchor_ids(m, 0), [])

    def test_empty_when_manifest_none(self) -> None:
        self.assertEqual(orch._batch_anchor_ids(None, 2), [])

    def test_empty_when_no_anchors(self) -> None:
        self.assertEqual(orch._batch_anchor_ids(AnchorManifest(), 2), [])

    def test_chunks_ac_then_ec_then_fr_in_order(self) -> None:
        m = AnchorManifest(
            ac_ids=["AC-1", "AC-2", "AC-3"],
            ec_ids=["EC-1"],
            fr_ids=["FR-1", "FR-2"],
        )
        # ids = AC-1, AC-2, AC-3, EC-1, FR-1, FR-2 chunked by 2
        self.assertEqual(
            orch._batch_anchor_ids(m, 2),
            [["AC-1", "AC-2"], ["AC-3", "EC-1"], ["FR-1", "FR-2"]],
        )

    def test_last_chunk_short(self) -> None:
        m = AnchorManifest(ac_ids=["AC-1", "AC-2", "AC-3"])
        self.assertEqual(orch._batch_anchor_ids(m, 2), [["AC-1", "AC-2"], ["AC-3"]])

    def test_batch_size_one(self) -> None:
        m = AnchorManifest(ac_ids=["AC-1"], ec_ids=["EC-1"])
        self.assertEqual(orch._batch_anchor_ids(m, 1), [["AC-1"], ["EC-1"]])


# ---------------------------------------------------------------------------
# _run_batched_te_generation (per-batch cold loop)
# ---------------------------------------------------------------------------
class TestRunBatchedTeGeneration(unittest.TestCase):
    def setUp(self) -> None:
        self.spec_run = SpecRun(spec_name="demo", run_id="r1")
        self.config = PipelineConfig()  # sa_max_retries defaults to 2 (> 0)

    def _call(self, batches, scenarios_dir, side_effect):
        mock = MagicMock(side_effect=side_effect)
        with patch.object(orch, "invoke_agent", mock) as m:
            salvaged = orch._run_batched_te_generation(
                base_prompt="BASE-PROMPT",
                batches=batches,
                scenarios_dir=scenarios_dir,
                spec_run=self.spec_run,
                config=self.config,
                barriers={"sentinel": "barriers"},  # opaque object, identity-checked
                project_dir=scenarios_dir.parent,
                contracts_ro_args=["-v", "x:y:ro"],
                events_file=None,
                audit_path=None,
            )
        return salvaged, m

    # (a) ceil(N/2) cold invocations each scoped to its own anchors + prefix
    def test_issues_one_cold_invocation_per_batch_with_scoped_prompt(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sd = Path(td) / "scen"
            sd.mkdir()
            batches = [["AC-1", "AC-2"], ["AC-3", "EC-1"], ["FR-1"]]
            calls = []

            def fake(role, prompt, **kw):
                calls.append((role, prompt, kw))
                return _ok()

            salvaged, m = self._call(batches, sd, fake)

            self.assertFalse(salvaged)
            # ceil(5/2) is 3 batches here (already pre-chunked) -> 3 calls
            self.assertEqual(m.call_count, 3)
            for bi, (role, prompt, kw) in enumerate(calls):
                # invariant 1: same role on every batch
                self.assertEqual(role, AgentRole.TEST_ENGINEER)
                # cold: empty session id, no warm continue
                self.assertEqual(kw["session_container_id"], "")
                self.assertNotIn("use_continue", kw)  # cold path never continues
                self.assertEqual(kw["phase"], "generate")
                self.assertFalse(kw["dry_run"])
                self.assertEqual(kw["attempt_offset"], bi)
                # invariant 1: same barriers object identity on every batch
                self.assertIs(kw["barriers"], calls[0][2]["barriers"])
                # prompt carries the base + only THIS batch's anchors + prefix
                self.assertIn("BASE-PROMPT", prompt)
                self.assertIn(f"BATCH {bi + 1} of 3", prompt)
                self.assertIn(f"Prefix EVERY file you write with 'b{bi}-'", prompt)
                for a in batches[bi]:
                    self.assertIn(a, prompt)
                # sibling anchors NOT named in this batch's scoped list line
                others = [a for j, b in enumerate(batches) if j != bi for a in b]
                scoped_line = prompt[prompt.find("generate visible scenarios ONLY"):]
                scoped_line = scoped_line[: scoped_line.find(".")]
                for o in others:
                    self.assertNotIn(o, scoped_line)

    def test_barriers_object_identical_across_batches(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sd = Path(td) / "scen"
            sd.mkdir()
            seen = []

            def fake(role, prompt, **kw):
                seen.append(kw["barriers"])
                return _ok()

            self._call([["AC-1"], ["AC-2"], ["AC-3"]], sd, fake)
            self.assertEqual(len(seen), 3)
            self.assertIs(seen[0], seen[1])
            self.assertIs(seen[1], seen[2])

    # (b) per-batch failure isolation: a non-timeout exit!=0 does NOT raise and
    # the remaining batches still run.
    def test_non_timeout_failure_does_not_raise_and_other_batches_run(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sd = Path(td) / "scen"
            sd.mkdir()
            n_calls = {"i": 0}

            def fake(role, prompt, **kw):
                n_calls["i"] += 1
                # second batch crashes (non-timeout); others succeed
                if n_calls["i"] == 2:
                    return AgentResult(
                        role=AgentRole.TEST_ENGINEER, exit_code=1, timed_out=False,
                    )
                return _ok()

            salvaged, m = self._call([["AC-1"], ["AC-2"], ["AC-3"]], sd, fake)
            # did not raise; all three batches attempted
            self.assertEqual(m.call_count, 3)
            # a non-timeout crash is NOT a salvage
            self.assertFalse(salvaged)

    # (d) a timed-out batch WITH files sets the salvage flag.
    def test_timeout_with_files_sets_salvage_flag(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sd = Path(td) / "scen"
            sd.mkdir()

            def fake(role, prompt, **kw):
                # simulate the TE having written a recognized scenario file
                # before the wall, then timing out.
                (sd / "b0-thing.scenarios.test.ts").write_text("x", encoding="utf-8")
                return AgentResult(
                    role=AgentRole.TEST_ENGINEER, exit_code=124, timed_out=True,
                )

            salvaged, m = self._call([["AC-1"]], sd, fake)
            self.assertTrue(salvaged)
            self.assertEqual(m.call_count, 1)

    def test_timeout_without_files_is_not_salvage(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sd = Path(td) / "scen"
            sd.mkdir()

            def fake(role, prompt, **kw):
                return AgentResult(
                    role=AgentRole.TEST_ENGINEER, exit_code=124, timed_out=True,
                )

            salvaged, m = self._call([["AC-1"]], sd, fake)
            self.assertFalse(salvaged)

    def test_timeout_with_files_but_validation_disabled_is_not_salvage(self) -> None:
        # invariant 3: salvage only marked when the SA-validate loop will run
        # (sa_max_retries > 0). With it disabled, do not pretend to salvage.
        import tempfile
        self.config.sa_max_retries = 0
        with tempfile.TemporaryDirectory() as td:
            sd = Path(td) / "scen"
            sd.mkdir()

            def fake(role, prompt, **kw):
                (sd / "b0-thing.scenarios.test.ts").write_text("x", encoding="utf-8")
                return AgentResult(
                    role=AgentRole.TEST_ENGINEER, exit_code=124, timed_out=True,
                )

            salvaged, _ = self._call([["AC-1"]], sd, fake)
            self.assertFalse(salvaged)


# ---------------------------------------------------------------------------
# Wiring inside _invoke_test_engineer_generation (source inspection)
# ---------------------------------------------------------------------------
class TestBatchedWiringSource(unittest.TestCase):
    def setUp(self) -> None:
        self.body = _scenario_gen_body()

    # (c) K=0 / dry_run takes the monolithic path; the helper is gated on
    # _use_batched, which is False when batches is empty or dry_run.
    def test_use_batched_gated_on_batches_and_not_dry_run(self) -> None:
        self.assertIn("_batches = _batch_anchor_ids(", self.body)
        self.assertIn("_use_batched = bool(_batches) and not dry_run", self.body)

    def test_batched_helper_only_called_when_use_batched(self) -> None:
        # the helper call sits inside `if _use_batched:`
        idx_guard = self.body.find("if _use_batched:")
        idx_call = self.body.find("_run_batched_te_generation(")
        self.assertNotEqual(idx_guard, -1)
        self.assertNotEqual(idx_call, -1)
        self.assertLess(idx_guard, idx_call)

    def test_monolithic_block_guarded_by_not_use_batched(self) -> None:
        # the whole warm-session block is skipped on the batched path.
        self.assertIn("if not _use_batched:", self.body)
        guard = self.body.find("if not _use_batched:")
        warm = self.body.find('te_session_id = ""')
        self.assertNotEqual(guard, -1)
        self.assertNotEqual(warm, -1)
        self.assertLess(guard, warm)

    def test_salvage_flag_threaded_from_batched_helper(self) -> None:
        # invariant 3: _te_timeout_salvaged is set from the batched helper's
        # return so the function-body salvage backstop runs on batched output.
        self.assertIn(
            "_te_timeout_salvaged = _run_batched_te_generation(", self.body,
        )

    def test_terminal_guards_run_after_either_path(self) -> None:
        # invariant 2 (assembly atomicity): fingerprints + SA-validate + floor
        # come AFTER both the batched call and the monolithic block.
        batched_idx = self.body.find("_run_batched_te_generation(")
        fp_idx = self.body.find("_emit_scenario_fingerprints(")
        validate_idx = self.body.find("validate_scenarios(")
        floor_idx = self.body.find("_enforce_coverage_floor(")
        self.assertLess(batched_idx, fp_idx)
        self.assertLess(batched_idx, validate_idx)
        self.assertLess(batched_idx, floor_idx)


if __name__ == "__main__":
    unittest.main()
