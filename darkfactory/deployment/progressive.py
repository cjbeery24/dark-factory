"""Progressive canary deployment with staged traffic shifting."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Protocol

from darkfactory.deployment.command import run_command
from darkfactory.deployment.metrics_gate import collect_and_evaluate
from darkfactory.deployment.models import (
    CanaryResult,
    CanaryStage,
    DeploymentResult,
    DeploymentStatus,
    MetricThreshold,
)
from darkfactory.events.types import (
    emit_canary_lock_timeout,
    emit_canary_promoted,
    emit_canary_stage_failed,
    emit_canary_stage_passed,
    emit_canary_stage_start,
)
from darkfactory.models.config import PipelineConfig
from darkfactory.state.lock import FileLock, LockError

log = logging.getLogger(__name__)


class Abortable(Protocol):
    """Protocol for objects that support abort signaling."""
    abort_requested: bool


DEFAULT_STAGES = [
    CanaryStage(percent=1, soak_seconds=60),
    CanaryStage(percent=10, soak_seconds=120),
    CanaryStage(percent=50, soak_seconds=300),
    CanaryStage(percent=100, soak_seconds=0),
]


def run_canary(
    config: PipelineConfig,
    spec_name: str,
    run_id: str,
    stages: list[CanaryStage] | None,
    thresholds: list[MetricThreshold],
    events_file: Path,
    project_dir: str,
    cleanup_manager: Abortable | None = None,
) -> DeploymentResult:
    """Execute the full canary rollout.

    Acquires canary lock, runs stages sequentially, evaluates metrics at each.
    On threshold breach: returns ROLLED_BACK.
    On lock timeout: returns FAILED.
    """
    if stages is None or not stages:
        stages = list(DEFAULT_STAGES)

    # Validate strictly increasing.
    percents = [s.percent for s in stages]
    if not all(a < b for a, b in zip(percents, percents[1:])):
        raise ValueError(f"canary stages percent must be strictly increasing, got {percents}")

    start = time.monotonic()

    # Acquire canary lock.
    lock_path = Path(project_dir) / ".dark-factory" / "state" / "canary.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(lock_path, timeout=config.canary_lock_timeout)

    try:
        lock.acquire()
    except LockError:
        log.warning("canary lock timeout after %ds — skipping canary", config.canary_lock_timeout)
        emit_canary_lock_timeout(events_file, spec=spec_name, run_id=run_id)
        return DeploymentResult(
            status=DeploymentStatus.FAILED,
            stages_completed=0,
            stages_total=len(stages),
            error="canary lock timeout",
            wall_seconds=time.monotonic() - start,
        )

    try:
        completed = 0
        for stage in stages:
            emit_canary_stage_start(
                events_file, spec=spec_name, run_id=run_id,
                stage_name=stage.name, percent=stage.percent,
            )

            if not _shift_traffic(config, stage.percent, project_dir):
                return DeploymentResult(
                    status=DeploymentStatus.FAILED,
                    stages_completed=completed,
                    stages_total=len(stages),
                    error=f"traffic shift to {stage.percent}% failed",
                    wall_seconds=time.monotonic() - start,
                )

            if stage.soak_seconds > 0:
                if not _soak(stage.soak_seconds, cleanup_manager):
                    return DeploymentResult(
                        status=DeploymentStatus.ROLLED_BACK,
                        stages_completed=completed,
                        stages_total=len(stages),
                        rollback_reason="aborted during soak",
                        wall_seconds=time.monotonic() - start,
                    )

            if thresholds:
                metrics, breached = collect_and_evaluate(thresholds, project_dir)
                if breached:
                    emit_canary_stage_failed(
                        events_file, spec=spec_name, run_id=run_id,
                        stage_name=stage.name, percent=stage.percent,
                        breached=",".join(breached),
                    )
                    return DeploymentResult(
                        status=DeploymentStatus.ROLLED_BACK,
                        stages_completed=completed,
                        stages_total=len(stages),
                        rollback_reason=f"threshold breach: {', '.join(breached)}",
                        wall_seconds=time.monotonic() - start,
                    )

            completed += 1
            emit_canary_stage_passed(
                events_file, spec=spec_name, run_id=run_id,
                stage_name=stage.name, percent=stage.percent,
            )

        emit_canary_promoted(
            events_file, spec=spec_name, run_id=run_id,
            stages_completed=completed,
        )

        return DeploymentResult(
            status=DeploymentStatus.PROMOTED,
            stages_completed=completed,
            stages_total=len(stages),
            wall_seconds=time.monotonic() - start,
        )
    finally:
        lock.release()


def _soak(seconds: int, cleanup_manager: Abortable | None) -> bool:
    """Sleep for the soak period, checking abort every second.

    Returns True if soak completed. False if aborted.
    """
    for _ in range(seconds):
        if cleanup_manager and cleanup_manager.abort_requested:
            return False
        time.sleep(1)
    return True


def _shift_traffic(config: PipelineConfig, percent: int, project_dir: str) -> bool:
    """Call canary_shift_cmd with CANARY_PERCENT env var."""
    if not config.canary_shift_cmd:
        log.warning("no canary_shift_cmd configured, skipping traffic shift")
        return True

    try:
        proc = run_command(
            config.canary_shift_cmd, project_dir,
            env={"CANARY_PERCENT": str(percent)},
            timeout=config.rollback_timeout,
        )
        return proc.returncode == 0
    except Exception as e:
        log.warning("traffic shift failed: %s", e)
        return False
