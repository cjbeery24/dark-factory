"""Parse structured artifacts from spec-validator agent output.

The validator agent emits free-form markdown plus a required machine-readable
``unresolved_contracts:`` YAML block. This module extracts that block and
audits the Free Variables table for misclassification (a "free variable" that
actually describes a serialized cross-boundary surface is not free — it is
an unresolved contract).
"""

from __future__ import annotations

import logging
import re

import yaml

from darkfactory.spec.shape_tokens import SHAPE_TOKENS

log = logging.getLogger(__name__)


# A fenced YAML block: ```yaml ... ``` (or ~~~yaml ... ~~~).
_YAML_FENCE_RE = re.compile(
    r"```(?:yaml|yml)\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)

# Word-boundary regex over SHAPE_TOKENS for the Free Variables audit.
_SHAPE_TOKEN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in SHAPE_TOKENS) + r")\b",
    re.IGNORECASE,
)


def parse_unresolved_contracts(validator_output: str) -> list[dict] | None:
    """Extract the ``unresolved_contracts:`` list from validator output.

    Returns:
        - ``[]`` if the validator emitted ``unresolved_contracts: []``.
        - A list of dicts (each with ``surface`` / ``section`` /
          ``sketches_diverged_on`` keys) if the validator emitted entries.
        - ``None`` if no parseable ``unresolved_contracts:`` YAML block is
          present. The orchestrator treats ``None`` as "missing block —
          re-prompt validator once".

    Tolerates markdown noise around the block. Searches all fenced YAML
    blocks in the output and returns the LAST one that contains an
    ``unresolved_contracts`` key (validators tend to put the structured
    block at the end).
    """
    if not validator_output:
        return None

    matches = list(_YAML_FENCE_RE.finditer(validator_output))
    last_parsed: list[dict] | None = None

    for m in matches:
        body = m.group(1)
        try:
            data = yaml.safe_load(body)
        except yaml.YAMLError as e:
            log.debug("skipping unparseable YAML block: %s", e)
            continue
        if not isinstance(data, dict):
            continue
        if "unresolved_contracts" not in data:
            continue
        value = data["unresolved_contracts"]
        if value is None:
            last_parsed = []
            continue
        if not isinstance(value, list):
            log.warning(
                "unresolved_contracts is not a list (got %s); ignoring",
                type(value).__name__,
            )
            continue
        # Coerce non-dict entries (e.g., bare strings) to dicts so callers
        # can rely on a stable shape.
        normalised: list[dict] = []
        for entry in value:
            if isinstance(entry, dict):
                normalised.append(entry)
            else:
                normalised.append({"surface": str(entry)})
        last_parsed = normalised

    return last_parsed


# Sentinel strings that satisfy the schema but carry no information.
# Lowercased for case-insensitive comparison; whitespace stripped before
# matching.
_VACUOUS_REASON_SENTINELS: frozenset[str] = frozenset({
    "", "?", "??", "???",
    "todo", "tbd", "n/a", "na", "none", "null",
    "unknown", "unclear", "ambiguous", "divergent",
    "...", "…",
})


def is_vacuous_divergence_reason(
    entry: dict,
    *,
    min_token_count: int = 4,
) -> bool:
    """Return True if the entry's ``sketches_diverged_on`` is content-free.

    A reason is vacuous if any of the following hold:

    1. The ``sketches_diverged_on`` key is missing entirely.
    2. The value is None or not a string.
    3. The whitespace-stripped, lowercased value is in the sentinel set.
    4. The value tokenizes to fewer than ``min_token_count`` whitespace-
       separated tokens.
    5. The value is a verbatim, case-insensitive echo of the entry's
       ``surface`` or ``section`` field (after whitespace normalization).
    """
    raw = entry.get("sketches_diverged_on")
    if raw is None:
        return True
    if not isinstance(raw, str):
        return True
    stripped = raw.strip()
    if stripped.lower() in _VACUOUS_REASON_SENTINELS:
        return True
    tokens = stripped.split()
    if len(tokens) < min_token_count:
        return True
    norm = " ".join(stripped.lower().split())
    for field in ("surface", "section"):
        other = entry.get(field)
        if isinstance(other, str) and " ".join(other.lower().split()) == norm:
            return True
    return False


def audit_free_variables(validator_output: str) -> list[str]:
    """Return Free Variable rows whose text contains a SHAPE_TOKENS token.

    A free variable that mentions a serialized cross-boundary surface is
    misclassified — it is not free, it is an unresolved contract. The
    orchestrator treats any hits as a contract violation.

    Returns the offending row strings (without the surrounding markdown
    table syntax) for printing in the failure summary. Empty list if no
    Free Variables section is present or no rows misclassify.
    """
    if not validator_output:
        return []

    lines = validator_output.splitlines()
    in_section = False
    section_level = 0
    offenders: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            heading = stripped.lstrip("#").strip().lower()
            if "free variable" in heading:
                in_section = True
                section_level = level
                continue
            if in_section and level <= section_level:
                # Same-or-shallower heading ends the section; deeper
                # sub-headings (e.g. "### Internal helpers") do not.
                in_section = False
                continue

        if not in_section:
            continue

        # Inside the section: only inspect markdown table data rows
        # (lines starting with "|" that are not the header separator).
        if not stripped.startswith("|"):
            continue
        if set(stripped.replace("|", "").replace("-", "").replace(":", "").strip()) == set():
            continue  # separator row like |---|---|

        if _SHAPE_TOKEN_RE.search(stripped):
            offenders.append(stripped)

    return offenders
