"""Phase 4.2 — gate policy resolver.

The smoke probe always runs and always reports. Whether a smoke failure
is *fatal* (aborts the iteration and restarts the Implementer) or
*advisory* (report it, continue to the quality gate) depends on the
spec's ``spec_type`` and iteration number.

The policy exists because bootstrap specs can't realistically start
their own server on iteration 1 — the bootstrap is literally the work
of getting a server running. Demanding smoke success on iter 1 of a
bootstrap creates a chicken-and-egg failure that burns the turn budget
on nothing. Every other case — bootstrap iter 2+, or any feature /
bugfix at any iteration — treats a smoke failure as a real blocker.

This module is intentionally tiny: one pure function, one constant.
Callers consume the return value and decide the control flow. Keeping
the policy as data (not behavior) makes it trivial to test and audit.
"""

from __future__ import annotations

from typing import Literal

GatePolicy = Literal["advisory", "fatal"]

# Spec types that are allowed to fail smoke on their very first iteration
# without aborting. Everything not in this set is fatal on iter 1.
_ADVISORY_ON_FIRST_ITER: frozenset[str] = frozenset({"bootstrap"})


def resolve_gate_policy(spec_type: str, iteration: int) -> GatePolicy:
    """Return ``advisory`` or ``fatal`` for this (spec_type, iteration) pair.

    Rules:
      - ``bootstrap`` + iter == 1 → ``advisory``  (can't start server yet)
      - ``bootstrap`` + iter  >= 2 → ``fatal``    (by iter 2 the bootstrap
        has had one Implementer pass; server must come up)
      - anything else               → ``fatal``

    An unknown / empty ``spec_type`` is treated as ``fatal`` — the safe
    default, because only the explicit bootstrap-iter1 case earns leniency.
    """
    if iteration < 1:
        # Defensive: the iteration loop is 1-indexed; anything less is a
        # caller bug. Fail closed.
        return "fatal"
    if iteration == 1 and spec_type in _ADVISORY_ON_FIRST_ITER:
        return "advisory"
    return "fatal"
