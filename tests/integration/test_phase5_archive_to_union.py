"""Phase 5 validation gate — end-to-end Python-layer test of the archive→union path.

Closes the Phase 5 exit criteria for the bits Phase 5 actually owns:

1. registry_register copies a shipped spec's scenarios into shipped-scenarios/
2. tag_archived_scenarios prepends spec_origin/spec_type headers
3. record_shipped_spec appends a row to shipped.jsonl with non-empty,
   stable env_hash_at_ship
4. A second SHIP appends a *second* row (not overwrite — append-only is
   load-bearing for Phase 6 plan replay)
5. run_scenarios called with spec_type="feature" builds a test command
   that unions the new scenarios dir with both archived spec dirs

This test is hermetic: no Docker, no agent calls, no Oracle. It exercises
the exact functions Phase 5.1–5.4 added by driving them directly. The
explicitly-deferred bits (quality-gate.sh per-spec_origin attribution) are
out of scope per the plan §5.4 deferred checkbox.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from darkfactory.models.config import PipelineConfig
from darkfactory.pipeline.post_ship_archive import (
    capture_env_hash,
    record_shipped_spec,
    tag_archived_scenarios,
)
from darkfactory.plan.state import load_shipped, shipped_log_path
from darkfactory.scenarios.runner import run_scenarios
from darkfactory.state.registry import register as registry_register
from darkfactory.utils.subprocess_runner import RunResult


def _seed_scenarios(project: Path, spec_id: str, files: dict[str, str]) -> None:
    """Write scenario files into .dark-factory/scenarios/<spec>/."""
    sdir = project / ".dark-factory" / "scenarios" / spec_id
    sdir.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (sdir / name).write_text(body, encoding="utf-8")


def _ship(project: Path, spec_id: str, spec_type: str) -> None:
    """Simulate the post-Oracle SHIP work for one spec.

    Mirrors the sequence in orchestrator._handle_ship's Phase 5.1 block:
    register scenarios, tag the archive copies, capture env hash, append
    the shipped.jsonl row.
    """
    assert registry_register(project, spec_id) is True
    archive_dir = project / ".dark-factory" / "shipped-scenarios" / spec_id
    tag_archived_scenarios(archive_dir, spec_id, spec_type)
    env_hash = capture_env_hash(project)
    row = record_shipped_spec(
        project,
        spec_id=spec_id,
        spec_type=spec_type,
        spec_origin=spec_id,
        env_hash=env_hash,
    )
    assert row is not None, f"record_shipped_spec returned None for {spec_id}"


def test_phase5_two_ship_archive_then_union(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "vitest.config.ts").write_text("", encoding="utf-8")  # node toolchain hint

    # ---- Ship #1: a bootstrap spec ----
    _seed_scenarios(project, "bootstrap-spec", {
        "01-contract.test.ts": "import {x} from 'y';\n",
        "02-edge.test.ts": "test('edge', () => {});\n",
    })
    _ship(project, "bootstrap-spec", "bootstrap")

    # Gate check 1: archive directory exists with both files copied.
    archive1 = project / ".dark-factory" / "shipped-scenarios" / "bootstrap-spec"
    assert archive1.is_dir()
    assert (archive1 / "01-contract.test.ts").exists()
    assert (archive1 / "02-edge.test.ts").exists()

    # Gate check 2: both files are tagged with header comments. The header
    # must be the *exact* line, not a substring match — Phase 5.1's
    # idempotency sentinel depends on this.
    f1 = (archive1 / "01-contract.test.ts").read_text()
    assert f1.startswith("// spec_origin: bootstrap-spec\n// spec_type: bootstrap\n")
    assert "import {x} from 'y';" in f1
    f2 = (archive1 / "02-edge.test.ts").read_text()
    assert f2.startswith("// spec_origin: bootstrap-spec\n// spec_type: bootstrap\n")

    # Gate check 3: shipped.jsonl exists with one row, env_hash non-empty.
    log_path = shipped_log_path(project)
    assert log_path.exists()
    rows = load_shipped(project)
    assert len(rows) == 1
    assert rows[0].spec_id == "bootstrap-spec"
    assert rows[0].spec_type == "bootstrap"
    assert rows[0].env_hash_at_ship  # non-empty
    assert rows[0].scenario_archive_path == ".dark-factory/shipped-scenarios/bootstrap-spec"
    first_env_hash = rows[0].env_hash_at_ship

    # Stability: re-reading the on-disk JSONL must yield the same hash —
    # not a stub, not a clock-derived value.
    raw = json.loads(log_path.read_text().strip().splitlines()[0])
    assert raw["env_hash_at_ship"] == first_env_hash

    # Idempotency: re-tagging the archive must not double-prepend.
    tag_archived_scenarios(archive1, "bootstrap-spec", "bootstrap")
    f1_again = (archive1 / "01-contract.test.ts").read_text()
    assert f1_again == f1

    # ---- Ship #2: a feature spec, with the bootstrap already in the archive ----
    _seed_scenarios(project, "feature-spec", {
        "01-happy.test.ts": "test('happy', () => {});\n",
    })
    _ship(project, "feature-spec", "feature")

    # Gate check 4: shipped.jsonl now has TWO rows (append, not overwrite).
    rows = load_shipped(project)
    assert [r.spec_id for r in rows] == ["bootstrap-spec", "feature-spec"]
    # The first row's env_hash must be unchanged across the second ship.
    assert rows[0].env_hash_at_ship == first_env_hash
    # And the on-disk file must have exactly two newline-terminated rows.
    raw_lines = log_path.read_text().splitlines()
    assert len(raw_lines) == 2

    # ---- Gate check 5: run_scenarios for feature-spec unions the archive ----
    config = dataclasses.replace(
        PipelineConfig(),
        scenario_test_cmd="npx vitest run {SCENARIOS_DIR}",
        gate_timeout=30,
    )
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return RunResult(exit_code=0, stdout="", stderr="")

    with patch("darkfactory.scenarios.runner.run", side_effect=fake_run):
        run_scenarios("feature-spec", config, project, spec_type="feature")

    # bash -c "<expanded>"
    assert captured["cmd"][0] == "bash"
    expanded = captured["cmd"][2]

    # The new scenarios dir must be present.
    assert "scenarios/feature-spec" in expanded
    # Both archived spec dirs must be present (bootstrap-spec is the only
    # one besides the current spec, which is excluded).
    assert "shipped-scenarios/bootstrap-spec" in expanded
    # The current spec must NOT appear under shipped-scenarios — its
    # archive copy would otherwise double-run via the union.
    assert "shipped-scenarios/feature-spec" not in expanded


def test_phase5_bootstrap_does_not_union_existing_archive(tmp_path: Path) -> None:
    """A bootstrap spec must run only its own new scenarios, even if a prior
    archive happens to exist (e.g., from a project that's mid-rebuild)."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "vitest.config.ts").write_text("", encoding="utf-8")

    _seed_scenarios(project, "older-feat", {"01.test.ts": "x\n"})
    _ship(project, "older-feat", "feature")

    _seed_scenarios(project, "new-bootstrap", {"01.test.ts": "y\n"})

    config = dataclasses.replace(
        PipelineConfig(),
        scenario_test_cmd="npx vitest run {SCENARIOS_DIR}",
        gate_timeout=30,
    )
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return RunResult(exit_code=0, stdout="", stderr="")

    with patch("darkfactory.scenarios.runner.run", side_effect=fake_run):
        run_scenarios("new-bootstrap", config, project, spec_type="bootstrap")

    expanded = captured["cmd"][2]
    assert "scenarios/new-bootstrap" in expanded
    assert "shipped-scenarios" not in expanded
