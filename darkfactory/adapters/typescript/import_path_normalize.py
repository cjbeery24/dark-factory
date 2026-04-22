"""Normalize ``.js``→``.ts`` specifier mismatches in the architect manifest.

**Problem.** The architect emits ``test_integration.entry_points[]`` with
specifiers like ``../../../apps/api/src/db/index.js`` paired with a
``path`` of ``apps/api/src/db/index.ts``. The ``.js`` suffix is a valid
TypeScript ESM convention *when* the project is configured for
``moduleResolution: NodeNext`` and the test runner participates in the
``.js``→``.ts`` redirect. The runs on budget-brain-v2 since
2026-04-18 all fail the Import Contract gate with::

    Error: Failed to load url ../../../apps/api/src/db/index.js (contract-00)

because vitest under that project's configuration cannot resolve the
``.js`` specifier to the on-disk ``.ts`` source.

**This module.** For each entry where ``path`` ends in ``.ts`` but the
``specifier`` ends in ``.js``, check the filesystem: if the ``.ts``
source exists at the path the specifier resolves to and the ``.js``
variant does not, rewrite the specifier in-place to match the on-disk
extension. Deterministic, filesystem-backed, reversible — the test
runner now receives a specifier that resolves against a real file, and
the Import Contract gate's scratch synthesis produces a working import.

**Why not fix vitest instead.** The pipeline has no safe way to edit the
target project's ``vitest.config.ts`` from inside the orchestrator —
that file is the user's contract. Adjusting the manifest is a scoped,
file-extension rewrite on an artifact Dark Factory owns end-to-end.

**Alignment with the scenario architect.** Scenarios are generated
*after* the manifest is normalized — the Scenario Architect reads the
same ``test_integration.entry_points`` block and emits scenario imports
using the normalized specifier. The gate, the scenarios, and the
scratch-file verifier therefore all agree on what the import specifier
should be.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


@dataclass
class ImportPathRewrite:
    """One specifier rewrite record emitted by ``normalize_manifest_import_paths``.

    ``entry_path`` is the ``test_integration.entry_points[i].path`` value —
    always relative to project root. ``old_specifier`` / ``new_specifier``
    are the before/after specifiers (relative paths with ``../``). Used
    in log output and returned to callers for event-emission.
    """
    entry_path: str
    old_specifier: str
    new_specifier: str
    reason: str


def _resolve_specifier(specifier: str) -> str:
    """Strip leading ``../`` segments to get a project-root-relative path.

    The Import Contract gate synthesizes scratch files at various depths
    below the project root (typically ``.dark-factory/scratch/.../<spec>/``),
    so architect-authored specifiers carry a variable number of leading
    ``../`` segments. This helper walks off every leading ``..`` and
    returns the remainder as a project-root-relative string — good
    enough for on-disk existence checks, which is all this module needs.
    Non-parent-prefixed paths are returned verbatim.
    """
    parts = specifier.split("/")
    i = 0
    while i < len(parts) and parts[i] == "..":
        i += 1
    if i == 0:
        return specifier
    return "/".join(parts[i:])


def _specifier_with_extension(specifier: str, new_ext: str) -> str:
    """Swap the specifier's final ``.ext`` for ``new_ext``.

    ``new_ext`` must include the leading dot. When the specifier has no
    recognizable extension the original string is returned unchanged.
    """
    idx = specifier.rfind(".")
    slash_idx = specifier.rfind("/")
    if idx <= slash_idx:
        return specifier
    return specifier[:idx] + new_ext


def normalize_manifest_import_paths(
    manifest_path: Path,
    project_root: Path,
) -> list[ImportPathRewrite]:
    """Rewrite ``.js`` specifiers to ``.ts`` when that's the actual on-disk file.

    Returns the list of rewrites applied (empty when no changes were
    needed). Silent no-op when:

    * Manifest has no ``test_integration`` block (non-Node specs).
    * ``test_integration.entry_points`` is absent or empty.
    * YAML parsing fails (we don't second-guess a broken manifest; the
      architect retry loop or downstream gate will surface the error).
    * Every entry already has a specifier that matches the on-disk
      source (the common steady-state case).

    Files are written back only when at least one rewrite occurred, so
    the manifest's mtime is not perturbed by a no-op pass.
    """
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return []

    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return []

    if not isinstance(doc, dict):
        return []

    ti = doc.get("test_integration")
    if not isinstance(ti, dict):
        return []

    entry_points = ti.get("entry_points")
    if not isinstance(entry_points, list) or not entry_points:
        return []

    rewrites: list[ImportPathRewrite] = []
    for entry in entry_points:
        if not isinstance(entry, dict):
            continue

        source_path = str(entry.get("path") or "")
        specifier = str(entry.get("specifier") or "")
        if not source_path or not specifier:
            continue

        src_ext = Path(source_path).suffix
        spec_ext = Path(specifier).suffix

        # Only rewrite the .js-specifier-on-.ts-source pattern. Other
        # combinations (.ts specifier on .ts source, .js on .js, etc.)
        # are either correct or outside this module's remit.
        if src_ext != ".ts" or spec_ext != ".js":
            continue

        abs_source = project_root / source_path
        if not abs_source.is_file():
            # Architect declared a path that doesn't exist. Skip —
            # this is a separate architect bug the Import Contract
            # gate will surface with a proper failure.
            continue

        # Check the ``.js`` variant actually isn't present. If a project
        # legitimately ships both ``foo.js`` and ``foo.ts`` (rare but
        # legal), the original specifier is intentional — don't touch.
        resolved = _resolve_specifier(specifier)
        abs_js = project_root / resolved
        if abs_js.is_file():
            continue

        new_specifier = _specifier_with_extension(specifier, ".ts")
        entry["specifier"] = new_specifier
        rewrites.append(ImportPathRewrite(
            entry_path=source_path,
            old_specifier=specifier,
            new_specifier=new_specifier,
            reason=(
                f"source is {source_path} (.ts) but specifier declared .js; "
                f"no .js file exists at the resolved path"
            ),
        ))

    if rewrites:
        manifest_path.write_text(
            yaml.safe_dump(doc, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        for r in rewrites:
            log.info(
                "import_path_normalize: %s %s → %s (%s)",
                r.entry_path, r.old_specifier, r.new_specifier, r.reason,
            )

    return rewrites
