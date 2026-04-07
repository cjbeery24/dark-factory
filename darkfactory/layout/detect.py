"""Best-effort project layout detection.

Shared by:

- ``darkfactory.docker.source_dirs_preflight`` — refuses to launch when
  ``layout.source_dirs`` looks wrong.
- ``darkfactory.architecture.backcompat`` — populates ``layout.source_dirs``
  in the synthesized bootstrap delta so monorepos don't trip the preflight
  on their very first run.

Detection here is a *convenience*, never a barrier. The preflight is the
hard stop: if detection under-populates, the preflight's missing-path or
monorepo-marker branches still refuse to launch. Keeping these in one
module guarantees both consumers see the same view of the project.
"""

from __future__ import annotations

from pathlib import Path

# Layout markers that strongly indicate the project does not put its
# source under ``src/``. Each marker here must be a directory whose
# *only* common purpose is holding source code in a non-``src`` layout.
# ``lib/`` is intentionally excluded — many projects use ``src/`` for
# code and ``lib/`` for vendored assets, fonts, native blobs, terraform
# modules, etc., and we do not want to refuse to run on those.
MONOREPO_MARKERS: tuple[str, ...] = ("apps", "packages", "cmd")


def detect_monorepo_markers(project_dir: Path) -> list[str]:
    """Return the subset of ``MONOREPO_MARKERS`` that exist as dirs."""
    found: list[str] = []
    for marker in MONOREPO_MARKERS:
        if (project_dir / marker).is_dir():
            found.append(marker)
    return found


def detect_candidate_source_dirs(project_dir: Path) -> list[str]:
    """Best-effort suggestion of what ``layout.source_dirs`` should be.

    Looks for ``apps/*/src`` and ``packages/*/src`` (the dominant monorepo
    conventions) plus ``cmd/`` (Go). Returns paths relative to the project
    root. Empty list when nothing matches — caller decides whether to
    fall back to the framework default or refuse.
    """
    suggestions: list[str] = []
    for parent in ("apps", "packages"):
        parent_dir = project_dir / parent
        if not parent_dir.is_dir():
            continue
        try:
            children = sorted(p for p in parent_dir.iterdir() if p.is_dir())
        except OSError:
            continue
        for child in children:
            if (child / "src").is_dir():
                suggestions.append(f"{parent}/{child.name}/src")
    if (project_dir / "cmd").is_dir():
        suggestions.append("cmd")
    return suggestions
