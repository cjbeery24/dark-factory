"""SHA-256 hashing utilities."""

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    """Return hex SHA-256 digest of a file's contents."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_string(s: str) -> str:
    """Return hex SHA-256 digest of a string."""
    return hashlib.sha256(s.encode()).hexdigest()
