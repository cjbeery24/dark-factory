"""Metrics recording and turn budget governor.

Replaces lib/metrics.sh — metrics_record_start, metrics_record_end,
_df_check_turn_budget, governor_check_budget_posthoc.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from darkfactory.errors import HardAbort
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.json_helpers import append_jsonl, read_jsonl

log = logging.getLogger(__name__)


def record_start(
    metrics_file: Path,
    spec_name: str,
    run_id: str,
    plan_run_id: str = "",
) -> None:
    """Record the start of a spec run in metrics."""
    record: dict[str, Any] = {
        "event": "start",
        "spec": spec_name,
        "run_id": run_id,
        "ts": time.time(),
    }
    if plan_run_id:
        record["plan_run_id"] = plan_run_id
    append_jsonl(metrics_file, record)


def record_end(
    metrics_file: Path,
    spec_name: str,
    run_id: str,
    status: str,
    iterations: int,
    total_turns: int,
    wall_seconds: float,
    plan_run_id: str = "",
) -> None:
    """Record the end of a spec run in metrics."""
    record: dict[str, Any] = {
        "event": "end",
        "spec": spec_name,
        "run_id": run_id,
        "status": status,
        "iterations": iterations,
        "total_turns": total_turns,
        "wall_s": wall_seconds,
        "ts": time.time(),
    }
    if plan_run_id:
        record["plan_run_id"] = plan_run_id
    append_jsonl(metrics_file, record)


def check_turn_budget(
    total_turns_allocated: int,
    max_total_agent_turns: int,
) -> None:
    """Check if the per-spec total turn budget is exceeded.

    Raises HardAbort if max_total_agent_turns > 0 and budget exceeded.
    """
    if max_total_agent_turns <= 0:
        return
    if total_turns_allocated > max_total_agent_turns:
        raise HardAbort(
            f"total agent turns ({total_turns_allocated}) exceeded "
            f"budget ({max_total_agent_turns})"
        )


def governor_check_budget_posthoc(
    metrics_file: Path,
    plan_run_id: str,
    plan_token_budget: int,
) -> bool:
    """Post-hoc check if plan-level turn budget is exceeded.

    Reads metrics.jsonl and sums total_turns for the plan.
    Returns True if budget exceeded.
    """
    if plan_token_budget <= 0:
        return False

    records = read_jsonl(metrics_file)
    total = 0
    for rec in records:
        if (rec.get("event") == "end"
                and rec.get("plan_run_id") == plan_run_id):
            total += rec.get("total_turns", 0)

    if total > plan_token_budget:
        log.warning(
            "plan turn budget exceeded: %d / %d",
            total, plan_token_budget,
        )
        return True
    return False
