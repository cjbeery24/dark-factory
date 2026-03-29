"""Multi-run scoring, weighted satisfaction.

Replaces lib/satisfaction.sh scoring functions.
All arithmetic uses integer floor division (//) to match bash $(( )) behavior.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

from darkfactory.models.config import PipelineConfig
from darkfactory.models.satisfaction import (
    CATEGORY_PREFIX_MAP,
    TIER1_PREFIXES,
    _NN_TO_CATEGORY,
    CategoryResult,
    SatisfactionScore,
)

log = logging.getLogger(__name__)


def compute_satisfaction(
    results: dict[str, tuple[int, int]],
    config: PipelineConfig,
    crashed_per_file: dict[str, int] | None = None,
) -> SatisfactionScore:
    """Compute weighted satisfaction score from per-file pass/fail results.

    Args:
        results: Mapping of filename → (passed_count, total_count).
                 Filenames should start with NN- prefix for category mapping.
        config: Pipeline config with category weights.
        crashed_per_file: Optional mapping of filename → crash count.
                          Crashed scenarios are excluded from the denominator
                          so scenario bugs don't penalize implementation quality.

    Returns:
        SatisfactionScore with per-category breakdown and total.

    Uses Formula B: normalize-then-weight.
    Per-category score = (passed / total * 100) integer.
    Total = sum(weight_i * score_i) // sum(weight_i).
    """
    weights = config.category_weights
    categories: dict[str, CategoryResult] = {}
    crash_counts = crashed_per_file or {}

    # Initialize categories from weight config.
    for cat_name, weight in weights.items():
        categories[cat_name] = CategoryResult(name=cat_name, weight=weight)

    # Map files to categories by prefix (Tier 2 files only — Tier 1 scored separately).
    for filename, (passed, total) in results.items():
        if _is_tier1(filename):
            continue

        crashed = crash_counts.get(filename, 0)
        adjusted_total = max(0, total - crashed)
        adjusted_passed = min(passed, adjusted_total)

        cat_name = _file_to_category(filename)
        if cat_name not in categories:
            categories[cat_name] = CategoryResult(
                name=cat_name, weight=weights.get(cat_name, 100),
            )
        cat = categories[cat_name]
        cat.passed += adjusted_passed
        cat.total += adjusted_total

    # Compute per-category scores (integer arithmetic).
    for cat in categories.values():
        if cat.total > 0:
            cat.score = (cat.passed * 100) // cat.total
            cat.failed = cat.total - cat.passed

    # Compute per-category weighted total (Tier 2 categories only).
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

    tier2_score: int | None = weighted_sum // weight_sum if weight_sum > 0 else None

    # Compute Tier 1 score (simple pass rate across all Tier 1 files).
    t1_passed = 0
    t1_total = 0
    for filename, (passed, total) in results.items():
        if _is_tier1(filename):
            crashed = crash_counts.get(filename, 0)
            adj_total = max(0, total - crashed)
            t1_passed += min(passed, adj_total)
            t1_total += adj_total

    tier1_score: int | None = (t1_passed * 100) // t1_total if t1_total > 0 else None

    # Include Tier 1 counts in aggregates.
    total_passed += t1_passed
    total_failed += t1_total - t1_passed
    total_scenarios += t1_total

    # Compute combined score with tier weights.
    if tier1_score is not None and tier2_score is not None:
        t1w = config.tier1_weight
        t2w = config.tier2_weight
        total_score = (t1w * tier1_score + t2w * tier2_score) // (t1w + t2w)
    elif tier1_score is not None:
        total_score = tier1_score
    elif tier2_score is not None:
        total_score = tier2_score
    else:
        total_score = 0

    pass_rate = (total_passed * 100) // total_scenarios if total_scenarios > 0 else 0

    return SatisfactionScore(
        total_score=total_score,
        total_passed=total_passed,
        total_failed=total_failed,
        total_scenarios=total_scenarios,
        pass_rate=pass_rate,
        categories=[c for c in categories.values() if c.total > 0],
        tier1_score=tier1_score,
        tier2_score=tier2_score,
    )


def _is_tier1(filename: str) -> bool:
    """Return True if *filename* is a Tier 1 contract file."""
    basename = Path(filename).name
    return any(basename.startswith(p) for p in TIER1_PREFIXES)


def _file_to_category(filename: str) -> str:
    """Map a scenario filename to its category.

    Supports both conventions:
    - NN-layer.test.ext  (Node.js: 01-contract.test.ts)
    - test_NN_layer.py   (Python: test_01_contract.py)
    """
    basename = Path(filename).name

    # Try NN- prefix convention first (Node.js, etc.).
    for cat_name, prefix in CATEGORY_PREFIX_MAP.items():
        if basename.startswith(prefix):
            return cat_name

    # Try test_NN_ prefix convention (Python).
    m = re.match(r'^test_(\d{2})_', basename)
    if m:
        nn = m.group(1)
        return _NN_TO_CATEGORY.get(nn, "unknown")

    return "unknown"


def multi_run_file(
    run_fn: Callable[[str], bool],
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
