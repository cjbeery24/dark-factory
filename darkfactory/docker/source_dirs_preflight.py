"""Phase 4.5 — refuse-to-run pre-flight for the source_dirs barrier.

Background: the framework default for ``layout.source_dirs`` is ``["src"]``.
On a project that does not actually use ``src/`` (e.g. a pnpm monorepo with
code in ``apps/`` and ``packages/``), the Scenario Architect's container
masks an empty ``/project/src`` while the real implementation directories
remain readable inside the container. The whole point of the framework — a
hard filesystem barrier between scenario generation and source code — is
silently non-functional.

This module is the hard stop. It refuses to launch the run when the
effective ``source_dirs`` is suspect, with an actionable error message.
Pure: no I/O beyond ``Path.exists`` / ``iterdir`` and no mutation.
"""

from __future__ import annotations

from pathlib import Path

from darkfactory.errors import DarkFactoryError
from darkfactory.layout.detect import (
    detect_candidate_source_dirs,
    detect_monorepo_markers,
)

_FRAMEWORK_DEFAULT: tuple[str, ...] = ("src",)


def _looks_like_default(source_dirs: list[str]) -> bool:
    return tuple(source_dirs) == _FRAMEWORK_DEFAULT


def check_source_dirs_preflight(
    project_dir: Path,
    source_dirs: list[str],
) -> None:
    """Refuse to run if ``source_dirs`` is suspect. Raises on failure.

    Two failure modes:

    1. ``source_dirs`` is the framework default ``["src"]`` AND the
       project root contains ``apps/`` / ``packages/`` / ``lib/`` / ``cmd/``.
       Almost certainly a monorepo with un-set ``layout.source_dirs``.

    2. ``source_dirs`` references a path that does not exist under the
       project root. An empty or missing masked dir is a leak — the
       container masks ``/project/<missing>`` (a no-op) while the real
       source remains visible elsewhere.
    """
    # 0. Greenfield bypass. If source_dirs is the framework default AND
    #    the project has neither a ``src/`` dir nor any monorepo markers,
    #    there is nothing to mask yet — this is a truly blank bootstrap
    #    run and the Architect will populate ``layout.source_dirs`` during
    #    the run, before any Scenario Architect container is started.
    #    Without this bypass, campaign-style resets and first-time greenfield
    #    users can't launch a bootstrap spec because the effective
    #    ``source_dirs`` falls back to ``["src"]`` (PipelineConfig default),
    #    which then fails check 2 below.
    if _looks_like_default(source_dirs):
        if (
            not (project_dir / "src").exists()
            and not detect_monorepo_markers(project_dir)
        ):
            return

    # 1. Default-on-monorepo trap.
    if _looks_like_default(source_dirs):
        markers = detect_monorepo_markers(project_dir)
        if markers:
            suggestions = detect_candidate_source_dirs(project_dir)
            hint = (
                f"\n\nDetected likely source directories: "
                f"{suggestions}.\nSet `layout.source_dirs = "
                f"{suggestions!r}` in your bootstrap spec's "
                f"environment delta."
                if suggestions
                else (
                    "\n\nSet `layout.source_dirs` explicitly in your "
                    "bootstrap spec's environment delta."
                )
            )
            raise DarkFactoryError(
                "source_dirs barrier pre-flight FAILED: "
                f"`layout.source_dirs` is the framework default "
                f"`['src']` but the project root contains monorepo "
                f"markers {markers}, which strongly suggests source "
                f"lives elsewhere. The Scenario Architect container "
                f"would mask an empty/non-existent `src/` while real "
                f"source code remained readable — silent barrier leak."
                + hint
            )

    # 2. Any source_dir that does not exist on disk is a leak.
    missing: list[str] = []
    for entry in source_dirs:
        candidate = project_dir / entry
        if not candidate.exists():
            missing.append(entry)
    if missing:
        raise DarkFactoryError(
            "source_dirs barrier pre-flight FAILED: "
            f"`layout.source_dirs` references paths that do not exist "
            f"under the project root: {missing}. A missing masked "
            f"directory is a barrier leak — the container would mask "
            f"a non-existent path (a no-op) while real source remained "
            f"readable. Fix `layout.source_dirs` in your environment "
            f"delta."
        )
