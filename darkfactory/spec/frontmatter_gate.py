"""Phase 4.6: validate-time gate for spec frontmatter + spec-type contracts.

Single source of truth for the structural checks `dark-factory validate`
applies *before* the LLM validator runs. The orchestrator's own checks
(``parse_spec_type`` at run start) remain the authoritative gate at run
time; this module exists so the CLI can fail fast with a clear message
without paying for an LLM round trip on a spec the pipeline would reject
anyway.

Returns ``(ok, message)``. ``ok=True`` means the spec passes the gate
and ``message`` is empty. ``ok=False`` means the gate failed and
``message`` is a single user-facing sentence.
"""

from __future__ import annotations

import re
from pathlib import Path

from darkfactory.config.profiles import parse_spec_type
from darkfactory.errors import ConfigError
from darkfactory.spec.spec_model import SpecModelParseError, parse_spec_model

# Heading-text matcher: a section qualifies as a Reproduction section
# if its heading text (already stripped of `#` markers by the AST
# parser) starts with "reproduc" or matches "steps to reproduce".
# Bugfix authors phrase this header a dozen different ways; a stricter
# literal would push them to fight the gate. We walk the parsed AST
# rather than regex over raw text so a `## Reproduction` line inside a
# fenced code block does not produce a false credit.
_REPRODUCTION_HEADING_RE = re.compile(
    r"^(?:steps\s+to\s+)?reproduc", re.IGNORECASE,
)


def _has_reproduction_section(text: str) -> bool:
    try:
        model = parse_spec_model(text)
    except SpecModelParseError:
        # If the spec doesn't even parse, fail closed: the LLM validator
        # will produce a far better error than we can.
        return False
    for section in model.iter_sections():
        title = (section.heading.text or "").strip()
        if _REPRODUCTION_HEADING_RE.search(title):
            return True
    return False


def check_spec_frontmatter_gate(spec_file: Path) -> tuple[bool, str]:
    """Apply Phase 4.6 validate-time structural gates to a spec file."""
    if not spec_file.exists():
        return False, f"spec file not found: {spec_file}"

    try:
        spec_type = parse_spec_type(spec_file)
    except ConfigError as exc:
        return False, str(exc)

    if spec_type is None:
        return False, (
            "missing required `spec_type` in dark-factory-metadata. "
            "Allowed: bootstrap, feature, bugfix, refactor. Run "
            "`dark-factory specs migrate-frontmatter --apply` to backfill."
        )

    if spec_type == "bugfix":
        try:
            text = spec_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return False, f"could not read spec for Reproduction check: {exc}"
        if not _has_reproduction_section(text):
            return False, (
                "bugfix specs must include a `## Reproduction` section "
                "with steps and observed symptom. The pipeline's coverage "
                "gate requires an @regression scenario, which is drawn "
                "from this section."
            )

    return True, ""
