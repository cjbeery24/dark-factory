"""JSONL read/write helpers using stdlib json."""

import json
from collections import deque
from pathlib import Path
from typing import Any


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append a single JSON record as one line to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


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
