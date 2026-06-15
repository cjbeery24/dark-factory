"""Tests for the advisory SAST compare-mode (Sprint 30, darkfactory/review/sast.py).

Two classes of test, per the Neo design pass:
- Pure reconciliation + graceful-no-op + offline-argv (no real semgrep needed).
- A FAKE-BINARY wiring test that exercises the REAL subprocess path end-to-end
  (run_semgrep spawns a `semgrep` process, parses its JSON, writes the artifact).
  It does NOT mock run_semgrep — mocking it would reproduce the Sprint-28 bypass
  (a mechanism that "passes" while never actually firing). The fake binary is on
  PATH; everything inside run_semgrep runs for real.

A real-semgrep test (importorskip) covers actual rule matching when the
`dark-factory[sast]` extra is installed; it skip-and-passes otherwise — the same
silent-coverage-gap pattern the CLAUDE.md `[dev]` note warns about, so install the
`[sast]` extra to exercise it.
"""
from __future__ import annotations

import inspect
import json
import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.review import sast
from darkfactory.review.prepass import SecurityFinding, SecurityResult
from darkfactory.review.sast import (
    SastFinding,
    SastResult,
    build_compare,
    run_semgrep,
    write_sast_compare,
)


def _reset_probe() -> None:
    sast._SEMGREP_VERSION = False  # clear per-process probe cache


def _sec(findings, advisory=None) -> SecurityResult:
    return SecurityResult(
        score=15, findings=findings, advisory_findings=advisory or [],
        language="python", scanned=True,
    )


def _rf(pattern, line, sev="HIGH", file="src/a.py") -> SecurityFinding:
    return SecurityFinding(file=file, line_number=line, pattern=pattern,
                           severity=sev, matched_text=f"{pattern}@{line}")


def _sf(category, line, sev="HIGH", file="src/a.py", rule="r") -> SastFinding:
    return SastFinding(file=file, line_number=line, category=category,
                       severity=sev, rule_id=rule, matched_text=f"{category}@{line}")


# ---------------------------------------------------------------------------
# Reconciliation (pure)
# ---------------------------------------------------------------------------


class TestReconcile(unittest.TestCase):
    def test_agree_same_file_category_within_window(self):
        reg = _sec([_rf("sql_injection", 10)])
        s = SastResult(ran=True, findings=[_sf("sql_injection", 11)])  # +1 line, within window
        summary, rows = build_compare(reg, s)
        self.assertEqual(summary["agree"], 1)
        self.assertEqual(summary["regex_only"], 0)
        self.assertEqual(summary["semgrep_only"], 0)
        self.assertEqual(rows[0]["verdict"], "agree")
        self.assertEqual(rows[0]["line_delta"], 1)

    def test_outside_window_is_disagreement(self):
        reg = _sec([_rf("sql_injection", 10)])
        s = SastResult(ran=True, findings=[_sf("sql_injection", 20)])  # far away
        summary, _ = build_compare(reg, s)
        self.assertEqual(summary["agree"], 0)
        self.assertEqual(summary["regex_only"], 1)
        self.assertEqual(summary["semgrep_only"], 1)

    def test_category_mismatch_same_line_is_disagreement(self):
        reg = _sec([_rf("sql_injection", 10)])
        s = SastResult(ran=True, findings=[_sf("xss", 10)])
        summary, _ = build_compare(reg, s)
        self.assertEqual(summary["agree"], 0)
        self.assertEqual(summary["regex_only"], 1)
        self.assertEqual(summary["semgrep_only"], 1)

    def test_regex_only_high_counts_high_severity_only(self):
        reg = _sec([_rf("sql_injection", 10, sev="HIGH"),
                    _rf("hardcoded_secret", 12, sev="MEDIUM")])
        s = SastResult(ran=True, findings=[])
        summary, _ = build_compare(reg, s)
        self.assertEqual(summary["regex_only"], 2)
        self.assertEqual(summary["regex_only_high"], 1)  # only the HIGH one

    def test_uncategorized_semgrep_never_agrees(self):
        reg = _sec([_rf("sql_injection", 10)])
        s = SastResult(ran=True, findings=[_sf("uncategorized", 10)])
        summary, _ = build_compare(reg, s)
        self.assertEqual(summary["agree"], 0)
        self.assertEqual(summary["semgrep_only"], 1)

    def test_advisory_scope_separated_from_gating(self):
        reg = _sec([_rf("sql_injection", 10)], advisory=[_rf("xss", 5, file="src/old.py")])
        s = SastResult(ran=True, findings=[_sf("sql_injection", 10)],
                       advisory_findings=[_sf("xss", 5, file="src/old.py")])
        summary, rows = build_compare(reg, s)
        # Summary counts ONLY the gating surface.
        self.assertEqual(summary["agree"], 1)
        scopes = {r["scope"] for r in rows}
        self.assertEqual(scopes, {"gating", "advisory"})


