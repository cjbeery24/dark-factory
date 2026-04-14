"""A3 — Library-symbol existence check (layered).

Extract library imports from spec §5 Interface Contract TypeScript fences
and cross-reference against the target project's declared dependencies.

A3 is intentionally layered because pnpm-workspace monorepos without a
completed ``pnpm install`` (common in CI snapshots and fresh clones)
cannot answer "does this exported symbol exist" — the ``node_modules``
tree doesn't exist yet.

**Layer 1 — dependency declaration** (always runs, zero filesystem cost
beyond reading ``package.json``): an import like ``from 'drizzle-orm'``
must name a package that is declared as a ``dependency`` or
``devDependency`` somewhere in the project's reach: the root
``package.json`` plus every workspace package named by
``pnpm-workspace.yaml`` (or the ``workspaces`` field of the root
package.json for npm/yarn flat setups). Workspace-internal packages
(e.g. ``@budget-brain/shared``) are exempt from this check.

**Layer 2 — symbol existence** (only runs when ``node_modules`` is
present): for each ``from '<pkg>/<subpath>'`` import, confirm that
``<pkg>/<subpath>`` is resolvable via the pinned package's ``exports``
map or its ``main``/``types`` field. Yarn PnP (no ``node_modules``) is
deferred with a one-line "unsupported layout" warning — Layer 1 still
provides value there.

Motivated by 2026-04-14 database-foundation escalation. See
``docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md``
(Sprint 2, A3). Layer 1 would *not* have caught that run's specific
failure mode (drizzle-orm was declared) but it is the substrate A6
builds on — ship it first so the pnpm-aware resolver is already in
place.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.spec.spec_model import FencedBlock, SpecModel

log = logging.getLogger(__name__)


# ``from '<pkg>'`` / ``from "<pkg>"`` / ``require('<pkg>')`` /
# ``import('<pkg>')`` — captures the import specifier. Catches static
# imports, CJS requires, and dynamic imports with literal string args.
# Does NOT capture dynamic imports *constructed at runtime* (e.g.
# ``import(\`pkg-\${name}\`)``); those are outside A3's remit.
_IMPORT_RE = re.compile(
    r"""(?:from|require\s*\(|import\s*\()\s*['"]([^'"]+)['"]""",
    re.MULTILINE,
)

# Strip a leading "5." / "A." / "5" / "A" numeric/alpha section prefix so
# that specs using numbered headings (``## 5. Interface Contract``) match
# the same rules as unnumbered ones (``## Interface Contract``). Matches
# the behavior of ``_HEADING_RE`` in precheck.py.
_HEADING_PREFIX_RE = re.compile(r"^(?:[0-9]+|[A-Za-z])\.?\s+")


def _normalize_heading(text: str) -> str:
    return _HEADING_PREFIX_RE.sub("", text.strip()).strip().lower()

# Builtin Node modules (these must NEVER be declared dependencies).
# The ``node:`` prefix form short-circuits earlier in _normalize_package,
# so this set is consulted only for bare builtins like ``fs``, ``path``.
_NODE_BUILTINS: frozenset[str] = frozenset({
    "assert", "async_hooks", "buffer", "child_process", "cluster",
    "console", "constants", "crypto", "dgram", "diagnostics_channel",
    "dns", "domain", "events", "fs", "http", "http2", "https", "inspector",
    "module", "net", "os", "path", "perf_hooks", "process", "punycode",
    "querystring", "readline", "repl", "stream", "string_decoder",
    "sys", "test", "timers", "tls", "trace_events", "tty", "url", "util",
    "v8", "vm", "wasi", "worker_threads", "zlib",
})


@dataclass(frozen=True)
class LibrarySymbolFinding:
    """A single undeclared library import from a spec §5 TypeScript fence.

    ``layer`` is either ``"dependency"`` (Layer 1: not declared in any
    reachable package.json) or ``"symbol"`` (Layer 2: declared but the
    subpath is not exported in the pinned version). Today only
    ``"dependency"`` is emitted; Layer 2 is scaffolded but returns empty
    until ``node_modules`` resolution lands.
    """

    line_number: int              # 1-based, opening fence line
    section_path: tuple[str, ...]
    imported_package: str         # e.g. "drizzle-orm" or "@foo/bar"
    import_specifier: str         # full specifier, e.g. "drizzle-orm/pg-core"
    layer: str                    # "dependency" | "symbol"
    message: str                  # human-readable diagnostic


@dataclass(frozen=True)
class PackageManifest:
    """Subset of a package.json relevant to dependency resolution."""

    path: Path
    name: str | None
    dependencies: frozenset[str]
    dev_dependencies: frozenset[str]
    peer_dependencies: frozenset[str]
    optional_dependencies: frozenset[str]

    def declares(self, pkg: str) -> bool:
        return (
            pkg in self.dependencies
            or pkg in self.dev_dependencies
            or pkg in self.peer_dependencies
            or pkg in self.optional_dependencies
        )


@dataclass(frozen=True)
class ProjectDependencyIndex:
    """All packages declared across a project's workspace tree."""

    root: Path
    manifests: tuple[PackageManifest, ...]
    workspace_names: frozenset[str]

    def any_declares(self, pkg: str) -> bool:
        return any(m.declares(pkg) for m in self.manifests)

    def is_workspace(self, pkg: str) -> bool:
        return pkg in self.workspace_names


# ---------------------------------------------------------------------------
# Spec-side: extract candidate imports from §5 Interface Contract fences.
# ---------------------------------------------------------------------------


def _is_interface_contract_fence(block: FencedBlock) -> bool:
    """True if this fence sits under any heading named 'Interface Contract'.

    Numbered headings (``## 5. Interface Contract``) and unnumbered
    (``## Interface Contract``) both count — we normalize the same way
    the checklist section detector does.
    """
    for segment in block.section_path:
        if _normalize_heading(segment) == "interface contract":
            return True
    return False


def _is_typescript_fence(block: FencedBlock) -> bool:
    lang = block.language.lower()
    # mts/cts are Node ESM/CJS TypeScript variants; ``tsx`` is JSX+TS.
    # Deliberately exclude plain ``js`` / ``jsx`` — a JS fence is not a
    # TypeScript interface contract.
    return lang in {"typescript", "ts", "tsx", "mts", "cts"}


def _normalize_package(specifier: str) -> str | None:
    """Return the bare package name from an import specifier.

    ``drizzle-orm/pg-core`` → ``drizzle-orm``
    ``@fastify/cors`` → ``@fastify/cors``
    ``@fastify/cors/plugin`` → ``@fastify/cors``
    ``./relative/path`` → None (relative)
    ``node:fs`` / ``fs`` → None (builtin)
    """
    s = specifier.strip()
    if not s or s.startswith((".", "/")):
        return None
    if s.startswith("node:"):
        return None
    if s.split("/", 1)[0] in _NODE_BUILTINS:
        return None
    if s.startswith("@"):
        parts = s.split("/", 2)
        if len(parts) < 2:
            # Malformed scoped import ("@foo"). Defensive — don't crash.
            return None
        return f"{parts[0]}/{parts[1]}"
    return s.split("/", 1)[0]


def extract_spec_imports(model: SpecModel) -> list[tuple[FencedBlock, str, str]]:
    """Return ``(block, bare_package, full_specifier)`` for every import
    in every §5 Interface Contract TypeScript fence.

    Deduplication is the caller's responsibility — we emit one entry per
    ``from '...'`` occurrence so line-number context is preserved for
    diagnostics.
    """
    out: list[tuple[FencedBlock, str, str]] = []
    for block in model.all_fenced_blocks:
        if not _is_interface_contract_fence(block):
            continue
        if not _is_typescript_fence(block):
            continue
        for match in _IMPORT_RE.finditer(block.content):
            specifier = match.group(1)
            pkg = _normalize_package(specifier)
            if pkg is None:
                continue
            out.append((block, pkg, specifier))
    return out


# ---------------------------------------------------------------------------
# Project-side: build the dependency index from package.json + workspace walk.
# ---------------------------------------------------------------------------


def _read_package_json(path: Path) -> PackageManifest | None:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("A3: cannot read %s — %s", path, exc)
        return None
    if not isinstance(raw, dict):
        return None

    def _keys(field: str) -> frozenset[str]:
        v = raw.get(field)
        if not isinstance(v, dict):
            return frozenset()
        return frozenset(k for k in v.keys() if isinstance(k, str))

    name = raw.get("name")
    return PackageManifest(
        path=path,
        name=name if isinstance(name, str) else None,
        dependencies=_keys("dependencies"),
        dev_dependencies=_keys("devDependencies"),
        peer_dependencies=_keys("peerDependencies"),
        optional_dependencies=_keys("optionalDependencies"),
    )


def _resolve_workspace_globs(project_root: Path) -> list[Path]:
    """Return the set of package.json files across a workspace layout.

    Supports:
    - pnpm-workspace.yaml ``packages: [...]``
    - npm/yarn flat ``workspaces: [...]`` in root package.json
    - Single-package repos (just root package.json)

    Yarn PnP (no ``node_modules``, no ``workspaces``) falls through to
    the single-package path; Layer 2 will warn about it separately.
    """
    roots: list[Path] = []
    root_pkg = project_root / "package.json"
    if root_pkg.exists():
        roots.append(root_pkg)

    patterns = _read_workspace_patterns(project_root)
    for pattern in patterns:
        for candidate in _expand_workspace_glob(project_root, pattern):
            pkg_json = candidate / "package.json"
            if pkg_json.exists() and pkg_json not in roots:
                roots.append(pkg_json)
    return roots


def _read_workspace_patterns(project_root: Path) -> list[str]:
    """Extract workspace patterns from pnpm-workspace.yaml or root package.json.

    Intentionally does a hand-rolled YAML scan rather than pulling in
    PyYAML — we need exactly one field (``packages:``) and the
    dependency cost isn't justified for a precheck advisory.
    """
    patterns: list[str] = []

    pnpm_ws = project_root / "pnpm-workspace.yaml"
    if pnpm_ws.exists():
        try:
            patterns.extend(_parse_pnpm_packages(pnpm_ws.read_text()))
        except OSError as exc:
            log.warning("A3: cannot read %s — %s", pnpm_ws, exc)

    # npm/yarn flat — ``workspaces`` in root package.json.
    root_pkg = project_root / "package.json"
    if root_pkg.exists():
        try:
            raw = json.loads(root_pkg.read_text())
            ws = raw.get("workspaces")
            if isinstance(ws, list):
                patterns.extend(p for p in ws if isinstance(p, str))
            elif isinstance(ws, dict):
                inner = ws.get("packages")
                if isinstance(inner, list):
                    patterns.extend(p for p in inner if isinstance(p, str))
        except (OSError, json.JSONDecodeError):
            # Already logged upstream in _read_package_json when it's read
            # for manifest purposes; swallow here to avoid duplicate noise.
            pass
    return patterns


def _parse_pnpm_packages(text: str) -> list[str]:
    """Minimal YAML scan for the ``packages:`` list in pnpm-workspace.yaml.

    Accepts two forms:
        packages:
          - 'apps/*'
          - packages/*

    Anything more exotic (anchors, merges, flow style) we don't support
    and will emit zero patterns for. That is intentional: A3 is advisory,
    and a parse miss means "no workspace packages found" — identical to
    a single-package repo. Layer 1 will still flag undeclared top-level
    imports from the root package.json.
    """
    out: list[str] = []
    in_packages = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        # Only treat ``packages:`` as the anchor if it sits at column 0.
        # A nested ``packages:`` under another map (``catalog:`` → ``packages:``)
        # is a different key in a different scope and must not seed the list.
        if (
            stripped.startswith("packages:")
            and not line.startswith((" ", "\t"))
        ):
            in_packages = True
            continue
        if in_packages:
            # End of list: next top-level key (no leading whitespace, ends in ':').
            if not line.startswith((" ", "\t")) and line.endswith(":"):
                in_packages = False
                continue
            if stripped.startswith("- "):
                item = stripped[2:].strip().strip("'\"")
                # Drop npm-style negations; we don't support those semantics.
                if item and not item.startswith("!"):
                    out.append(item)
            elif not line.startswith((" ", "\t")):
                # De-dented to a new top-level key without trailing ':' —
                # list is over.
                in_packages = False
    return out


def _expand_workspace_glob(root: Path, pattern: str) -> list[Path]:
    """Expand a workspace glob (e.g. ``apps/*``) into candidate package dirs.

    Only supports one ``*`` per path segment — matches pnpm/npm semantics
    for workspace globs in practice. We deliberately don't shell out or
    support ``**``; workspace layouts are shallow by convention.
    """
    # Negations ("!pattern") are an npm extension; treat as no-op for A3.
    if pattern.startswith("!"):
        return []
    try:
        # Path.glob handles ``*`` at segment level cleanly.
        return [p for p in root.glob(pattern) if p.is_dir()]
    except (OSError, ValueError):
        return []


def build_dependency_index(project_root: Path) -> ProjectDependencyIndex:
    """Walk the workspace tree once and build the dep declaration index."""
    manifests: list[PackageManifest] = []
    for pkg_json in _resolve_workspace_globs(project_root):
        m = _read_package_json(pkg_json)
        if m is not None:
            manifests.append(m)
    workspace_names = frozenset(
        m.name for m in manifests if m.name is not None
    )
    return ProjectDependencyIndex(
        root=project_root,
        manifests=tuple(manifests),
        workspace_names=workspace_names,
    )


# ---------------------------------------------------------------------------
# Top-level check
# ---------------------------------------------------------------------------


def check_library_symbols(
    model: SpecModel,
    *,
    project_dir: Path | None,
) -> list[LibrarySymbolFinding]:
    """Return findings for imports that aren't declared in the project tree.

    When ``project_dir`` is None, the check is a no-op — we cannot tell
    "undeclared" from "missing project context." Returning [] here means
    "A3 had no signal," identical in shape to "spec has no imports."
    """
    if project_dir is None:
        return []

    imports = extract_spec_imports(model)
    if not imports:
        return []

    index = build_dependency_index(project_dir)

    # Dedupe by (bare_package, first-seen block) so we emit one finding per
    # undeclared package — multiple imports of the same missing package
    # are one human fix, not N.
    seen: set[str] = set()
    findings: list[LibrarySymbolFinding] = []
    for block, pkg, specifier in imports:
        if pkg in seen:
            continue
        if index.is_workspace(pkg):
            continue
        if index.any_declares(pkg):
            continue
        seen.add(pkg)
        findings.append(
            LibrarySymbolFinding(
                line_number=block.line_number,
                section_path=block.section_path,
                imported_package=pkg,
                import_specifier=specifier,
                layer="dependency",
                message=(
                    f"spec §5 imports {specifier!r} but {pkg!r} is not "
                    f"declared as a dependency in any package.json "
                    f"under {project_dir}"
                ),
            )
        )
    return findings
