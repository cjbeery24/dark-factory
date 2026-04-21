"""Tests for _rewrite_counts_env_post_filter — the disk-state reconciliation
fix from run 3b1d2bc7's post-mortem.

Before this fix, ``_apply_scope_filter`` mutated ``gate_result`` in memory
but the canonical ``{spec}-scenario-counts.env`` on disk retained raw
pre-filter counts. Downstream consumers (review prepass, defect classifier)
read from disk and saw numbers that disagreed with the in-memory state,
triggering the ``count_mismatch`` classifier abort.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from darkfactory.pipeline.iteration import _rewrite_counts_env_post_filter


def _write_counts_env(project_dir: Path, spec_name: str, content: str) -> Path:
    reports = project_dir / ".dark-factory" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"{spec_name}-scenario-counts.env"
    path.write_text(content, encoding="utf-8")
    return path


def test_primary_keys_updated_to_filtered_values(tmp_path: Path) -> None:
    """SCENARIO_* primary keys reflect post-filter numbers."""
    path = _write_counts_env(
        tmp_path, "demo",
        "SCENARIO_PASSED=23\nSCENARIO_FAILED=11\nSCENARIO_TOTAL=34\n"
        "SCENARIO_RATE=67\n",
    )
    _rewrite_counts_env_post_filter(
        tmp_path, "demo",
        filtered_passed=23, filtered_failed=9, filtered_total=32, filtered_rate=72,
        raw_passed=23, raw_failed=11, raw_total=34, raw_rate=67,
    )
    data = {
        k: v
        for k, _, v in (
            line.partition("=") for line in path.read_text().splitlines()
        )
        if v
    }
    assert data["SCENARIO_PASSED"] == "23"
    assert data["SCENARIO_FAILED"] == "9"
    assert data["SCENARIO_TOTAL"] == "32"
    assert data["SCENARIO_RATE"] == "72"


def test_raw_shadow_keys_added(tmp_path: Path) -> None:
    """SCENARIO_*_RAW keys carry the pre-filter numbers for trajectory tools."""
    path = _write_counts_env(
        tmp_path, "demo",
        "SCENARIO_PASSED=23\nSCENARIO_FAILED=11\nSCENARIO_TOTAL=34\n"
        "SCENARIO_RATE=67\n",
    )
    _rewrite_counts_env_post_filter(
        tmp_path, "demo",
        filtered_passed=23, filtered_failed=9, filtered_total=32, filtered_rate=72,
        raw_passed=23, raw_failed=11, raw_total=34, raw_rate=67,
    )
    text = path.read_text()
    assert "SCENARIO_PASSED_RAW=23" in text
    assert "SCENARIO_FAILED_RAW=11" in text
    assert "SCENARIO_TOTAL_RAW=34" in text
    assert "SCENARIO_RATE_RAW=67" in text


def test_satisfaction_score_preserved(tmp_path: Path) -> None:
    """The scope filter does not affect satisfaction; it must be preserved verbatim."""
    path = _write_counts_env(
        tmp_path, "demo",
        "SCENARIO_PASSED=23\nSCENARIO_FAILED=11\nSCENARIO_TOTAL=34\n"
        "SCENARIO_RATE=67\nSATISFACTION_SCORE=85\n"
        "SCENARIO_SKIPPED=2\nSCENARIO_REGISTERED=36\n",
    )
    _rewrite_counts_env_post_filter(
        tmp_path, "demo",
        filtered_passed=23, filtered_failed=9, filtered_total=32, filtered_rate=72,
        raw_passed=23, raw_failed=11, raw_total=34, raw_rate=67,
    )
    text = path.read_text()
    assert "SATISFACTION_SCORE=85" in text
    assert "SCENARIO_SKIPPED=2" in text
    assert "SCENARIO_REGISTERED=36" in text


def test_missing_file_is_noop(tmp_path: Path) -> None:
    """No counts.env on disk → no-op (logged at debug, not an error)."""
    _rewrite_counts_env_post_filter(
        tmp_path, "demo",
        filtered_passed=0, filtered_failed=0, filtered_total=0, filtered_rate=0,
        raw_passed=0, raw_failed=0, raw_total=0, raw_rate=0,
    )
    # No file was written into a missing reports dir.
    assert not (
        tmp_path / ".dark-factory" / "reports" / "demo-scenario-counts.env"
    ).exists()


def test_write_is_atomic_no_tmp_leftover(tmp_path: Path) -> None:
    """Temp file must not be left on disk after a successful rewrite."""
    path = _write_counts_env(
        tmp_path, "demo",
        "SCENARIO_PASSED=1\nSCENARIO_FAILED=1\nSCENARIO_TOTAL=2\n"
        "SCENARIO_RATE=50\n",
    )
    _rewrite_counts_env_post_filter(
        tmp_path, "demo",
        filtered_passed=1, filtered_failed=0, filtered_total=1, filtered_rate=100,
        raw_passed=1, raw_failed=1, raw_total=2, raw_rate=50,
    )
    reports = path.parent
    assert not (reports / "demo-scenario-counts.env.tmp").exists()


def test_sorted_output_is_deterministic(tmp_path: Path) -> None:
    """Same inputs produce byte-identical output across calls (no dict ordering drift)."""
    path = _write_counts_env(
        tmp_path, "demo",
        "SCENARIO_PASSED=10\nSCENARIO_FAILED=5\nSCENARIO_TOTAL=15\n"
        "SCENARIO_RATE=67\nSATISFACTION_SCORE=70\n",
    )
    _rewrite_counts_env_post_filter(
        tmp_path, "demo",
        filtered_passed=10, filtered_failed=3, filtered_total=13, filtered_rate=77,
        raw_passed=10, raw_failed=5, raw_total=15, raw_rate=67,
    )
    bytes1 = path.read_bytes()

    _rewrite_counts_env_post_filter(
        tmp_path, "demo",
        filtered_passed=10, filtered_failed=3, filtered_total=13, filtered_rate=77,
        raw_passed=10, raw_failed=5, raw_total=15, raw_rate=67,
    )
    bytes2 = path.read_bytes()

    assert bytes1 == bytes2
