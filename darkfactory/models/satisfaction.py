"""Satisfaction scoring models."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


@dataclass
class CategoryResult:
    """Result for a single scenario category."""
    name: str
    passed: int = 0
    failed: int = 0
    total: int = 0
    weight: int = 100
    score: int = 0  # (passed/total * 100) integer


@dataclass
class SatisfactionScore:
    """Weighted satisfaction score across all categories."""
    total_score: int = 0  # 0-100, integer arithmetic
    total_passed: int = 0
    total_failed: int = 0
    total_scenarios: int = 0
    pass_rate: int = 0  # percentage, integer
    categories: list[CategoryResult] = field(default_factory=list)


# Category name → filename prefix mapping (NN- prefix convention).
CATEGORY_PREFIX_MAP: dict[str, str] = {
    "contract": "01-",
    "happy_path": "02-",
    "edge_cases": "03-",
    "error_conditions": "04-",
    "state_sequence": "05-",
    "security": "06-",
    "property_based": "07-",
    "adversarial": "08-",
}


class TrajectoryType(enum.Enum):
    """Trajectory classification."""
    INSUFFICIENT = "INSUFFICIENT"
    IMPROVING = "IMPROVING"
    DEGRADING = "DEGRADING"
    PLATEAU = "PLATEAU"
    OSCILLATING = "OSCILLATING"
    RECOVERING = "RECOVERING"
    MIXED = "MIXED"
