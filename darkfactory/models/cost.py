"""Cost estimation models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostEstimate:
    """Estimated cost for a set of agent turns."""
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_usd: float = 0.0


@dataclass
class BudgetStatus:
    """Current budget consumption state."""
    budget_usd: float = 0.0
    spent_usd: float = 0.0
    remaining_usd: float = 0.0
    percent_used: float = 0.0
    exceeded: bool = False
