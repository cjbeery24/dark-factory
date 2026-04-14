"""Manifest structural-equivalence harness for A4 (architect prompt split).

**Problem A4 creates:** A4 splits ``agents/architect.md`` into core + per-
adapter addenda. The architect runs against both prompts and we need to
prove the manifests are *meaningfully* the same. Byte-equality is
hopeless against a sampling LLM. Eyeballing two YAML files is not
regression-proof.

**Solution this harness provides:** a structural diff over the
``ArchitectureManifest`` dataclass (or its YAML equivalent) that:

1. Flattens the manifest into a dotted-path → value map.
2. Bins each path as ``LOAD_BEARING`` (must match) or ``TOLERATED``
   (whitespace, prose rephrasing, ordering noise).
3. Returns a ``StructuralDiff`` with separate counts for load-bearing
   vs tolerated diffs, and a ``is_equivalent`` boolean.

**The tolerance policy** (per plan A4 acceptance):

- LOAD_BEARING: ``commands.*``, ``runtime.*`` (except notes/rationale),
  ``services.*``, ``tier1_amendment_ops.*``, ``layout.*``, ``file_map``,
  ``data_models``, ``api_contracts``, ``dependencies``, ``environment``,
  ``language``, ``test_integration.*`` (structural fields only).
- TOLERATED: any path ending in ``.notes`` or ``.rationale``, free-text
  ``description`` fields, and the order of items inside lists where the
  list semantics are set-like (``dependencies``, ``environment`` —
  same set of items in any order is equivalent).

If a path doesn't match either bin, it defaults to LOAD_BEARING. New
fields added to the manifest must be explicitly classified — silent drift
is the failure mode this harness is built to catch.

**Sampling — N-of-N majority:** A4's acceptance calls for "3 runs each
side; majority semantic equivalence." ``majority_equivalent`` implements
this: given lists of N manifests from each side, returns True iff
``> N/2`` pairwise comparisons are equivalent. Defaults to 3-vs-3 with
strict-majority threshold (≥ 2 of 3); A4 escalates to 5-of-5 if 3-vs-3
proves unstable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import yaml

from darkfactory.spec.manifest import ArchitectureManifest, parse_manifest


# ---------------------------------------------------------------------------
# Tolerance policy
# ---------------------------------------------------------------------------


# Path suffixes that are always tolerated (whitespace / prose noise).
# Each entry must point at a real free-text field somewhere in the
# ``ArchitectureManifest`` dataclass tree; speculative additions get
# rejected by ``test_tolerated_suffixes_are_documented``.
_TOLERATED_SUFFIXES: tuple[str, ...] = (
    ".notes",  # not currently a field — kept for forward-compat with prose-only fields the architect emits in YAML
    ".rationale",  # not currently a field — same forward-compat reason
    ".purpose",  # FileEntry.purpose, Dependency.purpose
    ".note",  # EntryPoint.note
    ".existing_summary",  # FileEntry.existing_summary
)


# Path *patterns* whose order within the list is irrelevant. Each entry
# is matched against the dotted prefix as a glob: ``*`` matches one
# segment (including bracketed indices), ``**`` is not supported. Items
# in a matched list are sorted by ``_stable_key`` before comparison.
#
# Examples:
# - ``dependencies`` matches the top-level ``dependencies`` list.
# - ``data_models[*].fields`` matches the ``fields`` list inside any
#   element of ``data_models``.
_SET_LIKE_LIST_PATHS: tuple[str, ...] = (
    "dependencies",
    "environment",
    "file_map",
    "data_models",
    "data_models[*].fields",
    "api_contracts",
    "runtime.services",
)


def _is_tolerated_path(path: str) -> bool:
    return any(path.endswith(s) for s in _TOLERATED_SUFFIXES)


def _path_matches(pattern: str, prefix: str) -> bool:
    """Match a `_SET_LIKE_LIST_PATHS` glob pattern against a flatten prefix.

    ``*`` in the pattern matches exactly one segment in the prefix where
    a segment is delimited by ``.`` or ``[…]``. Index brackets in the
    prefix (e.g. ``data_models[3].fields``) collapse to a single ``*``
    target so ``data_models[*].fields`` matches ``data_models[3].fields``.
    """
    if "*" not in pattern:
        return prefix == pattern
    # Normalize indices: data_models[3].fields → data_models[*].fields
    import re
    normalized = re.sub(r"\[\d+\]", "[*]", prefix)
    return normalized == pattern


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------


def _to_dict(obj: Any) -> Any:
    """Convert dataclass / list / dict tree to plain dict/list/scalars."""
    # Dataclass -> dict (preserve field order, recurse).
    if hasattr(obj, "__dataclass_fields__"):
        return {f: _to_dict(getattr(obj, f)) for f in obj.__dataclass_fields__}
    if isinstance(obj, list):
        return [_to_dict(item) for item in obj]
    if isinstance(obj, tuple):
        return [_to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): _to_dict(v) for k, v in obj.items()}
    return obj


# Canonical identifiers used to deterministically order set-like list
# items. Order matters: the first match wins, so put more specific keys
# first. ``method`` is paired with ``path`` (api_contracts: same path
# can repeat with different methods) — handled in ``_stable_key``.
_CANONICAL_KEYS: tuple[str, ...] = (
    "name",
    "package",
    "tool",
    "path",
    "id",
)


def _stable_key(item: Any) -> str:
    """Deterministic sort key for set-like list items.

    Items are dicts (post-_to_dict). Each set-like list element must
    expose one canonical identifier from ``_CANONICAL_KEYS``. If none is
    present we raise — silently falling back to ``repr(sorted(...))``
    sorts anonymous items by hash-like noise and would mask real diffs
    by re-pairing entries that aren't actually counterparts.

    Special case: ``api_contracts`` items are keyed on ``(method, path)``
    because the same path can host multiple verbs. Single-key sort would
    pair GET /x against POST /x and surface bogus changes.

    To opt a new set-like list in, either give its elements one of the
    canonical keys or extend ``_CANONICAL_KEYS``.
    """
    if isinstance(item, dict):
        if "method" in item and "path" in item:
            return f"method:{item['method']}|path:{item['path']}"
        for candidate in _CANONICAL_KEYS:
            if candidate in item:
                return f"{candidate}:{item[candidate]}"
        raise KeyError(
            f"set-like list item has no canonical identifier "
            f"(expected one of {_CANONICAL_KEYS} or method+path); got keys "
            f"{sorted(item.keys())!r}"
        )
    # Scalars are fine — sort by value.
    return repr(item)


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a nested dict/list tree into ``dotted.path -> scalar``.

    List indices appear as ``[i]`` segments. Set-like lists (per
    ``_SET_LIKE_LIST_PATHS``) are sorted before flattening so order
    differences don't surface as diffs.
    """
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            child = f"{prefix}.{k}" if prefix else k
            out.update(_flatten(v, child))
    elif isinstance(value, list):
        items = list(value)
        # Set-like list: sort before flattening so order isn't a diff.
        # Match against the glob catalog so nested paths
        # (data_models[*].fields) get the same treatment as top-level
        # paths (dependencies).
        if any(_path_matches(p, prefix) for p in _SET_LIKE_LIST_PATHS):
            items.sort(key=_stable_key)
        for i, item in enumerate(items):
            out.update(_flatten(item, f"{prefix}[{i}]"))
    else:
        out[prefix] = value
    return out


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


