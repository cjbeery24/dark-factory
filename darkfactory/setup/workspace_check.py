"""pnpm workspace script consistency checker.

Verifies that every workspace member package.json defines the scripts
that the quality gate invokes recursively.

Required scripts are derived from the pipeline config's gate commands
(lint_cmd, typecheck_cmd, test_cmd) rather than being hardcoded, so the
check matches what the pipeline actually runs.

This module is called by the orchestrator before the iteration loop starts,
so misconfigured workspaces are caught before consuming any iteration budget.

Note on the gate-command parser: :func:`derive_required_scripts` uses a
best-effort regex parser that recognizes ``cd <path> && pnpm ...``,
recursive flags (``-r``, ``--recursive``, ``--filter``), and a handful
of value-taking flags. It is deliberately narrow. Do not extend the
regexes further; the planned long-term direction is a declarative
``gate_members`` config field naming which workspace members each
command applies to, which would eliminate the parser entirely and also
unblock Python/Rust/Go workspace checkers.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.setup.workspace_yaml import parse_workspace_yaml_patterns

log = logging.getLogger(__name__)


# Each required script is (script_name, scoped_to_path_or_None). ``None``
# means "require in every workspace member"; a path string scopes the
# requirement to that one package (relative to project root).
_DEFAULT_REQUIRED_SCRIPTS: frozenset[tuple[str, str | None]] = frozenset(
    (s, None) for s in ("test", "lint", "typecheck")
)


# Matches "cd <path> && <remainder>" so we can detect when a gate command
# is scoped to a single package instead of running across the whole
# workspace. Captures the path and remainder.
_CD_PREFIX_RE = re.compile(r"^\s*cd\s+(\S+)\s*&&\s*(.*)$")

# Flags that explicitly opt in to running across multiple packages,
# overriding any cd-prefix scoping. When these appear, the script is
# treated as unscoped (required in all members). Handles both the
# separated (``--filter foo``) and equals (``--filter=foo``) forms.
_RECURSIVE_FLAG_RE = re.compile(
    r"(?:^|\s)(?:-r|--recursive|--filter)(?:[\s=]|$)",
)

# Scoping forms we don't yet parse. Commands using these are treated as
# unscoped (conservative: require-in-all) and logged once. The
# cd-prefix form is the recommended way to scope a gate command today.
_UNHANDLED_SCOPING_RE = re.compile(r"(?:^|\s)(?:-C|--dir)(?:[\s=]|$)")

# Two-token value-taking flags. Stripped before script-name extraction
# so values like ``@pkg/foo`` or ``apps/api`` don't get captured as
# script names. The equals form (``--filter=@pkg/foo``) is handled by
# the main regex's ``--\S+`` alternative and does not need stripping.
_VALUE_FLAG_STRIP_RE = re.compile(
    r"(?:^|\s)(?:-C|--dir|-F|--filter)\s+\S+",
)

# pnpm subcommands that are never script names. If the parser ever
# captures one of these as a "script," it's a sign the regex was
# confused — drop it rather than propagating garbage.
_PNPM_NON_SCRIPT_SUBCOMMANDS: frozenset[str] = frozenset({
    "install", "i", "add", "remove", "rm", "update", "up",
    "exec", "dlx", "ls", "list", "outdated", "audit", "why",
    "create", "init", "publish", "pack", "prune", "rebuild",
    "store", "link", "unlink", "setup", "env", "config", "bin",
    "root", "help", "run",  # "run" is handled explicitly in pnpm_script_re
})


def _normalize_scope_path(raw: str) -> str:
    """Normalize a captured ``cd`` target to match ``relative_to`` output.

    Strips leading ``./`` and trailing ``/`` so ``cd apps/api/``,
    ``cd ./apps/api``, and ``cd apps/api`` all compare equal against
    ``str(member.relative_to(project_dir))``.
    """
    return raw.strip().removeprefix("./").rstrip("/")


def derive_required_scripts(
    lint_cmd: str = "",
    typecheck_cmd: str = "",
    test_cmd: str = "",
) -> frozenset[tuple[str, str | None]]:
    """Extract ``(script_name, scoped_path | None)`` pairs from gate commands.

    ``scoped_path`` is the workspace-relative directory the command runs
    in (e.g. ``apps/api`` for ``cd apps/api && pnpm lint``); ``None``
    means the requirement applies to every workspace member.

    Recognized shapes:

    * ``cd <path> && pnpm <script>`` — scoped to ``<path>``.
    * ``pnpm -r <script>`` / ``pnpm --recursive <script>`` /
      ``pnpm --filter ... <script>`` — unscoped (required in all).
    * ``pnpm run <script>`` without ``cd`` prefix — unscoped.

    Unrecognized scoping forms (``pnpm -C <path>``, ``pnpm --dir <path>``)
    are treated as unscoped and logged as a warning. Falls back to
    ``_DEFAULT_REQUIRED_SCRIPTS`` if no pnpm commands are detected.
    """
    found: set[tuple[str, str | None]] = set()
    # Best-effort parser: matches ``pnpm [flag ...] [run [flag ...]] <script>``.
    # Flag-munching runs both before and after an optional ``run`` so that
    # forms like ``pnpm run -r lint`` don't backtrack to capturing ``run``
    # as the script name. See module docstring for the planned long-term
    # replacement (declarative ``gate_members`` config).
    pnpm_script_re = re.compile(
        r"pnpm\s+"
        r"(?:(?:-\w+|--\S+)\s+)*"
        r"(?:run\s+(?:(?:-\w+|--\S+)\s+)*)?"
        r"(\w[\w-]*)",
    )
    for cmd in (lint_cmd, typecheck_cmd, test_cmd):
        if not cmd:
            continue

        # Detect cd-prefix scoping, then split off the remainder to
        # inspect for override flags like -r / --filter.
        scoped_to: str | None = None
        cd_match = _CD_PREFIX_RE.match(cmd)
        remainder = cmd
        if cd_match:
            scoped_to = _normalize_scope_path(cd_match.group(1))
            remainder = cd_match.group(2)

        if _RECURSIVE_FLAG_RE.search(remainder):
            # Explicit recursion/filter overrides cd-prefix scoping.
            scoped_to = None

        if scoped_to is None and _UNHANDLED_SCOPING_RE.search(remainder):
            log.warning(
                "workspace_check: unrecognized scoping form in command %r; "
                "treating as require-in-all. Use 'cd <path> && ...' to scope.",
                cmd,
            )

        # Strip two-token value-taking flags so the script-name regex
        # doesn't misparse their values (e.g. ``-C apps/api lint`` used
        # to capture ``apps`` as the script name; ``--filter @pkg/foo
        # lint`` used to fall through to defaults entirely).
        cleaned = _VALUE_FLAG_STRIP_RE.sub(" ", remainder)

        m = pnpm_script_re.search(cleaned)
        if m:
            name = m.group(1)
            # Skip pnpm subcommands that aren't script names. ``run`` is
            # in the blocklist defensively — the regex should never
            # capture it, but if it does we drop rather than propagate.
            if name not in _PNPM_NON_SCRIPT_SUBCOMMANDS:
                found.add((name, scoped_to))

    return frozenset(found) if found else _DEFAULT_REQUIRED_SCRIPTS


@dataclass
class WorkspaceCheckResult:
    errors: list[tuple[str, list[str]]] = field(default_factory=list)  # (rel_path, missing_scripts)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors


def check_workspace_scripts(
    project_dir: Path,
    required_scripts: frozenset[tuple[str, str | None]] | None = None,
) -> WorkspaceCheckResult:
    """Check pnpm workspace members define the scripts that will be invoked.

    ``required_scripts`` is a set of ``(script_name, scoped_path | None)``
    pairs. Each script is enforced only against the workspace members it
    actually applies to — a script with ``scoped_path="apps/api"`` is
    required only in ``apps/api``, not in sibling packages; a script with
    ``scoped_path=None`` is required in every member. Use
    :func:`derive_required_scripts` to compute this from config.

    Defaults to ``_DEFAULT_REQUIRED_SCRIPTS`` (test/lint/typecheck,
    unscoped) when called with no argument.

    Returns immediately with empty result if pnpm-workspace.yaml does not exist.
    No language guard — self-guards on file existence.
    """
    scripts = required_scripts if required_scripts is not None else _DEFAULT_REQUIRED_SCRIPTS
    if not scripts:
        return WorkspaceCheckResult()

    result = WorkspaceCheckResult()

    # Resolve project_dir up-front so the relative_to calculation below
    # matches the resolved paths that ``_resolve_members`` returns — on
    # macOS ``/var/folders/...`` and ``/private/var/folders/...`` are the
    # same directory via symlink, and only resolved forms compare cleanly.
    project_dir = project_dir.resolve()

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
        rel = str(member.relative_to(project_dir))
        # Only scripts whose scope is None (apply-everywhere) or matches
        # this member's path are required here. Sibling-scoped scripts
        # would never run in this member and must not be demanded.
        applicable = frozenset(
            name for (name, scoped_to) in scripts
            if scoped_to is None or scoped_to == rel
        )
        if not applicable:
            continue
        missing = _check_member(member, applicable, result)
        if missing:
            result.errors.append((rel, sorted(missing)))

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
