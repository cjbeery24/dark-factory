"""Phase 4.0 — spec_type frontmatter backfill."""

from pathlib import Path

import pytest

from darkfactory.spec.migrate_frontmatter import (
    MigrationResult,
    migrate_all,
    migrate_one,
)


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _with_metadata(title: str, extra: str = "") -> str:
    return (
        f"# {title}\n\n"
        "<!-- dark-factory-metadata\n"
        "feature-id: F-1\n"
        f"{extra}"
        "-->\n\n"
        "## Problem\n"
    )


# --- classification ---------------------------------------------------


def test_classifies_bootstrap_by_filename(tmp_path):
    p = _write(tmp_path, "2026-03-28-api-bootstrap.md", _with_metadata("API Bootstrap"))
    r = migrate_one(p, apply=True, force=False)
    assert r.inferred == "bootstrap"
    assert r.applied
    assert "spec_type: bootstrap" in p.read_text()


def test_classifies_bugfix_by_h1_when_filename_silent(tmp_path):
    """Dated filenames with no keyword fall back to H1 — the common
    '2026-04-01-login.md' titled 'Fix login regression' case."""
    p = _write(tmp_path, "2026-04-01-login.md", _with_metadata("Fix login regression"))
    r = migrate_one(p, apply=True, force=False)
    assert r.inferred == "bugfix"
    assert "spec_type: bugfix" in p.read_text()


def test_bugfix_beats_bootstrap_when_both_match(tmp_path):
    p = _write(tmp_path, "fix-bootstrap-crash.md", _with_metadata("Fix bootstrap crash"))
    r = migrate_one(p, apply=True, force=False)
    assert r.inferred == "bugfix"


def test_classifies_refactor(tmp_path):
    p = _write(tmp_path, "2026-04-01-refactor-auth.md", _with_metadata("Refactor auth layer"))
    r = migrate_one(p, apply=True, force=False)
    assert r.inferred == "refactor"


def test_migrate_in_filename_is_feature_not_refactor(tmp_path):
    """Regression: 'migrate' used to be a refactor token but it false-
    positives on feature specs ('migrate-to-postgres', this very tool).
    Removed from the bucket; these now classify as feature."""
    p = _write(tmp_path, "2026-04-07-migrate-to-postgres.md",
               _with_metadata("Migrate to Postgres"))
    r = migrate_one(p, apply=True, force=False)
    assert r.inferred == "feature"


def test_patch_in_filename_is_feature_not_bugfix(tmp_path):
    """Regression: 'patch' false-positives on 'monkey-patch' refactors
    and 'patch-release' features. Removed from bugfix bucket."""
    p = _write(tmp_path, "2026-04-07-monkey-patch-hook.md",
               _with_metadata("Monkey patch hook"))
    r = migrate_one(p, apply=True, force=False)
    assert r.inferred == "feature"


def test_defaults_to_feature(tmp_path):
    p = _write(tmp_path, "2026-04-01-dashboard-shell.md", _with_metadata("Dashboard shell"))
    r = migrate_one(p, apply=True, force=False)
    assert r.inferred == "feature"
    assert "defaulted to feature" in r.reason


# --- idempotence / safety --------------------------------------------


def test_skips_existing_spec_type(tmp_path):
    p = _write(
        tmp_path, "2026-04-01-x.md",
        _with_metadata("X", extra="spec_type: bootstrap\n"),
    )
    r = migrate_one(p, apply=True, force=False)
    assert not r.applied
    assert r.previous == "bootstrap"
    assert "already has" in r.reason


def test_force_overwrites_existing_spec_type(tmp_path):
    p = _write(
        tmp_path, "fix-db.md",
        _with_metadata("Fix db", extra="spec_type: feature\n"),
    )
    r = migrate_one(p, apply=True, force=True)
    assert r.applied
    assert r.inferred == "bugfix"
    text = p.read_text()
    # Old value must be gone, new value present exactly once.
    assert text.count("spec_type:") == 1
    assert "spec_type: bugfix" in text


def test_dry_run_does_not_write(tmp_path):
    p = _write(tmp_path, "2026-04-01-api-bootstrap.md", _with_metadata("API Bootstrap"))
    original = p.read_text()
    r = migrate_one(p, apply=False, force=False)
    assert r.inferred == "bootstrap"
    assert not r.applied
    assert p.read_text() == original


def test_missing_metadata_block_errors_cleanly(tmp_path):
    p = _write(tmp_path, "bad.md", "# No metadata\n\nJust body.\n")
    with pytest.raises(ValueError):
        migrate_one(p, apply=True, force=False)


# --- migrate_all ------------------------------------------------------


def test_migrate_all_walks_dir(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    _write(specs, "a-bootstrap.md", _with_metadata("A Bootstrap"))
    _write(specs, "b-feature.md", _with_metadata("B"))
    _write(specs, "c-fix.md", _with_metadata("C fix"))
    results = migrate_all(specs, apply=True, force=False)
    by_name = {r.path.name: r for r in results}
    assert by_name["a-bootstrap.md"].inferred == "bootstrap"
    assert by_name["b-feature.md"].inferred == "feature"
    assert by_name["c-fix.md"].inferred == "bugfix"


def test_migrate_all_raises_if_dir_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        migrate_all(tmp_path / "nope", apply=False, force=False)


def test_crlf_input_normalized(tmp_path):
    """Specs authored on Windows arrive with CRLF line endings. The
    tool must normalize on read and write LF — mixing \\r\\n and \\n
    inside a rewritten file confuses every downstream parser."""
    body = _with_metadata("API Bootstrap").replace("\n", "\r\n")
    p = tmp_path / "2026-04-01-api-bootstrap.md"
    p.write_bytes(body.encode("utf-8"))
    r = migrate_one(p, apply=True, force=False)
    assert r.inferred == "bootstrap"
    written = p.read_text()
    assert "\r" not in written
    assert "spec_type: bootstrap" in written


def test_force_preserves_field_ordering(tmp_path):
    """The new spec_type line is appended just before the closing -->.
    On --force over an existing entry, the file should still have
    feature-id before spec_type (not spec_type prepended above everything).
    """
    p = _write(
        tmp_path, "fix-thing.md",
        _with_metadata("Fix thing", extra="spec_type: feature\nplan: p1\n"),
    )
    r = migrate_one(p, apply=True, force=True)
    assert r.applied
    text = p.read_text()
    # feature-id appears before spec_type in the output.
    assert text.index("feature-id") < text.index("spec_type")
    # plan still present, spec_type line exactly once.
    assert "plan: p1" in text
    assert text.count("spec_type:") == 1


def test_migrated_file_still_parses_via_parse_spec_type(tmp_path):
    """End-to-end: backfill then read back via the Phase 3.1 parser."""
    from darkfactory.config.profiles import parse_spec_type
    p = _write(tmp_path, "2026-04-01-api-bootstrap.md", _with_metadata("API Bootstrap"))
    migrate_one(p, apply=True, force=False)
    assert parse_spec_type(p) == "bootstrap"
