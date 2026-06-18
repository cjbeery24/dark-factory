"""S41: TE timeout-coverage-salvage.

When the Test Engineer times out (exit 124) but left recognized scenario files
on disk, the orchestrator must SALVAGE that work — fall through to the
SA-validate + coverage-floor machinery — instead of aborting with an
unconditional raise (which incinerated 54/55-covered work on bb spec-4
database-foundation). A non-timeout crash, or a timeout with no files, still
aborts (nothing to salvage).

Covers the pure helper (count_scenario_files) and the emitter by execution, and
the orchestrator salvage branch by source inspection — the established
convention for `_step_scenario_generation` (see test_te_warm_session.py /
test_adapter_staging.py; the loop has no real-execution harness).

F Move 2b: the Test Engineer generation block (which owns the salvage branch)
was extracted verbatim from `_step_scenario_generation` into
`_invoke_test_engineer_generation` so the TE/QA fan-out has a single callable
for the TE branch. The salvage source-inspection invariants below now live in
the extracted helper.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase
from darkfactory.events.types import emit_scenario_timeout_salvage
from darkfactory.models.events import EventType
from darkfactory.scenarios.detection import count_scenario_files
from darkfactory.utils.json_helpers import read_jsonl

_SRC = Path(__file__).resolve().parents[2] / "darkfactory" / "pipeline" / "orchestrator.py"


def _scenario_gen_body() -> str:
    src = _SRC.read_text()
    start = src.find("def _invoke_test_engineer_generation(")
    assert start != -1, "_invoke_test_engineer_generation not found"
    nxt = src.find("\ndef ", start + 1)
    return src[start: nxt if nxt != -1 else len(src)]


class TestCountScenarioFiles(TempDirTestCase):
    def test_zero_when_dir_missing(self) -> None:
        self.assertEqual(count_scenario_files(self.tmp_dir / "nope"), 0)

    def test_zero_on_empty_dir(self) -> None:
        d = self.tmp_dir / "scen"
        d.mkdir()
        self.assertEqual(count_scenario_files(d), 0)

    def test_counts_recognized_test_files_only(self) -> None:
        d = self.tmp_dir / "scen"
        (d / "sub").mkdir(parents=True)
        # recognized
        (d / "a.scenarios.test.ts").write_text("x")
        (d / "b.spec.ts").write_text("x")
        (d / "sub" / "c.test.mjs").write_text("x")  # recursive
        # NOT recognized (helpers, docs)
        (d / "helpers.ts").write_text("x")
        (d / "COVERAGE-GAPS.md").write_text("x")
        self.assertEqual(count_scenario_files(d), 3)

    def test_excludes_directories_matching_pattern(self) -> None:
        d = self.tmp_dir / "scen"
        (d / "test_helpers").mkdir(parents=True)  # dir matching **/test_*
        (d / "real.test.ts").write_text("x")
        # is_file() filter => the directory is not counted
        self.assertEqual(count_scenario_files(d), 1)

    def test_no_double_count_across_patterns(self) -> None:
        # A file that could match multiple globs is counted once (dedup by path).
        d = self.tmp_dir / "scen"
        d.mkdir()
        (d / "x.test.ts").write_text("x")
        self.assertEqual(count_scenario_files(d), 1)


class TestEmitScenarioTimeoutSalvage(TempDirTestCase):
    def test_emits_expected_event_type_and_fields(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_scenario_timeout_salvage(
            ef, spec="db-foundation", run_id="r1", attempt=1, file_count=14,
        )
        rows = read_jsonl(ef)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event"], EventType.SCENARIO_TIMEOUT_SALVAGE.value)
        self.assertEqual(rows[0]["spec"], "db-foundation")
        self.assertEqual(rows[0]["run_id"], "r1")
        self.assertEqual(rows[0]["data"]["attempt"], 1)
        self.assertEqual(rows[0]["data"]["file_count"], 14)


class TestSalvageBranchSource(unittest.TestCase):
    """Source-inspection invariants on the salvage branch (convention for
    `_step_scenario_generation`)."""

    def setUp(self) -> None:
        self.body = _scenario_gen_body()

    def test_salvage_gated_on_timeout_files_and_validation_enabled(self) -> None:
        # The exit-124-with-files salvage must be guarded on the timeout signal,
        # the presence of recognized files, AND sa_max_retries > 0 — never a
        # blanket "proceed on any non-zero exit". The sa_max_retries gate
        # (Codex) ensures the SA-validate (tsc) loop actually runs on the
        # salvaged files; without it, salvage could ship unvalidated scenarios.
        self.assertIn("result.timed_out", self.body)
        self.assertIn("config.sa_max_retries > 0", self.body)
        self.assertIn("has_scenario_files(scenarios_dir)", self.body)

    def test_salvage_gate_is_a_single_and_conjunction(self) -> None:
        # The three conditions must be ANDed in the salvage guard (not an OR
        # that could fire on any one). Locate the guard and assert all three
        # appear within it before the body.
        gi = self.body.find("if (\n")
        # the salvage guard is the first multi-line `if (` after the exit-code
        # check; assert the conjunction terms cluster together.
        guard_region = self.body[self.body.find("result.timed_out"):]
        guard_region = guard_region[: guard_region.find("):")]
        self.assertIn("and config.sa_max_retries > 0", guard_region)
        self.assertIn("and has_scenario_files(scenarios_dir)", guard_region)

    def test_salvage_emits_event_and_breaks(self) -> None:
        # Salvage is visible (emits the event) and falls through (break), not
        # silently and not via raise.
        self.assertIn("emit_scenario_timeout_salvage", self.body)
        # the salvage branch's body contains a break (fall-through to validate +
        # coverage floor), located before the surviving unconditional raise.
        salvage_idx = self.body.find("result.timed_out")
        raise_idx = self.body.find(
            'raise DarkFactoryError(f"test engineer scenario generation failed',
        )
        self.assertNotEqual(salvage_idx, -1)
        self.assertNotEqual(raise_idx, -1)
        # the salvage branch precedes the raise...
        self.assertLess(salvage_idx, raise_idx)
        # ...and a break sits between the guard and the raise (the fall-through).
        between = self.body[salvage_idx:raise_idx]
        self.assertIn("break", between)

    def test_non_timeout_crash_still_raises(self) -> None:
        # The hard abort for a genuine crash (non-timeout, or timeout-no-files)
        # must remain — salvage only narrows the timeout-with-files case.
        self.assertIn(
            'raise DarkFactoryError(f"test engineer scenario generation failed '
            '(exit {result.exit_code})")',
            self.body,
        )

    def test_salvage_requires_genuine_tsc_pass_not_skip(self) -> None:
        # Codex backstop: on the salvage path, anything other than a genuine tsc
        # compile-pass (toolchain unavailable/hung/skipped, truncated, or code
        # errors) must NOT be accepted as the compile backstop for timed-out
        # (possibly truncated) output — it must abort instead.
        self.assertIn("_te_timeout_salvaged", self.body)
        self.assertIn("typescript_compilation", self.body)
        # S0(b): the contract is the STRUCTURED compile_status field, not a
        # "skipped" substring in the detail (which could not tell a truncated
        # file — only "Cannot find module" errors — from a genuine skip). Ship
        # ONLY on compile_status == "passed".
        self.assertIn("compile_status", self.body)
        self.assertIn('compile_status != "passed"', self.body)
        # validation-never-ran (sentinel coexists) is ALSO treated as unsafe on
        # the salvage path (Codex r3), not just a non-passing tsc.
        self.assertIn("validation is None or tsc_not_validated", self.body)
        # the salvage flag is set at the break and consulted after the
        # validate loop (ordering: set before the genuine-tsc check).
        set_idx = self.body.find("_te_timeout_salvaged = True")
        check_idx = self.body.find("if _te_timeout_salvaged:")
        self.assertNotEqual(set_idx, -1)
        self.assertNotEqual(check_idx, -1)
        self.assertLess(set_idx, check_idx)

    def test_salvage_falls_into_validate_and_floor(self) -> None:
        # After the salvage break, the SA-validate loop and the coverage floor
        # must still run (they sit after the for-loop). Guard their presence so
        # the salvaged files get tsc-validated AND the missing-anchor gap gets
        # targeted regen rather than shipping unvalidated/incomplete work.
        self.assertIn("validate_scenarios(", self.body)
        self.assertIn("_enforce_coverage_floor(", self.body)
        # ordering: the salvage break is inside the for-loop; the floor call is
        # after it. Anchor on ``result.timed_out`` (the salvage guard's first
        # term) rather than a contiguous multi-term string — the guard is split
        # across lines with ``config.sa_max_retries`` between the terms, so the
        # old contiguous ``find`` returned -1 and the assertion passed vacuously.
        salvage_idx = self.body.find("result.timed_out")
        floor_idx = self.body.find("_enforce_coverage_floor(")
        self.assertNotEqual(salvage_idx, -1, "salvage guard not found in helper body")
        self.assertNotEqual(floor_idx, -1, "coverage floor call not found in helper body")
        self.assertLess(salvage_idx, floor_idx)


if __name__ == "__main__":
    unittest.main()