# ---------------------------------------------------------------------------
# Graceful no-op matrix — run_semgrep NEVER raises, ran=False != clean
# ---------------------------------------------------------------------------


class TestGracefulNoOp(unittest.TestCase):
    def setUp(self):
        _reset_probe()
        self.addCleanup(_reset_probe)

    def test_unsupported_language_does_not_scan(self):
        with TemporaryDirectory() as d:
            r = run_semgrep(Path(d), ["src"], "go", None)
        self.assertFalse(r.ran)
        self.assertIn("no vendored ruleset", r.error)

    def test_semgrep_absent_is_ran_false_not_clean(self, ):
        # Point PATH at an empty dir so `semgrep` is unresolvable.
        with TemporaryDirectory() as empty, TemporaryDirectory() as proj:
            (Path(proj) / "src").mkdir()
            old = os.environ.get("PATH", "")
            os.environ["PATH"] = empty
            try:
                r = run_semgrep(Path(proj), ["src"], "python", None)
            finally:
                os.environ["PATH"] = old
        self.assertFalse(r.ran)
        self.assertNotEqual(r.findings, None)
        self.assertEqual(r.findings, [])

    def test_timeout_yields_ran_false(self):
        import subprocess

        def fake_run(argv, **kw):
            if "--version" in argv:
                return subprocess.CompletedProcess(argv, 0, "1.166.0\n", "")
            raise subprocess.TimeoutExpired(argv, 1)

        with TemporaryDirectory() as proj:
            (Path(proj) / "src").mkdir()
            orig = sast.subprocess.run
            sast.subprocess.run = fake_run
            try:
                r = run_semgrep(Path(proj), ["src"], "python", None)
            finally:
                sast.subprocess.run = orig
        self.assertFalse(r.ran)
        self.assertEqual(r.error, "timeout")

    def test_unparseable_output_yields_ran_false(self):
        import subprocess

        def fake_run(argv, **kw):
            if "--version" in argv:
                return subprocess.CompletedProcess(argv, 0, "1.166.0\n", "")
            return subprocess.CompletedProcess(argv, 2, "not json", "boom")

        with TemporaryDirectory() as proj:
            (Path(proj) / "src").mkdir()
            orig = sast.subprocess.run
            sast.subprocess.run = fake_run
            try:
                r = run_semgrep(Path(proj), ["src"], "python", None)
            finally:
                sast.subprocess.run = orig
        self.assertFalse(r.ran)
        self.assertIn("unparseable", r.error)

    def test_engine_error_with_valid_json_is_ran_false(self):
        # [Codex P1] rc>=2 (engine/config error) MUST be ran=False even if stdout
        # is parseable JSON — a failed scan must never read as clean.
        import subprocess

        def fake_run(argv, **kw):
            if "--version" in argv:
                return subprocess.CompletedProcess(argv, 0, "1.166.0\n", "")
            return subprocess.CompletedProcess(
                argv, 7, json.dumps({"results": [], "errors": ["no rules"]}), "fatal")

        with TemporaryDirectory() as proj:
            (Path(proj) / "src").mkdir()
            orig = sast.subprocess.run
            sast.subprocess.run = fake_run
            try:
                r = run_semgrep(Path(proj), ["src"], "python", None)
            finally:
                sast.subprocess.run = orig
        self.assertFalse(r.ran)
        self.assertIn("engine error", r.error)

    def test_unexpected_json_shape_is_ran_false_not_raise(self):
        # [Codex P2] Valid JSON of an unexpected shape must not raise.
        import subprocess
        for body in ("[1, 2, 3]", '{"results": null}', '"a string"'):
            with self.subTest(body=body):
                def fake_run(argv, _body=body, **kw):
                    if "--version" in argv:
                        return subprocess.CompletedProcess(argv, 0, "1.166.0\n", "")
                    return subprocess.CompletedProcess(argv, 0, _body, "")
                with TemporaryDirectory() as proj:
                    (Path(proj) / "src").mkdir()
                    orig = sast.subprocess.run
                    sast.subprocess.run = fake_run
                    try:
                        r = run_semgrep(Path(proj), ["src"], "python", None)  # must not raise
                    finally:
                        sast.subprocess.run = orig
                self.assertFalse(r.ran)

    def test_out_of_pin_version_skips(self):
        import subprocess

        def fake_run(argv, **kw):
            if "--version" in argv:
                return subprocess.CompletedProcess(argv, 0, "2.0.0\n", "")  # major >= 2
            raise AssertionError("scan must not run when version is out of pin")

        with TemporaryDirectory() as proj:
            (Path(proj) / "src").mkdir()
            orig = sast.subprocess.run
            sast.subprocess.run = fake_run
            try:
                r = run_semgrep(Path(proj), ["src"], "python", None)
            finally:
                sast.subprocess.run = orig
        self.assertFalse(r.ran)


