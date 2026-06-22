"""Cross-spec forward-reference AUDIT — the deferral chain's closure check.

A substrate primitive marked ``verifiable=false`` defers a capability to a later
build phase; the spec-validator's elicitation/deferral gates and
``/df-rescope-deferred`` correctly defer it WITHIN a spec. But nothing within one
spec can prove the promise is HONORED downstream — that a later spec actually
re-declares the primitive ``verifiable=true`` (the substrate landed) and tests
it. Empirically, that promise is silently broken (no bb spec re-declares the
``tenant_isolation`` primitive ``verifiable=true``), so the deferred isolation
guarantee just moves phases and is forgotten.

This module is the cross-spec ledger + audit. The ledger is DERIVED from the
specs themselves (no separate persisted file to drift — the specs ARE the
record, so it works identically for a planned chain and a brownfield repo where
specs accrete one at a time, and a future spec is "seen" simply because it's in
the directory). A capability is:

- **resolved** — some spec in the project declares the primitive TYPE
  ``verifiable=true`` (the substrate landed and is now gate-verified); OR
- **pending** — declared ``verifiable=false`` somewhere and ``verifiable=true``
  NOWHERE — an OPEN obligation that must be honored before the project can claim
  the deferred capability.

It does NOT auto-decide "the deferred ACs were re-tested" (that's a semantic
judgment the operator owns); it makes the obligation VISIBLE and fail-closed so
it can't be silently buried. Type-level matching (not identifier-level) so an
F-2 per-table re-declaration of the same capability counts as honoring it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.spec.deferral_check import _parse_substrate_toml, _section

_FWD_REF_ANCHOR_RE = re.compile(
    r"^\|\s*((?:AC|EC|FR)-\d+)\s*\|.*@untestable:\s*forward-reference",
    re.IGNORECASE,
)


@dataclass
class DeferredObligation:
    """A deferred capability (``verifiable=false`` primitive type) and where it
    was deferred. ``resolved_in`` is the spec(s) that re-declare it
    ``verifiable=true`` (empty → PENDING)."""

    primitive_type: str
    deferred_in: list[str] = field(default_factory=list)
    resolved_in: list[str] = field(default_factory=list)
    forward_ref_anchors: dict[str, list[str]] = field(default_factory=dict)

    @property
    def pending(self) -> bool:
        return not self.resolved_in


def _primitive_verifiability(spec_text: str) -> dict[str, set[bool | None]]:
    """For one spec: ``{primitive_type: {verifiable values seen}}`` (False, True,
    or None for default-verify). Empty if no parseable substrate block."""
    block = _section(spec_text, r"## Substrate Primitives")
    out: dict[str, set] = {}
    for primitive, entries in _parse_substrate_toml(block).items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            v = entry.get("verifiable")
            out.setdefault(primitive, set()).add(
                False if v is False else (True if v is True else None)
            )
    return out


def _forward_ref_anchors(spec_text: str) -> list[str]:
    """AC/EC/FR ids in this spec annotated ``@untestable: forward-reference``."""
    return [
        m.group(1).upper()
        for ln in spec_text.split("\n")
        if (m := _FWD_REF_ANCHOR_RE.match(ln))
    ]


def audit_deferred_obligations(project_dir: Path) -> list[DeferredObligation]:
    """Scan every spec in ``<project>/.dark-factory/specs`` and return one
    obligation per primitive TYPE that is declared ``verifiable=false`` in any
    spec. ``resolved_in`` lists specs declaring the same type ``verifiable=true``;
    a pending obligation (``resolved_in`` empty) is an unkept forward-reference.
    """
    specs_dir = project_dir / ".dark-factory" / "specs"
    if not specs_dir.is_dir():
        return []
    deferred: dict[str, list[str]] = {}          # type -> specs declaring it false
    resolved: dict[str, list[str]] = {}          # type -> specs declaring it true
    fwd_refs: dict[str, dict[str, list[str]]] = {}  # type -> {spec: anchors} (best-effort)
    for spec_file in sorted(specs_dir.glob("*.md")):
        text = spec_file.read_text(encoding="utf-8")
        name = spec_file.stem
        prim = _primitive_verifiability(text)
        for ptype, vals in prim.items():
            if False in vals:
                deferred.setdefault(ptype, []).append(name)
            if True in vals:
                resolved.setdefault(ptype, []).append(name)
        anchors = _forward_ref_anchors(text)
        if anchors and prim:
            # Best-effort: attach this spec's forward-ref anchors to each of its
            # deferred primitive types (the annotation doesn't name the primitive).
            for ptype, vals in prim.items():
                if False in vals:
                    fwd_refs.setdefault(ptype, {})[name] = anchors
    out: list[DeferredObligation] = []
    for ptype in sorted(deferred):
        out.append(DeferredObligation(
            primitive_type=ptype,
            deferred_in=deferred[ptype],
            resolved_in=resolved.get(ptype, []),
            forward_ref_anchors=fwd_refs.get(ptype, {}),
        ))
    return out
