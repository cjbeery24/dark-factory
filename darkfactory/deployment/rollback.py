"""Cascading multi-layer rollback engine."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from darkfactory.deployment.command import run_command
from darkfactory.deployment.models import RollbackLayer
from darkfactory.events.types import (
    emit_rollback_all_failed,
    emit_rollback_layer_failed,
    emit_rollback_started,
    emit_rollback_success,
)
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import run

log = logging.getLogger(__name__)

# Cascade order.
_CASCADE = [
    RollbackLayer.FLAG_KILL,
    RollbackLayer.TRAFFIC_REVERT,
    RollbackLayer.CONTAINER_ROLLBACK,
    RollbackLayer.GIT_REVERT,
]


def rollback(
    config: PipelineConfig,
    spec_name: str,
    run_id: str,
    reason: str,
    start_layer: RollbackLayer,
    project_dir: str,
    events_file: Path,
    post_merge: bool = False,
) -> RollbackLayer | None:
    """Execute cascading rollback starting at start_layer.

    Tries each layer in cascade order from start_layer onward.
    GIT_REVERT (L4) only attempted if post_merge=True.
    Returns the layer that succeeded, or None if all failed.
    """
    emit_rollback_started(
        events_file, spec=spec_name, run_id=run_id,
        layer=start_layer.value, reason=reason,
    )

    start_idx = _CASCADE.index(start_layer)
    for layer in _CASCADE[start_idx:]:

        if layer == RollbackLayer.GIT_REVERT and not post_merge:
            continue

        success = _attempt_layer(config, spec_name, layer, project_dir, reason)

        if success:
            emit_rollback_success(
                events_file, spec=spec_name, run_id=run_id,
                layer=layer.value,
            )
            return layer

        emit_rollback_layer_failed(
            events_file, spec=spec_name, run_id=run_id,
            layer=layer.value, error=f"{layer.value} failed",
        )

    emit_rollback_all_failed(
        events_file, spec=spec_name, run_id=run_id, reason=reason,
    )
    return None


def _attempt_layer(
    config: PipelineConfig,
    spec_name: str,
    layer: RollbackLayer,
    project_dir: str,
    reason: str,
) -> bool:
    """Attempt a single rollback layer. Returns True on success."""
    timeout = config.rollback_timeout

    if layer == RollbackLayer.FLAG_KILL:
        return _rollback_flag_kill(config, spec_name, project_dir, timeout)
    elif layer == RollbackLayer.TRAFFIC_REVERT:
        return _rollback_traffic(config, project_dir, timeout)
    elif layer == RollbackLayer.CONTAINER_ROLLBACK:
        return _rollback_container(config, project_dir, timeout)
    elif layer == RollbackLayer.GIT_REVERT:
        return _rollback_git_revert(spec_name, project_dir, reason, config)
    return False


def _rollback_flag_kill(
    config: PipelineConfig, spec_name: str, project_dir: str, timeout: int,
) -> bool:
    if not config.flag_disable_cmd:
        log.debug("no flag_disable_cmd configured, skipping L1")
        return False

    try:
        proc = run_command(
            config.flag_disable_cmd, project_dir,
            env={"FLAG_NAME": f"df-{spec_name}"},
            timeout=timeout,
        )
        return proc.returncode == 0
    except Exception as e:
        log.warning("L1 flag kill failed: %s", e)
        return False


def _rollback_traffic(
    config: PipelineConfig, project_dir: str, timeout: int,
) -> bool:
    if not config.canary_rollback_cmd:
        log.debug("no canary_rollback_cmd configured, skipping L2")
        return False

    try:
        proc = run_command(
            config.canary_rollback_cmd, project_dir, timeout=timeout,
        )
        return proc.returncode == 0
    except Exception as e:
        log.warning("L2 traffic revert failed: %s", e)
        return False


def _rollback_container(
    config: PipelineConfig, project_dir: str, timeout: int,
) -> bool:
    if not config.deploy_rollback_cmd:
        log.debug("no deploy_rollback_cmd configured, skipping L3")
        return False

    try:
        proc = run_command(
            config.deploy_rollback_cmd, project_dir, timeout=timeout,
        )
        return proc.returncode == 0
    except Exception as e:
        log.warning("L3 container rollback failed: %s", e)
        return False


def _rollback_git_revert(
    spec_name: str, project_dir: str, reason: str, config: PipelineConfig,
) -> bool:
    """Create a revert commit and open a new PR for it.

    Uses git revert (not reset --hard). Creates revert branch and PR.
    Saves/restores the original HEAD on failure and cleans up orphan branches.
    """
    timestamp = int(time.time())
    revert_branch = f"dark-factory/revert-{spec_name}-{timestamp}"
    base_branch = config.git_base_branch

    # Save current branch so we can restore on failure.
    orig_ref = run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=project_dir, timeout=10,
    )
    original_branch = orig_ref.stdout.strip() if orig_ref.exit_code == 0 else base_branch

    # Find the merge commit (anchored grep to avoid substring matches).
    log_result = run(
        ["git", "log", "--all",
         f"--grep=^dark-factory: {spec_name}$",
         "-1", "--format=%H"],
        cwd=project_dir, timeout=30,
    )
    if log_result.exit_code != 0 or not log_result.stdout.strip():
        log.warning("L4: cannot find merge commit for %s", spec_name)
        return False

    commit_sha = log_result.stdout.strip()

    # Create revert branch.
    result = run(
        ["git", "checkout", "-b", revert_branch, base_branch],
        cwd=project_dir, timeout=30,
    )
    if result.exit_code != 0:
        log.warning("L4: failed to create revert branch: %s", result.stderr)
        return False

    def _cleanup_revert_branch() -> None:
        """Restore original branch and delete the orphan revert branch."""
        run(["git", "checkout", original_branch], cwd=project_dir, timeout=10)
        run(["git", "branch", "-D", revert_branch], cwd=project_dir, timeout=10)

    # Revert the commit. Use -m 1 to handle merge commits.
    result = run(
        ["git", "revert", "--no-edit", "-m", "1", commit_sha],
        cwd=project_dir, timeout=60,
    )
    if result.exit_code != 0:
        log.warning("L4: git revert failed: %s", result.stderr)
        run(["git", "revert", "--abort"], cwd=project_dir, timeout=10)
        _cleanup_revert_branch()
        return False

    # Push.
    result = run(
        ["git", "push", "-u", config.git_remote, revert_branch],
        cwd=project_dir, timeout=60,
    )
    if result.exit_code != 0:
        log.warning("L4: push failed: %s", result.stderr)
        _cleanup_revert_branch()
        return False

    # Create PR.
    pr_result = run(
        ["gh", "pr", "create",
         "--title", f"Revert dark-factory: {spec_name}",
         "--body", f"Automated revert: {reason}",
         "--base", base_branch,
         "--head", revert_branch],
        cwd=project_dir, timeout=60,
    )
    if pr_result.exit_code != 0:
        log.warning("L4: PR creation failed: %s", pr_result.stderr)
        return False

    # Restore original branch after successful PR creation.
    run(["git", "checkout", original_branch], cwd=project_dir, timeout=10)

    log.info("L4: revert PR created for %s", spec_name)
    return True
