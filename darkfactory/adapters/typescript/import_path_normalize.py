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
``specifier`` ends in ``.js``, rewrite the specifier's extension to
``.ts`` in-place. The architect's ``path`` field is treated as the
authoritative declaration of what the implementer will write — we do
**not** require the file to exist on disk at this phase, because the
normalizer runs during the architect lock (before the implementer has
written any source). The one filesystem guard that survives is the
dual-shipped check: if a committed ``.js`` artifact exists *next to*
the declared ``.ts`` source, the ``.js`` specifier is intentional and
we leave it alone.

**Why filesystem checks were originally wrong.** A prior revision gated
rewrites on ``abs_source.is_file()``. At architect-lock time the
``.ts`` file does not yet exist (the implementer has not run), so the
guard was structurally unsatisfiable — the normalizer silently no-op'd
on every first run, and the ``.js`` specifiers persisted into the
Import Contract gate as the exact failure mode this module was built
to prevent. The fix is to trust the manifest's ``path`` field, which
is what the Scenario Architect and ``verify_export_contract`` already
do at the same phase.

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
    """Rewrite ``.js`` specifiers to ``.ts`` when the architect's ``path`` is ``.ts``.

    Returns the list of rewrites applied (empty when no changes were
    needed). Silent no-op when:

    * Manifest has no ``test_integration`` block (non-Node specs).
    * ``test_integration.entry_points`` is absent or empty.
    * YAML parsing fails (we don't second-guess a broken manifest; the
      architect retry loop or downstream gate will surface the error).
    * Every entry already has a specifier that matches its declared
      ``path`` extension (the common steady-state case).
    * A committed ``.js`` artifact exists next to the declared ``.ts``
      source (dual-shipped — the ``.js`` specifier is intentional).

    Files are written back only when at least one rewrite occurred, so
    the manifest's mtime is not perturbed by a no-op pass.
    """
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        log.debug("import_path_normalize: manifest not readable at %s", manifest_path)
        return []

    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        log.warning(
            "import_path_normalize: manifest unparseable (%s): %s", e, manifest_path,
        )
        return []

    if not isinstance(doc, dict):
        return []

    ti = doc.get("test_integration")
    if not isinstance(ti, dict):
        log.debug(
            "import_path_normalize: no test_integration block in %s", manifest_path.name,
        )
        return []

    entry_points = ti.get("entry_points")
    if not isinstance(entry_points, list) or not entry_points:
        log.debug(
            "import_path_normalize: no entry_points in %s", manifest_path.name,
        )
        return []

    rewrites: list[ImportPathRewrite] = []
    skipped = {
        "malformed_entry": 0,
        "missing_fields": 0,
        "extensions_ok": 0,
        "dual_shipped": 0,
    }
    for entry in entry_points:
        if not isinstance(entry, dict):
            skipped["malformed_entry"] += 1
            continue

        source_path = str(entry.get("path") or "")
        specifier = str(entry.get("specifier") or "")
        if not source_path or not specifier:
            skipped["missing_fields"] += 1
            continue

        src_ext = Path(source_path).suffix
        spec_ext = Path(specifier).suffix

        # Only rewrite the .js-specifier-on-.ts-source pattern. Other
        # combinations (.ts on .ts, .js on .js, etc.) are either already
        # correct or outside this module's remit.
        if src_ext != ".ts" or spec_ext != ".js":
            skipped["extensions_ok"] += 1
            continue

        # Dual-shipped guard: if a committed .js artifact lives *next to*
        # the declared .ts source (same directory, same stem), the .js
        # specifier is deliberate (project publishes both and prefers
        # the compiled one). Use the architect's authoritative ``path``
        # field with a suffix swap — independent of the specifier's
        # relative-depth prefix, which varies with scratch-dir layout.
        abs_js_sibling = (project_root / source_path).with_suffix(".js")
        if abs_js_sibling.is_file():
            skipped["dual_shipped"] += 1
            continue

        new_specifier = _specifier_with_extension(specifier, ".ts")
        entry["specifier"] = new_specifier
        rewrites.append(ImportPathRewrite(
            entry_path=source_path,
            old_specifier=specifier,
            new_specifier=new_specifier,
            reason=(
                f"architect declared path={source_path} (.ts) with "
                f".js specifier and no committed .js artifact exists "
                f"next to the source"
            ),
        ))

    log.info(
        "import_path_normalize: %s — considered=%d rewritten=%d skipped=%d "
        "(malformed_entry=%d missing_fields=%d extensions_ok=%d dual_shipped=%d)",
        manifest_path.name,
        len(entry_points),
        len(rewrites),
        sum(skipped.values()),
        skipped["malformed_entry"],
        skipped["missing_fields"],
        skipped["extensions_ok"],
        skipped["dual_shipped"],
    )

    if rewrites:
        manifest_path.write_text(
            yaml.safe_dump(doc, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        for r in rewrites:
            log.info(
                "import_path_normalize: rewrote %s: %s → %s (%s)",
                r.entry_path, r.old_specifier, r.new_specifier, r.reason,
            )

    return rewrites
