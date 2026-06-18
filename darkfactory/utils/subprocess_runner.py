"""Subprocess execution with timeout, dry-run, and output capture."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Synthetic exit code for timeout — matches coreutils timeout(1) behavior.
EXIT_TIMEOUT = 124

# S0(c) streaming-capture bounds — protect against a pathological agent stream
# (a Write with a megabyte body, or a runaway loop) inflating the sidecar.
_STREAM_SIDECAR_MAX_BYTES = 64 * 1024 * 1024  # 64 MB cap on the on-disk sidecar
# Bounded wait for reader threads after kill. Short on purpose: after the kill we
# force-close the read ends (below), so the readers exit near-immediately; this is
# only a backstop. A long value would add wall to every timeout (Linus) — and the
# pre-kill data is already flushed to disk per line, so we lose nothing by being
# impatient here.
_STREAM_DRAIN_JOIN_S = 3.0


@dataclass
class RunResult:
    """Result of a subprocess execution."""
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    command: list[str] = field(default_factory=list)
    # S0(c): set True when a streaming reader thread did not finish draining
    # within _STREAM_DRAIN_JOIN_S after the kill (capture may be incomplete).
    drain_incomplete: bool = False


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


def run_streaming(
    cmd: list[str],
    *,
    timeout: int | None = None,
    stream_path: "Path | None" = None,
    dry_run: bool = False,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> RunResult:
    """S0(c): run a subprocess capturing stdout INCREMENTALLY with per-line arrival
    timestamps, for wall-time attribution and SIGKILL-durable observability.

    Unlike ``run`` (which buffers via ``subprocess.run`` and only materializes
    output at the end — so a SIGKILL drops whatever is still in the OS pipe and
    nothing carries arrival timing), this hand-rolls ``Popen`` with concurrent
    reader threads so each stdout line is timestamped (monotonic seconds since
    start) and flushed to ``stream_path`` as a ``{"t":<s>,"line":<raw>}`` JSONL
    record the instant it arrives. On a timeout the process is killed and the
    readers drain the remaining pipe data (closing the POSIX ``run``-drops-the-
    final-pipe-chunk window), so the partial transcript survives. ``RunResult``
    contract matches ``run`` (exit_code 124 + timed_out on timeout; stdout is the
    newline-preserving join of the stdout reader — pure stdout, never interleaved
    with stderr — so ``_extract_result_envelope`` / line-based parsers consume it
    unchanged). NOTE: ``run`` uses ``text=True`` (universal-newline translation);
    this decodes raw bytes, so CRLF is preserved rather than folded to LF. The
    agent stream-json is LF-terminated and ``json.loads`` tolerates a trailing
    ``\r``, so line-based parsing is unaffected — but the bytes are not literally
    identical to ``run``'s on a CRLF stream.

    Scope: AGENT invocations only (long stream-json runs). General ``run`` stays
    buffered — this is the riskier path, isolated here. NOTE: this captures only
    what reaches the host pipe; if an upstream layer (claude/docker) buffers
    output before the host sees it, this cannot recover it — see the spec-4
    validate-att2 upstream-loss caveat (do NOT assume this fixes that).
    """
    log.debug("exec (streaming): %s", cmd)
    if dry_run:
        log.info("[dry-run] would execute (streaming): %s", cmd)
        return RunResult(exit_code=0, command=cmd)

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    reader_errors: list[str] = []
    t0 = time.monotonic()

    # Open the sidecar (best-effort — observability must never fail the run).
    sink = None
    if stream_path is not None:
        try:
            Path(stream_path).parent.mkdir(parents=True, exist_ok=True)
            sink = open(stream_path, "w", encoding="utf-8")
        except OSError as exc:
            log.debug("stream sidecar open failed (non-fatal): %s", exc)
            sink = None
    sink_bytes = [0]
    sink_lock = threading.Lock()

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        bufsize=0,  # unbuffered binary pipes; we decode per line ourselves
    )

    def _read_stdout() -> None:
        try:
            for raw in proc.stdout:  # bytes, newline-terminated
                line = raw.decode("utf-8", errors="replace")
                stdout_chunks.append(line)  # newline preserved → faithful join
                if sink is not None and sink_bytes[0] < _STREAM_SIDECAR_MAX_BYTES:
                    rec = json.dumps(
                        {"t": round(time.monotonic() - t0, 3), "line": line.rstrip("\n")}
                    ) + "\n"
                    with sink_lock:
                        sink.write(rec)
                        sink.flush()  # durable on SIGKILL via page cache; NOT fsync
                        sink_bytes[0] += len(rec)
        except Exception as exc:  # noqa: BLE001 — a reader exception silently truncates; record it
            reader_errors.append(f"stdout reader: {exc!r}")

    def _read_stderr() -> None:
        try:
            for raw in proc.stderr:
                stderr_chunks.append(raw.decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            reader_errors.append(f"stderr reader: {exc!r}")

    def _write_stdin() -> None:
        try:
            if proc.stdin is not None and input_text is not None:
                proc.stdin.write(input_text.encode("utf-8"))
                proc.stdin.flush()
        except Exception as exc:  # noqa: BLE001 — broken pipe if the child exits early
            reader_errors.append(f"stdin writer: {exc!r}")
        finally:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except OSError:
                pass

    t_out = threading.Thread(target=_read_stdout, daemon=True)
    t_err = threading.Thread(target=_read_stderr, daemon=True)
    t_out.start()
    t_err.start()
    # Write stdin on its own thread so a large prompt can't deadlock against a
    # child that's blocked writing stdout (the classic three-pipe deadlock —
    # the agent prompt IS piped to stdin and routinely exceeds the pipe buffer).
    t_in = None
    if input_text is not None:
        t_in = threading.Thread(target=_write_stdin, daemon=True)
        t_in.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        exit_code = EXIT_TIMEOUT  # 124 — match run()/coreutils, NOT the -9 from kill
        proc.kill()
        proc.wait()
        # Force the reader loops to exit NOW. A `docker run` grandchild (the
        # container's PID 1) can keep the pipe write-end open after the docker
        # client is SIGKILL'd, so `for raw in proc.stdout` would block past the
        # join deadline — leaking the reader threads AND adding ~drain×2 wall to
        # every timeout, contaminating the timing this sprint exists to measure
        # (Linus Finding A). The pre-kill lines are already flushed to disk + in
        # the chunk buffers, so closing the read ends loses nothing.
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    # Bounded drain — never join unbounded on the very path we're instrumenting.
    drain_incomplete = False
    for th in (t_out, t_err):
        th.join(timeout=_STREAM_DRAIN_JOIN_S)
        if th.is_alive():
            drain_incomplete = True
    if t_in is not None:
        t_in.join(timeout=1.0)

    if sink is not None:
        try:
            sink.close()
        except OSError:
            pass
    if reader_errors:
        log.warning("run_streaming reader thread errors: %s", reader_errors)

    return RunResult(
        exit_code=exit_code,
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
        timed_out=timed_out,
        command=cmd,
        drain_incomplete=drain_incomplete,
    )
