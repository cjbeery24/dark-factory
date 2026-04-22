"""Classify TypeScript declarations as runtime-present or type-only.

Motivated by 2026-04-21 database-foundation iter-3 escalation: the architect
listed ``DrizzleTx`` in ``entry_points[0].exports`` for ``apps/api/src/db/index.ts``,
but the declaration was::

    export type DrizzleTx = Parameters<Parameters<typeof db.transaction>[0]>[0];

TypeScript erases ``export type``, ``export interface``, and brace-clause
exports carrying the ``type`` modifier at compile time — the resulting JS
module has no runtime presence for those names. The Import Contract gate
synthesizes ``Object.keys(resolvedModule).includes('DrizzleTx')``, which
type-only exports cannot satisfy; the gate then fails every iteration
regardless of the Implementer's work.

This classifier runs at manifest-validation time (before the gate phase
even has a chance to fire). It reads the TS source file for each
``entry_points[].path`` and, for each declared name, returns one of:

- ``runtime``   — declared as const/let/var/function/class/enum, exported
                  as default, or re-exported without a ``type`` modifier.
- ``type_only`` — declared as ``type``/``interface``, or exported with a
                  ``type`` modifier in a brace clause.
- ``not_found`` — no declaration for this name in the file.

Only ``type_only`` triggers manifest rejection. ``not_found`` is a
separate concern handled by the gate-time export-contract verifier
(``darkfactory/extract/exports.py``). ``runtime`` includes the
conservatively-ambiguous case ``export { X }`` where ``X`` has no local
declaration we can resolve — the Import Contract gate is the backstop
for that; we only reject on unambiguous type-only declarations.

Regex-based; no TypeScript parser. Handles the forms seen in production
(``export type X = …``, ``export interface X``, ``export { type X }``,
``export type { X }``, ``export type { X } from '…'``).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


# Maximum re-export hops followed by ``classify_exports``. The canonical
# monorepo barrel pattern is ``apps/api/src/db/index.ts`` → ``./types``
# → ``../shared/types``; at depth 1 the second hop falls back to
# ``runtime`` and silently leaks the DrizzleTx bug class. Depth 2 catches
# that shape. Raise if real-world shapes need more — but every level
# costs one extra file read per brace re-export, so don't inflate this
# without evidence.
_REEXPORT_MAX_HOPS = 2


# Type-only declaration at the file level:
#   export type Foo = ...
#   export type Foo<T> = ...
#   export interface Foo { ... }
#   export interface Foo<T> { ... }
_EXPORT_TYPE_DECL_RE = re.compile(
    r"^\s*export\s+(?:type|interface)\s+(\w+)",
    re.MULTILINE,
)

# Runtime declaration at the file level:
#   export const|let|var NAME
#   export (async) function[*] NAME
#   export (abstract) class NAME
#   export enum NAME (plain enum emits a runtime object).
#
# NOTE: alternation ordering matters. ``enum`` variants MUST precede plain
# ``const`` — otherwise ``export const enum Foo`` greedily matches the
# ``const\s+`` branch first and captures ``enum`` as the NAME. The
# negative lookahead on ``const`` below makes the ordering explicit.
_EXPORT_RUNTIME_DECL_RE = re.compile(
    r"^\s*export\s+"
    r"(?:"
        r"(?:async\s+)?function\s*\*?\s+"
        r"|(?:abstract\s+)?class\s+"
        r"|(?:const\s+)?enum\s+"
        r"|const\s+(?!enum\b)"
        r"|let\s+"
        r"|var\s+"
        r"|default\s+(?:(?:async\s+)?function\s*\*?\s+|(?:abstract\s+)?class\s+)"
    r")"
    r"(\w+)",
    re.MULTILINE,
)

# ``export declare`` is ambient — the compiler emits NO runtime code for
# these, regardless of whether the inner keyword is ``const`` / ``function``
# / ``class`` / ``namespace`` etc. Every name introduced by ``export declare``
# is type-only in practice. Applies to .d.ts files and ambient merge blocks.
_EXPORT_DECLARE_RE = re.compile(
    r"^\s*export\s+declare\s+"
    r"(?:"
        r"(?:async\s+)?function\s*\*?\s+"
        r"|(?:abstract\s+)?class\s+"
        r"|(?:const\s+)?enum\s+"
        r"|const\s+(?!enum\b)"
        r"|let\s+"
        r"|var\s+"
        r"|type\s+"
        r"|interface\s+"
        r"|namespace\s+"
        r"|module\s+"
    r")"
    r"(\w+)",
    re.MULTILINE,
)

# Local type declaration (not exported directly; used to resolve
# ``export { X }`` where X is declared elsewhere in the file):
#   type Foo = ...
#   interface Foo { ... }
_LOCAL_TYPE_DECL_RE = re.compile(
    r"^\s*(?:type|interface)\s+(\w+)",
    re.MULTILINE,
)

# Local runtime declaration used to resolve ``export { X }`` where X has
# a value binding:
#   const|let|var NAME
#   (async) function[*] NAME
#   (abstract) class NAME
#   enum NAME
_LOCAL_RUNTIME_DECL_RE = re.compile(
    r"^\s*"
    r"(?:"
        r"(?:async\s+)?function\s*\*?\s+"
        r"|(?:abstract\s+)?class\s+"
        r"|(?:const\s+)?enum\s+"
        r"|const\s+(?!enum\b)"
        r"|let\s+"
        r"|var\s+"
    r")"
    r"(\w+)",
    re.MULTILINE,
)

# Brace-clause exports (both local and re-export forms):
#   export { A, B as C }
#   export { type D, type E as F }
#   export type { G, H as I }
#   export { A } from './x'
#   export type { A } from './x'
# Captures:
#   group(1) = outer "type" modifier (``export type {`` form) or None
#   group(2) = brace content
#   group(3) = "from '...'" re-export suffix or None (via presence check)
#   group(4) = module specifier inside the ``from`` clause, or None
_EXPORT_BRACE_RE = re.compile(
    r"^\s*export\s+(?:(type)\s+)?\{([^}]*)\}\s*"
    r"(from\s+['\"]([^'\"]+)['\"])?",
    re.MULTILINE,
)

# Default export of a value expression (not a class/function declaration,
# which is already covered by _EXPORT_RUNTIME_DECL_RE above). Maps to the
# literal export name "default" when the architect lists it.
_EXPORT_DEFAULT_EXPR_RE = re.compile(
    r"^\s*export\s+default\b",
    re.MULTILINE,
)


@dataclass
class ExportClassification:
    """Per-file classification of declared export names."""

    path: str
    runtime: set[str] = field(default_factory=set)
    type_only: set[str] = field(default_factory=set)

    def classify(self, name: str) -> str:
        """Return ``"runtime"``, ``"type_only"``, or ``"not_found"``.

        ``type_only`` wins if a name is both (a degenerate case: a name
        can't really have both a type and value binding visible to
        consumers, but if our regexes disagree we prefer the stricter
        rejection since a false positive is a clearer error than a
        false negative).
        """
        if name in self.type_only:
            return "type_only"
        if name in self.runtime:
            return "runtime"
        return "not_found"


def _strip_comments(source: str) -> str:
    """Remove // and /* */ comments. Mirrors ``extract/exports.py``.

    Best-effort — does not defend against ``//`` inside template literals.
    Acceptable here because our patterns anchor on ``^export`` at line
    start, which rarely appears inside a string literal.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    source = re.sub(r"(?<!:)//.*$", "", source, flags=re.MULTILINE)
    return source


