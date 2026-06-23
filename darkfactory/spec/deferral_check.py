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
capability?" — is delegated to a NARROW, focused LLM call using a REASON-THEN-
VERDICT prompt (the model reasons per anchor, then emits a ``FLAGGED: [...]``
line). That narrow framing is why it is tractable (the open-ended validator's
flip-flop comes from holistic defect-hunting, NOT from bounded judgments).

MEASUREMENT HONESTY (2026-06-22, corrected). The earlier "16/16 / 8/8 EXACT"
claim used a BARE-LABEL prompt ("output the JSON array, nothing else"), which
has a silent-failure mode: it suppresses reasoning and lets a snap
classification parse clean, INFLATING the apparent rate. Re-measured under
reason-then-verdict + PRE-REGISTERED locked labels on bb spec-4 (pre-rescope),
6 runs PER MODEL on sonnet-4-6 and opus-4-8 SEPARATELY (the rates below are
per-model-per-run — NOT a cross-model agreement claim; PRODUCTION runs ONE
configured spec-validator model, sampled K times, see the K-vote loop, which
passes no model_override):
  • Elicitation: 6/6 per model — NO observed per-run variance on this spec.
  • Deferral: 5/6 per model, ZERO false positives across all runs — every error
    is a RECALL miss on a subtler isolation anchor, never an over-block. To
    recover that recall without amplifying FP, the orchestrator samples the
    deferral check K times (one model) and takes the MAJORITY (``majority_vote``):
    a single-run miss is outvoted back IN, a single-run spurious flag is outvoted
    OUT. (Per-vote model rotation — a genuinely cross-model panel — is a possible
    future hardening; not done, so the panel is single-model variance reduction.)
Both remain N=1 (ONE real spec — spec-4; the A/B/C corpus is Claude-AUTHORED, so
it tests robustness-across-surface, not independent ground truth) — see below.

═══════════════════════════════════════════════════════════════════════════
GATE STATUS — PROVISIONAL (2026-06-22) — applies to BOTH gates equally. Deferral
shows ~1-in-6 per-run recall wobble (majority-of-K is the stabilizer); elicitation
showed none on this spec — but "no observed variance" is NOT "validated reliable":
both rest on N=1, so neither gate is exempt from this caveat. CRITICAL ASYMMETRY:
the coverage-floor F/E2/E3 check backstops FALSE NEGATIVES (a missed deferred-
outcome anchor is re-caught at E3), but NOTHING in the system backstops a FALSE
POSITIVE. The ONLY false-positive detector is an AUTHOR DISPUTING an
`@untestable:forward-reference` this gate imposed — so an author who COMPLIES with
a wrong annotation produces a SILENT, uncounted false positive. FP=0 is therefore
measured on N=1 with no backstop; the provisional posture is doing real work.
RE-EVALUATE on EITHER of these signals:
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


def _default_verify_aliases(substrate_block: str) -> dict[str, str]:
    """Map every alias a model might use to refer to a DEFAULT-VERIFY primitive
    back to its ``[[type]]`` header. A primitive is declared as ``[[type]]`` but
    may ALSO carry a ``name = "..."`` field; a model often flags the primitive by
    that ``name`` field rather than the bracketed type (observed: sonnet emitted
    the ``name`` ``percentage_bucketing`` for ``[[gradual_rollout]]``). Without
    this map the parser would drop that as a non-candidate → a FALSE NEGATIVE
    that is really just a label-form mismatch. Maps the type→type and each
    entry's ``name``→type, so ``parse_elicitation_output`` canonicalizes either
    form to the ``[[type]]``."""
    aliases: dict[str, str] = {}
    for primitive, entries in _parse_substrate_toml(substrate_block).items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("verifiable") is not False:
                aliases[primitive] = primitive
                name = entry.get("name")
                if isinstance(name, str) and name.strip():
                    aliases[name.strip()] = primitive
    return aliases


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
    """The NARROW, focused prompt — wide-set, REASON-THEN-VERDICT variant.

    Output format is reason-then-verdict (reason per relevant anchor, then a
    single ``FLAGGED: [...]`` line), NOT a bare JSON array. A bare-label prompt
    has a documented silent-failure mode: it suppresses the model's reasoning
    and lets a snap misclassification parse clean, which inflated the earlier
    "16/16-class" measurement (it hid a per-run recall wobble). Under
    reason-then-verdict + PRE-REGISTERED labels, the honest per-run rate on bb
    spec-4 (pre-rescope) is 10/12 EXACT (opus 5/6, sonnet 5/6) with ZERO false
    positives across all 12 runs — every error is a recall miss on a subtler
    isolation anchor, never an over-block. The orchestrator runs this K times
    and takes the MAJORITY (see ``majority_vote``) to recover that recall
    without amplifying FP. Stack-agnostic: it names no ecosystem concepts beyond
    what the spec text itself contains. The two discriminators that gave FP=0:
    (1) mechanism-vs-outcome, and (2) the 'concerns a DIFFERENT capability than
    the deferred one' exclusion."""
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

