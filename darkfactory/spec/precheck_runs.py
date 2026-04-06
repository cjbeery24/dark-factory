"""Round-count instrumentation for precheck runs.

Boundary: ``run_precheck`` does NO filesystem I/O for this counter. The CLI
(or any orchestrator) computes the spec hash, calls ``record_precheck_run``,
then calls ``run_precheck``. This keeps ``run_precheck`` pure and testable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def spec_hash(raw_bytes: bytes) -> str:
    """sha256 of the raw spec file bytes. Stable, content-addressed."""
    return hashlib.sha256(raw_bytes).hexdigest()


def record_precheck_run(project_dir: Path, spec_name: str, digest: str) -> int:
    """Increment precheck run count for a given spec digest. Returns new count.

    Schema (single object, NOT keyed by hash — counter resets when content
    changes)::

        {"schema_version": 1, "spec_hash": "abc...", "count": 3,
         "first_run": "...", "last_run": "..."}
    """
    state_dir = project_dir / ".dark-factory" / "state" / spec_name
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "precheck_runs.json"

    now = datetime.now(timezone.utc).isoformat()
    existing: dict | None = None
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = None

    if existing and isinstance(existing, dict) and existing.get("spec_hash") == digest:
        new_count = int(existing.get("count", 0)) + 1
        first_run = existing.get("first_run", now)
    else:
        new_count = 1
        first_run = now

    data = {
        "schema_version": 1,
        "spec_hash": digest,
        "count": new_count,
        "first_run": first_run,
        "last_run": now,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return new_count