def _parse_brace_item(item: str) -> tuple[str, str, bool] | None:
    """Parse ``(original, exported, inner_type_modifier)`` from a brace item.

    Returns ``None`` for empty / malformed entries. Handles the ``type``
    modifier prefix and ``X as Y`` aliasing.
    """
    item = item.strip()
    if not item:
        return None

    inner_type = False
    if item.startswith("type "):
        inner_type = True
        item = item[len("type "):].strip()
        if not item:
            return None

    if " as " in item:
        parts = [p.strip() for p in item.split(" as ")]
        original = parts[0]
        exported = parts[-1]
        if not exported:
            return None
    else:
        original = item
        exported = item

    return original, exported, inner_type


def classify_exports_text(
    source: str,
    *,
    path_for_log: str = "",
    reexport_resolver: "Callable[[str], ExportClassification | None] | None" = None,
) -> ExportClassification:
    """Classify exports in a TS/TSX source string.

    Accepts raw source so tests can exercise this without writing to disk.
    The ``path_for_log`` field populates ``ExportClassification.path`` for
    diagnostic readability; pass ``""`` in tests.

    ``reexport_resolver`` is an optional callback invoked for each brace
    re-export (``export { X } from '<specifier>'``) without a ``type``
    modifier. It receives the raw specifier string and should return an
    ``ExportClassification`` for the target module, or ``None`` if the
    specifier can't be resolved. Callers supplying this are expected to
    bound recursion to a single hop — passing the same resolver to nested
    calls will recurse indefinitely on cycles.

    If the resolver classifies ``original`` as ``type_only`` in the hop
    target, the re-exporter's exported name is flagged type-only (closing
    the DrizzleTx-via-sibling-file bypass). If the resolver returns
    ``None`` (unresolvable specifier — bare package, unknown extension),
    we fall back to the prior policy of classifying as runtime with the
    Import Contract gate as the backstop.
    """
    result = ExportClassification(path=path_for_log)
    source = _strip_comments(source)

    # Phase 1: collect local declarations (without the ``export`` keyword)
    # so brace exports like ``export { X }`` can be resolved against them.
    local_types: set[str] = set()
    local_runtime: set[str] = set()
    for m in _LOCAL_TYPE_DECL_RE.finditer(source):
        # _LOCAL_TYPE_DECL_RE matches both ``export type X`` and ``type X``
        # (since it doesn't anchor on the ``export`` keyword). That's fine
        # — having a name in local_types when it's also exported as type
        # doesn't produce a wrong answer; the exported-decl pass below is
        # the authoritative source for the exported set.
        local_types.add(m.group(1))
    for m in _LOCAL_RUNTIME_DECL_RE.finditer(source):
        local_runtime.add(m.group(1))

    # Phase 2a: exported type declarations.
    for m in _EXPORT_TYPE_DECL_RE.finditer(source):
        name = m.group(1)
        result.type_only.add(name)

    # Phase 2b: ``export declare`` (ambient) — every form is type-only.
    # Run before Phase 3 so a subsequent ``export const X`` value decl can
    # still upgrade X to runtime (declaration merging, same-name value).
    for m in _EXPORT_DECLARE_RE.finditer(source):
        name = m.group(1)
        result.type_only.add(name)

    # Phase 3: exported runtime declarations. If a name was somehow
    # classified as both (shouldn't happen for valid TS), the type_only
    # classification takes precedence — but we also *remove* it from
    # type_only here because a plain ``export function X`` is definitively
    # a value binding regardless of any prior ``type X`` matching.
    for m in _EXPORT_RUNTIME_DECL_RE.finditer(source):
        name = m.group(1)
        result.runtime.add(name)
        result.type_only.discard(name)

    # Phase 4: default. Already covered by _EXPORT_RUNTIME_DECL_RE for
    # ``export default function|class``; this handles bare ``export default
    # expr``.
    if _EXPORT_DEFAULT_EXPR_RE.search(source):
        result.runtime.add("default")

    # Phase 5: brace exports. Do these last so the Phase 3 runtime set
    # is already populated — a name that appears in a local declaration
    # AND in a brace export resolves against the runtime set.
    for m in _EXPORT_BRACE_RE.finditer(source):
        outer_type = m.group(1) is not None
        body = m.group(2) or ""
        has_from = m.group(3) is not None
        specifier = m.group(4)  # None when there is no ``from`` clause
        for item in body.split(","):
            parsed = _parse_brace_item(item)
            if parsed is None:
                continue
            original, exported, inner_type = parsed

            # Outer or inner ``type`` modifier → unambiguously type-only.
            if outer_type or inner_type:
                if exported not in result.runtime:
                    result.type_only.add(exported)
                continue

            if has_from:
                # Re-export without a ``type`` modifier. Try to resolve
                # ONE hop via the caller's resolver; if that finds the
                # original as type-only in the target module, propagate.
                # Otherwise fall back to runtime (prior policy).
                resolved_kind: str | None = None
                if reexport_resolver is not None and specifier:
                    hop = reexport_resolver(specifier)
                    if hop is not None:
                        resolved_kind = hop.classify(original)
                if resolved_kind == "type_only":
                    if exported not in result.runtime:
                        result.type_only.add(exported)
                else:
                    result.runtime.add(exported)
                    result.type_only.discard(exported)
                continue

            # Local-scope brace export — look up original in local decls.
            if original in local_types and original not in local_runtime:
                if exported not in result.runtime:
                    result.type_only.add(exported)
            else:
                result.runtime.add(exported)
                result.type_only.discard(exported)

    return result


