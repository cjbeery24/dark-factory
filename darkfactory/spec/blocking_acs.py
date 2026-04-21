"""Parse author-declared blocking ACs from a spec.

The "Non-Negotiable Acceptance Criteria" section is the authoritative
source for which AC / EC / FR / NFR IDs the spec author requires to
pass before SHIP, regardless of stable-failure-ratio promotion. See
``darkfactory.pipeline.iteration.should_ship_with_gaps`` for the veto
clause that consumes ``SpecRun.ac_blocking``.

Section shape (authoritative)::

    ## Non-Negotiable Acceptance Criteria

    - **AC-4** — RLS fail-closed when guc is unset. Tenant-isolation invariant.
    - **EC-3** — Null tenant_id rejected at middleware.

Empty-section shape (justification required)::

    ## Non-Negotiable Acceptance Criteria

    Justification: this spec is a pure refactor with no runtime-exposed
    invariants beyond existing coverage.

The parser is deliberately permissive on markup variance (bold, no bold,
em-dash vs hyphen) because spec authors will hand-write these. The
precheck rule (``check_non_negotiable_section``) validates structure;
this module only extracts IDs.
"""
from __future__ import annotations

import re

from darkfactory.spec.spec_model import Section, SpecModel

# Accept AC / EC / FR / NFR / SEC / INV / ERR prefixes — same family the
# pipeline already attributes via _EMBEDDED_TAG_RE and _SECTION_PREFIX_RE
# in iteration.py. Keep this list in sync with those.
_BLOCKING_ID_PREFIXES = "FR|EC|NFR|ERR|AC|SC|INT|SEC|INV|UC|BR|DR|TR|PR"
_BLOCKING_ID_RE = re.compile(rf"\b((?:{_BLOCKING_ID_PREFIXES})-\d+)\b", re.IGNORECASE)

_SECTION_ALIASES = {
    "non-negotiable acceptance criteria",
    "non negotiable acceptance criteria",
    "blocking acceptance criteria",
}


def _find_section(model: SpecModel) -> Section | None:
    for section in model.sections:
        name = section.heading.text.strip().lower()
        # Strip any leading numeric/letter prefix like "1a." or "1."
        stripped = re.sub(r"^\s*\d+[a-z]?\.\s*", "", name)
        if stripped in _SECTION_ALIASES or name in _SECTION_ALIASES:
            return section
    return None


def parse_blocking_acs(model: SpecModel) -> dict[str, bool]:
    """Extract the ``{ac_id: True}`` mapping from the Non-Negotiable section.

    Returns an empty dict if the section is missing or empty. Absence of
    the section is a precheck error (handled by
    ``check_non_negotiable_section``) but is *not* an error here — this
    function is the runtime populator and returning ``{}`` correctly
    leaves ship policy unchanged.

    Every ID extracted maps to ``True``. An ID listed under
    Non-Negotiable is, by construction, blocking. IDs NOT listed are
    absent from the dict and treated as non-blocking by
    ``should_ship_with_gaps`` — there's no need to encode ``False``.
    """
    section = _find_section(model)
    if section is None:
        return {}

    found: dict[str, bool] = {}
    for paragraph in section.paragraphs:
        # Justification paragraph is plain prose — still scan it so a
        # sloppy "we chose not to block AC-4" doesn't silently count.
        # The precheck rule catches the structural shape; the scan here
        # is intentionally promiscuous.
        for match in _BLOCKING_ID_RE.finditer(paragraph.text):
            found[match.group(1).upper()] = True
    # Bullet lists in markdown-it are not currently captured by
    # ``Section`` (no ``list_items`` attribute), so lists render as one
    # paragraph per bullet line. The paragraph scan above picks them up.
    # If spec_model later adds list_items, extend here.
    return found
