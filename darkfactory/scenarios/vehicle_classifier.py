"""Phase-1 coverage-floor vehicle classifier (host-side, SUGGEST-ONLY).

When the coverage floor aborts on anchors with zero Tier-2 scenarios, the static
``render_floor_remediation`` menu makes the author (a) know dark-factory's vehicle
taxonomy, (b) decide which anchors aren't Tier-2-coverable, and (c) author the
alternative verification. The TE-reason-only evidence at that point is free text,
so the floor deliberately does NOT auto-route (regex on per-run prose is a
mis-routing treadmill — see ``render_floor_remediation``).

This module closes sub-burdens (a)+(b) with a focused LLM that a measurement
harness validated at high label-consistency (ROUTING 6/6, WAIVER 5/5, MAX
false-preserve 0/8 over K=8; n=11, one spec — a go/no-go signal, not proof of
verification-preservation). It asks, per uncovered anchor, WHICH vehicle the
author should declare, and emits an explicit ABSTAIN when no vehicle clearly
fits (a forced guesser re-introduces the mis-route the static menu avoids).

It is SUGGEST-ONLY: grants no credit, changes no gate, never auto-applies. The
author confirms and applies via a spec edit; Phase-0's front-of-run precheck
(``check_subprocess_contract_blockers``) backstops any applied ``subprocess``
routing — a vacuous command contract FATALs. The trust TIER is assigned
DETERMINISTICALLY from the suggested vehicle (``VEHICLE_TIERS``) — the LLM never
decides whether verification is preserved; that classification is principled and
fixed (Neo Phase-1 review: ``subprocess`` REDUCES verification — it drops the
blind-holdout net — and must not be mis-sold as preserving).

Barrier: reads only the anchor id + its text + the TE's free-text gap reason
(already consumed by ``render_floor_remediation``) to advise a HUMAN. No source,
no holdouts; grants no credit. Crosses no information barrier.

Fail-safe: ANY failure (subprocess error, ``claude`` missing, timeout,
unparseable output, unknown vehicle) degrades that anchor to ABSTAIN → the static
full menu. A classifier failure must never mask or alter the floor abort. With
``claude`` absent every anchor ABSTAINs, and ``render_floor_remediation``
suppresses the per-anchor "no suggestion" note when NO anchor produced a real
suggestion — so the abort is identical to the pre-Phase-1 menu in that case.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable

from darkfactory.utils.subprocess_runner import RunResult
from darkfactory.utils.subprocess_runner import run as _default_subprocess_runner

log = logging.getLogger(__name__)

# Trust tiers (the 3-tier ladder + waiver + unsure). Assigned deterministically
# from the vehicle — NOT chosen by the LLM.
PRESERVING = "PRESERVING"
CONDITIONAL = "CONDITIONAL"
REDUCING = "REDUCING"
WAIVER = "WAIVER"
UNSURE = "UNSURE"

ABSTAIN = "ABSTAIN"

# vehicle -> (tier, one-line trust caption shown to the author). The captions
# carry the principled verification-impact statement so a REDUCING suggestion is
# never mis-read as free.
VEHICLE_TIERS: dict[str, tuple[str, str]] = {
    "runtime-boundary": (
        PRESERVING,
        "preserves verification — the anchor STILL requires a passing scenario "
        "AND a blind holdout; only the import-credit requirement is waived",
    ),
    "substrate-covers": (
        CONDITIONAL,
        "preserves verification ONLY if the substrate primitive is gated THIS "
        "build phase; `verifiable = false` defers it and leaves a gap",
    ),
    "subprocess": (
        REDUCING,
        "REDUCES verification — the anchor is removed from scenario+holdout "
        "scoring and verified only by one visible Tier-1 command contract (the "
        "blind-holdout net is dropped); confirm that is acceptable",
    ),
    "untestable": (
        WAIVER,
        "WAIVES verification — confirm the anchor is genuinely unobservable in "
        "the pipeline (real browser / computed CSS / perf at scale)",
    ),
    "rescope": (
        WAIVER,
        "WAIVES verification — rewrite the AC to assert an observable behavior",
    ),
}


@dataclass
class VehicleSuggestion:
    """A per-anchor vehicle suggestion. ``vehicle`` is a ``VEHICLE_TIERS`` key
    or ``ABSTAIN``; ``tier`` is the deterministic trust tier; ``reason`` is the
    LLM's one-sentence rationale (or a failure note for ABSTAIN-on-error)."""

    anchor_id: str
    vehicle: str
    tier: str
    reason: str

    @property
    def abstained(self) -> bool:
        return self.vehicle == ABSTAIN


SubprocessRunner = Callable[..., RunResult]