def _resolve_ts_module(
    base_dir: Path,
    specifier: str,
    *,
    project_root: Path | None = None,
) -> Path | None:
    """Resolve a relative TS module specifier to a file on disk.

    Handles the common import-specifier idioms:

    - ``./x`` → tries ``x.ts``, ``x.tsx``, ``x.mts``, ``x.cts``, ``x/index.ts``
    - ``./x.js`` → rewrites to ``x.ts`` (TS-with-NodeNext convention)
    - ``./x.ts`` → used as-is

    Returns ``None`` for bare package specifiers (``pkg``, ``@scope/pkg``) or
    unresolvable paths. ``..`` is allowed — intentionally: projects re-export
    across sibling directories.

    When ``project_root`` is provided, the resolved path MUST be within that
    directory (after ``Path.resolve()``). The specifier string comes from
    architect LLM output; without containment, a re-export clause like
    ``export { X } from '../../../../etc/passwd'`` would pull arbitrary
    host-FS contents into orchestrator-level RAM and leak existence via
    the type-only finding. Caller threads ``project_root=project_dir``.
    """
    if not specifier or not (specifier.startswith("./") or specifier.startswith("../")):
        return None

    candidate = (base_dir / specifier).resolve()

    # Containment check — reject any candidate that escapes the project.
    # The caller's ``project_root`` is already ``.resolve()``-ed; we
    # re-resolve here to be defensive.
    if project_root is not None:
        try:
            candidate.relative_to(project_root.resolve())
        except ValueError:
            return None

    # Strip trailing .js and retry as .ts (TS-with-NodeNext convention where
    # the specifier is written with .js but the source is .ts).
    if specifier.endswith(".js"):
        ts_candidate = candidate.with_suffix(".ts")
        if ts_candidate.is_file():
            return ts_candidate

    if candidate.is_file():
        return candidate

    for ext in (".ts", ".tsx", ".mts", ".cts"):
        path = candidate.with_suffix(ext) if candidate.suffix else Path(str(candidate) + ext)
        if path.is_file():
            return path

    # Barrel: ``./utils`` → ``./utils/index.ts``
    if candidate.is_dir():
        for ext in (".ts", ".tsx", ".mts", ".cts"):
            index_path = candidate / f"index{ext}"
            if index_path.is_file():
                return index_path

    return None


