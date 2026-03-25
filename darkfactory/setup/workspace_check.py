"""pnpm workspace script consistency checker.

Verifies that every workspace member package.json defines the scripts
that the quality gate invokes recursively (test, lint, type-check).

This module is called by the orchestrator before the iteration loop starts,
so misconfigured workspaces are caught before consuming any iteration budget.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.setup.workspace_yaml import parse_workspace_yaml_patterns


_REQUIRED_SCRIPTS: frozenset[str] = frozenset({"test", "lint", "type-check"})


@dataclass
class WorkspaceCheckResult:
    errors: list[tuple[str, list[str]]] = field(default_factory=list)  # (rel_path, missing_scripts)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors


def check_workspace_scripts(project_dir: Path) -> WorkspaceCheckResult:
    """Check all pnpm workspace members define required scripts (test, lint, type-check).

    Returns immediately with empty result if pnpm-workspace.yaml does not exist.
    No language guard — self-guards on file existence.
    """
    result = WorkspaceCheckResult()

    workspace_yaml = project_dir / "pnpm-workspace.yaml"
    if not workspace_yaml.exists():
        return result

    try:
        yaml_text = workspace_yaml.read_text(encoding="utf-8")
    except OSError as exc:
        result.warnings.append(f"could not read pnpm-workspace.yaml: {exc}")
        return result

    patterns = parse_workspace_yaml_patterns(yaml_text)
    if not patterns:
        return result

    members = _resolve_members(project_dir, patterns)
    if not members:
        return result

    for member in members:
        missing = _check_member(member, result)
        if missing:
            rel = member.relative_to(project_dir)
            result.errors.append((str(rel), sorted(missing)))

    return result



def _resolve_members(project_dir: Path, patterns: list[str]) -> list[Path]:
    """Resolve workspace glob patterns to a list of package directories.

    Handles '!' exclusion patterns. Returns sorted list of resolved directories.
    """
    includes: list[str] = []
    excludes: list[str] = []
    for p in patterns:
        if p.startswith("!"):
            excludes.append(p[1:])
        else:
            includes.append(p)

    included: set[Path] = set()
    for pattern in includes:
        for match in project_dir.glob(pattern):
            if match.is_dir() and (match / "package.json").exists():
                included.add(match.resolve())

    excluded: set[Path] = set()
    for pattern in excludes:
        for match in project_dir.glob(pattern):
            if match.is_dir():
                excluded.add(match.resolve())

    return sorted(included - excluded)


def _check_member(member_dir: Path, result: WorkspaceCheckResult) -> frozenset[str]:
    """Return the set of required scripts missing from a workspace member's package.json."""
    pkg_json_path = member_dir / "package.json"
    try:
        pkg = json.loads(pkg_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Treat unreadable/invalid package.json as missing all scripts.
        result.warnings.append(f"could not read {pkg_json_path}: {exc}")
        return _REQUIRED_SCRIPTS

    defined: set[str] = set((pkg.get("scripts") or {}).keys())
    return _REQUIRED_SCRIPTS - defined
