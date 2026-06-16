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

from darkfactory.utils.subprocess_runner import EXIT_TIMEOUT, run


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


if __name__ == "__main__":
    unittest.main()
