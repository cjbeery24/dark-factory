"""Deprecation shim — module moved to ``darkfactory.adapters.typescript.proposal_rehearsal``.

Sprint 1 A2 (``docs/plans/2026-04-14-pipeline-modularity-and-remediation.md``)
extracted this module out of ``scenarios/`` into the TypeScript ecosystem
adapter. See the ``darkfactory.spec.library_symbols`` shim for full
lifecycle notes; the policy is identical.

The orchestrator now imports through the new path; this shim exists to
keep external/downstream call sites working through the deprecation
window.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "darkfactory.scenarios.proposal_rehearsal moved to "
    "darkfactory.adapters.typescript.proposal_rehearsal (Sprint 1 A2). "
    "Update imports; this shim is removed two minor releases from now.",
    DeprecationWarning,
    stacklevel=2,
)

from darkfactory.adapters.typescript.proposal_rehearsal import (  # noqa: E402, F401
    TYPECHECK_FAIL_THRESHOLD,
    BannedPatternHit,
    Proposal,
    ProposalRehearsalResult,
    build_proposal_prompt,
    decide_rehearsal_outcome,
    has_ac_banned_hit,
    parse_proposals,
    run_proposal_rehearsal,
    scan_banned_patterns,
    typecheck_proposals,
)

__all__ = [
    "TYPECHECK_FAIL_THRESHOLD",
    "BannedPatternHit",
    "Proposal",
    "ProposalRehearsalResult",
    "build_proposal_prompt",
    "decide_rehearsal_outcome",
    "has_ac_banned_hit",
    "parse_proposals",
    "run_proposal_rehearsal",
    "scan_banned_patterns",
    "typecheck_proposals",
]
