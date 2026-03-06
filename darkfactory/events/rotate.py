"""Event file rotation and migration.

Replaces df-events-rotate.sh (72 lines) and df-events-migrate.sh (67 lines).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from darkfactory.utils.json_helpers import read_jsonl

log = logging.getLogger(__name__)

# Default rotation settings.
DEFAULT_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_KEEP_COUNT = 3


def rotate(
    events_file: Path,
    *,
    max_size: int = DEFAULT_MAX_SIZE_BYTES,
    keep_count: int = DEFAULT_KEEP_COUNT,
) -> bool:
    """Rotate the events file if it exceeds max_size.

    Creates backups: events.jsonl → events.jsonl.0, .0 → .1, etc.
    Keeps at most keep_count rotated copies.

    Returns True if rotation occurred.
    """
    if not events_file.exists():
        return False

    size = events_file.stat().st_size
    if size < max_size:
        return False

    # Rotate existing backups.
    for i in range(keep_count - 1, 0, -1):
        src = Path(f"{events_file}.{i - 1}")
        dst = Path(f"{events_file}.{i}")
        if src.exists():
            shutil.move(str(src), str(dst))

    # Move current to .0.
    shutil.move(str(events_file), f"{events_file}.0")

    log.info("rotated %s (was %d bytes)", events_file, size)
    return True


def migrate_metrics(
    metrics_file: Path,
    events_file: Path,
) -> int:
    """Migrate metrics.jsonl records to events.jsonl format.

    Converts end records (with outcome) to SPEC_SHIPPED/SPEC_FAILED/SPEC_ESCALATED
    events. Skips migration if events_file already exists and is non-empty.

    Returns count of migrated records.
    """
    if events_file.exists() and events_file.stat().st_size > 0:
        log.warning("events file already exists and is non-empty, skipping migration")
        return 0

    records = read_jsonl(metrics_file)
    from darkfactory.utils.json_helpers import append_jsonl

    count = 0
    for rec in records:
        outcome = rec.get("outcome", "")
        if not outcome or outcome == "in_progress":
            continue

        event_type = {
            "shipped": "SPEC_SHIPPED",
            "escalated": "SPEC_ESCALATED",
        }.get(outcome, "SPEC_FAILED")

        event_record = {
            "ts": rec.get("ts", rec.get("end_ts", "")),
            "event": event_type,
            "spec": rec.get("spec", ""),
            "run_id": rec.get("run_id", ""),
            "data": {
                "iterations": rec.get("iters", 0),
                "wall_s": rec.get("wall_s", 0),
                "pass_rates": rec.get("pass_rates", ""),
            },
        }
        append_jsonl(events_file, event_record)
        count += 1

    log.info("migrated %d records from %s to %s", count, metrics_file, events_file)
    return count
