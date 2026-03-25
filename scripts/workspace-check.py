#!/usr/bin/env python3
"""Gate 0: pnpm workspace script consistency check.

Verifies that every workspace member package.json defines the scripts
that the quality gate invokes recursively (test, lint, type-check).

Exits 0 if all members are consistent.
Exits 1 with a structured error message listing missing scripts per package.

Usage: python3 workspace-check.py <project_dir>
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


_REQUIRED_SCRIPTS: frozenset[str] = frozenset({"test", "lint", "type-check"})


def _parse_workspace_yaml(yaml_text: str) -> list[str]:
    """Parse the packages list from pnpm-workspace.yaml.

    Only handles the simple structure pnpm uses:
        packages:
          - 'apps/*'
          - 'packages/*'
          - '!**/test-fixtures/**'

    Returns a list of raw pattern strings (including exclusions prefixed with '!').
    No third-party libraries — minimal hand-rolled parser.
    """
    patterns: list[str] = []
    in_packages = False
    for line in yaml_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"^packages\s*:", stripped):
            in_packages = True
            continue
        if in_packages:
            # A new top-level key signals the end of the packages block.
            if re.match(r"^\S", line) and not stripped.startswith("-"):
                in_packages = False
                continue
            # Match list items: optional leading whitespace, dash, then the value.
            m = re.match(r"^\s*-\s+['\"]?([^'\"#\n]+?)['\"]?\s*(?:#.*)?$", line)
            if m:
                patterns.append(m.group(1).strip())
    return patterns


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


def _check_member(member_dir: Path) -> frozenset[str]:
    """Return the set of required scripts missing from a workspace member's package.json."""
    pkg_json_path = member_dir / "package.json"
    try:
        pkg = json.loads(pkg_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Treat unreadable/invalid package.json as missing all scripts.
        print(f"  WARNING: could not read {pkg_json_path}: {exc}", file=sys.stderr)
        return _REQUIRED_SCRIPTS

    defined: set[str] = set((pkg.get("scripts") or {}).keys())
    return _REQUIRED_SCRIPTS - defined


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: workspace-check.py <project_dir>", file=sys.stderr)
        return 1

    project_dir = Path(sys.argv[1])
    workspace_yaml = project_dir / "pnpm-workspace.yaml"

    if not workspace_yaml.exists():
        # Not a pnpm workspace project — nothing to check.
        return 0

    try:
        yaml_text = workspace_yaml.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: could not read {workspace_yaml}: {exc}", file=sys.stderr)
        return 1

    patterns = _parse_workspace_yaml(yaml_text)
    if not patterns:
        # Empty or unrecognized packages list — skip.
        return 0

    members = _resolve_members(project_dir, patterns)
    if not members:
        return 0

    errors: list[tuple[str, list[str]]] = []
    for member in members:
        missing = _check_member(member)
        if missing:
            rel = member.relative_to(project_dir)
            errors.append((str(rel), sorted(missing)))

    if errors:
        print("WORKSPACE CONSISTENCY ERROR: Missing required scripts")
        for pkg_path, missing_scripts in errors:
            scripts_str = ", ".join(f"'{s}'" for s in missing_scripts)
            print(f"  {pkg_path}: missing {scripts_str}")
        print("Add the missing scripts to each package.json before running the pipeline.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
