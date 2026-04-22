"""Per-run artifact cleanup invariant.

Purges stale per-iter artifacts from a prior run of the same spec
before the current run's first agent container launches. Without this
step the implementer of iter-N+1 (or the escalation agent) can read
feedback/oracle/gate files left behind by an older run of the same
spec — the failure mode diagnosed in the 2026-04-22 re-run of
``2026-03-28-database-foundation`` (run ``d7b0c5b5``), where iter-3's
implementer burned ~$126 of budget against an 11-hour-older
"operator intervention required" feedback file from a different run.

Lossless by construction
------------------------
The pipeline's own ``snapshot_iteration`` — called at the top of each
iteration loop (``iteration.py:1973``) and at every terminal handler
(ship at ``orchestrator.py:4565``, escalate at ``4808``, abort at
``4856``) — plus ``snapshot_final`` preserve per-iter artifacts under
``archive/<prior-ts>-<spec>-<short-id>/``. When this cleanup runs at
the next run's start, everything being purged is already captured in
that archive. The cleanup reads only from the live working tree; it
does NOT touch ``archive/``.

Scope
-----
Spec-scoped globs only. No blanket wipe. The purge list is every
per-iter and per-run output the pipeline writes under
``.dark-factory/reviews/`` and ``.dark-factory/reports/`` that the
plan identifies as feedback-loop inputs for the *next* iteration or
escalation agent. Inputs (specs, plans, architecture, agent defs,
config, project-hints, scenarios) are NOT touched.

The pre-flight spec-validation inputs (``<spec>-validation.md`` and
``<spec>-validation.status``) are explicitly preserved — they are
read by ``dark-factory run`` itself before the in-pipeline
spec-validator overwrites them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)


# Relative to ``.dark-factory/``. Each glob is expanded against the
# live project tree. ``{spec}`` is substituted with the spec name.
# The list below is the full set of pipeline-authored per-spec
# outputs under ``reports/`` and ``reviews/`` — enumerated via
# ``grep -rEn '\.dark-factory/(reports|reviews)/' darkfactory --include='*.py'``
# against the write sites. See review 2026-04-22 Linus §2.1.
_PURGE_GLOBS: tuple[str, ...] = (
    "reviews/{spec}-feedback-iter*.md",
    "reviews/{spec}-oracle-output-iter*.md",
    "reviews/{spec}-review.md",
    "reviews/{spec}-escalation.md",
    "reports/{spec}-defects-iter*.json",
    "reports/{spec}-*-iter*.env",
    "reports/{spec}-substrate-findings.json",
    "reports/{spec}-quality-gate*.md",
    "reports/{spec}-smoke-test*.md",
    "reports/{spec}-pre-gate*.md",
    "reports/{spec}-review-prepass*",
    "reports/{spec}-scenario-validation.md",
    "reports/{spec}-scenario-counts*.env",
    "reports/{spec}-holdouts.md",
    # Added 2026-04-22 after full authored-set audit: pipeline writes
    # these per-run, they survive across runs, contaminating post-mortem
    # and downstream prompts in the same class as the feedback files.
    "reports/{spec}-proposal-rehearsal.md",
    "reports/{spec}-scenarios.md",
    "reports/{spec}-architecture.md",
    "reports/{spec}-evaluation-iter*.md",
)


def _exact_spec_prefix(name: str, spec_name: str) -> bool:
    """Does ``name`` start with ``spec_name`` followed by a hyphen?

    ``Path.glob('{spec}-*')`` treats ``{spec}`` as a literal prefix and
    matches any extension of it before the next hyphen, which means the
    purge for spec ``foo`` also sweeps files belonging to a sibling
    spec ``foo-v2``. Post-filtering on an exact ``<spec>-`` prefix —
    i.e. spec name, then one hyphen separator, then the rest —
    eliminates that leak.

    Every file in ``_PURGE_GLOBS`` starts with ``{spec}-`` (hyphen is
    mandatory in the pattern), so an exact-prefix check is sufficient;
    no pattern produces a filename that is *only* the spec name.
    """
    return name.startswith(spec_name + "-")


def _iter_matches(
    dark_factory_dir: Path, patterns: Iterable[str], spec_name: str,
) -> list[Path]:
    """Expand the glob patterns against the live tree and return matches.

    Only file matches are returned. Matches whose filename extends the
    spec name (``foo-v2-*`` when purging ``foo``) are filtered out —
    glob patterns treat ``{spec}-*`` as a literal prefix, which is too
    permissive for hyphen-separated spec-name families.
    """
    matches: list[Path] = []
    for pattern in patterns:
        concrete = pattern.format(spec=spec_name)
        for hit in dark_factory_dir.glob(concrete):
            if not hit.is_file():
                continue
            if not _exact_spec_prefix(hit.name, spec_name):
                continue
            matches.append(hit)
    return matches


def purge_prior_run_artifacts(
    project_dir: Path,
    spec_name: str,
) -> tuple[int, list[str]]:
    """Remove spec-scoped per-iter artifacts from ``.dark-factory/``.

    Returns ``(count, purged_rel_paths)`` where ``purged_rel_paths`` is
    sorted for stable event emission. Best-effort: ``OSError`` on any
    individual unlink is logged and skipped — a partial sweep is
    strictly better than aborting the whole run.

    ``spec_name`` is expected to have been validated by the caller
    (``run_pipeline`` and the CLI both route through
    ``validate_spec_name``). The belt-and-suspenders check below
    refuses path separators and parent traversals — three-line
    insurance on a destructive filesystem operation is cheap.
    """
    if "/" in spec_name or ".." in spec_name or "\\" in spec_name:
        raise ValueError(
            f"spec_name {spec_name!r} contains path separators — refusing "
            "to expand globs that could escape .dark-factory/"
        )

    dark_factory_dir = project_dir / ".dark-factory"
    if not dark_factory_dir.is_dir():
        return 0, []

    purged: list[str] = []
    for hit in _iter_matches(dark_factory_dir, _PURGE_GLOBS, spec_name):
        try:
            hit.unlink()
        except OSError as exc:
            log.warning(
                "run_start_cleanup: could not remove %s (%s)", hit, exc,
            )
            continue
        # ``hit`` came from ``dark_factory_dir.glob()`` so
        # ``relative_to(dark_factory_dir)`` is always safe.
        purged.append(str(hit.relative_to(dark_factory_dir)))

    purged.sort()
    if purged:
        log.info(
            "run_start_cleanup: purged %d stale artifact(s) for spec %s",
            len(purged), spec_name,
        )
    return len(purged), purged


def run_start_cleanup(
    project_dir: Path,
    spec_name: str,
    *,
    run_id: str,
    events_file: Optional[Path] = None,
) -> tuple[int, list[str]]:
    """Pipeline-layer wrapper: purge + emit.

    Calls ``purge_prior_run_artifacts`` and, when ``events_file`` is
    non-None, emits a single ``RUN_START_CLEANUP`` event. Extracted
    so the orchestrator call site is one line and can be unit-tested
    without standing up the full ``run_pipeline`` harness.
    """
    count, files = purge_prior_run_artifacts(project_dir, spec_name)
    if events_file is not None:
        # Imported lazily to avoid a circular dependency — ``events/types``
        # is a heavy surface that pulls in ``models/events`` and every
        # emitter helper.
        from darkfactory.events.types import emit_run_start_cleanup
        emit_run_start_cleanup(
            events_file,
            spec=spec_name,
            run_id=run_id,
            purged_count=count,
            purged_files=files,
        )
    return count, files
