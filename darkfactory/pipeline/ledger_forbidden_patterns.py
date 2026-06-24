"""Derive deterministic ``forbidden_patterns`` from the validator's ledger.

The spec-validator's cross-section pass emits a consistency ledger
(``.dark-factory/reviews/<spec>-consistency-ledger.yaml``) whose claims
are typed tuples ``(subject, predicate, polarity, object, anchor, raw)``
over a closed predicate vocabulary (``agents/spec-validator.md``). One of
those predicates is ``forbids`` — the validator's *structural* extraction
of a spec's MUST-NOT clauses.

This module turns the ``forbids`` claims that name a **code symbol** into
``forbidden_patterns`` entries the existing deterministic pre-gate
(``pipeline/forbidden_patterns.py``) scans against implementer source.

Why this exists (the validator-determinism pilot, 2026-06-23):
    The validator reliably *detects* a spec's MUST-NOT constraints (the
    ``forbids`` claims are emitted ~deterministically) but *elevates* a
    detected defect to a ship-blocker only ~1/3 of the time — it argues
    both sides of severity within one run. A prompt rubric can't fix a
    model that rationalizes around it (the task-#30 flip-flop lesson).
    So we take the *elevation* decision out of the LLM's hands: a
    ``forbids`` claim naming a code call deterministically becomes a
    forbidden pattern. The gate, not the validator's coin-flip, decides
    whether the implementer violated it.

    The motivating case is the ``migrate()`` MUST-NOT (bb F-1c FR-1:
    "the server startup path MUST NOT invoke the ``migrate(...)``
    programmatic helper"). The validator extracts it as a ``forbids``
    claim every run, but elevates it to a blocker non-deterministically.
    This module makes the impl-harm path (an actual ``migrate(`` call in
    source) a deterministic gate failure regardless of the coin-flip.

Design (the non-bespoke / low-false-positive discipline, Neo+Linus-gated):

- **Ride the validator's own structured extraction, not new parsing.**
  We read ``predicate == "forbids"`` claims the validator already typed.
  We do NOT re-derive "is this prose a MUST-NOT of a code symbol" — that
  would be a second flip-flopping judgment in a smaller hat.

- **Code-symbol discriminator: an identifier followed by EMPTY ``()`` or
  ELLIPSIS ``(...)`` args, with no intervening space before the paren.**
  ``migrate()`` / ``migrate(...)`` is the author naming a symbol *qua
  symbol* (the real F-1c ledger bytes — and ``raw`` does NOT preserve
  backticks, so we cannot lean on code-formatting). This deliberately
  does TWO rejections, and the docstring is precise about both because
  conflating them was the original bug (Linus C1):
    1. Prose *parentheticals* — ``requests (status 401)`` — rejected by
       the no-space rule (a space precedes the paren).
    2. Illustrative *calls with real args* embedded in prose — ``MUST NOT
       use cache(key)`` / ``fetch(url)`` / ``process(input)`` — rejected
       by the empty-or-ellipsis rule (real args ⇒ illustrative prose, not
       a symbol reference). Without this, any call-shaped word in a
       behavioral MUST-NOT would wall an implementer who calls a common
       library fn (``fetch(``, ``Date(``) for an unrelated reason.
  KNOWN RESIDUAL: an *empty-arg* call-token in incidental prose (``MUST
  NOT init() during render`` → ``init(``) still emits. This is a much
  smaller surface than any-arg, irreducible without semantic judgment,
  and acceptable for an opt-in fail-open pre-gate. Extraction is stack-
  agnostic: a function call looks the same in every language we target.

- **Feedstock: the claim's ``object`` first, ``raw`` only as fallback.**
  ``object`` is the validator's STRUCTURED extraction of *what is
  forbidden*; ``raw`` is the whole sentence, where incidental prose
  lives. Preferring ``object`` minimises the residual above (Linus Q1).

- **Conservative scope (v1): function-call tokens only.** Import paths
  (``drizzle-orm/migrator``) and shell commands (``drizzle-kit push``)
  are deliberately NOT extracted yet — they have higher false-positive
  surface in prose and need their own discriminator. Tracked as a
  follow-up; this module stays minimal and correct.

- **Opt-in.** The caller gates this behind ``forbidden_patterns_from_
  ledger`` (default off): land tested-and-dormant, validate on the spec
  corpus, then flip — same roll-forward posture as the other gates.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# A code-symbol token: an identifier immediately followed (no space) by
# EMPTY ``()`` or ELLIPSIS ``(...)`` args. Two discriminators in one:
# the no-space rule rejects prose parentheticals (``requests (status)``);
# the empty-or-ellipsis arg rule rejects illustrative calls with real
# args (``cache(key)``, ``fetch(url)``) — those are prose, not a symbol
# reference. Matches the real F-1c ledger bytes ("migrate()", "migrate(...)").
_CALL_TOKEN_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\(\s*(?:\.\.\.\s*)?\)")

# Anchor prefix: a ledger claim's anchor is ``FR-1.processing@line39`` /
# ``AC-7`` / ``EC-3``. The forbidden_patterns gate keys by the bare
# FR-N / AC-N / EC-N anchor id, so we strip everything after the id.
_ANCHOR_PREFIX_RE = re.compile(r"^((?:FR|AC|EC)-\d+)")

# Control-flow / declaration keywords that take a ``(`` but are never the
# subject of a code-symbol MUST-NOT. Without this stoplist a forbids
# claim phrased like "MUST NOT return(...)" would emit a useless pattern.
# Small and language-spanning on purpose; the no-space rule already does
# the heavy lifting, this just trims obvious non-symbols.
_KEYWORD_STOPLIST = frozenset({
    "if", "for", "while", "switch", "catch", "return", "function",
    "with", "do", "else", "case",
})


def derive_forbidden_patterns_from_ledger(
    ledger: dict | None,
) -> dict[str, list[str]]:
    """Return ``{anchor_id: [pattern, ...]}`` from the ledger's forbids claims.

    Walks every ``predicate == "forbids"`` claim, extracts function-call
    tokens (no-space-before-paren) from its ``object`` and ``raw`` fields,
    and emits a ``re:`` pattern matching that call for the claim's anchor.

    Pure and total: returns ``{}`` for a missing/malformed ledger or a
    ledger with no code-symbol forbids claims. Never raises. The result is
    merged (union) with the architect-authored ``forbidden_patterns`` by
    the caller — it never replaces them.

    Patterns are deterministic, deduplicated per anchor, and emitted in
    first-seen order so the output is stable across runs (no set-iteration
    nondeterminism leaking into implementer feedback).
    """
    if not isinstance(ledger, dict):
        return {}
    fr_ledgers = ledger.get("fr_ledgers")
    if not isinstance(fr_ledgers, list):
        return {}

    result: dict[str, list[str]] = {}
    for fr_ledger in fr_ledgers:
        if not isinstance(fr_ledger, dict):
            continue
        claims = fr_ledger.get("claims")
        if not isinstance(claims, list):
            continue
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            if str(claim.get("predicate", "")).strip().lower() != "forbids":
                continue
            anchor_id = _anchor_id(claim.get("anchor"))
            if anchor_id is None:
                continue
            # Feedstock: structured ``object`` first; fall back to the
            # whole-sentence ``raw`` only when ``object`` names no symbol
            # (e.g. F-1c's "drizzle-kit migrate invocation at startup"
            # object yields nothing → raw "...migrate(...)..." does).
            idents = list(_call_identifiers(str(claim.get("object", ""))))
            if not idents:
                idents = list(_call_identifiers(str(claim.get("raw", ""))))
            for ident in idents:
                pattern = rf"re:\b{re.escape(ident)}\s*\("
                bucket = result.setdefault(anchor_id, [])
                if pattern not in bucket:
                    bucket.append(pattern)
    return result


def _anchor_id(anchor: object) -> str | None:
    """Reduce a claim anchor to its bare FR-N / AC-N / EC-N id, or None.

    ``FR-1.processing@line39`` -> ``FR-1``; ``AC-7`` -> ``AC-7``. An
    anchor that does not start with a recognised id (malformed validator
    output) yields None and the claim is skipped — we never key the gate
    on an id the anchor manifest can't cross-check.
    """
    if not anchor:
        return None
    match = _ANCHOR_PREFIX_RE.match(str(anchor).strip())
    return match.group(1) if match else None


def _call_identifiers(text: str):
    """Yield distinct call-token identifiers in ``text`` (first-seen order).

    A call token is an identifier immediately followed by ``(`` (no
    space). Control-flow keywords are dropped. Order-preserving dedupe so
    the caller's pattern list is deterministic.
    """
    seen: set[str] = set()
    for match in _CALL_TOKEN_RE.finditer(text):
        ident = match.group(1)
        if ident in _KEYWORD_STOPLIST:
            continue
        if ident in seen:
            continue
        seen.add(ident)
        yield ident
