"""Task #18 — Setup-output observability for silent-no-op substrates.

When a substrate ``bootstrap_check`` fails AFTER the Setup command exits 0
(the migration ran but established nothing), the gate captures the Setup
command's stdout/stderr so the Oracle can author root-cause feedback. The
captured text is barrier-audited and dropped on any leak.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.pipeline.quality_gate import _persist_substrate_setup_output
from darkfactory.pipeline.iteration import _format_substrate_setup_output_inline
from darkfactory.utils.subprocess_runner import RunResult


def _write_findings(reports: Path, spec: str, status: str) -> None:
    (reports / f"{spec}-substrate-findings.json").write_text(
        json.dumps({"status": status, "findings": [
            {"severity": "error", "code": "substrate-bootstrap-not-applied",
             "message": "table absent"}
        ]}),
        encoding="utf-8",
    )


class TestPersistSetupOutput(unittest.TestCase):
    SPEC = "spec-x"

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        self.reports = self.proj / ".dark-factory" / "reports"
        self.reports.mkdir(parents=True)
        (self.proj / ".dark-factory" / "holdouts").mkdir()
        (self.proj / ".dark-factory" / "scenarios" / self.SPEC).mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _out_file(self) -> Path:
        return self.reports / f"{self.SPEC}-substrate-setup-output.txt"

    def test_writes_on_fail_with_output(self) -> None:
        _write_findings(self.reports, self.SPEC, "fail")
        rr = RunResult(exit_code=0, stdout="applying migrations...\n", stderr="warn: skipped\n")
        _persist_substrate_setup_output(self.proj, self.SPEC, rr)
        self.assertTrue(self._out_file().exists())
        body = self._out_file().read_text()
        self.assertIn("applying migrations", body)
        self.assertIn("warn: skipped", body)

    def test_noop_when_status_pass(self) -> None:
        _write_findings(self.reports, self.SPEC, "pass")
        rr = RunResult(exit_code=0, stdout="all good\n")
        _persist_substrate_setup_output(self.proj, self.SPEC, rr)
        self.assertFalse(self._out_file().exists())

    def test_noop_when_no_findings_file(self) -> None:
        rr = RunResult(exit_code=0, stdout="output\n")
        _persist_substrate_setup_output(self.proj, self.SPEC, rr)
        self.assertFalse(self._out_file().exists())

    def test_noop_when_output_empty(self) -> None:
        # Pre-seed stale to prove the empty-output exit also clears it.
        self._out_file().write_text("STALE\n")
        _write_findings(self.reports, self.SPEC, "fail")
        rr = RunResult(exit_code=0, stdout="", stderr="   ")
        _persist_substrate_setup_output(self.proj, self.SPEC, rr)
        self.assertFalse(self._out_file().exists())

    def test_dropped_on_holdout_filename_leak(self) -> None:
        # A migration that echoes a holdout test filename must be dropped
        # whole — the barrier audit catches it.
        (self.proj / ".dark-factory" / "holdouts" / "rls-boundary.test.ts").write_text("x")
        _write_findings(self.reports, self.SPEC, "fail")
        rr = RunResult(exit_code=0, stdout="reading rls-boundary.test.ts ...\n")
        _persist_substrate_setup_output(self.proj, self.SPEC, rr)
        self.assertFalse(self._out_file().exists())

    def test_dropped_on_forbidden_path_prefix(self) -> None:
        _write_findings(self.reports, self.SPEC, "fail")
        rr = RunResult(
            exit_code=0,
            stdout="cat /project/.dark-factory/holdouts/secret.ts\n",
        )
        _persist_substrate_setup_output(self.proj, self.SPEC, rr)
        self.assertFalse(self._out_file().exists())

    def test_leak_caught_even_when_split_by_truncation(self) -> None:
        # Audit runs on the FULL text before the 8KB tail truncation, so a
        # leak in the head (that truncation would drop) still suppresses the
        # whole capture (defensive — no partial leak written).
        (self.proj / ".dark-factory" / "holdouts" / "leak.test.ts").write_text("x")
        _write_findings(self.reports, self.SPEC, "fail")
        rr = RunResult(
            exit_code=0,
            stdout="leak.test.ts\n" + ("A" * 9000),
        )
        _persist_substrate_setup_output(self.proj, self.SPEC, rr)
        self.assertFalse(self._out_file().exists())

    def test_truncates_to_tail(self) -> None:
        _write_findings(self.reports, self.SPEC, "fail")
        rr = RunResult(exit_code=0, stdout="HEAD" + ("x" * 9000) + "TAIL")
        _persist_substrate_setup_output(self.proj, self.SPEC, rr)
        body = self._out_file().read_text()
        self.assertLessEqual(len(body), 8192)
        self.assertIn("TAIL", body)
        self.assertNotIn("HEAD", body)

    def test_stderr_only_writes(self) -> None:
        _write_findings(self.reports, self.SPEC, "fail")
        rr = RunResult(exit_code=0, stdout="", stderr="ERROR: migration aborted\n")
        _persist_substrate_setup_output(self.proj, self.SPEC, rr)
        self.assertTrue(self._out_file().exists())
        self.assertIn("migration aborted", self._out_file().read_text())

    def test_malformed_findings_json_noop(self) -> None:
        (self.reports / f"{self.SPEC}-substrate-findings.json").write_text("{not json")
        rr = RunResult(exit_code=0, stdout="output\n")
        _persist_substrate_setup_output(self.proj, self.SPEC, rr)
        self.assertFalse(self._out_file().exists())

    def test_non_dict_findings_json_noop(self) -> None:
        (self.reports / f"{self.SPEC}-substrate-findings.json").write_text('["a", "b"]')
        rr = RunResult(exit_code=0, stdout="output\n")
        _persist_substrate_setup_output(self.proj, self.SPEC, rr)
        self.assertFalse(self._out_file().exists())

    def test_stale_artifact_removed_when_current_pass(self) -> None:
        # THE bug Codex caught: a .txt from a prior failing iteration must
        # NOT survive into a later passing iteration (the Oracle formatter
        # inlines any existing file). Unlink-at-start guarantees the file
        # exists iff THIS iteration produced a clean capture.
        self._out_file().write_text("STALE prior-iteration output\n")
        _write_findings(self.reports, self.SPEC, "pass")
        rr = RunResult(exit_code=0, stdout="all good now\n")
        _persist_substrate_setup_output(self.proj, self.SPEC, rr)
        self.assertFalse(self._out_file().exists())

    def test_stale_artifact_removed_when_findings_absent(self) -> None:
        self._out_file().write_text("STALE\n")
        rr = RunResult(exit_code=0, stdout="x\n")  # no findings file at all
        _persist_substrate_setup_output(self.proj, self.SPEC, rr)
        self.assertFalse(self._out_file().exists())

    def test_stale_artifact_removed_on_leak(self) -> None:
        # A prior clean capture must be cleared even when THIS iteration's
        # output is suppressed by the barrier audit.
        self._out_file().write_text("STALE clean output\n")
        (self.proj / ".dark-factory" / "holdouts" / "h.test.ts").write_text("x")
        _write_findings(self.reports, self.SPEC, "fail")
        rr = RunResult(exit_code=0, stdout="echo h.test.ts\n")
        _persist_substrate_setup_output(self.proj, self.SPEC, rr)
        self.assertFalse(self._out_file().exists())


class TestFormatSetupOutputInline(unittest.TestCase):
    SPEC = "spec-x"

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        self.reports = self.proj / ".dark-factory" / "reports"
        self.reports.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_when_absent(self) -> None:
        self.assertEqual(_format_substrate_setup_output_inline(self.proj, self.SPEC), "")

    def test_empty_when_blank(self) -> None:
        (self.reports / f"{self.SPEC}-substrate-setup-output.txt").write_text("   \n")
        self.assertEqual(_format_substrate_setup_output_inline(self.proj, self.SPEC), "")

    def test_block_when_present(self) -> None:
        (self.reports / f"{self.SPEC}-substrate-setup-output.txt").write_text(
            "migrate: applied 0 of 1\n"
        )
        block = _format_substrate_setup_output_inline(self.proj, self.SPEC)
        self.assertIn("Setup Command Output", block)
        self.assertIn("migrate: applied 0 of 1", block)
        self.assertIn("silent no-op", block)


if __name__ == "__main__":
    unittest.main()