_CLASSIFIER_PROMPT = """You are classifying a single acceptance-criterion anchor that the test engineer could NOT cover with a unit/integration test scenario. Decide which verification VEHICLE the spec author should declare for it.

Vehicles:
- runtime-boundary: a runtime/framework-injected global with NO importable project module (e.g. import.meta.env). STILL verified by a scenario + a blind holdout.
- substrate-covers: verified by a DB/substrate primitive (a GUC, an RLS policy, a migration) the architect declares.
- subprocess: a CLI / startup / build / connection-failure behavior verified by RUNNING a command and asserting a BEHAVIORAL outcome (an exit code on a specific input, or a specific string in a file the command produces).
- untestable: genuinely not observable in the pipeline (needs a real browser, computed CSS, perf at scale) or enforced by a non-scenario gate.
- rescope: a documentation/guidance constraint with no observable predicate; the AC itself should be rewritten.
- ABSTAIN: you are not confident the anchor clearly fits exactly ONE vehicle above.

Prefer ABSTAIN over a low-confidence guess. A wrong suggestion that removes a coverage demand is worse than no suggestion.

Anchor (the text includes the test-engineer's reason it could not be a Tier-2 scenario):
{anchor}

Reply with ONLY one JSON object and nothing else: {{"vehicle": "runtime-boundary|substrate-covers|subprocess|untestable|rescope|ABSTAIN", "reason": "<one sentence>"}}"""


def _claude_command(prompt: str, model: str) -> list[str]:
    return [
        "claude", "-p", prompt,
        "--model", model,
        "--output-format", "json",
        "--max-turns", "1",
    ]


def _abstain(anchor_id: str, reason: str) -> VehicleSuggestion:
    return VehicleSuggestion(anchor_id, ABSTAIN, UNSURE, reason)


def _classify_one(
    anchor_id: str,
    anchor_text: str,
    model: str,
    runner: SubprocessRunner,
    timeout: int,
) -> VehicleSuggestion:
    """Classify a single anchor. SELF-CONTAINED fail-safe: returns an ABSTAIN
    suggestion on ANY error (subprocess failure, malformed RunResult, unparseable
    output) rather than raising — the floor abort must never be masked, and the
    guarantee does not depend on the caller's wrapper."""
    try:
        prompt = _CLASSIFIER_PROMPT.format(anchor=anchor_text)
        try:
            result = runner(_claude_command(prompt, model), timeout=timeout)
        except FileNotFoundError:
            return _abstain(anchor_id, "classifier unavailable (claude not found)")
        if getattr(result, "timed_out", False):
            return _abstain(anchor_id, "classifier timed out")
        if getattr(result, "exit_code", 1) != 0:
            return _abstain(anchor_id, "classifier exited non-zero")
        # Strip a leading BOM/whitespace so the envelope parse below sees clean
        # JSON. claude -p --output-format json wraps the reply in a "result" str.
        text = (getattr(result, "stdout", "") or "").lstrip("﻿ \t\r\n")
        try:
            envelope = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            envelope = None
        if isinstance(envelope, dict) and isinstance(envelope.get("result"), str):
            text = envelope["result"]
        vehicle, reason = _parse_reply(text)
        if vehicle == ABSTAIN:
            return _abstain(anchor_id, reason or "no confident vehicle")
        if vehicle not in VEHICLE_TIERS:
            return _abstain(anchor_id, f"unrecognized vehicle {vehicle!r}")
        tier = VEHICLE_TIERS[vehicle][0]
        return VehicleSuggestion(anchor_id, vehicle, tier, reason)
    except Exception as exc:  # noqa: BLE001 — self-contained fail-safe
        log.debug("vehicle classifier failed for %s: %s", anchor_id, exc)
        return _abstain(anchor_id, "classifier error")


def _iter_json_objects(text: str):
    """Yield each TOP-LEVEL balanced ``{...}`` substring of ``text`` parsed as
    JSON. Brace-matching (not a greedy first-``{``/last-``}`` slice) so prose
    with stray braces, or multiple objects, degrade gracefully — each candidate
    is tried independently instead of one over-wide slice failing on all of it."""
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    yield json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    pass
                start = -1


def _parse_reply(text: str) -> tuple[str, str]:
    """Extract (vehicle, reason) from the model reply — the first balanced JSON
    object that is a dict carrying a non-empty ``vehicle``. Tolerant of prose,
    stray braces, and multiple objects. Returns (ABSTAIN, "") if none qualifies."""
    if not text:
        return ABSTAIN, ""
    for obj in _iter_json_objects(text):
        if not isinstance(obj, dict):
            continue
        vehicle = str(obj.get("vehicle", "")).strip()
        if vehicle:
            return vehicle, str(obj.get("reason", "")).strip()
    return ABSTAIN, ""


def classify_floor_anchors(
    anchors: list[tuple[str, str]],
    *,
    model: str,
    runner: SubprocessRunner | None = None,
    timeout: int = 60,
) -> dict[str, VehicleSuggestion]:
    """Suggest a vehicle for each uncovered floor anchor.

    ``anchors`` is a list of ``(anchor_id, anchor_text)`` where ``anchor_text``
    is the AC text plus the test-engineer's gap reason (the same evidence
    ``render_floor_remediation`` shows). Returns ``{anchor_id: VehicleSuggestion}``
    — every input anchor is present; failures/low-confidence are ABSTAIN.
    Independent per-anchor calls (mirrors the harness). Never raises.
    """
    if runner is None:
        runner = _default_subprocess_runner
    out: dict[str, VehicleSuggestion] = {}
    for anchor_id, anchor_text in anchors:
        out[anchor_id] = _classify_one(
            anchor_id, anchor_text, model, runner, timeout
        )
    return out