@dataclass
class PathDiff:
    path: str
    left: Any
    right: Any
    kind: str  # "added" | "removed" | "changed"


@dataclass
class StructuralDiff:
    load_bearing: list[PathDiff] = field(default_factory=list)
    tolerated: list[PathDiff] = field(default_factory=list)

    @property
    def is_equivalent(self) -> bool:
        """No load-bearing diffs == semantically equivalent."""
        return not self.load_bearing

    def summary(self) -> str:
        lines = [
            f"Structural diff: "
            f"{len(self.load_bearing)} load-bearing, "
            f"{len(self.tolerated)} tolerated."
        ]
        if self.load_bearing:
            lines.append("Load-bearing changes (FAIL):")
            for d in self.load_bearing[:25]:
                lines.append(f"  [{d.kind}] {d.path}: {d.left!r} -> {d.right!r}")
            if len(self.load_bearing) > 25:
                lines.append(f"  ... and {len(self.load_bearing) - 25} more")
        return "\n".join(lines)


def structural_diff(
    left: ArchitectureManifest | dict,
    right: ArchitectureManifest | dict,
) -> StructuralDiff:
    """Compute load-bearing / tolerated diffs between two manifests.

    Inputs may be ``ArchitectureManifest`` dataclasses or already-flat dicts
    (e.g. raw YAML-parsed dicts, useful when comparing pre/post-prompt-
    split runs without round-tripping through the parser).
    """
    left_flat = _flatten(_to_dict(left))
    right_flat = _flatten(_to_dict(right))

    diff = StructuralDiff()
    all_paths = set(left_flat) | set(right_flat)

    for path in sorted(all_paths):
        in_l, in_r = path in left_flat, path in right_flat
        if in_l and in_r:
            if left_flat[path] == right_flat[path]:
                continue
            kind = "changed"
            l_val, r_val = left_flat[path], right_flat[path]
        elif in_l:
            kind = "removed"
            l_val, r_val = left_flat[path], None
        else:
            kind = "added"
            l_val, r_val = None, right_flat[path]

        bin_target = (
            diff.tolerated if _is_tolerated_path(path) else diff.load_bearing
        )
        bin_target.append(PathDiff(path=path, left=l_val, right=r_val, kind=kind))

    return diff


