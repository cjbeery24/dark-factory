"""Cost estimation and budget enforcement.

Replaces lib/cost.sh — cost_estimate_from_turns, cost_check_budget, cost_spec_summary.
All arithmetic uses the same formula as the bash version.
"""

from __future__ import annotations

import logging
from pathlib import Path

from darkfactory.errors import BudgetExceeded
from darkfactory.events.types import emit_budget_alert, emit_budget_exceeded, emit_cost_summary
from darkfactory.models.config import PipelineConfig
from darkfactory.models.cost import BudgetStatus, CostEstimate
from darkfactory.notify.dispatcher import notify_budget_alert

log = logging.getLogger(__name__)


def estimate_cost(turns: int, config: PipelineConfig) -> CostEstimate:
    """Estimate USD cost for a given number of turns."""
    input_tokens = turns * config.cost_est_input_tokens_per_turn
    output_tokens = turns * config.cost_est_output_tokens_per_turn
    cost = (
        input_tokens * config.cost_per_input_token
        + output_tokens * config.cost_per_output_token
    )
    return CostEstimate(
        turns=turns,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_usd=cost,
    )


def check_budget(
    total_turns: int,
    config: PipelineConfig,
    events_file: Path,
    spec_name: str,
    run_id: str,
    *,
    alerted: set[int] | None = None,
) -> BudgetStatus:
    """Check if the current spend is within budget.

    Args:
        total_turns: Total turns used so far.
        config: Pipeline config with budget settings.
        events_file: Path for event emission.
        spec_name: Current spec name.
        run_id: Current run ID.
        alerted: Set of already-alerted thresholds (modified in place).

    Returns:
        BudgetStatus with current state.

    Raises:
        BudgetExceeded: If budget is exceeded (at 100% threshold).
    """
    if config.cost_budget_usd <= 0:
        return BudgetStatus()

    est = estimate_cost(total_turns, config)
    spent = est.estimated_usd
    budget = config.cost_budget_usd
    remaining = budget - spent
    percent = (spent / budget) * 100

    status = BudgetStatus(
        budget_usd=budget,
        spent_usd=spent,
        remaining_usd=max(0.0, remaining),
        percent_used=percent,
        exceeded=spent >= budget,
    )

    if alerted is None:
        alerted = set()

    for threshold in config.cost_alert_thresholds:
        if percent >= threshold and threshold not in alerted:
            alerted.add(threshold)
            if threshold >= 100:
                emit_budget_exceeded(
                    events_file, spec=spec_name, run_id=run_id,
                    spent=spent, budget=budget,
                )
                raise BudgetExceeded(
                    f"budget exceeded: ${spent:.4f} / ${budget:.4f} "
                    f"({percent:.1f}%)"
                )
            else:
                emit_budget_alert(
                    events_file, spec=spec_name, run_id=run_id,
                    percent=percent, spent=spent, budget=budget,
                )
                log.warning(
                    "budget alert: %.1f%% used ($%.4f / $%.4f)",
                    percent, spent, budget,
                )
                notify_budget_alert(config, spec_name, percent, spent, budget)

    return status


def emit_spec_cost_summary(
    events_file: Path,
    spec_name: str,
    run_id: str,
    total_turns: int,
    config: PipelineConfig,
) -> None:
    """Emit a COST_SUMMARY event for the spec."""
    est = estimate_cost(total_turns, config)
    emit_cost_summary(
        events_file, spec=spec_name, run_id=run_id,
        total_turns=total_turns, estimated_usd=est.estimated_usd,
    )
