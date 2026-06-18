"""F Move 2c (2026-06-16): TE warm-session self-correction loop.

Covers the pure helpers by execution and the warm-session loop invariants by
source inspection (the established convention for `_step_scenario_generation`
in test_adapter_staging.py — this loop has no real-execution harness; the
dual-gate reviewers decide whether to mandate one via extraction).
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.models.pipeline import SpecRun
from darkfactory.pipeline.orchestrator import (
    _dump_misnamed_failure,
    _format_misnamed_corrective,
)

_SRC = Path(__file__).resolve().parents[2] / "darkfactory" / "pipeline" / "orchestrator.py"


def _scenario_gen_body() -> str:
    # F Move 2b: the Test Engineer generation block (including the warm-session
    # self-correction loop) was extracted verbatim from _step_scenario_generation
    # into _invoke_test_engineer_generation so the TE/QA fan-out has a single
    # callable for the TE branch. The warm-session source-inspection invariants
    # below now live in the extracted helper.
    src = _SRC.read_text()
    start = src.find("def _invoke_test_engineer_generation(")
    assert start != -1, "_invoke_test_engineer_generation not found"
    # bound to the next top-level def so we don't match unrelated code
    nxt = src.find("\ndef ", start + 1)
    return src[start: nxt if nxt != -1 else len(src)]


class TestFormatMisnamedCorrective(unittest.TestCase):
    def test_deterministic(self):
        files = ["b.js", "a.js"]
        self.assertEqual(
            _format_misnamed_corrective(files), _format_misnamed_corrective(files)
        )

    def test_names_files_and_required_pattern(self):
        out = _format_misnamed_corrective(["fr-1-workspace-install.js"])
        self.assertIn("fr-1-workspace-install.js", out)
        self.assertIn(".test.ts", out)
        self.assertIn("RENAME", out)
        # must instruct preserve-content (rename only), not regenerate
        self.assertIn("Do NOT regenerate", out)

    def test_caps_long_lists(self):
        files = [f"f{i}.js" for i in range(30)]
        out = _format_misnamed_corrective(files)
        self.assertIn("and 10 more", out)


class TestDumpMisnamedFailure(unittest.TestCase):
    def test_writes_structured_artifact(self):
        with TemporaryDirectory() as td:
            project = Path(td)
            (project / ".dark-factory").mkdir()
            sr = SpecRun(spec_name="demo", run_id="r1")
            _dump_misnamed_failure(project, sr, ["a.js", "b.js"])
            art = project / ".dark-factory" / "failures" / "demo-misnamed-scenarios.json"
            self.assertTrue(art.exists())
            data = json.loads(art.read_text())
            self.assertEqual(data["abort_reason"], "MISNAMED_SCENARIOS")
            self.assertEqual(data["offending_files"], ["a.js", "b.js"])
            self.assertEqual(data["spec"], "demo")

    def test_never_raises_on_unwritable(self):
        # missing parent .dark-factory is created; a truly bad path must not raise
        _dump_misnamed_failure(Path("/nonexistent/xyz"), SpecRun(spec_name="d"), ["a.js"])


class TestWarmSessionLoopStructure(unittest.TestCase):
    """Source-inspection invariants for the warm-session loop (matches the
    existing _step_scenario_generation test convention)."""

    def setUp(self):
        self.body = _scenario_gen_body()

    def test_starts_te_session_gated_on_flag_and_retries(self):
        self.assertIn("config.scenario_warm_session", self.body)
        self.assertIn("start_agent_session(", self.body)
        self.assertIn("AgentRole.TEST_ENGINEER", self.body)

    def test_session_start_failure_degrades_to_cold(self):
        # an exception on start must set te_session_id = "" (cold fallback), not abort
        self.assertIn("cold-retry fallback", self.body)
        self.assertIn("te_session_id = \"\"", self.body)

    def test_use_continue_gated_on_attempt_and_session(self):
        self.assertIn("use_continue=bool(te_session_id and _empty_attempt > 0)", self.body)
        self.assertIn("session_container_id=te_session_id", self.body)

    def test_preserve_and_rename_not_clear_in_warm_misnamed_branch(self):
        # the misnamed+warm branch must NOT clear; only the else (truly-empty /
        # cold) branch clears. Assert the misnamed branch builds a corrective.
        self.assertIn("find_misnamed_scenario_files(", self.body)
        self.assertIn("_format_misnamed_corrective(", self.body)
        self.assertIn("if misnamed and te_session_id:", self.body)

    def test_misnamed_terminal_error_and_artifact(self):
        self.assertIn("MISNAMED_SCENARIOS", self.body)
        self.assertIn("_dump_misnamed_failure(", self.body)

    def test_session_torn_down_in_finally(self):
        self.assertIn("finally:", self.body)
        self.assertIn("stop_agent_session(te_session_id", self.body)

    def test_dry_run_disables_warm(self):
        # warm is gated on `not dry_run`
        self.assertIn("not dry_run and config.scenario_warm_session", self.body)


if __name__ == "__main__":
    unittest.main()
