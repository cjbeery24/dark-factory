"""Family-coverage check for §8a Non-Negotiable Acceptance Criteria.

**Problem.** Run 4736bbd9 (2026-04-22 budget-brain-v2 / database-foundation)
shipped via SHIP_WITH_GAPS while a scenario anchored to ``EC-4`` was in the
stable-failure set. EC-4 and AC-12 describe the *same* invariant — the
fail-closed behavior of an unset ``app.tenant_id`` GUC — but only AC-12 was
listed under §8a "Non-Negotiable Acceptance Criteria". The ``ac_blocking``
veto in ``should_ship_with_gaps`` looks up IDs verbatim; EC-4 wasn't in the
blocking map, so the veto didn't fire.

The author's intent — "an unset-GUC scenario must pass before SHIP" — was
documented twice (once as AC-12, once as EC-4), but only one of the two
statements carries the SHIP-veto property. The pipeline cannot infer that
EC-4 and AC-12 are the same invariant from the string-equality check alone.

**This rule.** For each §6 Edge Case row that is *not* already listed in §8a,
we count how many distinctive backtick-quoted code tokens it shares
verbatim with any §8 AC that *is* listed in §8a. Shared tokens above
threshold emit a ``BlockingAcFamilyFinding`` — FATAL — that names the EC
and the overlapping blocking AC. The spec author must resolve the
coverage gap one of two ways:

1. **Add the EC-N to §8a.** Correct when the author genuinely intended
   the invariant to block SHIP (the canonical EC-4/AC-12 case).
2. **Rewrite the EC so it doesn't duplicate the AC's observable
   behavior.** Correct when the EC is describing a *narrative* edge
   case that doesn't actually re-state the blocking invariant.

**Why FATAL.** Neo's round-2 review of the 4736bbd9 post-mortem identified
this as a "silent veto escape": a deterministic structural defect where
the shipping criteria look correct but the veto coverage has a hole.
FATAL forces the author to make the blocking decision explicit before any
agent can be launched against the spec.

**Heuristic — backtick-code-token overlap.**

Backtick inline code is the most discriminating signal in spec language.
Two specs describing the same invariant will use the same backtick-
quoted identifiers, SQL fragments, status codes, and error-code
constants. Two specs describing *different* invariants will not.

Two thresholds:

* ``>= 2`` shared backtick tokens — covers the common case where an EC
  re-uses multiple identifiers from a blocking AC.
* ``>= 1`` shared backtick token of length ``>= 20`` — covers the case
  of a single very-specific fragment (e.g. a full SQL expression like
  ``current_setting('app.tenant_id', true)``) that alone is enough to
  establish the overlap.

Plain English overlap is deliberately *not* scored: "fail-closed",
"zero rows", "tenant isolation" are all common vocabulary; scoring them
would produce false positives on ECs that happen to use the same
ambient jargon. Backtick-code is the discriminator.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from darkfactory.spec.blocking_acs import parse_blocking_acs
from darkfactory.spec.spec_model import Section, SpecModel

# Backtick inline code: `...` or ``...`` (double for embedded backticks).
# Greedy up to first closing backtick of same count — we use two separate
# patterns rather than a single backreferenced one for regex portability.
_INLINE_CODE_DOUBLE_RE = re.compile(r"``([^`]+?)``")
_INLINE_CODE_SINGLE_RE = re.compile(r"`([^`]+?)`")

# AC / EC row ID at the head of a table row.
_AC_ID_RE = re.compile(r"\bAC-(\d+)\b", re.IGNORECASE)
_EC_ID_RE = re.compile(r"\bEC-(\d+)\b", re.IGNORECASE)

# Thresholds — see module docstring for rationale.
_MIN_SHARED_TOKENS = 2
_MIN_LONG_TOKEN_LEN = 20

# Section-name aliases — match the precheck's §8 / §6 detection so we
# stay consistent with how the rest of the validator locates these
# sections.
_AC_SECTION_NAMES = {
    "acceptance criteria",
    "behavioral invariants",  # refactor template
}
_EC_SECTION_NAMES = {
    "edge cases",
}


@dataclass
class BlockingAcFamilyFinding:
    """A §6 Edge Case verbatim-overlaps a §8a blocking AC.

    ``ec_id`` names the non-blocking EC; ``blocking_ac_id`` names the
    blocking AC whose behavior it re-states; ``shared_tokens`` lists the
    backtick-code fragments that overlap (capped at 6 for the error
    message). Consumed by ``run_precheck`` which surfaces it in the
    summary and sets ``fatal=True`` when non-empty.
    """
    ec_id: str
    blocking_ac_id: str
    shared_tokens: list[str]
    ec_line_number: int
    message: str


def _extract_inline_code_tokens(text: str) -> set[str]:
    """Return the set of distinctive backtick-code fragments in ``text``.

    Deduplicated, case-sensitive, whitespace-normalized. Short tokens
    (length < 3) are discarded — a single-character backtick like `` `x` ``
    is not discriminating enough to count toward overlap.
    """
    tokens: set[str] = set()
    # Double-backtick first so its content isn't re-captured by the
    # single-backtick pattern.
    doubles = list(_INLINE_CODE_DOUBLE_RE.finditer(text))
    scrubbed = text
    for m in reversed(doubles):
        tok = m.group(1).strip()
        if len(tok) >= 3:
            tokens.add(tok)
        scrubbed = scrubbed[: m.start()] + " " * (m.end() - m.start()) + scrubbed[m.end():]
    for m in _INLINE_CODE_SINGLE_RE.finditer(scrubbed):
        tok = m.group(1).strip()
        if len(tok) >= 3:
            tokens.add(tok)
    return tokens


def _find_section_by_names(model: SpecModel, names: set[str]) -> Section | None:
    for section in model.sections:
        heading = section.heading.text.strip().lower()
        stripped = re.sub(r"^\s*\d+[a-z]?\.\s*", "", heading)
        if stripped in names or heading in names:
            return section
    return None


def _extract_rows_with_raw(
    model: SpecModel,
    section_names: set[str],
    id_re: re.Pattern[str],
    prefix: str,
    raw_lines: list[str],
) -> dict[str, tuple[int, str]]:
    """Return ``{ID: (line_number, raw_row_text)}`` for a table section.

    ``TableRow.cells`` is post-inline-flatten text — markdown-it strips
    backticks during tokenization, so ``cell`` text can never yield
    backtick-code tokens. We therefore index back into ``raw_lines``
    using the row's 1-based line number and extract tokens from the
    original source. This preserves backtick fidelity end-to-end at the
    cost of one extra dependency (the caller must pass ``raw_lines``).
    """
    section = _find_section_by_names(model, section_names)
    if section is None:
        return {}

    rows: dict[str, tuple[int, str]] = {}
    for table in section.tables:
        for row in table.rows:
            if not row.cells:
                continue
            first = row.cells[0].strip()
            m = id_re.search(first)
            if not m:
                continue
            row_id = f"{prefix}-{m.group(1)}".upper()
            idx = row.line_number - 1
            if idx < 0 or idx >= len(raw_lines):
                continue
            rows[row_id] = (row.line_number, raw_lines[idx])
    return rows


def _qualifies_as_overlap(shared: set[str]) -> bool:
    if len(shared) >= _MIN_SHARED_TOKENS:
        return True
    for tok in shared:
        if len(tok) >= _MIN_LONG_TOKEN_LEN:
            return True
    return False


def check_blocking_ac_family(
    model: SpecModel, *, raw_text: str,
) -> list[BlockingAcFamilyFinding]:
    """Detect §6 ECs that verbatim-overlap §8a blocking ACs.

    ``raw_text`` is the unparsed spec source. We index into its lines
    by ``TableRow.line_number`` to extract backtick-code tokens —
    ``TableRow.cells`` contains post-inline-flatten text with backticks
    stripped, so it can't be the overlap source.

    Silent no-op (returns ``[]``) when:

    * §8a declares no blocking ACs (empty dict from ``parse_blocking_acs``).
    * §8 AC table is missing or has no rows.
    * §6 EC table is missing or has no rows.
    * No EC shares enough backtick-code tokens with any blocking AC.

    Non-empty return fails the precheck hard and sets ``fatal=True``.
    """
    blocking_ids = parse_blocking_acs(model)
    if not blocking_ids:
        return []

    raw_lines = raw_text.split("\n")
    ac_rows = _extract_rows_with_raw(
        model, _AC_SECTION_NAMES, _AC_ID_RE, "AC", raw_lines,
    )
    ec_rows = _extract_rows_with_raw(
        model, _EC_SECTION_NAMES, _EC_ID_RE, "EC", raw_lines,
    )
    if not ac_rows or not ec_rows:
        return []

    # Pre-compute tokens for each blocking AC that actually has a row in
    # §8. (Blocking IDs that don't resolve are already caught by the
    # UNRESOLVED_ID finding in check_non_negotiable_section.)
    blocking_ac_tokens: dict[str, set[str]] = {}
    for ac_id in blocking_ids:
        if not ac_id.startswith("AC-"):
            continue
        if ac_id not in ac_rows:
            continue
        _, body = ac_rows[ac_id]
        blocking_ac_tokens[ac_id] = _extract_inline_code_tokens(body)

    findings: list[BlockingAcFamilyFinding] = []
    for ec_id, (ec_line, ec_body) in ec_rows.items():
        # Skip ECs the author already declared blocking.
        if ec_id in blocking_ids:
            continue

        ec_tokens = _extract_inline_code_tokens(ec_body)
        if not ec_tokens:
            continue

        # Pair the EC against each blocking AC; emit the *best* overlap
        # (most shared tokens) rather than one finding per AC — one
        # finding per EC is what the author needs to act on.
        best_ac: str = ""
        best_shared: set[str] = set()
        for ac_id, ac_tokens in blocking_ac_tokens.items():
            shared = ec_tokens & ac_tokens
            if _qualifies_as_overlap(shared) and len(shared) > len(best_shared):
                best_ac = ac_id
                best_shared = shared

        if best_ac:
            shared_list = sorted(best_shared, key=lambda t: (-len(t), t))[:6]
            rendered = ", ".join(f"`{t}`" for t in shared_list)
            findings.append(BlockingAcFamilyFinding(
                ec_id=ec_id,
                blocking_ac_id=best_ac,
                shared_tokens=shared_list,
                ec_line_number=ec_line,
                message=(
                    f"{ec_id} (line {ec_line}) appears to re-state the "
                    f"invariant defined by {best_ac}, which is listed under "
                    f"§8a Non-Negotiable Acceptance Criteria. Shared "
                    f"distinctive tokens: {rendered}. The SHIP_WITH_GAPS "
                    f"veto in the orchestrator keys on IDs verbatim, so a "
                    f"failing scenario anchored to {ec_id} will not trigger "
                    f"the {best_ac} veto. Either (a) add {ec_id} to §8a, "
                    f"or (b) rewrite {ec_id} so it does not duplicate "
                    f"{best_ac}'s observable behavior."
                ),
            ))

    return findings
