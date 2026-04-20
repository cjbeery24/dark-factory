"""Ephemeral per-spec precision hint for the Architect prompt.

The spec validator emits a ``divergence`` rating (LOW/MEDIUM/HIGH) in
``.dark-factory/reviews/<spec>-validation.status``. Today HIGH logs a
warning and MEDIUM passes silently — but MEDIUM is the common driver of
the "visible tests pass, holdouts fail" plateau: two LLMs reading the
spec agree on the public surface yet disagree on a subtle behavior,
and that divergence surfaces when the QA-Holdout probes adversarial
inputs.

This module renders a short advisory block on MEDIUM and writes it to
``.dark-factory/architecture/<spec>-precision-hint.md`` so the architect
prompt composer can append it. The file is spec-scoped and regenerated
per invocation — it does not persist as project policy, and it is not
consumed by any agent other than the Architect.
"""

from __future__ import annotations

import logging
from pathlib import Path


log = logging.getLogger(__name__)


_HINT_TEMPLATE = """\
## PRECISION ADVISORY (validator divergence = MEDIUM)

The spec validator rated this spec MEDIUM divergence. That means two \
LLMs reading the spec independently would likely produce manifests \
that agree on the public surface but disagree on subtle behavior \
(return types, error shapes, sequencing, edge-case handling, or \
precedence). MEDIUM divergence is the common driver of \
visible-tests-pass-but-holdouts-fail failures — the Implementer picks \
one reading, the blind QA probes the other.

Before emitting your manifest:

1. Read `/project/.dark-factory/reviews/{spec_name}-validation.md` and \
find the **Precision Analysis** section. It enumerates the ambiguities \
the validator detected.
2. For each ambiguity, pick exactly one reading and pin it in the \
manifest's §5.5 wire contracts or the ``test_integration`` entry \
points. Do not hedge with unions or "either X or Y" phrasing — the \
Implementer must not be left to choose.
3. If an ambiguity cannot be resolved from the spec alone, prefer the \
**conservative** reading (stricter validation, explicit error shape, \
deterministic ordering) over the permissive one.

Pinning one reading per ambiguity is the cheapest way to eliminate the \
MEDIUM plateau.
"""


def _read_divergence(status_file: Path) -> str | None:
    """Return the ``divergence`` value from a validator status file.

    Returns ``None`` if the file is absent or missing the key. Never
    raises — a missing/unreadable status file is treated as "no hint".
    """
    if not status_file.exists():
        return None
    try:
        text = status_file.read_text(encoding="utf-8")
    except OSError as e:
        log.debug("precision-hint: could not read %s: %s", status_file, e)
        return None
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "divergence":
            return value.strip().upper()
    return None


def compose_precision_hint(project_dir: Path, spec_name: str) -> str:
    """Return the precision-hint block to append to the architect prompt.

    Reads the validator status file. If ``divergence=MEDIUM``, writes the
    rendered hint to ``.dark-factory/architecture/<spec>-precision-hint.md``
    (for auditability) and returns the same text so the caller can append
    it to the prompt. Returns the empty string in every other case
    (status file absent, divergence LOW, divergence HIGH — HIGH is
    already either blocked or logged elsewhere).

    The hint file is a side artifact, not an input to any other agent.
    It is regenerated on every invocation; callers that need to clear
    stale hints (e.g., when divergence downgrades LOW→MEDIUM→LOW across
    runs) can ``unlink(missing_ok=True)`` before calling this function.
    """
    status_file = (
        project_dir / ".dark-factory" / "reviews" / f"{spec_name}-validation.status"
    )
    divergence = _read_divergence(status_file)

    arch_dir = project_dir / ".dark-factory" / "architecture"
    hint_file = arch_dir / f"{spec_name}-precision-hint.md"

    if divergence != "MEDIUM":
        # Clear any stale hint from a prior run at a different divergence.
        hint_file.unlink(missing_ok=True)
        return ""

    rendered = _HINT_TEMPLATE.format(spec_name=spec_name)
    try:
        arch_dir.mkdir(parents=True, exist_ok=True)
        hint_file.write_text(rendered, encoding="utf-8")
    except OSError as e:
        # Auditability is nice-to-have; prompt injection is the primary
        # behavior. Log and return the rendered text anyway.
        log.warning(
            "precision-hint: could not write %s: %s (prompt injection still applied)",
            hint_file,
            e,
        )

    log.info(
        "precision-hint: MEDIUM divergence detected for %s — injecting advisory",
        spec_name,
    )
    return rendered
