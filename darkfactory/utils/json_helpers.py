"""JSONL read/write helpers using stdlib json."""

import json
import threading
from collections import deque
from pathlib import Path
from typing import Any

# Single process-wide lock guarding every append-only write to host-side
# log files (events.jsonl, run-telemetry.jsonl, the audit log). F Move 2b
# introduced the first intra-spec concurrency (the Test Engineer and QA
# Holdout scenario-generation agents now run on two host threads); both
# threads emit events, telemetry, and audit-log lines for their respective
# containers. A bare ``open("a") + write()`` is NOT torn-line-safe under
# concurrency: O_APPEND makes the *syscall* atomic, but text-mode
# ``TextIOWrapper`` may split a record larger than its buffer (~8 KB) into
# multiple syscalls, which another thread's write can interleave between.
# A long redacted ``docker run`` audit line easily exceeds that buffer.
#
# Serializing all three writers through this one lock guarantees each
# record lands as a single intact line. Same-process threads only — the
# lock does NOT protect against a second OS process appending to the same
# file. That is sufficient by construction: these files are written ONLY by
# the single orchestrator host process (agents write inside their
# containers to disjoint per-role paths, never to these files). A future
# plan-level change that parallelizes specs across processes would need an
# ``fcntl``/bounded-record O_APPEND scheme instead.
_WRITE_LOCK = threading.Lock()


def locked_append_text(path: Path, text: str) -> None:
    """Append ``text`` to ``path`` as one atomic write under ``_WRITE_LOCK``.

    Shared primitive for all host-side append-only log writers so they
    serialize against each other and never interleave a partial line.
    ``text`` must include its own trailing newline. Creates parent dirs.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        with path.open("a") as f:
            f.write(text)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append a single JSON record as one line to a JSONL file."""
    locked_append_text(path, json.dumps(record, separators=(",", ":")) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read all records from a JSONL file. Returns empty list if file missing."""
    if not path.exists():
        return []
    records = []
    with path.open() as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                records.append(json.loads(stripped))
    return records


def read_jsonl_tolerant(path: Path) -> list[dict[str, Any]]:
    """Read all records from a JSONL file, SKIPPING malformed lines.

    Unlike ``read_jsonl`` (which raises on the first bad line), this tolerates
    partial/torn lines — relevant when reading an ``events.jsonl`` another
    process may be appending to. Returns ``[]`` if the file is missing.
    """
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except ValueError:
                continue
    return records


def read_jsonl_last(path: Path, n: int = 1) -> list[dict[str, Any]]:
    """Read the last N records from a JSONL file, keeping only N in memory."""
    if not path.exists():
        return []
    lines: deque[str] = deque(maxlen=n)
    with path.open() as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
    return [json.loads(line) for line in lines]
