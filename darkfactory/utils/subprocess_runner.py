"""Subprocess execution with timeout, dry-run, and output capture."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Synthetic exit code for timeout — matches coreutils timeout(1) behavior.
EXIT_TIMEOUT = 124


@dataclass
class RunResult:
    """Result of a subprocess execution."""
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    command: list[str] = field(default_factory=list)


def run(
    cmd: list[str],
    *,
    timeout: int | None = None,
    capture: bool = True,
    dry_run: bool = False,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = False,
    input_text: str | None = None,
) -> RunResult:
    """Run a subprocess with optional timeout and dry-run support.

    Args:
        cmd: Command and arguments.
        timeout: Timeout in seconds. None = no timeout.
        capture: Whether to capture stdout/stderr.
        dry_run: If True, log the command but don't execute.
        cwd: Working directory.
        env: Environment variables (replaces entire env if set).
        check: If True, raise subprocess.CalledProcessError on non-zero exit.

    Returns:
        RunResult with exit code and captured output.
    """
    log.debug("exec: %s", cmd)

    if dry_run:
        log.info("[dry-run] would execute: %s", cmd)
        return RunResult(exit_code=0, command=cmd)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
            input=input_text,
        )
        result = RunResult(
            exit_code=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            command=cmd,
        )
        if check and proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode, cmd, proc.stdout, proc.stderr
            )
        return result
    except subprocess.TimeoutExpired as e:
        # Preserve the PARTIAL output captured before the kill (Sprint 33 — this
        # is what makes timeout observability work: agents run stream-json, and a
        # SIGKILL'd agent's partial transcript lives here). Even with text=True,
        # ``TimeoutExpired.stdout``/``.stderr`` come back as BYTES, so the old
        # ``isinstance(str)`` guard silently dropped them → empty failure dumps.
        # Decode defensively (best-effort; never raise on the timeout path).
        return RunResult(
            exit_code=EXIT_TIMEOUT,
            stdout=_coerce_timeout_output(e.stdout),
            stderr=_coerce_timeout_output(e.stderr),
            timed_out=True,
            command=cmd,
        )


def _coerce_timeout_output(value: "str | bytes | None") -> str:
    """Best-effort decode of TimeoutExpired stdout/stderr (str | bytes | None)."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
