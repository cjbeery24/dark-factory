"""CLI dashboard renderer — human-readable summary of events.

Replaces scripts/df-dashboard.sh (227 lines).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from darkfactory.models.events import EventType
from darkfactory.utils.json_helpers import read_jsonl


@dataclass
class DashboardData:
    """Pre-processed data for dashboard rendering."""
    total_specs: int = 0
    shipped: int = 0
    failed: int = 0
    escalated: int = 0
    total_iterations: int = 0
    total_cost_usd: float = 0.0
    latest_trust: int = 0
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    recent_runs: list[dict[str, Any]] = field(default_factory=list)


def build_dashboard(events_file: Path, *, last_n: int = 0, spec_filter: str = "") -> DashboardData:
    """Build dashboard data from events file.

    Args:
        events_file: Path to events.jsonl.
        last_n: Show only last N runs. 0 = all.
        spec_filter: Show only events for this spec. Empty = all.
    """
    records = read_jsonl(events_file)
    data = DashboardData()

    # Track per-run data for recent runs table.
    runs: dict[str, dict[str, Any]] = {}  # run_id -> run data

    for rec in records:
        event = rec.get("event", "")
        spec = rec.get("spec", "")
        run_id = rec.get("run_id", "")
        event_data = rec.get("data", {})

        if spec_filter and spec != spec_filter:
            continue

        if event == EventType.SPEC_START.value:
            runs[run_id] = {
                "spec": spec, "run_id": run_id, "ts": rec.get("ts", ""),
                "status": "running", "iterations": 0, "cost": 0.0,
            }

        elif event == EventType.SPEC_SHIPPED.value:
            data.shipped += 1
            data.total_specs += 1
            if run_id in runs:
                runs[run_id]["status"] = "shipped"
                runs[run_id]["iterations"] = event_data.get("iterations", 0)

        elif event == EventType.SPEC_FAILED.value:
            data.failed += 1
            data.total_specs += 1
            if run_id in runs:
                runs[run_id]["status"] = "failed"

        elif event == EventType.SPEC_ESCALATED.value:
            data.escalated += 1
            data.total_specs += 1
            if run_id in runs:
                runs[run_id]["status"] = "escalated"

        elif event == EventType.GATE_COMPLETED.value:
            data.total_iterations += 1

        elif event == EventType.COST_SUMMARY.value:
            cost = event_data.get("estimated_usd", 0.0)
            data.total_cost_usd += cost
            if run_id in runs:
                runs[run_id]["cost"] = cost

        elif event == EventType.TRUST_SCORED.value:
            data.latest_trust = event_data.get("trust", 0)

        elif event == EventType.ANOMALY_DETECTED.value:
            data.anomalies.append({
                "spec": spec, "type": event_data.get("anomaly_type", ""),
                "detail": event_data.get("detail", ""), "ts": rec.get("ts", ""),
            })

    # Build recent runs list.
    run_list = list(runs.values())
    run_list.sort(key=lambda r: r.get("ts", ""), reverse=True)
    if last_n > 0:
        run_list = run_list[:last_n]
    data.recent_runs = run_list
    return data


def format_dashboard(data: DashboardData) -> str:
    """Format dashboard data as a human-readable string."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  Dark Factory Dashboard")
    lines.append("=" * 60)
    lines.append("")

    # Health metrics.
    lines.append("[Health]")
    total = data.total_specs or 1
    ship_rate = data.shipped * 100 // total if data.total_specs else 0
    lines.append(f"  Shipped:     {data.shipped}")
    lines.append(f"  Failed:      {data.failed}")
    lines.append(f"  Escalated:   {data.escalated}")
    lines.append(f"  Ship rate:   {ship_rate}%")
    if data.total_iterations > 0 and data.total_specs > 0:
        avg_iter = data.total_iterations // data.total_specs
        lines.append(f"  Avg iters:   {avg_iter}")
    lines.append("")

    # Cost.
    if data.total_cost_usd > 0:
        lines.append("[Cost]")
        lines.append(f"  Total:       ${data.total_cost_usd:.4f}")
        if data.total_specs > 0:
            avg = data.total_cost_usd / data.total_specs
            lines.append(f"  Per spec:    ${avg:.4f}")
        lines.append("")

    # Trust.
    if data.latest_trust > 0:
        lines.append("[Trust]")
        lines.append(f"  Latest:      {data.latest_trust}/100")
        lines.append("")

    # Anomalies.
    if data.anomalies:
        lines.append("[Recent Anomalies]")
        for a in data.anomalies[-5:]:
            lines.append(f"  {a['ts']}  {a['spec']}  {a['type']}: {a['detail']}")
        lines.append("")

    # Recent runs.
    if data.recent_runs:
        lines.append("[Recent Runs]")
        lines.append(f"  {'SPEC':<25} {'STATUS':<12} {'ITERS':<6} {'COST':>10}")
        lines.append("  " + "-" * 55)
        for run in data.recent_runs[:10]:
            cost_str = f"${run.get('cost', 0):.4f}" if run.get("cost") else "-"
            lines.append(
                f"  {run['spec'][:25]:<25} {run['status']:<12} "
                f"{run.get('iterations', '-')!s:<6} {cost_str:>10}"
            )
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)
