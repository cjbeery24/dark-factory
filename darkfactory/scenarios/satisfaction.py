"""Multi-run scoring, weighted satisfaction.

Replaces lib/satisfaction.sh scoring functions.
All arithmetic uses integer floor division (//) to match bash $(( )) behavior.
"""

from __future__ import annotations

import logging
from pathlib import Path

from darkfactory.models.config import PipelineConfig
from darkfactory.models.satisfaction import (
    CATEGORY_PREFIX_MAP,
    CategoryResult,
    SatisfactionScore,
)

log = logging.getLogger(__name__)


def compute_satisfaction(
    results: dict[str, tuple[int, int]],
    config: PipelineConfig,
) -> SatisfactionScore:
    """Compute weighted satisfaction score from per-file pass/fail results.

    Args:
        results: Mapping of filename → (passed_count, total_count).
                 Filenames should start with NN- prefix for category mapping.
        config: Pipeline config with category weights.

    Returns:
        SatisfactionScore with per-category breakdown and total.

    Uses Formula B: normalize-then-weight.
    Per-category score = (passed / total * 100) integer.
    Total = sum(weight_i * score_i) // sum(weight_i).
    """
    weights = config.category_weights
    categories: dict[str, CategoryResult] = {}

    # Initialize categories from weight config.
    for cat_name, weight in weights.items():
        categories[cat_name] = CategoryResult(name=cat_name, weight=weight)

    # Map files to categories by prefix.
    for filename, (passed, total) in results.items():
        cat_name = _file_to_category(filename)
        if cat_name not in categories:
            categories[cat_name] = CategoryResult(
                name=cat_name, weight=weights.get(cat_name, 100),
            )
        cat = categories[cat_name]
        cat.passed += passed
        cat.total += total

    # Compute per-category scores (integer arithmetic).
    for cat in categories.values():
        if cat.total > 0:
            cat.score = (cat.passed * 100) // cat.total
            cat.failed = cat.total - cat.passed

    # Compute weighted total.
    weighted_sum = 0
    weight_sum = 0
    total_passed = 0
    total_failed = 0
    total_scenarios = 0

    for cat in categories.values():
        if cat.total > 0:
            weighted_sum += cat.weight * cat.score
            weight_sum += cat.weight
        total_passed += cat.passed
        total_failed += cat.failed
        total_scenarios += cat.total

    total_score = weighted_sum // weight_sum if weight_sum > 0 else 0
    pass_rate = (total_passed * 100) // total_scenarios if total_scenarios > 0 else 0

    return SatisfactionScore(
        total_score=total_score,
        total_passed=total_passed,
        total_failed=total_failed,
        total_scenarios=total_scenarios,
        pass_rate=pass_rate,
        categories=[c for c in categories.values() if c.total > 0],
    )


def _file_to_category(filename: str) -> str:
    """Map a scenario filename to its category using the NN- prefix convention."""
    basename = Path(filename).name
    for cat_name, prefix in CATEGORY_PREFIX_MAP.items():
        if basename.startswith(prefix):
            return cat_name
    return "unknown"


def multi_run_file(
    run_fn: callable,
    filepath: str,
    n: int,
    k: int,
) -> bool:
    """Run a test file N times, pass if >= K runs succeed.

    Uses early exit optimization: stop as soon as K passes
    or (N - K + 1) failures are accumulated.

    Args:
        run_fn: Callable that takes a filepath and returns True if passed.
        filepath: Path to the test file.
        n: Number of runs.
        k: Required passes.

    Returns:
        True if the file passed (>= K of N).
    """
    passes = 0
    failures = 0
    max_failures = n - k + 1

    for _ in range(n):
        if run_fn(filepath):
            passes += 1
            if passes >= k:
                return True
        else:
            failures += 1
            if failures >= max_failures:
                return False

    return passes >= k