# ---------------------------------------------------------------------------
# Offline / hermetic argv
# ---------------------------------------------------------------------------


class TestOfflineArgv(unittest.TestCase):
    def setUp(self):
        _reset_probe()
        self.addCleanup(_reset_probe)

    def test_argv_is_offline_and_local_config(self):
        import subprocess
        captured = {}

        def fake_run(argv, **kw):
            if "--version" in argv:
                return subprocess.CompletedProcess(argv, 0, "1.166.0\n", "")
            captured["argv"] = argv
            captured["env"] = kw.get("env", {})
            return subprocess.CompletedProcess(argv, 0, json.dumps({"results": [], "errors": []}), "")

        with TemporaryDirectory() as proj:
            (Path(proj) / "src").mkdir()
            orig = sast.subprocess.run
            sast.subprocess.run = fake_run
            try:
                r = run_semgrep(Path(proj), ["src"], "python", None)
            finally:
                sast.subprocess.run = orig
        self.assertTrue(r.ran)
        argv = captured["argv"]
        self.assertIn("--metrics=off", argv)
        self.assertIn("--disable-version-check", argv)
        # --config points at a LOCAL dir, never a registry pack name or URL.
        cfg = argv[argv.index("--config") + 1]
        self.assertTrue(Path(cfg).is_dir())
        self.assertFalse(cfg.startswith("p/"))
        self.assertNotIn("://", cfg)
        self.assertEqual(captured["env"].get("SEMGREP_SEND_METRICS"), "off")


# ---------------------------------------------------------------------------
# Owned-surface partition + path normalization parity
# ---------------------------------------------------------------------------


class TestOwnedSurface(unittest.TestCase):
    def setUp(self):
        _reset_probe()
        self.addCleanup(_reset_probe)

    def _fake_two_file_scan(self, proj):
        import subprocess
        payload = {"results": [
            {"check_id": "r.sql", "path": str(Path(proj) / "src" / "owned.py"),
             "start": {"line": 1}, "end": {"line": 1},
             "extra": {"severity": "ERROR", "metadata": {"cwe": ["CWE-89: SQLi"]}, "lines": "x"}},
            {"check_id": "r.sql", "path": str(Path(proj) / "src" / "old.py"),
             "start": {"line": 1}, "end": {"line": 1},
             "extra": {"severity": "ERROR", "metadata": {"cwe": ["CWE-89: SQLi"]}, "lines": "y"}},
        ], "errors": []}

        def fake_run(argv, **kw):
            if "--version" in argv:
                return subprocess.CompletedProcess(argv, 0, "1.166.0\n", "")
            return subprocess.CompletedProcess(argv, 1, json.dumps(payload), "")
        return fake_run

    def test_out_of_surface_finding_is_advisory(self):
        with TemporaryDirectory() as proj:
            (Path(proj) / "src").mkdir()
            (Path(proj) / "src" / "owned.py").write_text("q = 1\n")
            (Path(proj) / "src" / "old.py").write_text("q = 1\n")
            orig = sast.subprocess.run
            sast.subprocess.run = self._fake_two_file_scan(proj)
            try:
                r = run_semgrep(Path(proj), ["src"], "python", {"src/owned.py"})
            finally:
                sast.subprocess.run = orig
        self.assertEqual([f.file for f in r.findings], ["src/owned.py"])
        self.assertEqual([f.file for f in r.advisory_findings], ["src/old.py"])

    def test_none_surface_keeps_everything_gating(self):
        with TemporaryDirectory() as proj:
            (Path(proj) / "src").mkdir()
            (Path(proj) / "src" / "owned.py").write_text("q = 1\n")
            (Path(proj) / "src" / "old.py").write_text("q = 1\n")
            orig = sast.subprocess.run
            sast.subprocess.run = self._fake_two_file_scan(proj)
            try:
                r = run_semgrep(Path(proj), ["src"], "python", None)
            finally:
                sast.subprocess.run = orig
        self.assertEqual(len(r.findings), 2)
        self.assertEqual(r.advisory_findings, [])


# ---------------------------------------------------------------------------
# FAKE-BINARY wiring — exercises the REAL subprocess path (anti-bypass)
# ---------------------------------------------------------------------------


_FAKE_SEMGREP = """#!/usr/bin/env python3
import sys, json, os
if "--version" in sys.argv:
    print("1.166.0"); sys.exit(0)
finding_path = os.environ["FAKE_FINDING_PATH"]
out = {"results": [{
    "check_id": "rules.fake.formatted-sql-query",
    "path": finding_path,
    "start": {"line": 1}, "end": {"line": 1},
    "extra": {"severity": "ERROR",
              "metadata": {"cwe": ["CWE-89: SQL Injection"]},
              "lines": "requires login"}}],
       "errors": []}
print(json.dumps(out)); sys.exit(1)
"""


