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
    """Estimate USD cost for a given number of turns.

    Applies cost_correction_factor from calibration to adjust estimates.
    """
    factor = config.cost_correction_factor
    input_tokens = int(turns * config.cost_est_input_tokens_per_turn * factor)
    output_tokens = int(turns * config.cost_est_output_tokens_per_turn * factor)
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


# Token-based per-model pricing (USD per token) — the single source of truth for
# REAL-usage costing from measured tokens. (``estimate_cost`` above is the
# legacy turn-based/cap path; it ignores caching and assumes every agent maxes
# its turns — see memory ``project_cost_accounting_estimate_at_cap``. Fixing the
# pipeline to use this is a tracked follow-up.) Keyed by model-family substring
# because model ids carry date suffixes (e.g. ``claude-sonnet-4-20250514``).
# Rates are current Anthropic published prices; approximate by design.
MODEL_RATES: dict[str, tuple[float, float]] = {
    # family: (input_per_token, output_per_token). Verified against the
    # authoritative claude-api pricing reference 2026-06-09 (do NOT copy the
    # stale $15/$75 Opus rate from config.cost_per_input_token defaults).
    "opus": (0.000005, 0.000025),    # $5 / $25 per 1M  (Opus 4.6/4.7/4.8)
    "sonnet": (0.000003, 0.000015),  # $3 / $15 per 1M  (Sonnet 4.6)
    "haiku": (0.000001, 0.000005),   # $1 / $5 per 1M   (Haiku 4.5)
}

# Anthropic 5-minute prompt-cache multipliers, relative to the INPUT rate:
# cache reads bill at 0.1x, cache writes (creation) at 1.25x. The 1-hour cache
# tier (2.0x) is intentionally NOT modeled — the pipeline does not use it.
_CACHE_READ_MULT = 0.1
_CACHE_CREATE_MULT = 1.25


def _rates_for_model(model_id: str) -> tuple[float, float] | None:
    mid = (model_id or "").lower()
    for family, rates in MODEL_RATES.items():
        if family in mid:
            return rates
    return None


def cost_from_tokens(
    model_id: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float | None:
    """Real USD cost from MEASURED token counts, cache-aware.

    Returns ``None`` when ``model_id`` is unrecognized, so callers report real
    token counts without inventing a price. Cache tokens are priced relative to
    the model's input rate (read 0.1x, creation 1.25x — Anthropic 5-minute tier).
    """
    rates = _rates_for_model(model_id)
    if rates is None:
        return None
    in_rate, out_rate = rates
    return (
        input_tokens * in_rate
        + output_tokens * out_rate
        + cache_read_tokens * in_rate * _CACHE_READ_MULT
        + cache_creation_tokens * in_rate * _CACHE_CREATE_MULT
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
