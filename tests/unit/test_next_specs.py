"""Tests for plan/next_specs.py — runnable-next computation from the
durable, rollback-aware shipped ledger (NOT ephemeral state files).

Regression guard for the selector bug: a spec recorded in shipped.jsonl
but missing a state/*.state file (wiped on reset / fresh clone) must be
treated as shipped and never recommended as "next".
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from darkfactory.plan.next_specs import compute_next, is_shipped
from darkfactory.plan.state import ShippedSpec, append_shipped


def _ship(project: Path, spec_id: str) -> None:
    append_shipped(
        project,
        ShippedSpec(
            spec_id=spec_id,
            spec_type="feature",
            shipped_at=datetime.now(timezone.utc),
            env_hash_at_ship="h",
            delta_filename=None,
            scenario_archive_path=f".dark-factory/shipped-scenarios/{spec_id}",
            spec_origin=spec_id,
        ),
    )


def _spec(project: Path, name: str, feature_id: str, depends_on: str = "") -> None:
    d = project / ".dark-factory" / "specs"
    d.mkdir(parents=True, exist_ok=True)
    meta = (
        "<!-- dark-factory-metadata\n"
        f"feature-id: {feature_id}\n"
        f"depends-on: [{depends_on}]\n"
        "-->\n"
    )
    (d / f"{name}.md").write_text(meta + f"# {name}\n")


def _plan(project: Path, rows: list[tuple[str, str, str]]) -> None:
    """rows = (feature_id, feature_name, deps_ids_csv)."""
    d = project / ".dark-factory" / "plans"
    d.mkdir(parents=True, exist_ok=True)
    body = "# Plan\n\n| ID | Feature | Type | Depends-on | Notes |\n"
    body += "|----|---------|------|-----------|-------|\n"
    for fid, name, deps in rows:
        body += f"| {fid} | {name} | feature | {deps or '—'} | {name} |\n"
    (d / "plan.md").write_text(body)


def _scaffold(tmp_path: Path) -> Path:
    """alpha (F-1) <- beta (F-2) <- gamma (F-3)."""
    _spec(tmp_path, "alpha", "F-1", "")
    _spec(tmp_path, "beta", "F-2", "F-1")
    _spec(tmp_path, "gamma", "F-3", "F-2")
    _plan(
        tmp_path,
        [("F-1", "alpha", "—"), ("F-2", "beta", "F-1"), ("F-3", "gamma", "F-2")],
    )
    return tmp_path


def test_ledger_shipped_excluded_without_state_file(tmp_path: Path) -> None:
    """A ledger-shipped spec with NO state file is excluded + unblocks its dependent."""
    _scaffold(tmp_path)
    _ship(tmp_path, "alpha")  # shipped.jsonl only — no state/*.state written
    assert not (tmp_path / ".dark-factory" / "state").exists()

    nxt = compute_next(tmp_path, ["alpha", "beta", "gamma"])

    assert nxt.shipped_count == 1
    assert "alpha" not in nxt.runnable  # not recommended despite missing state file
    assert "alpha" not in nxt.blocked
    assert nxt.runnable == ["beta"]  # dep alpha satisfied
    assert nxt.blocked == {"gamma": ["beta"]}  # still waiting on unshipped beta


def test_next_is_first_runnable_in_dep_order(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _ship(tmp_path, "alpha")
    _ship(tmp_path, "beta")
    nxt = compute_next(tmp_path, ["alpha", "beta", "gamma"])
    assert nxt.runnable == ["gamma"]
    assert nxt.blocked == {}


def test_nothing_shipped_only_root_is_runnable(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    nxt = compute_next(tmp_path, ["alpha", "beta", "gamma"])
    assert nxt.runnable == ["alpha"]
    assert nxt.blocked == {"beta": ["alpha"], "gamma": ["beta"]}
    assert nxt.shipped_count == 0


def test_no_plan_treats_all_unshipped_as_runnable(tmp_path: Path) -> None:
    _spec(tmp_path, "alpha", "F-1", "")
    _spec(tmp_path, "beta", "F-2", "")
    _ship(tmp_path, "alpha")
    nxt = compute_next(tmp_path, ["alpha", "beta"])
    assert nxt.runnable == ["beta"]
    assert nxt.blocked == {}


def test_dated_stems_match_slug_deps(tmp_path: Path) -> None:
    """Real-shape regression: spec stems carry date prefixes, plan deps resolve
    to bare slugs, shipped ids carry the date prefix. The dependent must be
    runnable (date-normalized comparison) — this fails without _strip_date."""
    _spec(tmp_path, "2026-01-01-alpha", "F-1", "")
    _spec(tmp_path, "2026-01-02-beta", "F-2", "F-1")
    _plan(tmp_path, [("F-1", "alpha", "—"), ("F-2", "beta", "F-1")])
    _ship(tmp_path, "2026-01-01-alpha")  # ledger id is date-prefixed

    nxt = compute_next(tmp_path, ["2026-01-01-alpha", "2026-01-02-beta"])

    assert nxt.shipped_count == 1
    assert nxt.runnable == ["2026-01-02-beta"]  # slug dep 'alpha' matches dated ship
    assert nxt.blocked == {}


def test_corrupt_ledger_degrades_without_crash(tmp_path: Path) -> None:
    """A corrupt durable ledger must degrade (logged), not crash the picker."""
    _scaffold(tmp_path)
    plan_dir = tmp_path / ".dark-factory" / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    # Mid-file garbage: load_shipped(strict=False) raises -> we degrade.
    (plan_dir / "shipped.jsonl").write_text(
        '{"spec_id": "alpha"}\n{ NOT JSON mid-file\n{"spec_id": "beta"}\n'
    )
    nxt = compute_next(tmp_path, ["alpha", "beta", "gamma"])
    # Degrades to "nothing shipped" — no exception.
    assert isinstance(nxt.runnable, list)
    assert nxt.shipped_count == 0


def test_slug_collision_does_not_crash(tmp_path: Path) -> None:
    """Two specs collapsing to the same date-stripped slug: last wins, no crash."""
    _spec(tmp_path, "2026-01-01-dup", "F-1", "")
    _spec(tmp_path, "2026-02-01-dup", "F-2", "")
    _plan(tmp_path, [("F-1", "dup", "—")])
    nxt = compute_next(tmp_path, ["2026-01-01-dup", "2026-02-01-dup"])
    # Both surface (no blocking deps); behavior is defined, not a crash.
    assert set(nxt.runnable) == {"2026-01-01-dup", "2026-02-01-dup"}


def test_is_shipped_rollback_overrides_stale_state_file(tmp_path: Path) -> None:
    """Rollback wins over a lingering state file (ledger already drops rolled-back rows)."""
    state_dir = tmp_path / ".dark-factory" / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "alpha.state").write_text("status=shipped\n")

    # In ledger -> shipped.
    assert is_shipped(tmp_path, "alpha", shipped_ids={"alpha"}, rolled_back=set())
    # Not in ledger (rolled back) but rolled_back set names it -> NOT shipped,
    # even though the stale state file still says shipped.
    assert not is_shipped(tmp_path, "alpha", shipped_ids=set(), rolled_back={"alpha"})
    # Not in ledger, not rolled back -> state-file fallback still honored.
    assert is_shipped(tmp_path, "alpha", shipped_ids=set(), rolled_back=set())
