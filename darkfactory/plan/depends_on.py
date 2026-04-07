"""Phase 6.4 — depends_on soft check for standalone `dark-factory run`.

When a user runs a single spec outside of `dark-factory plan`, the
orchestrator has no plan context to enforce ordering. This module reads
the spec's ``depends-on:`` metadata and checks each declared dependency
against ``.dark-factory/plan/shipped.jsonl``. Missing dependencies are
returned as a list — the CLI layer decides whether to warn, require
``--force``, or abort.

This is deliberately a *soft* check: the spec's depends-on field is
meant for plan-runner ordering. Running a spec standalone with unmet
dependencies is sometimes legitimate (recovery, debugging, spec on a
fresh branch). The check's purpose is to catch the common mistake of
running specs out of order, not to gate every execution.
"""

from __future__ import annotations

import re
from pathlib import Path

from darkfactory.plan.state import load_shipped

# Matches the bracketed list form the plan parser also accepts
# (darkfactory/plan/parser.py _DEPENDS_ON_RE). We deliberately stay in
# sync with the parser rather than broadening — if a spec's depends-on
# line doesn't parse here, it also doesn't parse when the plan runner
# topologically sorts, so silent divergence is impossible. If the canon
# ever broadens (e.g. bare scalar YAML), update both sites together.
_DEPENDS_ON_RE = re.compile(r'^depends-on:\s*\[([^\]]*)\]')
# Feature-ID form (F-1, F-12) vs spec-name form. Standalone runs only know
# spec names, so any ID-form deps are simply unresolvable here.
_FEATURE_ID_RE = re.compile(r'^[A-Z]-\d+[a-z]?$')


def parse_depends_on(spec_file: Path) -> list[str]:
    """Extract the depends-on list from a spec file's metadata block.

    Returns the raw list of declared deps (may be empty). Handles both
    plain-line and HTML-comment-wrapped metadata blocks by scanning
    every line for the pattern.
    """
    for line in spec_file.read_text().splitlines():
        m = _DEPENDS_ON_RE.match(line.strip())
        if m:
            raw = m.group(1).strip()
            if not raw:
                return []
            return [d.strip() for d in raw.split(",") if d.strip()]
    return []


def missing_dependencies(
    spec_file: Path, project_dir: Path
) -> tuple[list[str], list[str]]:
    """Return ``(missing_deps, unresolvable_ids)`` for a standalone run.

    - ``missing_deps``: spec-name deps that are not present in shipped.jsonl
    - ``unresolvable_ids``: feature-ID-form deps (F-1 etc.) that can't be
      resolved without a plan context; caller should treat these as a
      yellow flag, not a hard miss.
    """
    declared = parse_depends_on(spec_file)
    if not declared:
        return ([], [])

    shipped = {r.spec_id for r in load_shipped(project_dir, strict=False)}
    missing: list[str] = []
    unresolvable: list[str] = []
    for dep in declared:
        if _FEATURE_ID_RE.match(dep):
            unresolvable.append(dep)
            continue
        if dep not in shipped:
            missing.append(dep)
    return (missing, unresolvable)
