"""Plan checkpoint — JSONL-based spec completion tracking for resumable plans.

Replaces lib/plan-state.sh — plan_checkpoint_write, plan_checkpoint_load,
plan_checkpoint_completed_specs, plan_checkpoint_spec_hash.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from darkfactory.state.lock import FileLock
from darkfactory.utils.hash import sha256_file
from darkfactory.utils.json_helpers import append_jsonl, read_jsonl, read_jsonl_last

log = logging.getLogger(__name__)

# Specs with these statuses are skipped on resume.
_SKIP_STATUSES = frozenset({"shipped", "budget_skipped"})


def checkpoint_path(
    state_dir: Path,
    plan_name: str,
    run_id: str,
) -> Path:
    """Return the checkpoint file path for a plan run."""
    return state_dir / f"plan-{plan_name}-{run_id[:16]}.jsonl"


def write(
    state_dir: Path,
    plan_name: str,
    run_id: str,
    spec_name: str,
    status: str,
    exit_code: int,
    *,
    spec_path: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Append a spec completion record to the plan checkpoint.

    Returns the checkpoint file path.
    """
    path = checkpoint_path(state_dir, plan_name, run_id)
    lock_path = state_dir / ".plan-checkpoint.lock"
    lock = FileLock(lock_path, timeout=30.0)

    record: dict[str, Any] = {
        "spec": spec_name,
        "status": status,
        "exit_code": exit_code,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
    }

    if spec_path and spec_path.exists():
        record["spec_hash"] = sha256_file(spec_path)

    if extra:
        record.update(extra)

    with lock:
        append_jsonl(path, record)

    log.info("checkpoint: %s %s=%s (exit %d)", plan_name, spec_name, status, exit_code)
    return path


def load_latest(state_dir: Path, plan_name: str) -> Path | None:
    """Find the most recent checkpoint file for a plan.

    Returns None if no checkpoint exists.
    """
    pattern = f"plan-{plan_name}-*.jsonl"
    candidates = sorted(state_dir.glob(pattern))
    if not candidates:
        return None

    # Pick the one with the latest timestamp in its last record.
    best: Path | None = None
    best_ts = ""
    for candidate in candidates:
        last = read_jsonl_last(candidate, 1)
        if last:
            ts = last[0].get("ts", "")
            if ts > best_ts:
                best_ts = ts
                best = candidate

    return best or (candidates[-1] if candidates else None)


def completed_specs(checkpoint_file: Path) -> set[str]:
    """Return spec names that should be skipped on resume.

    Specs with status 'shipped' or 'budget_skipped' are skipped.
    Specs with status 'failed' or 'escalated' are re-run.
    """
    records = read_jsonl(checkpoint_file)
    result: set[str] = set()
    for record in records:
        if record.get("status") in _SKIP_STATUSES:
            result.add(record["spec"])
    return result


def spec_hash(checkpoint_file: Path, spec_name: str) -> str:
    """Return the recorded spec_hash for a spec, or empty string."""
    records = read_jsonl(checkpoint_file)
    # Last record for this spec wins.
    result = ""
    for record in records:
        if record.get("spec") == spec_name:
            result = record.get("spec_hash", "")
    return result


def all_records(checkpoint_file: Path) -> list[dict[str, Any]]:
    """Return all records from a checkpoint file."""
    return read_jsonl(checkpoint_file)
