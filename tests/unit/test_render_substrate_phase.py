"""Tests for scripts/lib/render_substrate_phase.py — in-gate phase renderer."""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase


def _load_renderer():
    """Import the script as a module despite living under scripts/lib/.

    The script is intentionally stdlib-only (mounts as part of scripts/
    in the gate container, NOT as part of the darkfactory package), so
    we load it via spec_from_file_location.
    """
    src = Path(__file__).resolve().parents[2] / "scripts" / "lib" / "render_substrate_phase.py"
    spec = importlib.util.spec_from_file_location("render_substrate_phase", src)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_renderer = _load_renderer()


class _RendererCase(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.report = self.tmp_dir / "report.md"
        self.summary = self.tmp_dir / "summary.md"
        self.findings = self.tmp_dir / "findings.json"
        # Pre-populate with a header so we can assert appends, not overwrites.
        self.report.write_text("# Quality Gate Report\n\n| Gate | Status | Details |\n")
        self.summary.write_text("# Summary\n\n| Gate | Status |\n")


class TestRenderMissingFindingsFile(_RendererCase):
    def test_fail_when_findings_file_absent(self) -> None:
        # Absent file means the dispatcher didn't run / crashed before
        # writing — that's a bug upstream, not a legitimate skip.
        rc = _renderer.render(self.report, self.summary, self.findings)
        self.assertEqual(rc, 1)
        self.assertIn("Substrate Verification | FAIL", self.report.read_text())
        self.assertIn("findings file missing", self.report.read_text())
        self.assertIn("Substrate Verification | FAIL", self.summary.read_text())

    def test_skip_requires_explicit_envelope(self) -> None:
        # Explicit {"status":"skip"} envelope should still render SKIP.
        self.findings.write_text(json.dumps({"status": "skip", "findings": []}))
        rc = _renderer.render(self.report, self.summary, self.findings)
        self.assertEqual(rc, 0)
        self.assertIn("Substrate Verification | SKIP", self.report.read_text())


class TestRenderSkipPassFail(_RendererCase):
    def test_skip(self) -> None:
        self.findings.write_text(json.dumps({"status": "skip", "findings": []}))
        rc = _renderer.render(self.report, self.summary, self.findings)
        self.assertEqual(rc, 0)
        self.assertIn("Substrate Verification | SKIP", self.report.read_text())

    def test_pass(self) -> None:
        self.findings.write_text(json.dumps({"status": "pass", "findings": []}))
        rc = _renderer.render(self.report, self.summary, self.findings)
        self.assertEqual(rc, 0)
        self.assertIn("Substrate Verification | PASS", self.report.read_text())
        self.assertIn("Substrate Verification | PASS", self.summary.read_text())

    def test_fail_with_finding_details(self) -> None:
        payload = {
            "status": "fail",
            "findings": [
                {"severity": "error", "code": "substrate-guc-unregistered",
                 "message": "GUC app.tenant_id not registered", "path": "x.toml"},
                {"severity": "error", "code": "substrate-rls-missing",
                 "message": "RLS off on table users", "path": "x.toml"},
            ],
        }
        self.findings.write_text(json.dumps(payload))
        rc = _renderer.render(self.report, self.summary, self.findings)
        self.assertEqual(rc, 1)
        report_text = self.report.read_text()
        self.assertIn("Substrate Verification | FAIL | [pipeline] 2 findings", report_text)
        self.assertIn("substrate-guc-unregistered: GUC app.tenant_id not registered", report_text)
        self.assertIn("substrate-rls-missing: RLS off on table users", report_text)
        self.assertIn("Substrate Verification | FAIL", self.summary.read_text())

    def test_fail_detail_block_mirrored_into_summary(self) -> None:
        """Persisted-but-not-consumed regression test: the substrate
        finding bodies must be written to BOTH report_file and
        summary_file. Reviewer's prompt construction inlines
        ``quality-gate-summary.md`` verbatim (iteration.py:3550); without
        the detail block in summary, Reviewer/Oracle saw only the FAIL
        row and lost the diagnostic bodies that name root causes like
        substrate-bootstrap-not-applied. This test pins the mirroring."""
        payload = {
            "status": "fail",
            "findings": [
                {"severity": "error", "code": "substrate-bootstrap-not-applied",
                 "message": "drizzle-kit migrate exit 0 but tracking table missing",
                 "path": "x.toml"},
            ],
        }
        self.findings.write_text(json.dumps(payload))
        _renderer.render(self.report, self.summary, self.findings)
        summary_text = self.summary.read_text()
        # Table row (was already there)
        self.assertIn("Substrate Verification | FAIL", summary_text)
        # Detail block must now also appear in summary
        self.assertIn("Substrate Verification Failure Details", summary_text)
        self.assertIn(
            "substrate-bootstrap-not-applied: drizzle-kit migrate exit 0 "
            "but tracking table missing",
            summary_text,
        )

    def test_fail_singular_finding_count(self) -> None:
        payload = {
            "status": "fail",
            "findings": [{"severity": "error", "code": "x", "message": "y", "path": ""}],
        }
        self.findings.write_text(json.dumps(payload))
        _renderer.render(self.report, self.summary, self.findings)
        # "1 finding" not "1 findings"
        self.assertIn("| FAIL | [pipeline] 1 finding |", self.report.read_text())


class TestRenderMalformedFindingsFile(_RendererCase):
    def test_unreadable_json_renders_fail_with_explanation(self) -> None:
        self.findings.write_text("{not json")
        rc = _renderer.render(self.report, self.summary, self.findings)
        self.assertEqual(rc, 1)
        self.assertIn("findings file unreadable", self.report.read_text())


class TestMainArgvHandling(_RendererCase):
    def test_main_wrong_argc_returns_2(self) -> None:
        rc = _renderer.main(["render_substrate_phase.py"])
        self.assertEqual(rc, 2)

    def test_main_correct_argc_dispatches(self) -> None:
        self.findings.write_text(json.dumps({"status": "pass", "findings": []}))
        rc = _renderer.main([
            "render_substrate_phase.py",
            str(self.report), str(self.summary), str(self.findings),
        ])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
