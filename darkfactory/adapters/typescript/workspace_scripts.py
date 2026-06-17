"""pnpm workspace script consistency checker (TypeScript-adapter-owned).

Verifies that every workspace member package.json defines the scripts
that the quality gate invokes recursively.

Required scripts are derived from the pipeline config's gate commands
(lint_cmd, typecheck_cmd, test_cmd) rather than being hardcoded, so the
check matches what the pipeline actually runs.

This module holds the **pnpm-specific** command grammar. It lives in the
TypeScript adapter package (not the ecosystem-blind core) because the
parser recognizes pnpm CLI shapes (``pnpm -r``, ``--filter``, ``cd <pkg>
&& pnpm <script>``). The core orchestrator no longer calls this directly;
it dispatches through ``EcosystemAdapter.check_workspace_consistency``,
which the TypeScript adapter implements on top of these functions. Other
ecosystems (cargo, Go) inherit the ABC's default no-op.

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
from pathlib import Path

from darkfactory.adapters.base import WorkspaceCheckResult
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

# Applied to a ROOT package.json script BODY to decide whether running it
# (via a bare ``pnpm <name>``) depends on per-member ``<name>`` scripts.
# A body matches when it DELEGATES to members — pnpm recursion/filter, a
# monorepo task runner (turbo/nx/lerna), or npm/yarn workspaces — OR when
# it is an OPAQUE runner whose fan-out we cannot statically determine
# (make, a node/deno/bun script, a shell, npm-run-all/run-s/run-p,
# concurrently). In all of those cases we conservatively require the
# script in every member (fail-closed: a member missing it is a real
# misconfiguration the gate command would hit). A body that does NOT match
# is a self-contained root-level invocation (``eslint .``, ``tsc -b``,
# ``biome check .``) that runs once at root and needs NO per-member script.
_ROOT_DELEGATES_OR_OPAQUE_RE = re.compile(
    r"(?:^|\s)(?:-r|--recursive|--filter)(?:[\s=]|$)"          # pnpm recursion/filter
    r"|\b(?:turbo|nx|lerna)\b"                                 # monorepo task runners
    r"|(?:^|\s)--workspaces?(?:[\s=]|$)"                       # npm/yarn workspaces
    r"|\b(?:make|node|deno|bun|bash|sh|npm-run-all|run-s|run-p|concurrently)\b",  # opaque runners
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
    root_scripts: dict[str, str] | None = None,
) -> frozenset[tuple[str, str | None]]:
    """Extract ``(script_name, scoped_path | None)`` pairs from gate commands.

    ``scoped_path`` is the workspace-relative directory the command runs
    in (e.g. ``apps/api`` for ``cd apps/api && pnpm lint``); ``None``
    means the requirement applies to every workspace member.

    Recognized shapes:

    * ``cd <path> && pnpm <script>`` — scoped to ``<path>``.
    * ``pnpm -r <script>`` / ``pnpm --recursive <script>`` /
      ``pnpm --filter ... <script>`` — unscoped (required in all).
    * ``pnpm run <script>`` without ``cd`` prefix — see ``root_scripts``.

    ``root_scripts`` is the root ``package.json`` ``scripts`` map. When
    provided, a bare unscoped ``pnpm <script>`` (no ``cd``, no ``-r`` /
    ``--filter``) is NOT blindly required in every member: we resolve the
    root script body and DROP the per-member requirement only on positive
    evidence the body is a self-contained root-level invocation (``eslint
    .``, ``biome check .``, ``tsc -b``) — it runs once at root and needs NO
    per-member script, so requiring one would false-fail an idiomatic
    monorepo (the gate command string ``pnpm lint`` is identical whether
    the root script aggregates or delegates; only the root body
    distinguishes them). A body that DELEGATES to members (``pnpm -r run``,
    ``turbo run``, ``nx``, ``lerna run``, npm/yarn ``--workspaces``) OR is an
    OPAQUE runner whose fan-out can't be determined (``make``, a node/deno/
    bun script, a shell, ``npm-run-all``/``run-s``/``run-p``,
    ``concurrently``) OR is ABSENT falls back to the conservative
    require-in-all (fail-closed — a member missing the script is a real
    misconfiguration the gate command would hit). When ``root_scripts`` is
    ``None`` (legacy callers / no project dir), behavior is unchanged: every
    parsed unscoped script is required-in-all.

    Unrecognized scoping forms (``pnpm -C <path>``, ``pnpm --dir <path>``)
    are treated as unscoped and logged as a warning. Falls back to
    ``_DEFAULT_REQUIRED_SCRIPTS`` if no pnpm commands are detected.
    """
    found: set[tuple[str, str | None]] = set()
    # Whether ANY gate command parsed to a real script name (even one that
    # was then dropped as a root-level non-recursive script). Distinguishes
    # "no gate commands configured" (fall back to _DEFAULT_REQUIRED_SCRIPTS)
    # from "commands parsed but all are root-level → genuinely no per-member
    # requirement" (return empty, do NOT fall back — else the drop is undone).
    parsed_any = False
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

        cmd_recursive = bool(_RECURSIVE_FLAG_RE.search(remainder))
        if cmd_recursive:
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
                parsed_any = True
                # Bare unscoped ``pnpm <name>`` (no cd-scope, no command-
                # level recursion): whether members need their own ``name``
                # script depends on what the ROOT script body actually does.
                # We DROP the per-member requirement only on POSITIVE evidence
                # the root body is a self-contained root-level invocation
                # (``eslint .``, ``tsc -b``) — it runs once at root and needs
                # no per-member script, so requiring one false-fails an
                # idiomatic monorepo. A body that delegates (``pnpm -r run``,
                # turbo/nx/lerna, npm/yarn workspaces) OR is an opaque runner
                # (make, node-script, …) OR an ABSENT root script falls
                # through to the conservative require-in-all (fail-closed —
                # a member missing the script is a real misconfig the gate
                # command would hit). ``root_scripts is None`` (legacy
                # callers) keeps the old require-in-all behavior.
                if (
                    scoped_to is None
                    and not cmd_recursive
                    and root_scripts is not None
                    and name in root_scripts
                    and not _ROOT_DELEGATES_OR_OPAQUE_RE.search(root_scripts[name])
                ):
                    continue
                found.add((name, scoped_to))

    if found:
        return frozenset(found)
    # Parsed real script(s) but dropped them all as root-level → no
    # per-member requirement. Only fall back to the conservative default
    # when nothing parsed at all (no gate commands configured).
    return frozenset() if parsed_any else _DEFAULT_REQUIRED_SCRIPTS


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
