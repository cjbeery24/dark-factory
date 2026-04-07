"""Phase 6.4 — depends_on soft check helper."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from darkfactory.plan.depends_on import missing_dependencies, parse_depends_on
from darkfactory.plan.state import ShippedSpec, append_shipped


def _write_spec(project: Path, name: str, depends_on: str | None) -> Path:
    spec_file = project / ".dark-factory" / "specs" / f"{name}.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    body = "# Spec\n"
    if depends_on is not None:
        body += f"depends-on: [{depends_on}]\n"
    spec_file.write_text(body)
    return spec_file


def _seed_shipped(project: Path, name: str) -> None:
    append_shipped(
        project,
        ShippedSpec(
            spec_id=name, spec_type="feature",
            shipped_at=datetime.now(timezone.utc),
            env_hash_at_ship="hash", delta_filename=None,
            scenario_archive_path=f".dark-factory/shipped-scenarios/{name}",
            spec_origin=name,
        ),
    )


def test_parse_no_depends_on(tmp_path: Path) -> None:
    f = _write_spec(tmp_path, "x", None)
    assert parse_depends_on(f) == []


def test_parse_empty_depends_on(tmp_path: Path) -> None:
    f = _write_spec(tmp_path, "x", "")
    assert parse_depends_on(f) == []


def test_parse_single(tmp_path: Path) -> None:
    f = _write_spec(tmp_path, "x", "alpha")
    assert parse_depends_on(f) == ["alpha"]


def test_parse_multiple(tmp_path: Path) -> None:
    f = _write_spec(tmp_path, "x", "alpha, beta, gamma")
    assert parse_depends_on(f) == ["alpha", "beta", "gamma"]


def test_missing_deps_none_declared(tmp_path: Path) -> None:
    f = _write_spec(tmp_path, "x", None)
    missing, unres = missing_dependencies(f, tmp_path)
    assert missing == [] and unres == []


def test_missing_deps_all_shipped(tmp_path: Path) -> None:
    f = _write_spec(tmp_path, "x", "alpha, beta")
    _seed_shipped(tmp_path, "alpha")
    _seed_shipped(tmp_path, "beta")
    missing, unres = missing_dependencies(f, tmp_path)
    assert missing == [] and unres == []


def test_missing_deps_some_unshipped(tmp_path: Path) -> None:
    f = _write_spec(tmp_path, "x", "alpha, beta")
    _seed_shipped(tmp_path, "alpha")
    missing, unres = missing_dependencies(f, tmp_path)
    assert missing == ["beta"]
    assert unres == []


def test_missing_deps_id_form_reported_unresolvable(tmp_path: Path) -> None:
    f = _write_spec(tmp_path, "x", "F-1, alpha")
    _seed_shipped(tmp_path, "alpha")
    missing, unres = missing_dependencies(f, tmp_path)
    assert missing == []
    assert unres == ["F-1"]


def test_parse_ignores_non_bracketed_form(tmp_path: Path) -> None:
    """Bare-scalar YAML form is NOT currently accepted — this test pins
    that behavior so any future broadening is an intentional change that
    must also update the plan parser in lockstep."""
    spec = tmp_path / ".dark-factory" / "specs" / "x.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("depends-on: alpha\n")
    assert parse_depends_on(spec) == []  # bracket form required
