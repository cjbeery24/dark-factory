"""Integration: ``run_start_cleanup`` purges and emits a single event.

End-to-end through ``run_pipeline`` would be the ideal level. (Historical
note: the shared ``pipeline_env`` fixture used to patch a non-existent
``darkfactory.pipeline.orchestrator.run_regression`` — ``run_regression``
is imported lazily inside the orchestrator — so every ``pipeline_env``-
dependent test errored at fixture setup. That patch-target bug is now
fixed in ``conftest.py`` (patches the source module
``darkfactory.scenarios.regression.run_regression``). NOTE: the wider
integration suite still requires a Docker daemon to pass end-to-end, so
this test continues to drive the wrapper directly.)

This test drives ``run_start_cleanup``
(the pipeline-layer wrapper the orchestrator calls) directly — it
exercises the exact purge+emit path the orchestrator takes.

The back-to-back mtime check also runs through the wrapper directly:
the contamination failure mode is specifically ``reviews/``+
``reports/`` files from a prior run surviving into the next run's
working tree, and that invariant is enforced entirely inside
``run_start_cleanup``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from darkfactory.pipeline.run_start_cleanup import run_start_cleanup


def _read_events(events_file: Path) -> list[dict]:
    if not events_file.exists():
        return []
    out = []
    with events_file.open() as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                out.append(json.loads(stripped))
    return out


def _seed_prior_run(project_dir: Path, spec_name: str) -> list[Path]:
    """Seed a live tree the way a terminated prior run leaves it.

    Returns the exact files that must be purged — used to make the
    back-to-back mtime check deterministic.
    """
    dark = project_dir / ".dark-factory"
    per_iter = [
        dark / "reviews" / f"{spec_name}-feedback-iter1.md",
        dark / "reviews" / f"{spec_name}-feedback-iter2.md",
        dark / "reviews" / f"{spec_name}-oracle-output-iter2.md",
        dark / "reviews" / f"{spec_name}-review.md",
        dark / "reviews" / f"{spec_name}-escalation.md",
        dark / "reports" / f"{spec_name}-defects-iter2.json",
        dark / "reports" / f"{spec_name}-quality-gate.md",
        dark / "reports" / f"{spec_name}-scenario-counts.env",
    ]
    for p in per_iter:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("stale from a prior run")
    return per_iter


@pytest.mark.integration
class TestRunStartCleanupIntegration:
    def test_emits_one_event_with_count_and_files(self, tmp_path):
        events = tmp_path / ".dark-factory" / "events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        seeded = _seed_prior_run(tmp_path, "myspec")

        count, files = run_start_cleanup(
            tmp_path, "myspec",
            run_id="deadbeef01234567",
            events_file=events,
        )

        assert count == len(seeded)
        assert sorted(files) == files  # stable order for event payload

        records = _read_events(events)
        cleanup = [e for e in records if e["event"] == "RUN_START_CLEANUP"]
        assert len(cleanup) == 1, f"expected one event, got {len(cleanup)}"
        data = cleanup[0]["data"]
        assert data["purged_count"] == count
        assert data["purged_files"] == files
        assert cleanup[0]["run_id"] == "deadbeef01234567"
        assert cleanup[0]["spec"] == "myspec"

        for p in seeded:
            assert not p.exists(), f"{p} survived cleanup"

    def test_emits_event_even_when_nothing_to_purge(self, tmp_path):
        """A clean tree still emits a zero-count event — observable
        signal that the cleanup *ran* (not that it was skipped)."""
        events = tmp_path / ".dark-factory" / "events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)

        count, files = run_start_cleanup(
            tmp_path, "myspec",
            run_id="abcdef0011223344",
            events_file=events,
        )
        assert count == 0
        assert files == []

        records = _read_events(events)
        cleanup = [e for e in records if e["event"] == "RUN_START_CLEANUP"]
        assert len(cleanup) == 1
        assert cleanup[0]["data"]["purged_count"] == 0

    def test_no_emission_without_events_file(self, tmp_path):
        _seed_prior_run(tmp_path, "myspec")
        count, _ = run_start_cleanup(
            tmp_path, "myspec",
            run_id="00000000deadbeef",
            events_file=None,
        )
        assert count > 0
        # No events.jsonl created.
        assert not (tmp_path / ".dark-factory" / "events.jsonl").exists()

    def test_back_to_back_no_stale_mtime(self, tmp_path):
        """Per-iter files after run-2-cleanup are either gone or have
        mtime >= run-2 start — the exact invariant the plan's
        acceptance criteria require."""
        events = tmp_path / ".dark-factory" / "events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)

        # Simulate run-1's final working-tree state.
        _seed_prior_run(tmp_path, "myspec")

        # Force-distinct mtime boundary. APFS timestamps have
        # sub-nanosecond resolution; 1.1s is overkill but safe on
        # every filesystem this project targets.
        time.sleep(1.1)
        run2_start = time.time()

        run_start_cleanup(
            tmp_path, "myspec",
            run_id="00000000facade42",
            events_file=events,
        )

        # Simulate run-2 writing ONE new per-iter file after cleanup.
        new_file = (
            tmp_path / ".dark-factory" / "reviews" / "myspec-feedback-iter1.md"
        )
        new_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.write_text("fresh iter-1 feedback from run 2")

        dark = tmp_path / ".dark-factory"
        per_iter_names: list[Path] = []
        for sub in ("reviews", "reports"):
            sub_dir = dark / sub
            if sub_dir.is_dir():
                per_iter_names.extend(
                    p for p in sub_dir.iterdir()
                    if p.is_file() and p.name.startswith("myspec-")
                )
        # Every per-iter file in the live tree now has mtime >= run2_start.
        assert per_iter_names, "expected the freshly-written file"
        for p in per_iter_names:
            assert p.stat().st_mtime >= run2_start - 0.05, (
                f"{p} older than run 2's start — contamination"
            )
