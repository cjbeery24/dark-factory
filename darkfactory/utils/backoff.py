"""Exponential backoff utilities for transient failure retry."""

from __future__ import annotations

import random


def transient_backoff(attempt: int, base_s: float) -> float:
    """Exponential backoff with jitter.

    Args:
        attempt: Zero-indexed attempt number.
        base_s: Base delay in seconds.

    Returns:
        Delay in seconds: base_s * 2^attempt + uniform jitter [0, 25% of delay].
    """
    delay = base_s * (2 ** attempt)
    jitter = random.uniform(0, delay * 0.25)
    return delay + jitter
