"""Append-only audit log.

Replaces the audit_log function from lib/audit.sh.
Each entry is a timestamped line in the audit log file.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def audit_log(audit_path: Path, message: str) -> None:
    """Append a timestamped entry to the audit log."""
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = f"[{ts}] {message}\n"
    with audit_path.open("a") as f:
        f.write(entry)
    log.debug("audit: %s", message)


def audit_summary(audit_path: Path) -> str:
    """Return the full audit log contents, or empty string if missing."""
    if not audit_path.exists():
        return ""
    return audit_path.read_text()
