"""Metrics summary — aggregates pipeline metrics from JSONL.

Replaces scripts/metrics-summary.sh. Reads .dark-factory/metrics.jsonl
and produces human-readable summary statistics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.utils.json_helpers import read_jsonl


@dataclass
class MetricsRow:
    """A single metrics record."""
    spec: str = ""
    run_id: str = ""
    outcome: str = ""
    iterations: int = 0
    wall_seconds: int = 0
    agent_failures: int = 0
    pass_rates: str = ""


@dataclass
class MetricsSummary:
    """Aggregated metrics summary."""
    rows: list[MetricsRow] = field(default_factory=list)
    total: int = 0
    shipped: int = 0
    escalated: int = 0
    failed: int = 0
    total_iterations: int = 0
    total_wall_seconds: int = 0

    @property
    def ship_rate(self) -> int:
        """Ship rate as integer percentage (floor division)."""
        if self.total == 0:
            return 0
        return self.shipped * 100 // self.total

    @property
    def avg_iterations(self) -> int:
        if self.total == 0:
            return 0
        return self.total_iterations // self.total

    @property
    def avg_wall_seconds(self) -> int:
        if self.total == 0:
            return 0
        return self.total_wall_seconds // self.total

    @property
    def first_pass_rate(self) -> int:
        """Percentage of shipped specs that shipped on iteration 1."""
        if self.shipped == 0:
            return 0
        first_pass = sum(
            1 for r in self.rows
            if r.outcome == "shipped" and r.iterations == 1
        )
        return first_pass * 100 // self.shipped

    @property
    def first_pass_count(self) -> int:
        return sum(
            1 for r in self.rows
            if r.outcome == "shipped" and r.iterations == 1
        )


def summarize_metrics(metrics_file: Path) -> MetricsSummary:
    """Read metrics JSONL and compute summary statistics."""
    summary = MetricsSummary()
    records = read_jsonl(metrics_file)

    for rec in records:
        outcome = rec.get("outcome", "")
        if not outcome or outcome == "in_progress":
            continue

        iters = _safe_int(rec.get("iters", 0))
        wall_s = _safe_int(rec.get("wall_s", 0))

        row = MetricsRow(
            spec=rec.get("spec", ""),
            run_id=rec.get("run_id", ""),
            outcome=outcome,
            iterations=iters,
            wall_seconds=wall_s,
            agent_failures=_safe_int(rec.get("agent_failures", 0)),
            pass_rates=str(rec.get("pass_rates", "")),
        )
        summary.rows.append(row)
        summary.total += 1

        if outcome == "shipped":
            summary.shipped += 1
        elif outcome == "escalated":
            summary.escalated += 1
        else:
            summary.failed += 1

        summary.total_iterations += iters
        summary.total_wall_seconds += wall_s

    return summary


def format_table(summary: MetricsSummary) -> str:
    """Format metrics summary as a human-readable table."""
    lines: list[str] = []
    header = (
        f"{'SPEC':<30} {'RUN_ID':<20} {'OUTCOME':<10} "
        f"{'ITERS':<6} {'WALL(s)':<8} {'FAILURES':<10} {'PASS_RATES':<12}"
    )
    lines.append(header)
    lines.append("-" * 100)

    for row in summary.rows:
        lines.append(
            f"{row.spec[:30]:<30} {row.run_id[:20]:<20} {row.outcome:<10} "
            f"{row.iterations:<6} {row.wall_seconds:<8} {row.agent_failures:<10} "
            f"{row.pass_rates[:12]:<12}"
        )

    lines.append("-" * 100)
    lines.append("")
    lines.append("Summary:")
    lines.append(f"  Total runs:      {summary.total}")
    lines.append(f"  Shipped:         {summary.shipped}")
    lines.append(f"  Escalated:       {summary.escalated}")
    lines.append(f"  Failed/Other:    {summary.failed}")

    if summary.total > 0:
        lines.append(f"  Ship rate:       {summary.ship_rate}%")
        lines.append(f"  Avg iterations:  {summary.avg_iterations}")
        lines.append(f"  Avg wall time:   {summary.avg_wall_seconds}s")
        if summary.shipped > 0:
            lines.append(
                f"  First-pass rate: {summary.first_pass_rate}% "
                f"({summary.first_pass_count}/{summary.shipped})"
            )

    return "\n".join(lines)


def _safe_int(val: object) -> int:
    """Convert to int, returning 0 on failure."""
    try:
        return int(val)  # type: ignore[call-overload]
    except (ValueError, TypeError):
        return 0
