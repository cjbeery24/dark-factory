"""Read and validate the spec-validator's cross-section consistency artifacts.

The validator emits two artifacts that travel together:

* ``.dark-factory/reviews/<spec>-validation.status`` — adds a
  ``consistency=PASS|FAIL`` line to the existing key=value status file.
* ``.dark-factory/reviews/<spec>-consistency-ledger.yaml`` — per-FR
  claim ledger plus a list of typed contradictions. The ledger is the
  byte-addressable evidence the orchestrator parses for the failure
  message; no LLM call at gate-time.

The orchestrator gates on ``consistency=FAIL`` and raises a
``DarkFactoryError`` whose message names every offending claim pair
with its anchors and typology slot. The theater protection — defended
against the "validator says PASS without doing the work" failure mode
— asserts the ledger *file* was written when ``consistency=PASS``
(an absent ledger means the extraction pass never ran). An empty
``fr_ledgers: []`` inside a present file is legitimate (FR-less
specs, e.g., bug-fix or refactor).

The reader pattern mirrors ``darkfactory.architecture.precision_hint``
— never raises on a missing or unreadable artifact; returns ``None``.
The orchestrator decides what to do with absence (back-compat with
agent versions that pre-date this field treats absence as PASS).

**Naming note.** Distinct from ``darkfactory.architecture.consistency``,
which handles spec↔environment consistency rules (a different concern:
that module reconciles `project-hints.toml` rules against architect
manifests). This module handles cross-section claim contradictions
inside a single spec.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


_VALID_TYPES = {"T1", "T2", "T3", "T4", "T5"}


def read_consistency_status(status_file: Path) -> str | None:
    """Return the ``consistency`` value from a validator status file.

    Returns ``"PASS"`` / ``"FAIL"`` / ``None``. ``None`` covers both
    "file absent" and "key missing" — back-compat with validator
    versions that don't yet emit the field. Never raises.
    """
    if not status_file.exists():
        return None
    try:
        text = status_file.read_text(encoding="utf-8")
    except OSError as e:
        log.debug("consistency-status: could not read %s: %s", status_file, e)
        return None
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "consistency":
            # Exact-case contract: the validator agent's emit
            # instruction is verbatim ``PASS|FAIL``. Loose-input here
            # would mask a regression where the validator drifts to
            # lowercase or mixed case.
            v = value.strip()
            if v in {"PASS", "FAIL"}:
                return v
            log.warning(
                "consistency-status: unrecognized value %r in %s "
                "(exact case PASS|FAIL required)",
                value, status_file,
            )
            return None
    return None


def parse_consistency_ledger(ledger_path: Path) -> dict | None:
    """Load and return the consistency ledger YAML, or ``None`` if absent
    or malformed.

    The expected top-level shape is::

        spec: <spec-name>
        fr_ledgers:
          - fr_id: FR-N
            claims: [...]
        contradictions: [...]

    Returns the parsed dict on success. Returns ``None`` if the file is
    absent, unreadable, unparseable, or not a dict at top level.
    """
    if not ledger_path.exists():
        return None
    try:
        text = ledger_path.read_text(encoding="utf-8")
    except OSError as e:
        log.debug("consistency-ledger: could not read %s: %s", ledger_path, e)
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        log.warning("consistency-ledger: unparseable YAML in %s: %s", ledger_path, e)
        return None
    if not isinstance(data, dict):
        log.warning(
            "consistency-ledger: top-level not a mapping in %s (got %s)",
            ledger_path, type(data).__name__,
        )
        return None
    return data


def ledger_has_claims(ledger: dict | None) -> bool:
    """Return True if the ledger has at least one FR-ledger with at least
    one claim.

    Defends the "validator emits ``consistency=PASS`` but never produced
    the ledger" theater failure mode. An empty or absent ledger paired
    with a PASS status is itself a validator-side error.
    """
    if not isinstance(ledger, dict):
        return False
    fr_ledgers = ledger.get("fr_ledgers")
    if not isinstance(fr_ledgers, list) or not fr_ledgers:
        return False
    for fr_ledger in fr_ledgers:
        if not isinstance(fr_ledger, dict):
            continue
        claims = fr_ledger.get("claims")
        if isinstance(claims, list) and claims:
            return True
    return False


def extract_contradictions(ledger: dict | None) -> list[dict]:
    """Return the ``contradictions`` list from the ledger, or ``[]``.

    Each entry SHOULD have ``claim_a``, ``claim_b``, ``type``,
    ``type_name``, ``discriminator``. Entries are returned as-is — the
    formatter below tolerates missing keys but logs them. Entries with
    a ``type`` outside ``{T1..T5}`` are dropped (the typology forbids
    open-vocabulary contradictions; an out-of-vocab type is a
    malformed validator output).
    """
    if not isinstance(ledger, dict):
        return []
    raw = ledger.get("contradictions")
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        type_value = str(entry.get("type", "")).upper()
        if type_value not in _VALID_TYPES:
            log.warning(
                "consistency-ledger: dropping contradiction with out-of-vocab type %r",
                entry.get("type"),
            )
            continue
        out.append(entry)
    return out


def _build_anchor_index(ledger: dict) -> dict[str, str]:
    """Pre-compute claim_id → anchor lookup once per ledger.

    Avoids the O(N²) walk when formatting M contradictions over an
    N-claim ledger. Missing or empty anchors are stored as empty
    strings; the formatter handles fallback.
    """
    index: dict[str, str] = {}
    fr_ledgers = ledger.get("fr_ledgers")
    if not isinstance(fr_ledgers, list):
        return index
    for fr_ledger in fr_ledgers:
        if not isinstance(fr_ledger, dict):
            continue
        claims = fr_ledger.get("claims")
        if not isinstance(claims, list):
            continue
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            claim_id = claim.get("id")
            if not claim_id:
                continue
            anchor = claim.get("anchor") or ""
            index[str(claim_id)] = str(anchor)
    return index


def _format_claim_ref(claim_id: str, anchor_index: dict[str, str]) -> str:
    anchor = anchor_index.get(claim_id, "")
    if anchor:
        return f"{claim_id} ({anchor})"
    return claim_id


def format_consistency_failure(ledger: dict, contradictions: list[dict]) -> str:
    """Build the human-readable error message for a consistency=FAIL gate.

    Names each offending claim pair with its anchors and typology slot.
    Used by the orchestrator's DarkFactoryError message — the user reads
    this to know exactly which spec lines disagree.

    The literal string ``consistency=FAIL`` is included so operators
    grepping logs for the status field find this failure.
    """
    anchor_index = _build_anchor_index(ledger)
    lines = [
        "spec validation failed: consistency=FAIL — cross-section "
        "contradiction(s) detected:",
    ]
    for entry in contradictions:
        a = _format_claim_ref(str(entry.get("claim_a", "?")), anchor_index)
        b = _format_claim_ref(str(entry.get("claim_b", "?")), anchor_index)
        type_value = entry.get("type", "?")
        type_name = entry.get("type_name", "?")
        discriminator = entry.get("discriminator", "?")
        lines.append(
            f"  - {a} vs {b}: {type_value} {type_name} — discriminator: {discriminator}"
        )
    lines.append(
        "Resolve each by editing the spec so the conflicting clauses agree, "
        "or by removing the redundant clause. The validator's "
        "consistency-ledger.yaml is the byte-addressable record of the "
        "claims it extracted."
    )
    return "\n".join(lines)
