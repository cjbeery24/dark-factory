"""pnpm workspace script consistency checker.

Verifies that every workspace member package.json defines the scripts
that the quality gate invokes recursively.

Required scripts are derived from the pipeline config's gate commands
(lint_cmd, typecheck_cmd, test_cmd) rather than being hardcoded, so the
check matches what the pipeline actually runs.

This module is called by the orchestrator before the iteration loop starts,
so misconfigured workspaces are caught before consuming any iteration budget.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.setup.workspace_yaml import parse_workspace_yaml_patterns


_DEFAULT_REQUIRED_SCRIPTS: frozenset[str] = frozenset({"test", "lint", "typecheck"})


def derive_required_scripts(
    lint_cmd: str = "",
    typecheck_cmd: str = "",
    test_cmd: str = "",
) -> frozenset[str]:
    """Extract pnpm script names from gate commands.

    Parses commands like ``pnpm -r lint``, ``pnpm run typecheck``,
    ``pnpm -r --filter ... typecheck`` to find the script name that
    workspaces need to define.  Falls back to *_DEFAULT_REQUIRED_SCRIPTS*
    if no pnpm commands are detected.
    """
    scripts: set[str] = set()
    # Match: pnpm [flags...] <script>  or  pnpm run [flags...] <script>
    pnpm_script_re = re.compile(
        r"pnpm\s+(?:(?:-\w+|--\S+)\s+)*(?:run\s+)?(\w[\w-]*)\s*",
    )
    for cmd in (lint_cmd, typecheck_cmd, test_cmd):
        if not cmd:
            continue
        m = pnpm_script_re.search(cmd)
        if m:
            name = m.group(1)
            # Skip pnpm subcommands that aren't script names
            if name not in {"install", "exec", "dlx", "add", "remove", "ls"}:
                scripts.add(name)
    return frozenset(scripts) if scripts else _DEFAULT_REQUIRED_SCRIPTS


@dataclass
class WorkspaceCheckResult:
    errors: list[tuple[str, list[str]]] = field(default_factory=list)  # (rel_path, missing_scripts)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors


def check_workspace_scripts(
    project_dir: Path,
    required_scripts: frozenset[str] | None = None,
) -> WorkspaceCheckResult:
    """Check all pnpm workspace members define required scripts.

    *required_scripts* defaults to ``_DEFAULT_REQUIRED_SCRIPTS`` when
    ``None``.  Use :func:`derive_required_scripts` to compute from config.

    Returns immediately with empty result if pnpm-workspace.yaml does not exist.
    No language guard — self-guards on file existence.
    """
    scripts = required_scripts if required_scripts is not None else _DEFAULT_REQUIRED_SCRIPTS
    if not scripts:
        return WorkspaceCheckResult()

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
        missing = _check_member(member, scripts, result)
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


def _check_member(
    member_dir: Path,
    required: frozenset[str],
    result: WorkspaceCheckResult,
) -> frozenset[str]:
    """Return the set of required scripts missing from a workspace member's package.json."""
    pkg_json_path = member_dir / "package.json"
    try:
        pkg = json.loads(pkg_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Treat unreadable/invalid package.json as missing all scripts.
        result.warnings.append(f"could not read {pkg_json_path}: {exc}")
        return required

    defined: set[str] = set((pkg.get("scripts") or {}).keys())
    return required - defined
