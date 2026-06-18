"""Tests for darkfactory.utils.subprocess_runner.

The timeout test exercises the REAL subprocess path (not a mocked RunResult) —
Sprint 33's observability depends on the runner PRESERVING the partial stdout a
process emitted before being SIGKILL'd at the timeout. Even with text=True,
subprocess.TimeoutExpired returns stdout/stderr as BYTES, so the runner must
decode them rather than drop them (the old isinstance-str guard returned "").
A mocked RunResult would bypass exactly this bug (Codex P1 / the "verify it
activates end-to-end" lesson).
"""
from __future__ import annotations

import sys
import unittest

import json
import tempfile
from pathlib import Path

from darkfactory.utils.subprocess_runner import EXIT_TIMEOUT, run, run_streaming


class TestTimeoutPreservesPartialOutput(unittest.TestCase):
    def test_partial_stdout_preserved_on_timeout(self):
        # Child prints+flushes, then sleeps past the timeout → SIGKILL. The
        # flushed line must survive in r.stdout (decoded from bytes).
        child = ('import time,sys; sys.stdout.write("PARTIAL_TRANSCRIPT"); '
                 'sys.stdout.flush(); time.sleep(5)')
        r = run([sys.executable, "-c", child], timeout=1)
        self.assertEqual(r.exit_code, EXIT_TIMEOUT)
        self.assertTrue(r.timed_out)
        self.assertIn("PARTIAL_TRANSCRIPT", r.stdout)

    def test_partial_stderr_preserved_on_timeout(self):
        child = ('import time,sys; sys.stderr.write("PARTIAL_ERR"); '
                 'sys.stderr.flush(); time.sleep(5)')
        r = run([sys.executable, "-c", child], timeout=1)
        self.assertEqual(r.exit_code, EXIT_TIMEOUT)
        self.assertIn("PARTIAL_ERR", r.stderr)

    def test_timeout_with_no_output_is_empty_not_none(self):
        r = run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)
        self.assertEqual(r.exit_code, EXIT_TIMEOUT)
        self.assertEqual(r.stdout, "")
        self.assertEqual(r.stderr, "")

    def test_normal_run_unaffected(self):
        r = run([sys.executable, "-c", 'print("ok")'], timeout=10)
        self.assertEqual(r.exit_code, 0)
        self.assertIn("ok", r.stdout)


class TestRunStreaming(unittest.TestCase):
    """S0(c): run_streaming drives a REAL subprocess (never a mocked RunResult —
    that's the bug class that shipped S33 non-functional). These exercise the
    deadlock/drain/timestamp paths a mock cannot."""

    def test_clean_run_stdout_faithful_and_exit0(self):
        child = 'import sys\nfor i in range(5): sys.stdout.write(f"L{i}\\n")'
        r = run_streaming([sys.executable, "-c", child], timeout=10)
        self.assertEqual(r.exit_code, 0)
        self.assertFalse(r.timed_out)
        # stdout is the faithful newline-preserving join (line-based parsers rely on it)
        self.assertEqual(r.stdout, "L0\nL1\nL2\nL3\nL4\n")

    def test_timeout_partial_capture_exit_124(self):
        child = ('import time,sys\nsys.stdout.write("BEFORE_KILL\\n")\n'
                 'sys.stdout.flush()\ntime.sleep(10)')
        r = run_streaming([sys.executable, "-c", child], timeout=1)
        self.assertEqual(r.exit_code, EXIT_TIMEOUT)  # 124, not -9 from kill
        self.assertTrue(r.timed_out)
        self.assertIn("BEFORE_KILL", r.stdout)

    def test_sidecar_records_arrival_timestamps(self):
        # Two lines ~0.4s apart → sidecar must carry monotonic arrival times that
        # differ, enabling wall-time attribution.
        child = ('import time,sys\nsys.stdout.write("A\\n");sys.stdout.flush()\n'
                 'time.sleep(0.4)\nsys.stdout.write("B\\n");sys.stdout.flush()')
        with tempfile.TemporaryDirectory() as d:
            sc = Path(d) / "stream.jsonl"
            r = run_streaming([sys.executable, "-c", child], timeout=10, stream_path=sc)
            self.assertEqual(r.exit_code, 0)
            recs = [json.loads(l) for l in sc.read_text().splitlines() if l.strip()]
            self.assertEqual([x["line"] for x in recs], ["A", "B"])
            self.assertGreater(recs[1]["t"], recs[0]["t"])  # B arrived later than A

    def test_large_stdin_no_deadlock(self):
        # The three-pipe deadlock: a >pipe-buffer stdin payload while the child
        # also writes stdout. Child echoes stdin length. Must NOT hang.
        big = "x" * (1024 * 1024)  # 1 MB stdin, far exceeds the 64KB pipe buffer
        child = ('import sys\ndata=sys.stdin.read()\n'
                 'sys.stdout.write(f"GOT {len(data)}\\n")')
        r = run_streaming([sys.executable, "-c", child], timeout=15, input_text=big)
        self.assertEqual(r.exit_code, 0)
        self.assertIn(f"GOT {len(big)}", r.stdout)

    def test_grandchild_holding_pipe_does_not_hang(self):
        # Linus Finding A: a grandchild inheriting stdout (what `docker run`'s
        # container PID 1 does) keeps the pipe write-end open after the parent is
        # killed, so `for raw in proc.stdout` blocks past the join — old behavior
        # was a 1s timeout taking 21s + leaked threads. The post-kill pipe-close
        # must make run_streaming return promptly.
        import time as _t
        child = (
            "import subprocess,sys,time\n"
            "subprocess.Popen(['sleep','30'])\n"   # grandchild inherits stdout fd
            "sys.stdout.write('BEFORE\\n');sys.stdout.flush()\n"
            "time.sleep(30)"
        )
        t0 = _t.monotonic()
        r = run_streaming([sys.executable, "-c", child], timeout=1)
        elapsed = _t.monotonic() - t0
        self.assertEqual(r.exit_code, EXIT_TIMEOUT)
        self.assertTrue(r.timed_out)
        self.assertIn("BEFORE", r.stdout)  # pre-kill data still captured
        self.assertLess(elapsed, 10.0, f"returned in {elapsed:.1f}s — pipe-close didn't unblock readers")

    def test_sidecar_survives_kill(self):
        child = ('import time,sys\nsys.stdout.write("DURABLE\\n");sys.stdout.flush()\n'
                 'time.sleep(10)')
        with tempfile.TemporaryDirectory() as d:
            sc = Path(d) / "stream.jsonl"
            r = run_streaming([sys.executable, "-c", child], timeout=1, stream_path=sc)
            self.assertTrue(r.timed_out)
            # The line emitted before the kill is durable on disk (flushed per line).
            self.assertIn("DURABLE", sc.read_text())


if __name__ == "__main__":
    unittest.main()