TASK. For each candidate anchor that plausibly relates to a deferred primitive, \
REASON in one sentence: does passing it REQUIRE the enforcement of a DEFERRED \
primitive (an OUTCOME of that capability), or does it assert only an in-scope \
mechanism / a different capability? Then end with EXACTLY one final line listing \
the IDs that assert a deferred OUTCOME — the line must be exactly this form:
FLAGGED: ["AC-1", "EC-2"]
If none qualify, end with `FLAGGED: []`. Anchors asserting an in-scope mechanism \
or a different capability must NOT appear on the FLAGGED line.
"""


_ANCHOR_RE = re.compile(r"^(?:AC|EC)-\d+$")
_FLAGGED_LINE_RE = re.compile(r"FLAGGED:\s*(\[.*?\])", re.DOTALL)


def _extract_verdict_list(text: str) -> list:
    """Extract the JSON list from a reason-then-verdict output. PREFERS the
    final ``FLAGGED: [...]`` line (the verdict), so any JSON-looking array the
    model wrote WHILE REASONING is ignored.

    The fallback to the LAST bracketed array fires ONLY when there is no
    ``FLAGGED:`` token at all (a model that dropped the prefix but still ended
    with the verdict array). If a ``FLAGGED:`` token IS present but the array is
    unparseable (truncated ``FLAGGED: [AC-1``, or bracket-less ``FLAGGED: AC-1``),
    we return ``[]`` rather than guessing — falling back to the reasoning
    scratchpad there would re-introduce exactly the verdict-contamination this
    format was built to kill (Linus C1). ``[]`` on no parse."""
    m = _FLAGGED_LINE_RE.search(text)
    if m:
        raw_arrays = [m.group(1)]
    elif "FLAGGED:" in text:
        return []  # verdict line present but unparseable — do NOT guess
    else:
        raw_arrays = re.findall(r"\[.*?\]", text, re.DOTALL)
    for chunk in reversed(raw_arrays):  # last array = the verdict, not reasoning
        try:
            parsed = json.loads(chunk)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, list):
            return parsed
    return []


def parse_deferral_check_output(text: str) -> set[str]:
    """Parse the focused LLM call's reason-then-verdict output into a canonical
    anchor set, reading the ``FLAGGED: [...]`` verdict line. Tolerant of
    surrounding reasoning prose; ignores non-canonical entries."""
    out: set[str] = set()
    for item in _extract_verdict_list(text):
        if isinstance(item, str):
            cand = item.strip().upper()
            if _ANCHOR_RE.match(cand):
                out.add(cand)
    return out


# How many SUCCESSFUL focused-check votes the orchestrator collects before taking
# the majority. 3 is the smallest ODD K where a single-run recall miss (observed
# ~1-in-6) is outvoted back IN by the other two, and a single-run spurious flag
# is outvoted OUT — at 3× one cheap validate-time LLM call. Should land ODD: an
# even panel makes majority_vote's >half threshold demand UNANIMITY, which drops
# a single-run miss. The orchestrator refills failed votes to hold the panel at
# this odd K AS LONG AS failures stay under K; under heavy failure the bounded
# refill can still settle on a smaller (possibly even) panel deciding under
# unanimity, with recall then leaning on the coverage-floor backstop (the loop
# logs that degraded case). The elicitation
# gate is NOT ensembled (no observed variance on the one measured spec, N=1);
# bump it the same way if a wobble ever appears on a real spec.
DEFERRAL_VOTE_K = 3


def majority_vote(votes: list[set[str]]) -> set[str]:
    """Combine K focused-check verdicts by MAJORITY (an item is kept iff it
    appears in STRICTLY MORE THAN HALF the votes). Chosen over union because the
    per-run errors are recall MISSES with zero false positives: majority
    recovers a single-run miss (the other runs outvote it) WHILE suppressing a
    single-run spurious flag (it is outvoted) — protecting the "never over-block
    a legitimate anchor" direction the gate's PROVISIONAL posture requires.
    Empty ``votes`` → empty set (fail-open, same as a failed focused check)."""
    if not votes:
        return set()
    threshold = len(votes) / 2
    tally: dict[str, int] = {}
    for v in votes:
        for item in v:
            tally[item] = tally.get(item, 0) + 1
    return {item for item, n in tally.items() if n > threshold}


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
    alias_to_type: dict[str, str]    # name-field / type → canonical [[type]]


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
        alias_to_type=_default_verify_aliases(substrate_block),
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
    """The narrow elicitation prompt — REASON-THEN-VERDICT variant (reason per
    candidate primitive, then a single ``FLAGGED: [...]`` line), NOT a bare JSON
    array (same silent-failure rationale as the deferral prompt). Under this
    format + PRE-REGISTERED labels, measured 6/6 PER MODEL (opus, sonnet
    separately) on bb spec-4 — no observed per-run variance, which is NOT the
    same as validated reliability (still N=1; see the module PROVISIONAL block).
    Not ensembled for that reason — bump it to majority-of-K like the deferral
    gate if a real spec ever wobbles. Stack-agnostic: it reasons in-scope
    (delivered in the FRs) vs deferred (Out-of-Scope), naming no ecosystem
    concepts beyond the spec's own text."""
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

TASK. For EACH candidate primitive, REASON in 1-2 sentences: is its enabling \
infrastructure delivered this phase (appears in the Functional Requirements) or \
deferred (Out-of-Scope)? Then end with EXACTLY one final line listing the \
identifiers of the primitives whose infrastructure is DEFERRED (and which \
therefore must NOT be gate-verified this phase). Use the bracketed `[[…]]` \
header identifier (e.g. `tenant_isolation`), one of the CANDIDATE identifiers \
listed above — the line must be exactly this form:
FLAGGED: ["tenant_isolation"]
If none qualify, end with `FLAGGED: []`. Primitives whose infrastructure is \
delivered this phase must NOT appear on the FLAGGED line.
"""


def parse_elicitation_output(
    text: str, candidates: list[str], aliases: dict[str, str] | None = None
) -> set[str]:
    """Parse the elicitation reason-then-verdict output into the set of flagged
    candidate primitive TYPES, reading the ``FLAGGED: [...]`` verdict line.
    Tolerant of reasoning prose; only returns actual candidate types (guards
    against the LLM inventing/echoing a non-candidate).

    ``aliases`` (``ElicitationInputs.alias_to_type``) canonicalizes a flagged
    item to its ``[[type]]`` — so a model that names a primitive by its ``name``
    field (e.g. ``percentage_bucketing`` for ``[[gradual_rollout]]``) is scored
    correctly, not dropped as a false negative. Falls back to the bare candidate
    set when no alias map is supplied (back-compat)."""
    amap = aliases or {c: c for c in candidates}
    cand_set = set(candidates)
    out: set[str] = set()
    for item in _extract_verdict_list(text):
        if not isinstance(item, str):
            continue
        canon = amap.get(item.strip())
        if canon in cand_set:
            out.add(canon)
    return out
