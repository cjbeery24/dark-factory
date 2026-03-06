"""Trust scoring models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TrustDimensions:
    """Individual trust dimension scores (0-100 each)."""
    first_pass_rate: int = 0
    avg_iterations: int = 0
    regression_fail_rate: int = 0
    spec_complexity: int = 0
    module_familiarity: int = 0


@dataclass
class TrustScore:
    """Composite trust score with dimension breakdown."""
    total: int = 50  # 0-100
    dimensions: TrustDimensions = field(default_factory=TrustDimensions)
    cold_start: bool = True
