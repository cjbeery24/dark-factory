"""Structured event emitter — writes JSONL to events file.

Replaces lib/events.sh emit_event + all 24 wrapper functions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from darkfactory.models.events import Event, EventType
from darkfactory.utils.json_helpers import append_jsonl

log = logging.getLogger(__name__)


def emit_event(
    events_file: Path,
    event_type: EventType,
    *,
    spec_name: str = "",
    run_id: str = "",
    plan_run_id: str = "",
    iteration: int = 0,
    data: dict[str, Any] | None = None,
) -> Event:
    """Emit a structured event to the JSONL events file."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    event = Event(
        event_type=event_type,
        timestamp=ts,
        spec_name=spec_name,
        run_id=run_id,
        plan_run_id=plan_run_id,
        iteration=iteration,
        data=data or {},
    )
    record: dict[str, Any] = {
        "ts": ts,
        "event": event_type.value,
    }
    if spec_name:
        record["spec"] = spec_name
    if run_id:
        record["run_id"] = run_id
    if plan_run_id:
        record["plan_run_id"] = plan_run_id
    if iteration:
        record["iter"] = iteration
    if data:
        record["data"] = data

    append_jsonl(events_file, record)
    log.debug("event: %s spec=%s iter=%d", event_type.value, spec_name, iteration)
    return event
