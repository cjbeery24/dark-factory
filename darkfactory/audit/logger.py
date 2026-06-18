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
    """Append a timestamped entry to the audit log.

    Routed through the shared host-write lock (``locked_append_text``) so a
    concurrent writer (F Move 2b: the Test Engineer and QA Holdout now run on
    two host threads, each launching/inspecting its own container) can never
    interleave a partial audit line — important because the barrier-violation
    detector and the independent barrier audit consume these records.
    """
    from darkfactory.utils.json_helpers import locked_append_text

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    locked_append_text(audit_path, f"[{ts}] {message}\n")
    log.debug("audit: %s", message)


def audit_summary(audit_path: Path) -> str:
    """Return the full audit log contents, or empty string if missing."""
    if not audit_path.exists():
        return ""
    return audit_path.read_text()
