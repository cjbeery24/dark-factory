"""Deterministic TypeScript/JavaScript export extraction via regex.

Extracts named exports from source files without requiring a full AST
parser.  Used at gate time to verify the Implementer honored the
architecture manifest's entry_point export contract.

Supported patterns (covers ~95% of real-world TS/JS):
  export function NAME
  export const NAME / export let NAME / export var NAME
  export class NAME
  export type NAME / export interface NAME / export enum NAME
  export { A, B, C }               (local re-exports)
  export { A, B, C } from '...'    (named re-exports)
  export default ...                (mapped to "default")

Flagged but not resolved:
  export * from '...'              (star re-exports — would require
                                    transitive resolution)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Named declaration exports:
#   export function foo
#   export const foo
#   export class Foo
#   export type Foo / export interface Foo / export enum Foo
#   export async function foo
_NAMED_DECL_RE = re.compile(
    r"^export\s+(?:async\s+)?"
    r"(?:function\*?|const|let|var|class|type|interface|enum)"
    r"\s+(\w+)",
    re.MULTILINE,
)

# Brace exports (with or without `from`):
#   export { A, B, C }
#   export { A, B as C } from './module'
#   export { type A, B } from './module'
#   export type { A, B } from './module'
_BRACE_RE = re.compile(
    r"^export\s*(?:type\s+)?\{([^}]+)\}",
    re.MULTILINE,
)

# Default export:
#   export default function ...
#   export default class ...
#   export default ...
_DEFAULT_RE = re.compile(
    r"^export\s+default\b",
    re.MULTILINE,
)

# Star re-export:
#   export * from './module'
#   export * as ns from './module'
_STAR_RE = re.compile(
    r"^export\s+\*\s+(?:as\s+(\w+)\s+)?from\s+['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)


def _strip_comments(source: str) -> str:
    """Remove single-line (//) and multi-line (/* */) comments.

    Best-effort heuristic that handles the common cases without a full
    parser.  Does NOT protect string or template literals — a ``//``
    inside a string will still be stripped.  Acceptable for export
    extraction where false positives from mangled strings are extremely
    unlikely (patterns anchor on ``^export`` at line start).
    """
    # Remove multi-line comments first (non-greedy).
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    # Remove single-line comments, but not inside strings.
    # Simple approach: remove // that aren't preceded by : (URLs).
    source = re.sub(r"(?<!:)//.*$", "", source, flags=re.MULTILINE)
    return source


def _parse_brace_names(brace_content: str) -> list[str]:
    """Extract symbol names from a brace export list.

    Handles: ``A, B, C`` and ``A as B, type C, D as E``.
    Returns the *exported* name (the ``as`` alias if present, otherwise
    the original name).  Skips ``type``-only re-exports.
    """
    names: list[str] = []
    for item in brace_content.split(","):
        item = item.strip()
        if not item:
            continue
        # "type Foo" is a type-only export — still a valid import name
        # (TypeScript allows `import { type Foo } from ...`).  Extract
        # the name after the `type` keyword.
        if item.startswith("type ") and " as " not in item:
            type_name = item[len("type "):].strip()
            if type_name:
                names.append(type_name)
            continue
        # Handle "A as B" — exported name is B.
        if " as " in item:
            parts = item.split(" as ")
            exported = parts[-1].strip()
            # Filter out "type" keyword prefix on the original
            if exported:
                names.append(exported)
        else:
            names.append(item.strip())
    return names


@dataclass
class ExportResult:
    """Result of extracting exports from a single source file."""
    path: str
    exports: list[str] = field(default_factory=list)
    has_default: bool = False
    star_reexports: list[str] = field(default_factory=list)


def extract_exports(file_path: Path) -> ExportResult:
    """Extract named exports from a TypeScript/JavaScript source file.

    Returns an ``ExportResult`` with the list of exported symbol names.
    Does not resolve ``export * from`` — those are recorded in
    ``star_reexports`` for the caller to handle.
    """
    result = ExportResult(path=str(file_path))

    if not file_path.exists():
        log.warning("extract_exports: file does not exist: %s", file_path)
        return result

    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("extract_exports: could not read %s: %s", file_path, exc)
        return result

    source = _strip_comments(source)

    seen: set[str] = set()

    # Named declaration exports.
    for m in _NAMED_DECL_RE.finditer(source):
        name = m.group(1)
        if name not in seen:
            result.exports.append(name)
            seen.add(name)

    # Brace exports.
    for m in _BRACE_RE.finditer(source):
        for name in _parse_brace_names(m.group(1)):
            if name not in seen:
                result.exports.append(name)
                seen.add(name)

    # Default export.
    if _DEFAULT_RE.search(source):
        result.has_default = True
        if "default" not in seen:
            result.exports.append("default")
            seen.add("default")

    # Star re-exports (flagged, not resolved).
    for m in _STAR_RE.finditer(source):
        alias = m.group(1)  # e.g. "ns" from "export * as ns from ..."
        module = m.group(2)
        if alias:
            # "export * as ns from '...'" — ns is a named export.
            if alias not in seen:
                result.exports.append(alias)
                seen.add(alias)
        else:
            # Plain "export * from '...'" — cannot resolve without reading the target.
            result.star_reexports.append(module)

    result.exports.sort()
    return result


@dataclass
class ContractViolation:
    """A mismatch between manifest-declared exports and actual exports."""
    entry_point_idx: int
    path: str
    declared: list[str]
    actual: list[str]
    missing: list[str]       # declared but not found in file
    unexpected: list[str]    # found in file but not declared
    star_reexports: list[str]  # unresolved export * targets


def verify_export_contract(
    entry_points: list,
    project_dir: Path,
) -> list[ContractViolation]:
    """Verify that implemented files export the symbols declared in the manifest.

    Parameters
    ----------
    entry_points
        List of ``EntryPoint`` objects from the manifest's
        ``test_integration.entry_points``.
    project_dir
        Root of the target project (entry_point paths are relative to this).

    Returns
    -------
    list[ContractViolation]
        One violation per entry point whose actual exports don't match
        the declared exports.  Empty list = all contracts honored.
    """
    violations: list[ContractViolation] = []

    for idx, ep in enumerate(entry_points):
        if not ep.path:
            continue

        # Skip entry points with empty declared exports — the Architect
        # left this unspecified (e.g. barrel file).  Gate-time extraction
        # will fill these in.
        if not ep.exports:
            continue

        file_path = project_dir / ep.path
        extracted = extract_exports(file_path)

        actual_set = set(extracted.exports)
        declared_set = set(ep.exports)

        missing = sorted(declared_set - actual_set)
        unexpected = sorted(actual_set - declared_set)

        if missing or unexpected:
            violations.append(ContractViolation(
                entry_point_idx=idx,
                path=ep.path,
                declared=sorted(ep.exports),
                actual=sorted(extracted.exports),
                missing=missing,
                unexpected=unexpected,
                star_reexports=extracted.star_reexports,
            ))

    return violations


def format_violations(violations: list[ContractViolation]) -> str:
    """Format contract violations as human-readable feedback.

    This output is injected into the Oracle/Implementer iteration
    feedback so the Implementer knows exactly what to fix.
    """
    if not violations:
        return ""

    lines = ["## Export Contract Violations", ""]
    for v in violations:
        lines.append(f"**Entry point `{v.path}`:**")
        if v.missing:
            lines.append(
                f"  - Missing exports (declared in manifest but not found in file): "
                f"{', '.join(f'`{n}`' for n in v.missing)}"
            )
        if v.unexpected:
            lines.append(
                f"  - Unexpected exports (in file but not in manifest): "
                f"{', '.join(f'`{n}`' for n in v.unexpected)}"
            )
        if v.star_reexports:
            lines.append(
                f"  - Unresolved `export *` from: "
                f"{', '.join(f'`{m}`' for m in v.star_reexports)} "
                f"(missing symbols may come from these modules)"
            )
        lines.append(
            f"  - **Fix:** Rename or add exports to match the manifest contract. "
            f"The blind test scenarios import `{', '.join(f'{n}' for n in v.declared)}` "
            f"from this entry point — mismatched names will cause import failures."
        )
        lines.append("")

    return "\n".join(lines)
