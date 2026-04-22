"""Tests for the architect-manifest ``.js``→``.ts`` import-path normalizer.

Motivated by the recurring Import Contract gate failure on budget-brain-v2
runs (e614a176, 4736bbd9, etc.) where the architect declared ``.js``
specifiers against ``.ts`` source files and vitest could not resolve
them under the project's configuration.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from darkfactory.adapters.typescript.import_path_normalize import (
    normalize_manifest_import_paths,
)


def _write_manifest(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _read_manifest(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_rewrites_js_specifier_when_path_is_ts(tmp_path: Path) -> None:
    """The canonical budget-brain-v2 pattern — .ts path, .js specifier."""
    proj = tmp_path
    manifest = proj / ".dark-factory/architecture/spec-manifest.yaml"
    _write_manifest(manifest, {
        "test_integration": {
            "module_system": "esm",
            "import_extension": ".js",
            "entry_points": [
                {
                    "path": "apps/api/src/db/index.ts",
                    "specifier": "../../../apps/api/src/db/index.js",
                    "exports": ["db"],
                },
            ],
        },
    })

    rewrites = normalize_manifest_import_paths(manifest, proj)

    assert len(rewrites) == 1
    assert rewrites[0].entry_path == "apps/api/src/db/index.ts"
    assert rewrites[0].old_specifier == "../../../apps/api/src/db/index.js"
    assert rewrites[0].new_specifier == "../../../apps/api/src/db/index.ts"

    doc = _read_manifest(manifest)
    assert (
        doc["test_integration"]["entry_points"][0]["specifier"]
        == "../../../apps/api/src/db/index.ts"
    )


def test_rewrites_when_ts_source_not_yet_on_disk(tmp_path: Path) -> None:
    """Normalizer runs during architect lock, before implementer writes files.

    The architect's ``path`` field is authoritative — do NOT require the
    .ts file to exist on disk. A prior revision gated rewrites on
    ``abs_source.is_file()`` which silently no-op'd at this phase.
    """
    proj = tmp_path
    # Deliberately do NOT create the .ts file — the implementer hasn't run.
    manifest = proj / ".dark-factory/architecture/spec-manifest.yaml"
    _write_manifest(manifest, {
        "test_integration": {
            "entry_points": [
                {
                    "path": "apps/api/src/db/index.ts",
                    "specifier": "../../../apps/api/src/db/index.js",
                    "exports": ["db"],
                },
            ],
        },
    })

    rewrites = normalize_manifest_import_paths(manifest, proj)

    assert len(rewrites) == 1
    assert rewrites[0].new_specifier == "../../../apps/api/src/db/index.ts"


def test_leaves_specifier_alone_when_js_file_exists_too(tmp_path: Path) -> None:
    """Dual-shipped .js + .ts — specifier is intentional, don't rewrite."""
    proj = tmp_path
    src_dir = proj / "apps/api/src/db"
    src_dir.mkdir(parents=True)
    (src_dir / "index.ts").write_text("// ts\n", encoding="utf-8")
    (src_dir / "index.js").write_text("// compiled\n", encoding="utf-8")

    manifest = proj / ".dark-factory/architecture/spec-manifest.yaml"
    _write_manifest(manifest, {
        "test_integration": {
            "entry_points": [
                {
                    "path": "apps/api/src/db/index.ts",
                    "specifier": "../../../apps/api/src/db/index.js",
                    "exports": ["db"],
                },
            ],
        },
    })

    rewrites = normalize_manifest_import_paths(manifest, proj)
    assert rewrites == []
    doc = _read_manifest(manifest)
    assert (
        doc["test_integration"]["entry_points"][0]["specifier"]
        == "../../../apps/api/src/db/index.js"
    )


def test_rewrites_even_for_hallucinated_path(tmp_path: Path) -> None:
    """Architect-declared path is authoritative; don't second-guess it here.

    If the path is hallucinated (the implementer never writes it), the
    Import Contract gate will surface that as a separate failure with
    an accurate message. Not the normalizer's job.
    """
    proj = tmp_path
    manifest = proj / ".dark-factory/architecture/spec-manifest.yaml"
    _write_manifest(manifest, {
        "test_integration": {
            "entry_points": [
                {
                    "path": "apps/api/src/db/missing.ts",
                    "specifier": "../../../apps/api/src/db/missing.js",
                    "exports": ["db"],
                },
            ],
        },
    })

    rewrites = normalize_manifest_import_paths(manifest, proj)
    assert len(rewrites) == 1
    assert rewrites[0].new_specifier == "../../../apps/api/src/db/missing.ts"