class TestFakeBinaryWiring(unittest.TestCase):
    """Proves run_semgrep + write_sast_compare run end-to-end against a real
    external `semgrep` process (no run_semgrep mock = no Sprint-28 bypass)."""

    def setUp(self):
        _reset_probe()
        self.addCleanup(_reset_probe)
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        # Fake semgrep on PATH.
        binp = root / "bin"
        binp.mkdir()
        fake = binp / "semgrep"
        fake.write_text(_FAKE_SEMGREP)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        self._old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{binp}{os.pathsep}{self._old_path}"
        self.addCleanup(lambda: os.environ.__setitem__("PATH", self._old_path))
        # Project with a real source file the fake finding points at.
        self.proj = root / "proj"
        (self.proj / "src").mkdir(parents=True)
        self.src = self.proj / "src" / "app.py"
        self.src.write_text('db.execute(f"SELECT {x}")\n')
        os.environ["FAKE_FINDING_PATH"] = str(self.src)
        self.addCleanup(lambda: os.environ.pop("FAKE_FINDING_PATH", None))

    def test_run_semgrep_against_real_subprocess(self):
        r = run_semgrep(self.proj, ["src"], "python", None)
        self.assertTrue(r.ran, msg=f"semgrep subprocess did not run: {r.error}")
        self.assertEqual(len(r.findings), 1)
        f = r.findings[0]
        self.assertEqual(f.category, "sql_injection")   # via CWE-89
        self.assertEqual(f.severity, "HIGH")            # ERROR → HIGH
        self.assertEqual(f.file, "src/app.py")          # made project-relative
        # matched_text comes from the REAL source line, not the "requires login" placeholder.
        self.assertIn("SELECT", f.matched_text)
        self.assertNotIn("requires login", f.matched_text)

    def test_compare_artifact_written_with_ran_1(self):
        reg = _sec([_rf("sql_injection", 1, file="src/app.py")])
        r = run_semgrep(self.proj, ["src"], "python", None)
        summary = write_sast_compare(self.proj, "spec-x", 2, reg, r)
        artifact = self.proj / ".dark-factory" / "reports" / "spec-x-sast-compare-iter2.jsonl"
        self.assertTrue(artifact.exists())
        first = json.loads(artifact.read_text().splitlines()[0])
        self.assertEqual(first["sast_ran"], 1)
        self.assertEqual(summary["agree"], 1)  # regex sql@1 ⟷ semgrep sql@1


# ---------------------------------------------------------------------------
# Structural invariant lock — the helper cannot touch the security gate
# ---------------------------------------------------------------------------


class TestInvariantLock(unittest.TestCase):
    def test_helper_takes_iteration_number_not_iter_state(self):
        """_maybe_run_sast_compare must take the iteration NUMBER, never
        iter_state — so it is structurally incapable of mutating
        security_high_count/score. If a refactor re-introduces iter_state, this
        fails loudly (the d2fecbf-class regression guard)."""
        from darkfactory.pipeline.iteration import _maybe_run_sast_compare
        params = set(inspect.signature(_maybe_run_sast_compare).parameters)
        self.assertIn("iteration", params)
        self.assertNotIn("iter_state", params)

    def test_disabled_flag_is_no_op(self):
        from darkfactory.models.config import PipelineConfig
        from darkfactory.pipeline.iteration import _maybe_run_sast_compare

        class _Prepass:
            security = _sec([])
        with TemporaryDirectory() as proj:
            cfg = PipelineConfig(language="python", sast_compare_enabled=False)
            out = _maybe_run_sast_compare(cfg, Path(proj), "spec", 1, _Prepass(), None)
            self.assertIsNone(out)
            self.assertFalse((Path(proj) / ".dark-factory" / "reports").exists())


# ---------------------------------------------------------------------------
# Real-semgrep rule match (skips without the [sast] extra)
# ---------------------------------------------------------------------------


class TestRealSemgrep(unittest.TestCase):
    def setUp(self):
        _reset_probe()
        self.addCleanup(_reset_probe)

    def test_real_semgrep_flags_command_injection(self):
        import importlib.util
        if importlib.util.find_spec("semgrep") is None or sast._probe_semgrep() is None:
            self.skipTest("semgrep (dark-factory[sast] extra) not installed / out of pin")
        with TemporaryDirectory() as proj:
            (Path(proj) / "src").mkdir()
            (Path(proj) / "src" / "v.py").write_text(
                "import os\ndef f(u):\n    os.system('ls ' + u)\n")
            r = run_semgrep(Path(proj), ["src"], "python", None)
        self.assertTrue(r.ran)
        self.assertIn("command_injection", {f.category for f in r.findings})


if __name__ == "__main__":
    unittest.main()
