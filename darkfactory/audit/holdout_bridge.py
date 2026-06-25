"""Validator-Bridge: sanitized, spec-anchored holdout feedback (P2, NARROW first cut).

Today the blind-holdout gate emits a single fixed BINARY string ("Blind adversarial
validation failed") for every blind-holdout failure — leak-safe, but the Implementer
cannot FIX a holdout catch and the run escalates. This module restores the canonical
"Validator Bridge": it relays a single templated, spec-anchor-only line per failing
holdout whose author-declared ``spec_anchor`` RESOLVES to a real anchor in the locked
spec. There is NO category gate and NO security-domain screen — adversarial/security
holdouts anchor to the requirement whose robustness they probe and are relayable as an
anchor pointer; truly-unanchorable (type-3) holdouts naturally fall to binary via the
anchor-resolution check.

DESIGN INVARIANTS (leak-safety dominates):
  * The relay is built BY TEMPLATE from author-declared structured fields
    (``holdout-bridge.json``), NEVER by parsing test output, source, or titles.
  * The ONLY string-construction path is the fixed template below — no f-string
    interpolates raw runtime/path/source/input. The single interpolated token is the
    ``spec_anchor`` (e.g. ``FR-2``), and only AFTER it is confirmed to EXIST in the
    locked spec (word-boundary match, FR-2 != FR-20).
  * FAIL-TO-BINARY: any missing/unparseable input, no failing ids, or no resolvable
    anchor returns EXACTLY ``binary_fallback``. The output is therefore always in
    {binary_fallback, well-formed anchor-only template} — never raw holdout content.
  * The relay is anchor-only: no observed value, no input instance, no test title, no
    free text from the manifest. (The decision is anchor-resolution + objective-not-
    instance.) The caller additionally re-scans the rendered output through the
    Oracle-feedback ``_LEAKAGE_PATTERNS`` pass as general defense-in-depth.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# The FIXED relay template. The ONLY interpolated value is ``anchor`` — a validated
# spec-section token (e.g. "FR-2") confirmed to exist in the locked spec. No observed
# value, no input instance, no test name, no free text from the manifest.
_TEMPLATE = (
    "- {anchor}: behavioral deviation — review the spec's stated behavior for this "
    "requirement and the visible scenario suite."
)

_HEADER = "## Blind Adversarial Validation\n\n"

# A spec anchor token: FR-N / EC-N / AC-N / NFR-N / SI-N (and dotted/lettered
# sub-forms like FR-2.1 or AC-16a). Used both to validate the declared anchor's
# SHAPE and to locate it in the locked spec.
_ANCHOR_RE = re.compile(r"^(?:FR|EC|AC|NFR|SI)-\d+[A-Za-z0-9.]*$")


def _resolve_anchor_exists(anchor: str, spec_body: str) -> bool:
    """True if ``anchor`` EXISTS as a literal token in the locked spec.

    Word-boundary-anchored literal match on the anchor token so ``FR-2`` does not
    spuriously match ``FR-20`` or ``FR-2.1`` (each distinct anchor must be declared
    and present). ``re.escape`` because anchors can contain '.' (e.g. FR-2.1).
    """
    token_re = re.compile(rf"(?<![A-Za-z0-9]){re.escape(anchor)}(?![A-Za-z0-9.])")
    return token_re.search(spec_body) is not None


def _load_manifest(manifest_path: Path) -> list[dict] | None:
    """Load and shape-validate ``holdout-bridge.json``.

    Returns a list of entry dicts, or None on ANY problem (missing, unreadable,
    not-JSON, not-a-list, malformed entries) → caller falls to binary.
    """
    try:
        raw = manifest_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, ValueError):
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    entries: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            return None
        entries.append(item)
    return entries


def build_holdout_feedback(
    *,
    failed_ids: list[str] | set[str] | None,
    manifest_path: Path,
    spec_path: Path,
    binary_fallback: str,
) -> str:
    """Build sanitized, anchor-only holdout feedback — or fall to ``binary_fallback``.

    Relays ONE templated line per failing holdout id whose manifest entry has a
    ``spec_anchor`` that (a) is a valid anchor token by SHAPE and (b) RESOLVES to a
    real anchor present in the locked spec. There is NO category gate and NO
    security-domain screen — the relay decision is solely anchor-resolution.

    Returns EXACTLY ``binary_fallback`` when: no failing ids, manifest missing/
    unparseable, or no resolvable anchor survives. The returned relay (when non-empty)
    is the templated anchor-only lines followed by the full binary fallback — a STRICT
    SUPERSET of the binary signal.
    """
    ids = {str(x) for x in (failed_ids or []) if str(x).strip()}
    if not ids:
        return binary_fallback

    entries = _load_manifest(manifest_path)
    if entries is None:
        return binary_fallback

    try:
        spec_body = spec_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, ValueError):
        return binary_fallback
    if not spec_body.strip():
        return binary_fallback

    # Index manifest by opaque test_id. A duplicate/malformed test_id makes the manifest
    # untrustworthy → binary (we never guess which entry a failing id refers to).
    by_id: dict[str, dict] = {}
    for entry in entries:
        tid = entry.get("test_id")
        if not isinstance(tid, str) or not tid.strip():
            continue  # entries without a usable test_id can never be relayed
        if tid in by_id:
            return binary_fallback  # ambiguous mapping → fail-to-binary
        by_id[tid] = entry

    relay_anchors: list[str] = []
    seen_anchors: set[str] = set()
    for tid in ids:
        entry = by_id.get(tid)
        if entry is None:
            # A failing id with no manifest entry — cannot relay it. (Do NOT fail the
            # whole relay; other ids may be relayable. This one stays covered only by
            # the binary fallback, which is always appended.)
            continue
        anchor = entry.get("spec_anchor")
        if not isinstance(anchor, str):
            continue
        anchor = anchor.strip()
        if not _ANCHOR_RE.match(anchor):
            continue  # not a valid anchor SHAPE → not relayable
        if not _resolve_anchor_exists(anchor, spec_body):
            continue  # anchor does not exist in the locked spec → not relayable
        if anchor in seen_anchors:
            continue  # dedupe — one line per anchor regardless of how many ids hit it
        seen_anchors.add(anchor)
        relay_anchors.append(anchor)

    if not relay_anchors:
        return binary_fallback

    # Construct from the FIXED template only. Stable order for determinism.
    lines = [_TEMPLATE.format(anchor=a) for a in sorted(relay_anchors)]
    relay_block = (
        _HEADER
        + "The following requirements show a behavioral deviation under blind "
        "adversarial validation:\n\n"
        + "\n".join(lines)
        + "\n\n"
    )
    # ALWAYS append the full binary fallback (strict superset of the binary signal).
    return relay_block + binary_fallback
