"""Portable file-based locking using mkdir (POSIX-atomic).

This replicates the bash mkdir-based locking from lib/state.sh.
mkdir is atomic on all POSIX filesystems — if it succeeds, you hold the lock.

For intra-process thread safety, a threading.Lock serializes access before
attempting the filesystem lock. Cross-process safety relies on mkdir atomicity.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

STALE_LOCK_TIMEOUT = 300

# Per-path threading locks to serialize intra-process access.
_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def _get_thread_lock(path: Path) -> threading.Lock:
    """Get or create a threading.Lock for the given filesystem path."""
    key = str(path.resolve())
    with _thread_locks_guard:
        if key not in _thread_locks:
            _thread_locks[key] = threading.Lock()
        return _thread_locks[key]


class LockError(Exception):
    """Failed to acquire lock."""


class FileLock:
    """mkdir-based portable file lock with thread safety.

    Usage:
        lock = FileLock(Path("/tmp/my.lock"))
        with lock:
            # critical section
            ...
    """

    def __init__(
        self,
        path: Path,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.1,
        stale_timeout: float = STALE_LOCK_TIMEOUT,
    ) -> None:
        self.path = path
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.stale_timeout = stale_timeout
        self._held = False
        self._tlock = _get_thread_lock(path)

    def acquire(self) -> None:
        """Acquire the lock, waiting up to timeout seconds."""
        deadline = time.monotonic() + self.timeout
        remaining = self.timeout

        # First, acquire in-process thread lock.
        if not self._tlock.acquire(timeout=remaining):
            raise LockError(
                f"timeout acquiring thread lock {self.path} "
                f"after {self.timeout}s"
            )

        # Now acquire the filesystem lock.
        pid_file = self.path / "pid"
        try:
            while True:
                try:
                    os.mkdir(self.path)
                except FileExistsError:
                    if self._is_stale(pid_file):
                        log.warning("removing stale lock: %s", self.path)
                        self._force_remove()
                        continue

                    if time.monotonic() >= deadline:
                        raise LockError(
                            f"timeout acquiring file lock {self.path} "
                            f"after {self.timeout}s"
                        )
                    time.sleep(self.poll_interval)
                    continue

                # mkdir succeeded — write PID. If the directory vanished
                # (concurrent stale removal race), go around the loop.
                try:
                    pid_file.write_text(str(os.getpid()))
                except OSError:
                    continue
                self._held = True
                log.debug("acquired lock: %s", self.path)
                return
        except BaseException:
            # Release thread lock if filesystem lock fails.
            self._tlock.release()
            raise

    def release(self) -> None:
        """Release the lock."""
        if not self._held:
            return
        self._force_remove()
        self._held = False
        self._tlock.release()
        log.debug("released lock: %s", self.path)

    def _is_stale(self, pid_file: Path) -> bool:
        """Check if the lock holder process is still alive."""
        try:
            pid_text = pid_file.read_text().strip()
            if not pid_text:
                return True
            pid = int(pid_text)
            os.kill(pid, 0)
            return False
        except (FileNotFoundError, ValueError):
            return True
        except ProcessLookupError:
            return True
        except PermissionError:
            return False

    def _force_remove(self) -> None:
        """Remove the lock directory and its contents."""
        pid_file = self.path / "pid"
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            self.path.rmdir()
        except OSError:
            pass

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()
