"""Regression test runner — run all shipped scenarios against current codebase.

Replaces run-regression.sh.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from darkfactory.events.types import emit_regression_run
from darkfactory.models.config import PipelineConfig
from darkfactory.scenarios.runner import run_scenarios
from darkfactory.state.registry import list_specs, migrate_from_state, scenario_dir

log = logging.getLogger(__name__)


def run_regression(
    config: PipelineConfig,
    project_dir: Path,
    *,
    exclude: list[str] | None = None,
    timeout: int = 300,
    events_file: Path | None = None,
    run_id: str = "",
) -> tuple[int, int, int]:
    """Run regression tests for all shipped specs.

    Returns (passed, failed, total) counts.
    """
    # One-time migration from legacy state files.
    migrate_from_state(project_dir)

    exclude_set = set(exclude or [])
    specs = [s for s in list_specs(project_dir) if s not in exclude_set]

    if not specs:
        log.warning("no registered specs for regression")
        return (0, 0, 0)

    start = time.monotonic()
    passed = 0
    failed = 0

    for spec_name in specs:
        sdir = scenario_dir(project_dir, spec_name)
        if not sdir:
            log.warning("no scenario dir for %s, skipping", spec_name)
            continue

        # Symlink registry scenarios into the expected location.
        target = project_dir / ".dark-factory" / "scenarios" / spec_name
        _link_scenarios(sdir, target)

        try:
            result = run_scenarios(spec_name, config, project_dir, timeout=timeout)
            if result.exit_code == 0:
                passed += 1
                log.info("regression PASS: %s", spec_name)
            else:
                failed += 1
                log.warning("regression FAIL: %s (exit %d)", spec_name, result.exit_code)
        finally:
            _unlink_scenarios(target)

    total = passed + failed
    wall_s = round(time.monotonic() - start, 2)

    if events_file:
        emit_regression_run(
            events_file, spec="regression", run_id=run_id,
            passed=passed, failed=failed, total=total,
            wall_s=wall_s,
        )

    return (passed, failed, total)


def _link_scenarios(source: Path, target: Path) -> None:
    """Create a symlink from registry scenarios to expected location."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        return  # Don't overwrite existing scenarios.
    target.symlink_to(source)


def _unlink_scenarios(target: Path) -> None:
    """Remove the symlink created for regression."""
    if target.is_symlink():
        target.unlink()