def test_no_rewrite_when_source_already_js(tmp_path: Path) -> None:
    """JS source + JS specifier — nothing to normalize."""
    proj = tmp_path
    source = proj / "apps/api/src/db/index.js"
    source.parent.mkdir(parents=True)
    source.write_text("module.exports = {};\n", encoding="utf-8")

    manifest = proj / ".dark-factory/architecture/spec-manifest.yaml"
    _write_manifest(manifest, {
        "test_integration": {
            "entry_points": [
                {
                    "path": "apps/api/src/db/index.js",
                    "specifier": "../../../apps/api/src/db/index.js",
                    "exports": ["db"],
                },
            ],
        },
    })

    rewrites = normalize_manifest_import_paths(manifest, proj)
    assert rewrites == []


def test_no_rewrite_when_specifier_already_ts(tmp_path: Path) -> None:
    """TS source + TS specifier — idempotent; no-op."""
    proj = tmp_path
    source = proj / "apps/api/src/db/index.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const db = {};\n", encoding="utf-8")

    manifest = proj / ".dark-factory/architecture/spec-manifest.yaml"
    _write_manifest(manifest, {
        "test_integration": {
            "entry_points": [
                {
                    "path": "apps/api/src/db/index.ts",
                    "specifier": "../../../apps/api/src/db/index.ts",
                    "exports": ["db"],
                },
            ],
        },
    })

    rewrites = normalize_manifest_import_paths(manifest, proj)
    assert rewrites == []


def test_multiple_entries_rewritten_independently(tmp_path: Path) -> None:
    proj = tmp_path
    for stem in ("index", "tenant-context", "schema/helpers", "health-check"):
        p = proj / "apps/api/src/db" / f"{stem}.ts"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("export {};\n", encoding="utf-8")

    manifest = proj / ".dark-factory/architecture/spec-manifest.yaml"
    _write_manifest(manifest, {
        "test_integration": {
            "entry_points": [
                {
                    "path": "apps/api/src/db/index.ts",
                    "specifier": "../../../apps/api/src/db/index.js",
                    "exports": ["db"],
                },
                {
                    "path": "apps/api/src/db/tenant-context.ts",
                    "specifier": "../../../apps/api/src/db/tenant-context.js",
                    "exports": ["tenantContext"],
                },
                {
                    "path": "apps/api/src/db/schema/helpers.ts",
                    "specifier": "../../../apps/api/src/db/schema/helpers.js",
                    "exports": ["timestamps"],
                },
                {
                    "path": "apps/api/src/db/health-check.ts",
                    "specifier": "../../../apps/api/src/db/health-check.js",
                    "exports": ["registerDatabaseHealthCheck"],
                },
            ],
        },
    })

    rewrites = normalize_manifest_import_paths(manifest, proj)
    assert len(rewrites) == 4

    doc = _read_manifest(manifest)
    for ep in doc["test_integration"]["entry_points"]:
        assert ep["specifier"].endswith(".ts"), (
            f"expected .ts ending, got {ep['specifier']}"
        )


def test_no_test_integration_silent_no_op(tmp_path: Path) -> None:
    proj = tmp_path
    manifest = proj / ".dark-factory/architecture/spec-manifest.yaml"
    _write_manifest(manifest, {"file_map": []})

    rewrites = normalize_manifest_import_paths(manifest, proj)
    assert rewrites == []


def test_empty_entry_points_silent_no_op(tmp_path: Path) -> None:
    proj = tmp_path
    manifest = proj / ".dark-factory/architecture/spec-manifest.yaml"
    _write_manifest(manifest, {"test_integration": {"entry_points": []}})

    rewrites = normalize_manifest_import_paths(manifest, proj)
    assert rewrites == []


def test_missing_manifest_silent_no_op(tmp_path: Path) -> None:
    proj = tmp_path
    manifest = proj / ".dark-factory/architecture/does-not-exist.yaml"

    rewrites = normalize_manifest_import_paths(manifest, proj)
    assert rewrites == []


def test_malformed_yaml_silent_no_op(tmp_path: Path) -> None:
    proj = tmp_path
    manifest = proj / ".dark-factory/architecture/spec-manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("not: [valid: yaml: at: all\n", encoding="utf-8")

    rewrites = normalize_manifest_import_paths(manifest, proj)
    assert rewrites == []


def test_mtime_unchanged_when_no_rewrites(tmp_path: Path) -> None:
    """Idempotent — no-op pass shouldn't re-write the file."""
    proj = tmp_path
    source = proj / "apps/api/src/db/index.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const db = {};\n", encoding="utf-8")

    manifest = proj / ".dark-factory/architecture/spec-manifest.yaml"
    _write_manifest(manifest, {
        "test_integration": {
            "entry_points": [
                {
                    "path": "apps/api/src/db/index.ts",
                    "specifier": "../../../apps/api/src/db/index.ts",
                    "exports": ["db"],
                },
            ],
        },
    })
    mtime_before = manifest.stat().st_mtime_ns

    normalize_manifest_import_paths(manifest, proj)

    assert manifest.stat().st_mtime_ns == mtime_before