def structural_diff_yaml(left_yaml: str, right_yaml: str) -> StructuralDiff:
    """Convenience: parse two YAML manifests and diff them.

    Falls through ``parse_manifest`` so dataclass shape is enforced. If
    parsing fails on either side, returns a single load-bearing diff
    naming the parse error so the harness fails loudly rather than
    silently equivalent.
    """
    l_result = parse_manifest(left_yaml)
    r_result = parse_manifest(right_yaml)

    if l_result.errors or r_result.errors:
        diff = StructuralDiff()
        for err in l_result.errors:
            diff.load_bearing.append(
                PathDiff(path="<parse:left>", left=err, right=None, kind="changed")
            )
        for err in r_result.errors:
            diff.load_bearing.append(
                PathDiff(path="<parse:right>", left=None, right=err, kind="changed")
            )
        return diff

    assert l_result.manifest is not None and r_result.manifest is not None
    return structural_diff(l_result.manifest, r_result.manifest)


# ---------------------------------------------------------------------------
# N-of-N majority equivalence (A4 sampling policy)
# ---------------------------------------------------------------------------


@dataclass
class MajorityResult:
    sample_size: int
    equivalent_pairs: int
    threshold: int
    is_equivalent: bool
    per_pair_diffs: list[StructuralDiff] = field(default_factory=list)


def majority_equivalent(
    lefts: list[ArchitectureManifest],
    rights: list[ArchitectureManifest],
    *,
    threshold: int | None = None,
) -> MajorityResult:
    """A4-style "majority semantic equivalence across N runs".

    ``lefts`` and ``rights`` must be the same length N. Each pair (l_i, r_i)
    is diffed; if at least ``threshold`` pairs are load-bearing-equivalent,
    the overall result is equivalent.

    Default threshold: strict majority (``N // 2 + 1``). A4 calls this for
    N=3 (threshold=2). If 3-vs-3 proves unstable, escalate to N=5
    (threshold=3) before declaring the harness broken.
    """
    if len(lefts) != len(rights):
        raise ValueError(
            f"lefts ({len(lefts)}) and rights ({len(rights)}) "
            f"must be equal-length samples"
        )
    if not lefts:
        raise ValueError("samples must be non-empty")

    n = len(lefts)
    th = threshold if threshold is not None else n // 2 + 1

    diffs = [structural_diff(l, r) for l, r in zip(lefts, rights)]
    equivalent = sum(1 for d in diffs if d.is_equivalent)

    return MajorityResult(
        sample_size=n,
        equivalent_pairs=equivalent,
        threshold=th,
        is_equivalent=equivalent >= th,
        per_pair_diffs=diffs,
    )