def _is_dts_path(file_path: Path) -> bool:
    """Return True if ``file_path`` is a TypeScript declaration file."""
    return file_path.name.endswith(".d.ts") or file_path.name.endswith(".d.mts") \
        or file_path.name.endswith(".d.cts")


def classify_exports(
    file_path: Path,
    *,
    project_root: Path | None = None,
    _hops_remaining: int = _REEXPORT_MAX_HOPS,
) -> ExportClassification:
    """Read ``file_path`` and return its export classification.

    Missing files, I/O errors, and decode errors return an empty
    classification with the path preserved — the caller distinguishes
    "file not found" from "no type-only exports" by checking the path's
    existence separately. This matches the non-crashing contract of
    ``darkfactory/extract/exports.py::extract_exports``.

    When ``_hops_remaining > 0``, bare re-exports
    (``export { X } from './y'`` without a ``type`` modifier) are resolved
    via ``_resolve_ts_module``. The recursive call decrements the counter,
    so a chain of re-exports is bounded to ``_REEXPORT_MAX_HOPS``
    levels — enough to catch the canonical monorepo barrel
    ``index.ts → ./types → ../shared/types``, which is where the DrizzleTx
    bug class hides in practice.

    ``project_root`` is the directory every resolved hop must live inside.
    Specifiers that escape it (``../../../../etc/passwd``) are treated as
    unresolvable. When ``None``, no containment check runs — caller is
    trusted to stay inside the project. Manifest-level callers MUST pass
    ``project_root`` explicitly.
    """
    result = ExportClassification(path=str(file_path))
    if not file_path.exists():
        return result
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("classify_exports: could not read %s: %s", file_path, exc)
        return result

    # ``.d.ts`` declaration files contain only type information — there is
    # no runtime module. Every declared name is type-only by construction.
    # We still run the classifier (to populate the full name set) but then
    # flip the classification to type-only for every name.
    if _is_dts_path(file_path):
        classification = classify_exports_text(source, path_for_log=str(file_path))
        all_names = classification.runtime | classification.type_only
        return ExportClassification(
            path=str(file_path),
            runtime=set(),
            type_only=all_names,
        )

    resolver: Callable[[str], ExportClassification | None] | None = None
    if _hops_remaining > 0:
        base_dir = file_path.parent
        next_hops = _hops_remaining - 1

        def resolver(specifier: str) -> ExportClassification | None:
            resolved = _resolve_ts_module(
                base_dir, specifier, project_root=project_root,
            )
            if resolved is None:
                return None
            return classify_exports(
                resolved,
                project_root=project_root,
                _hops_remaining=next_hops,
            )

    return classify_exports_text(
        source,
        path_for_log=str(file_path),
        reexport_resolver=resolver,
    )


