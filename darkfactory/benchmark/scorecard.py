"""Pure scorecard logic: outcome classification, run-metric parsing, row I/O.

No subprocess, no Docker — fully unit-testable. The impure orchestration lives
in ``runner.py``.
"""

from __future__ import annotations

import enum
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from darkfactory.utils.json_helpers import read_jsonl_tolerant


class Outcome(enum.Enum):
    """Terminal outcome of one ``dark-factory run`` invocation.

    ``SHIP_WITH_GAPS`` is a real (Oracle-approved) ship that nonetheless carried
    documented known-gaps; ``CLEAN_SHIP`` is a pristine ship. Both exit 0 — they
    are distinguished by the presence of the defect registry, not the exit code.
    ``STABLE_FAILURE`` (exit 5) is a NON-ship (Oracle said NO-SHIP).
    """

    CLEAN_SHIP = "clean_ship"
    SHIP_WITH_GAPS = "ship_with_gaps"
    FAILED = "failed"
    ESCALATED = "escalated"
    ABORTED = "aborted"
    HOLD = "hold"
    STABLE_FAILURE = "stable_failure"
    ERROR = "error"


def classify_outcome(exit_code: int, defect_registry_present: bool) -> Outcome:
    """Map a ``run_pipeline`` exit code (+ defect-registry presence) to Outcome.

    Exit-code map (``orchestrator.run_pipeline``): 0=shipped, 1=failed,
    2=escalated, 3=aborted, 4=hold, 5=stable_failure. On exit 0, a present
    ``<spec>-defect-registry.md`` (written only for ship-with-gaps-eligible
    runs) marks SHIP_WITH_GAPS; otherwise CLEAN_SHIP.

    ASSUMES enterprise is disabled. Under ``enterprise_enabled=true`` the
    pipeline overloads exit 1 (rollback) and exit 2 (regression/canary failure),
    so 1/2 become ambiguous — the battery validator warns in that case.
    """
    if exit_code == 0:
        return Outcome.SHIP_WITH_GAPS if defect_registry_present else Outcome.CLEAN_SHIP
    return {
        1: Outcome.FAILED,
        2: Outcome.ESCALATED,
        3: Outcome.ABORTED,
        4: Outcome.HOLD,
        5: Outcome.STABLE_FAILURE,
    }.get(exit_code, Outcome.ERROR)


def is_ship(outcome: Outcome) -> bool:
    """Did the run ship working code? CLEAN_SHIP only.

    SHIP_WITH_GAPS and STABLE_FAILURE are deliberately NOT clean ships — that
    distinction is the entire point of Sprint 1.
    """
    return outcome is Outcome.CLEAN_SHIP


_TERMINAL_EVENTS = {
    "SPEC_SHIPPED", "SPEC_ESCALATED", "SPEC_STABLE_FAILURE", "SPEC_FAILED",
}


@dataclass
class RunMetrics:
    """Metrics extracted from a single run's events.jsonl."""

    cost_usd: float | None = None
    iterations: int | None = None
    run_id: str | None = None


def parse_run_metrics(events_path: Path) -> RunMetrics:
    """Extract cost / iterations / run_id from a SINGLE run's events.jsonl.

    The runner wipes events.jsonl before each run, so the whole file is one
    run's events. ``cost_usd`` comes from the last COST_SUMMARY's
    ``data.estimated_usd``; ``iterations`` from the terminal SPEC_* event's
    ``data.iterations`` (SHIPPED/ESCALATED/STABLE_FAILURE carry it; SPEC_FAILED
    does not → None); ``run_id`` from any event's top-level ``run_id``.

    Tolerant: missing file or torn lines → best-effort, never raises.
    """
    records = read_jsonl_tolerant(events_path)
    metrics = RunMetrics()
    for rec in records:
        if metrics.run_id is None and rec.get("run_id"):
            metrics.run_id = rec["run_id"]
        event = rec.get("event")
        data = rec.get("data") or {}
        if event == "COST_SUMMARY":
            usd = data.get("estimated_usd")
            if usd is not None:
                metrics.cost_usd = float(usd)
        if event in _TERMINAL_EVENTS:
            iters = data.get("iterations")
            if iters is not None:
                metrics.iterations = int(iters)
    return metrics


@dataclass
class ScorecardRow:
    """One row per run. Minimal by design — analysis is post-hoc over the JSONL."""

    spec: str
    stack: str
    target: str
    run: int
    outcome: str
    exit_code: int
    cost_usd: float | None
    iterations: int | None
    run_id: str | None
    wall_s: float
    ts: float


def row_to_json(row: ScorecardRow) -> str:
    """Serialize one row to a compact JSON line (no trailing newline)."""
    return json.dumps(asdict(row), separators=(",", ":"))


def append_scorecard(path: Path, row: ScorecardRow) -> None:
    """Append one scorecard row as a JSONL line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(row_to_json(row) + "\n")
