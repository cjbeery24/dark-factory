"""Selective regression — cross-reference changed files against registry target_files."""

from __future__ import annotations

from pathlib import Path

from darkfactory.state.registry import list_specs, read_target_files


def affected_specs(project_dir: Path, changed_files: set[str]) -> list[str]:
    """Return spec names whose target_files overlap with changed_files."""
    if not changed_files:
        return []

    result = []
    for spec_name in list_specs(project_dir):
        target_files = read_target_files(project_dir, spec_name)
        if target_files & changed_files:
            result.append(spec_name)
    return sorted(result)
