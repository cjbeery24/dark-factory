"""Validator-time substrate-deferral GATE (stack-agnostic, LLM-judged).

A spec may declare substrate primitives marked ``verifiable = false`` — a
capability whose enforcement is DEFERRED to a later build phase. If a Blocking
(Non-Negotiable) acceptance criterion asserts the runtime OUTCOME of such a
deferred capability, that AC is unsatisfiable at this phase: its enforcement
does not exist yet, so its test cannot pass and it must not block ship. (This is
distinct from a Blocking AC that asserts only an in-scope MECHANISM — input
validation, error-throwing, a signature — which IS satisfiable now.)

This module surfaces that contradiction at VALIDATION time (cheap, pre-pipeline)
instead of only at the coverage floor (expensive, post-architect+TE). The
DETERMINISTIC, STACK-AGNOSTIC inputs are extracted here — the ``verifiable=false``
primitives (a TOML field in ``## Substrate Primitives``, any stack) and the WIDE
candidate set (every AC/EC anchor not already ``@untestable``/``vehicle``
annotated). The single irreducibly SEMANTIC judgment — "does this anchor assert
the deferred capability's OUTCOME, or only an in-scope mechanism / a different
capability?" — is delegated to a NARROW, focused LLM call. That narrow framing
is why it is reliable (the open-ended validator's flip-flop comes from holistic
defect-hunting, NOT from bounded judgments). Measured on bb spec-4: the WIDE
candidate set scored 8/8 EXACT per model (K=8 × sonnet-4-6 + opus-4-8) with ZERO
false positives across an adversarial 35-anchor must-not-flag surface (incl.
induced-failure/resilience ACs); the earlier focused §8a-only set scored 16/16.
Both are N=1 (one spec) — see the PROVISIONAL block below.

═══════════════════════════════════════════════════════════════════════════
GATE STATUS — PROVISIONAL (2026-06-22). Wide-set FP measured = 0 on spec-4 (K=8
× sonnet-4-6 + opus-4-8, adversarial resilience ACs included, perfectly
consistent), but that is N=1. RE-EVALUATE on EITHER of these signals:
  • a spec gets stuck ILLEGITIMATELY (a false positive blocking a spec whose
    anchor is actually an in-scope mechanism / a different capability); OR
  • an author DISPUTES an `@untestable:forward-reference` this gate caused —
    this is the one failure mode the floor does NOT backstop: a wide-set FP
    silently under-covers an anchor that should have been tested (the floor
    only backstops MISSES, i.e. false negatives, by re-catching them at E3).
If either appears, demote from a hard gate to a REVISE-class advisory, or add an
author-override marker. The coverage-floor F/E2/E3 check
(``coverage.check_substrate_deferral_consistency``) remains the DETERMINISTIC
backstop for the false-NEGATIVE direction, so demoting never loses a catch — it
only moves it later (to a 2-3h post-architect+TE run).
═══════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


def deferral_report_path(reviews_dir: Path, spec_name: str) -> Path:
    """Canonical location of the deferral-consistency findings report — written
    by BOTH the validate-time gate and the coverage-floor check, consumed by the
    ``/df-rescope-deferred`` skill. Single source so the two producers and the
    one consumer never drift."""
    return reviews_dir / f"{spec_name}-deferral-consistency.json"


def write_deferral_consistency_report(
    reviews_dir: Path,
    spec_name: str,
    run_id: str,
    findings: list[dict],
) -> Path:
    """Persist deferral-consistency findings so ``/df-rescope-deferred`` can
    apply the re-scope without hand-editing the spec. ``findings`` is a list of
    ``{kind, primitive, anchor, message}`` dicts (the floor builds them from
    ``DeferralConsistencyFinding``; the validate gate builds them from the
    flagged anchor set). Returns the written path."""
    path = deferral_report_path(reviews_dir, spec_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"spec": spec_name, "run_id": run_id, "findings": findings}, indent=2
        ),
        encoding="utf-8",
    )
    return path


@dataclass(frozen=True)
class DeferralCheckInputs:
    """Deterministic, stack-agnostic inputs for the focused LLM judgment."""

    substrate_block: str           # the verbatim ## Substrate Primitives TOML region
    deferred_primitive_names: list[str]  # primitives with verifiable=false
    candidate_anchors: list[str]   # ALL non-@untestable/vehicle AC/EC ids (LLM input)
    blocking_anchors: list[str]    # Non-Negotiable subset (drives the drop-§8a action)
    anchor_text: str               # the candidate AC/EC rows (WHEN/THEN per anchor)


_AC_EC_ROW_RE = re.compile(r"^\|\s*(?:AC|EC)-\d+\s*\|")


def _section(text: str, start_pat: str, stop_pat: str = r"^## ") -> str:
    """Return the lines of the first section whose heading matches start_pat,
    up to the next heading matching stop_pat."""
    out: list[str] = []
    cap = False
    for ln in text.split("\n"):
        if cap and re.match(stop_pat, ln) and not re.search(start_pat, ln):
            break
        if re.search(start_pat, ln):
            cap = True
        if cap:
            out.append(ln)
    return "\n".join(out)


def _parse_substrate_toml(substrate_block: str) -> dict:
    """Parse the fenced TOML body of a ``## Substrate Primitives`` block
    (tolerating no lang tag); ``{}`` on any parse failure."""
    m = re.search(r"```[a-zA-Z]*\n(.*?)```", substrate_block, re.DOTALL)
    body = m.group(1) if m else substrate_block
    try:
        data = tomllib.loads(body)
    except tomllib.TOMLDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _deferred_primitive_names(substrate_block: str) -> list[str]:
    """Labels of primitives with ``verifiable = false`` (boolean identity, not
    truthiness — a string sentinel must not route through here, same lesson as
    the adapter's ``force`` key)."""
    names: list[str] = []
    for primitive, entries in _parse_substrate_toml(substrate_block).items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("verifiable") is False:
                label = entry.get("name") or entry.get("tenant_table") or primitive
                names.append(f"{primitive}:{label}")
    return names


def _default_verify_primitive_types(substrate_block: str) -> list[str]:
    """Primitive TYPES (the ``[[name]]``) with ≥1 entry that DEFAULTS TO VERIFY
    (``verifiable`` absent or true — i.e. NOT explicitly ``verifiable=false``).
    These are the elicitation candidates: the gate WILL try to verify them, so
    their enabling infrastructure must exist this phase. Order-preserving,
    de-duplicated."""
    types: list[str] = []
    for primitive, entries in _parse_substrate_toml(substrate_block).items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("verifiable") is not False:
                if primitive not in types:
                    types.append(primitive)
                break
    return types


# Annotation FORMS, not bare nouns (Linus): match `@untestable`, a `vehicle:`
# annotation, or a bare `| subprocess |` cell — but NOT a criterion that merely
# mentions "subprocess"/"vehicle" as prose (that prose-collision would wrongly
# exclude — and silently no-op the gate on — a real deferred-outcome anchor).
_ANNOTATED_CELL_RE = re.compile(
    r"@untestable|vehicle\s*:|\|\s*subprocess\s*\|", re.IGNORECASE
)


def extract_deferral_check_inputs(
    spec_text: str, blocking_anchors: set[str]
) -> DeferralCheckInputs | None:
    """Extract the deterministic precondition for the deferral gate.

    Returns ``None`` (skip the gate — no contradiction is possible) UNLESS the
    spec declares ≥1 ``verifiable=false`` substrate primitive AND ≥1 candidate
    anchor.

    The candidate set (the LLM's input) is the WIDE set: every AC/EC row whose
    3rd table column is NOT already ``@untestable`` / ``vehicle:subprocess``
    annotated (those are already excluded from scenario generation, so they
    can't get an unsatisfiable scenario). This is deliberately broader than the
    §8a Blocking set — measured wide-set FP = 0 on spec-4 (it caught a
    non-blocking deferred-outcome EC the blocking-only scope missed, with zero
    spurious flags even on adversarial resilience ACs). ``blocking_anchors`` (the
    §8a Non-Negotiable set) is retained so the caller knows which flagged anchors
    are ALSO ship-blockers (→ drop from §8a) vs merely scenario-unsatisfiable
    (→ @untestable only).
    """
    substrate_block = _section(spec_text, r"## Substrate Primitives")
    if not substrate_block:
        return None
    deferred = _deferred_primitive_names(substrate_block)
    if not deferred:
        return None
    cand_rows: list[str] = []
    candidates: list[str] = []
    for ln in spec_text.split("\n"):
        if not _AC_EC_ROW_RE.match(ln):
            continue
        # Exclude already-annotated rows by scanning the WHOLE row, not a fixed
        # column index (Linus C1/C2): the annotation lives in the AC table's
        # Blocking column but INLINE in EC-table prose (ECs have no such
        # column), and AC tables may carry extra columns — so a positional
        # cells[3] check is wrong for both. The markers match annotation FORMS,
        # not bare prose nouns (see _ANNOTATED_CELL_RE), so a stray match is
        # rare AND fails SAFE: excluding a row only defers it. A wrongly-dropped
        # anchor is re-caught by the floor F/E2/E3 ONLY to the degree the
        # architect listed it in `covers` (E3 = covers ∩ live; the floor fails
        # CLOSED on architect omission) — i.e. the same net that already
        # existed, not a new unconditional backstop.
        if _ANNOTATED_CELL_RE.search(ln):
            continue
        candidates.append(ln.split("|")[1].strip())
        cand_rows.append(ln)
    if not candidates:
        return None
    return DeferralCheckInputs(
        substrate_block=substrate_block,
        deferred_primitive_names=deferred,
        candidate_anchors=candidates,
        blocking_anchors=sorted(blocking_anchors),
        anchor_text="\n".join(cand_rows),
    )


def build_deferral_check_prompt(inputs: DeferralCheckInputs) -> str:
    """The NARROW, focused prompt — wide-set variant, measured 8/8 EXACT +
    zero false-positives on spec-4 (sonnet + opus), including correct rejection
    of adversarial induced-failure/resilience ACs. Stack-agnostic: it names no
    ecosystem concepts beyond what the spec text itself contains. The two
    discriminators that gave FP=0: (1) mechanism-vs-outcome, and (2) the
    'concerns a DIFFERENT capability than the deferred one' exclusion."""
    return f"""\
You are auditing a software specification for one specific internal-consistency \
defect. Answer ONLY about this defect.

BACKGROUND. A spec declares "substrate primitives" (capabilities the running \
system provides). A primitive marked `verifiable = false` is DEFERRED: its \
enforcement is scoped to a LATER build phase and does not exist yet at this \
phase. Acceptance criteria (AC) / edge cases (EC) are the candidate anchors below.

THE DEFECT. An anchor that asserts the runtime OUTCOME of a `verifiable = false` \
(deferred) capability is unsatisfiable at this phase — the enforcement that \
would make it pass does not exist yet. This is DIFFERENT from an anchor that \
asserts only an IN-SCOPE MECHANISM that exists now (input validation, throwing \
on bad/absent input, a signature, a type contract), OR that concerns a \
DIFFERENT capability than the deferred one(s) (e.g. connection health, pooling, \
logging, config shape, migrations, identifier validation). Judge each anchor by \
its WHEN/THEN: does passing it REQUIRE the enforcement of one of the DEFERRED \
primitives below (an OUTCOME of THAT capability), or not?

DEFERRED SUBSTRATE PRIMITIVES (verifiable = false) declared by this spec:
{inputs.substrate_block}

CANDIDATE anchors (full WHEN/THEN text):
{inputs.anchor_text}

TASK. Return ONLY a JSON array of the IDs of candidate anchors that assert the \
OUTCOME of one of the DEFERRED (verifiable=false) primitives above. Anchors that \
assert an in-scope mechanism, OR that concern a different capability, must NOT \
be included. If none, return []. Output the JSON array and nothing else, e.g. \
["AC-1","EC-2"].
"""


_ANCHOR_RE = re.compile(r"^(?:AC|EC)-\d+$")


def parse_deferral_check_output(text: str) -> set[str]:
    """Parse the focused LLM call's JSON-array output into a canonical anchor
    set. Tolerant of surrounding prose; ignores non-canonical entries."""
    m = re.search(r"\[.*?\]", text, re.DOTALL)
    if not m:
        return set()
    try:
        raw = json.loads(m.group(0))
    except (ValueError, TypeError):
        return set()
    out: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            cand = item.strip().upper()
            if _ANCHOR_RE.match(cand):
                out.add(cand)
    return out


# ---------------------------------------------------------------------------
# UPSTREAM ELICITATION (the root: make the framework ASK for verifiable=false)
# ---------------------------------------------------------------------------
# A substrate primitive that DEFAULTS TO VERIFY (no explicit verifiable=false)
# will be gate-verified — which only makes sense if its enabling infrastructure
# is delivered THIS build phase. If the spec's own Functional Requirements /
# Out-of-Scope make clear the infra is DEFERRED, the primitive must be marked
# verifiable=false (or its infra brought in scope), else the gate fails 2-3h
# late as "table missing". This elicits that need at VALIDATE time, deterministic
# inputs + one narrow LLM judgment — measured 16/16 EXACT (recall 16/16, FP 0/16,
# sonnet-4-6 + opus-4-8) on bb spec-4-with-verifiable-stripped. Same PROVISIONAL
# posture + same false-negative backstop (the late substrate gate) as the
# deferral gate below — see the GATE STATUS block at the top of this module.


@dataclass(frozen=True)
class ElicitationInputs:
    substrate_block: str            # full ## Substrate Primitives TOML (context)
    candidate_primitives: list[str]  # primitive types that default-verify
    functional_requirements: str     # what this phase delivers (in-scope)
    out_of_scope: str                # what is deferred to later phases


def extract_elicitation_inputs(spec_text: str) -> ElicitationInputs | None:
    """Deterministic precondition for the elicitation check. Returns ``None``
    (skip) unless the spec declares ≥1 substrate primitive that DEFAULTS TO
    VERIFY (an elicitation candidate). The LLM then judges each candidate
    against the FRs (in-scope) and Out-of-Scope (deferred)."""
    substrate_block = _section(spec_text, r"## Substrate Primitives")
    if not substrate_block:
        return None
    candidates = _default_verify_primitive_types(substrate_block)
    if not candidates:
        return None
    return ElicitationInputs(
        substrate_block=substrate_block,
        candidate_primitives=candidates,
        # FR pattern must NOT match "## N. Non-Functional Requirements" (Linus
        # C1) — else `_section` self-suppresses its break on the NFR heading and
        # leaks NFR (performance/observability prose) into the in-scope input,
        # which can flip a deferred capability to "looks in-scope" (false neg).
        # `(?:\d+\.\s*)?Functional` only matches an optional "N. " before
        # "Functional", so "Non-Functional" (which has "Non-" before it) is
        # rejected.
        functional_requirements=_section(
            spec_text, r"## (?:\d+\.\s*)?Functional Requirements"
        )[:6000],
        out_of_scope=_section(
            spec_text, r"## .*(Out of Scope|Dependencies)"
        )[:6000],
    )


def build_elicitation_prompt(inputs: ElicitationInputs) -> str:
    """The narrow elicitation prompt (measured 16/16 EXACT). Stack-agnostic:
    it reasons in-scope (delivered in the FRs) vs deferred (Out-of-Scope), naming
    no ecosystem concepts beyond the spec's own text."""
    cand = ", ".join(inputs.candidate_primitives)
    return f"""\
You are auditing a software specification at one specific point: substrate \
primitive verifiability vs build-phase scope. Answer ONLY about this.

BACKGROUND. A spec declares "substrate primitives" — capabilities the framework \
will GATE-VERIFY at this build phase (it checks the running system actually \
provides them). Verification only makes sense if the primitive's ENABLING \
INFRASTRUCTURE is delivered THIS phase. The Functional Requirements describe \
what this phase delivers (in-scope); the Out-of-Scope / Dependencies section \
describes what is DEFERRED to later phases.

THE DEFECT. A primitive that will be gate-verified but whose enabling \
infrastructure the spec DEFERS to a later phase is a contradiction: the gate \
will try to verify a capability whose machinery does not exist yet, and fail \
2-3 hours into a run. Such a primitive must be marked deferred (so the gate \
skips it this phase) OR have its infrastructure brought into scope. A primitive \
whose infrastructure IS delivered this phase (appears in the Functional \
Requirements) is correctly gate-verified — do NOT flag it.

CANDIDATE primitives (these default to gate-verified; judge each): {cand}

SUBSTRATE PRIMITIVES (full declaration):
{inputs.substrate_block}

FUNCTIONAL REQUIREMENTS (what this phase delivers — in-scope):
{inputs.functional_requirements}

OUT OF SCOPE / DEPENDENCIES (what is deferred to later phases):
{inputs.out_of_scope}

TASK. Return ONLY a JSON array of the candidate primitive identifiers (the \
`[[name]]` types, e.g. "tenant_isolation") whose enabling infrastructure is \
DEFERRED to a later phase and therefore must NOT be gate-verified this phase. \
Primitives whose infrastructure is delivered this phase must NOT be included. \
If none, return []. Output the JSON array and nothing else.
"""


def parse_elicitation_output(text: str, candidates: list[str]) -> set[str]:
    """Parse the elicitation LLM output into the set of flagged candidate
    primitive types. Tolerant of prose; only returns names that are actual
    candidates (guards against the LLM inventing/echoing a non-candidate)."""
    m = re.search(r"\[.*?\]", text, re.DOTALL)
    if not m:
        return set()
    try:
        raw = json.loads(m.group(0))
    except (ValueError, TypeError):
        return set()
    cand_set = set(candidates)
    return {
        item.strip() for item in raw
        if isinstance(item, str) and item.strip() in cand_set
    }
