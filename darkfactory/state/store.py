"""File-based key=value state store.

Replaces lib/state.sh — provides read_state/write_state with atomic
writes (write to tmp + rename) and optional file locking.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from darkfactory.state.lock import FileLock

log = logging.getLogger(__name__)


def read_state(path: Path, key: str) -> str:
    """Read the last value for a key from a state file.

    State files are line-oriented: KEY=VALUE. If the key appears
    multiple times, the last occurrence wins (matches bash grep behavior).
    Returns empty string if key not found or file doesn't exist.
    """
    if not path.exists():
        return ""
    result = ""
    with path.open() as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith(f"{key}="):
                result = stripped[len(key) + 1:]
    return result


def write_state(path: Path, key: str, value: str) -> None:
    """Write a key=value pair to a state file atomically.

    If the key already exists, it is replaced. Otherwise, it is appended.
    Uses a lock file to prevent concurrent writes.
    """
    lock_path = path.parent / f".{path.name}.lock"
    lock = FileLock(lock_path, timeout=10.0)

    with lock:
        # Read existing lines, filtering out the key.
        lines: list[str] = []
        if path.exists():
            with path.open() as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith(f"{key}="):
                        lines.append(stripped)
        lines.append(f"{key}={value}")

        # Atomic write: tmp file + rename.
        # POSIX rename is atomic on local filesystems — readers see
        # either the old or new complete file without locking.
        fd, tmp_path = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write("\n".join(lines) + "\n")
            Path(tmp_path).replace(path)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    log.debug("state %s: %s=%s", path.name, key, value)


def write_state_or_warn(path: Path, key: str, value: str) -> None:
    """Write state, logging a warning instead of raising on failure."""
    try:
        write_state(path, key, value)
    except Exception as e:
        log.warning("failed to write state %s=%s to %s: %s", key, value, path, e)