# ---------------------------------------------------------------------------
# Manifest-level integration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TypeOnlyFinding:
    """A manifest entry_point declares an export that is type-only on disk."""

    entry_point_idx: int
    path: str
    type_only_names: tuple[str, ...]


def find_type_only_exports(
    entry_points: list,
    project_dir: Path,
) -> list[TypeOnlyFinding]:
    """Return one finding per entry_point that declares a type-only export.

    Only applies to ``.ts`` / ``.tsx`` / ``.mts`` / ``.cts`` files. Entry
    points whose ``path`` is empty, resolves outside the project, has a
    non-TS extension, or doesn't exist on disk are skipped silently —
    those are separate failure modes addressed by other checks in the
    manifest validator.

    Returns a list (not a bool / exception) so the caller can format a
    single error message covering every offending entry point, which is
    less annoying to act on than receiving them one at a time across
    architect retries.
    """
    findings: list[TypeOnlyFinding] = []
    ts_exts = {".ts", ".tsx", ".mts", ".cts"}

    for idx, ep in enumerate(entry_points):
        path = getattr(ep, "path", "") or ""
        exports = getattr(ep, "exports", []) or []
        if not path or not exports:
            continue

        # Path.suffix handles the common cases; a double-extension
        # ``.d.ts`` has suffix ``.ts`` which is correct — a .d.ts file
        # contains only type declarations and SHOULD be rejected if the
        # architect listed it as a runtime entry point.
        if Path(path).suffix.lower() not in ts_exts:
            continue

        file_path = project_dir / path
        if not file_path.exists():
            # Expected on pre-implementer manifest validation: the
            # architect declares paths the implementer hasn't yet written.
            # Log at INFO so an operator grepping the orchestrator logs
            # can distinguish "check actively skipped" from "check ran
            # and found nothing". A misconfigured project_dir silently
            # skipping every entry point is a real failure mode; this
            # line is the only breadcrumb.
            log.info(
                "type_only_exports: skipping entry_point[%d] — source "
                "path %s not found under %s",
                idx, path, project_dir,
            )
            continue

        classification = classify_exports(file_path, project_root=project_dir)
        type_only_names: list[str] = []
        for name in exports:
            if classification.classify(name) == "type_only":
                type_only_names.append(name)

        if type_only_names:
            findings.append(TypeOnlyFinding(
                entry_point_idx=idx,
                path=path,
                type_only_names=tuple(type_only_names),
            ))

    return findings
