"""Shared parser for pnpm-workspace.yaml package glob patterns.

Imported by both darkfactory.docker.mounts (for overlay enumeration)
and darkfactory.setup.workspace_check (for script validation).
"""

from __future__ import annotations

import re


def parse_workspace_yaml_patterns(yaml_text: str) -> list[str]:
    """Parse the packages list from pnpm-workspace.yaml.

    Only handles the simple structure pnpm uses::

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
