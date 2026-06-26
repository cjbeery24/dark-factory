"""Differential (positive-control) pairing for behavioral-rejection holdouts.

Part of the discrimination gate (docs/dev/2026-06-25-discrimination-gate-design.md
§"P1 DESIGN"). A behavioral-rejection holdout for an *input-attributable* AC must
come as a fixture-sharing PAIR — a CONTROL leg (the otherwise-valid input → it
succeeds) and a REJECTION leg (the triggering deviation → it is rejected) — so the
rejection is *attributable* to the input, not to an unrelated cause (the run20
ghost-token inertness). The QA-Holdout declares the pairing in
``holdout-bridge.json`` via two additive, optional fields on each entry:

    ``leg``: ``"control"`` | ``"rejection"``   (absent ⇒ an ordinary, non-paired holdout)
    ``pair_id``: an opaque token shared by the two legs of one differential

This module is the PURE pairing logic consumed by (a) the gen-time lint
(manifest-consistency: every declared-differential AC has a complete pair — catches
a MISSING control before any run) and (b) the runtime router. It does NOT parse
test source (stack-agnostic) and grants no coverage credit; ``leg``/``pair_id`` are
QA-*declared* metadata, so a complete pair is a declared PROXY for same-fixture
attribution, never a proof of it (the gate cannot prove the two legs share a
fixture — that residual is disclosed, not claimed; see the design doc's R3
label-honesty note applied to pairing).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from darkfactory.spec.anchors import _BLOCKING_ORPHAN_LAYERS

if TYPE_CHECKING:
    from darkfactory.spec.anchors import ScenarioIntent

# The author-declared "this AC is an input-attributable rejection" set is a set of
# canonical anchor ids (FR/AC/EC-N). Invariant rejection ACs (no-secrets-in-logs)
# are NOT in this set — they route to content/forbidden-pattern checks and need no
# control (see /df-write-spec "Differential rejection ACs"). The partition is
# spec-declared, never an LLM call.
_ANCHOR_RE = re.compile(r"^(?:FR|AC|EC)-\d+$")

_LEG_CONTROL = "control"
_LEG_REJECTION = "rejection"

MISSING_CONTROL = "missing_control"
MISSING_REJECTION = "missing_rejection"
UNPAIRED = "unpaired"


def derive_differential_anchors(
    intents: "list[ScenarioIntent]",
    invariant_anchors: set[str],
) -> set[str]:
    """The ACs that must carry a differential (control + rejection) holdout.

    By construction this is the **behavioral-rejection slice**: every AC that a
    ``security`` / ``error`` / ``adversarial`` §9 intent maps to (the same
    :data:`~darkfactory.spec.anchors._BLOCKING_ORPHAN_LAYERS` slice the
    addressability gate already isolates). The author-declared
    ``invariant_anchors`` (content/log invariants like "no secrets in logs",
    which have no valid sibling and route to forbidden-pattern checks) are
    SUBTRACTED. The partition is therefore spec-declared — default-differential
    for the slice, explicit opt-out for invariants — never an LLM call.

    An intent with ``mapped_anchor is None`` contributes nothing (the
    addressability gate forces blocking-layer intents to be mapped before this
    runs, so post-gate that set is empty; pre-gate orphans simply aren't demanded
    here — they are the addressability gate's job).
    """
    out: set[str] = set()
    for it in intents:
        if it.layer not in _BLOCKING_ORPHAN_LAYERS:
            continue
        anchor = it.mapped_anchor
        if anchor is None or not _ANCHOR_RE.match(anchor):
            continue
        if anchor in invariant_anchors:
            continue
        out.add(anchor)
    return out


@dataclass(frozen=True)
class DifferentialIssue:
    """A gen-time pairing defect for a declared-differential AC."""

    anchor: str
    kind: str  # MISSING_CONTROL | MISSING_REJECTION | UNPAIRED
    message: str


def _entry_anchor(entry: dict) -> str | None:
    a = entry.get("spec_anchor")
    if isinstance(a, str) and _ANCHOR_RE.match(a):
        return a
    return None


def check_differential_pairs(
    entries: list[dict],
    differential_anchors: set[str],
) -> list[DifferentialIssue]:
    """Gen-time manifest-consistency check for declared-differential ACs.

    For each anchor in ``differential_anchors`` (the spec-declared
    input-attributable rejection ACs), the holdout-bridge ``entries`` must contain
    a CONTROL leg and a REJECTION leg that **share a ``pair_id``**. Returns one
    issue per defect; an empty list means every declared-differential AC has a
    complete pair.

    Anchors NOT in ``differential_anchors`` are ignored (invariant rejections,
    happy-path ACs, etc. — they need no control). Entries with no ``leg`` are
    ignored for pairing (ordinary holdouts). Fail-soft on malformed entries: a
    non-dict or anchor-less entry contributes nothing rather than raising.
    """
    # anchor -> {pair_id -> set(legs)}, plus the loose leg presence per anchor.
    by_anchor: dict[str, dict[str, set[str]]] = {}
    loose_legs: dict[str, set[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        leg = entry.get("leg")
        if leg not in (_LEG_CONTROL, _LEG_REJECTION):
            continue
        anchor = _entry_anchor(entry)
        if anchor is None:
            continue
        loose_legs.setdefault(anchor, set()).add(leg)
        pid = entry.get("pair_id")
        if isinstance(pid, str) and pid:
            by_anchor.setdefault(anchor, {}).setdefault(pid, set()).add(leg)

    issues: list[DifferentialIssue] = []
    for anchor in sorted(differential_anchors):
        if not _ANCHOR_RE.match(anchor):
            continue
        pairs = by_anchor.get(anchor, {})
        # A complete pair = one pair_id carrying BOTH legs.
        complete = any(
            _LEG_CONTROL in legs and _LEG_REJECTION in legs
            for legs in pairs.values()
        )
        if complete:
            continue
        present = loose_legs.get(anchor, set())
        if _LEG_REJECTION in present and _LEG_CONTROL not in present:
            issues.append(DifferentialIssue(
                anchor, MISSING_CONTROL,
                f"{anchor} is a declared input-attributable rejection but its "
                f"holdout has no CONTROL leg (an otherwise-valid input that "
                f"succeeds). Without it the rejection is not attributable — a "
                f"test can 401 for the wrong reason (run20). Add a control leg "
                f"sharing a pair_id with the rejection leg.",
            ))
        elif _LEG_CONTROL in present and _LEG_REJECTION not in present:
            issues.append(DifferentialIssue(
                anchor, MISSING_REJECTION,
                f"{anchor} has a CONTROL leg but no REJECTION leg — the "
                f"differential is incomplete.",
            ))
        elif _LEG_CONTROL in present and _LEG_REJECTION in present:
            # Both legs exist but no single pair_id binds them.
            issues.append(DifferentialIssue(
                anchor, UNPAIRED,
                f"{anchor} has both a control and a rejection leg but they do "
                f"not share a pair_id — the framework cannot bind them as one "
                f"differential. Give the two legs a common pair_id.",
            ))
        else:
            issues.append(DifferentialIssue(
                anchor, MISSING_CONTROL,
                f"{anchor} is a declared input-attributable rejection but has no "
                f"differential holdout (control + rejection legs). Add one.",
            ))
    return issues
