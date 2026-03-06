"""Tests for state/lock.py — mkdir-based portable locking."""

import os
import threading
import time
import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase
from darkfactory.state.lock import FileLock, LockError


class TestFileLock(TempDirTestCase):
    def test_acquire_release(self) -> None:
        lock = FileLock(self.tmp_dir / "test.lock")
        lock.acquire()
        self.assertTrue((self.tmp_dir / "test.lock").is_dir())
        pid_text = (self.tmp_dir / "test.lock" / "pid").read_text().strip()
        self.assertEqual(int(pid_text), os.getpid())
        lock.release()
        self.assertFalse((self.tmp_dir / "test.lock").is_dir())

    def test_context_manager(self) -> None:
        lock_path = self.tmp_dir / "ctx.lock"
        with FileLock(lock_path):
            self.assertTrue(lock_path.is_dir())
        self.assertFalse(lock_path.is_dir())

    def test_timeout_cross_process(self) -> None:
        """Simulate a lock held by another process (different PID)."""
        lock_path = self.tmp_dir / "busy.lock"
        # Manually create the lock dir with our own PID so it's not stale.
        lock_path.mkdir()
        (lock_path / "pid").write_text(str(os.getpid()))

        # Use a different lock instance — but since we're in the same process,
        # the thread lock will also block. Test cross-process by using a PID
        # that is alive (init/launchd).
        lock = FileLock(lock_path, timeout=0.3, poll_interval=0.05)
        with self.assertRaises(LockError):
            lock.acquire()

    def test_stale_lock_removed(self) -> None:
        lock_path = self.tmp_dir / "stale.lock"
        lock_path.mkdir()
        # PID 99999999 almost certainly doesn't exist.
        (lock_path / "pid").write_text("99999999")

        lock = FileLock(lock_path, timeout=1.0)
        lock.acquire()
        self.assertTrue(lock._held)
        lock.release()

    def test_contention(self) -> None:
        """Multiple threads compete for the same lock."""
        lock_path = self.tmp_dir / "contention.lock"
        results: list[int] = []
        counter = [0]

        def worker() -> None:
            with FileLock(lock_path, timeout=10.0, poll_interval=0.01):
                val = counter[0]
                time.sleep(0.01)
                counter[0] = val + 1
                results.append(counter[0])

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # If locking works, counter increments sequentially.
        self.assertEqual(counter[0], 5)
        self.assertEqual(sorted(results), [1, 2, 3, 4, 5])

    def test_double_release_safe(self) -> None:
        lock = FileLock(self.tmp_dir / "double.lock")
        lock.acquire()
        lock.release()
        lock.release()  # Should not raise.

    def test_stale_empty_pid(self) -> None:
        lock_path = self.tmp_dir / "empty.lock"
        lock_path.mkdir()
        (lock_path / "pid").write_text("")

        lock = FileLock(lock_path, timeout=1.0)
        lock.acquire()
        self.assertTrue(lock._held)
        lock.release()


if __name__ == "__main__":
    unittest.main()
