"""Signal handling and graceful shutdown — resource cleanup.

Replaces the trap handlers in dark-factory.sh. Tracks Docker resources
(containers, networks, proxies) and cleans them up on exit or signal.
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from darkfactory.docker.containers import cleanup_containers
from darkfactory.docker.networks import cleanup_networks, teardown_proxies
from darkfactory.utils.subprocess_runner import run

log = logging.getLogger(__name__)


@dataclass
class CleanupManager:
    """Tracks all Docker resources for cleanup on exit/signal."""

    run_id: str = ""
    dry_run: bool = False
    deploy_teardown_cmd: str = ""
    deploy_cmd_allow_shell: bool = False
    project_dir: str = ""
    on_interrupt: Callable[[], None] | None = None
    state_dir: Path | None = None

    abort_requested: bool = field(default=False, init=False)
    _cleaning_up: bool = field(default=False, init=False)
    _registered: bool = field(default=False, init=False)
    _owner_pid: int = field(default=0, init=False)

    def register(self) -> None:
        """Install signal handlers and atexit hook."""
        if self._registered:
            return
        self._owner_pid = os.getpid()
        self._write_pid_file()
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        atexit.register(self._cleanup_all)
        self._registered = True

    def _pid_file_path(self) -> Path | None:
        """Return path to PID ownership file, or None if state_dir not set."""
        if self.state_dir and self.run_id:
            return self.state_dir / f"{self.run_id}.pid"
        return None

    def _write_pid_file(self) -> None:
        """Write our PID to the ownership file."""
        path = self._pid_file_path()
        if path:
            try:
                path.write_text(str(self._owner_pid))
            except OSError as e:
                log.warning("could not write PID file %s: %s", path, e)

    def _owns_resources(self) -> bool:
        """Check if this process still owns the Docker resources.

        If another process has overwritten the PID file (e.g. a resumed
        pipeline), we should NOT tear down resources — the new process
        owns them now.
        """
        path = self._pid_file_path()
        if not path or not path.exists():
            return True  # No PID file — assume ownership (backward compat).
        try:
            stored_pid = int(path.read_text().strip())
            return stored_pid == self._owner_pid
        except (ValueError, OSError):
            return True  # Can't read — assume ownership to be safe.

    def _handle_signal(self, signum: int, frame: object) -> None:
        log.warning("received signal %d, cleaning up", signum)
        self.abort_requested = True
        self._cleanup_all()
        # sys.exit raises SystemExit which triggers atexit handlers.
        # The reentrancy guard in _cleanup_all prevents double cleanup.
        sys.exit(128 + signum)

    def _cleanup_all(self) -> None:
        if self._cleaning_up:
            return
        self._cleaning_up = True

        # Let the orchestrator write interrupted state.
        if self.on_interrupt:
            try:
                self.on_interrupt()
            except Exception as e:
                log.warning("on_interrupt callback failed: %s", e)

        if self.run_id:
            # Skip Docker resource cleanup if another process has taken
            # ownership (e.g. a resumed pipeline with the same run_id).
            if not self._owns_resources():
                log.info(
                    "skipping Docker cleanup — another process owns run_id %s",
                    self.run_id,
                )
            else:
                try:
                    teardown_proxies(self.run_id, dry_run=self.dry_run)
                except Exception as e:
                    log.warning("proxy teardown failed: %s", e)

                try:
                    cleanup_containers(self.run_id, dry_run=self.dry_run)
                except Exception as e:
                    log.warning("container cleanup failed: %s", e)

                try:
                    cleanup_networks(self.run_id, dry_run=self.dry_run)
                except Exception as e:
                    log.warning("network cleanup failed: %s", e)

                # Clean up the PID file since we're the rightful owner.
                path = self._pid_file_path()
                if path and path.exists():
                    try:
                        path.unlink()
                    except OSError:
                        pass

        if self.deploy_teardown_cmd:
            try:
                if self.deploy_cmd_allow_shell:
                    run(["bash", "-c", self.deploy_teardown_cmd], timeout=30)
                elif self.project_dir:
                    from darkfactory.deployment.command import run_command
                    run_command(self.deploy_teardown_cmd, self.project_dir, timeout=30)
                else:
                    log.warning("project_dir unknown; running teardown via bash -c (unvalidated)")
                    run(["bash", "-c", self.deploy_teardown_cmd], timeout=30)
            except Exception as e:
                log.warning("deploy teardown failed: %s", e)

        log.info("cleanup complete")

    def cleanup(self) -> None:
        """Manual cleanup (e.g. on normal exit)."""
        self._cleanup_all()
