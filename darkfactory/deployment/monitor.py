"""Post-merge rollback monitor — poll health after merge, trigger cascade on breach."""

from __future__ import annotations

import logging
import os
import shlex
import time
from pathlib import Path

from darkfactory.deployment.models import RollbackLayer
from darkfactory.deployment.rollback import rollback
from darkfactory.events.types import emit_rollback_window_expired
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import run as run_subprocess

log = logging.getLogger(__name__)


def monitor_rollback_window(
    config: PipelineConfig,
    spec_name: str,
    run_id: str,
    events_file: Path,
    project_dir: str,
) -> bool:
    """Poll rollback_monitor_cmd for rollback_window_seconds duration.

    Returns True if window expired cleanly, False if a breach was detected
    and rollback cascade triggered.
    """
    if not config.rollback_monitor_cmd:
        if not _has_valid_builtin_config(config):
            log.warning("no rollback_monitor_cmd and no valid health check config — skipping monitor")
            return True

    window = config.rollback_window_seconds
    interval = config.rollback_poll_interval
    cmd = config.rollback_monitor_cmd
    cmd_timeout = min(interval, 30)

    # Build env once — reused for every poll.
    poll_env = dict(os.environ)
    poll_env.update({
        "DF_SPEC_NAME": spec_name,
        "DF_RUN_ID": run_id,
        "DF_ROLLBACK_WINDOW_S": str(window),
    })

    log.info("starting rollback monitor: window=%ds, interval=%ds", window, interval)

    start = time.monotonic()
    poll_count = 0

    while True:
        elapsed = time.monotonic() - start
        if elapsed >= window:
            break

        poll_count += 1
        if cmd:
            ok, output = _poll_health(cmd, project_dir, poll_env, cmd_timeout)
        else:
            ok, output = _poll_builtin_health(config, project_dir, cmd_timeout)

        if not ok:
            log.warning("rollback monitor breach at poll %d (%.0fs): %s",
                        poll_count, elapsed, output[:200])
            rollback(
                config, spec_name, run_id,
                reason=f"monitor breach: {output[:200]}",
                start_layer=RollbackLayer.FLAG_KILL,
                project_dir=project_dir,
                events_file=events_file,
                post_merge=True,
            )
            return False

        remaining = window - (time.monotonic() - start)
        if remaining > 0:
            time.sleep(min(interval, remaining))

    emit_rollback_window_expired(events_file, spec=spec_name, run_id=run_id)
    log.info("rollback window expired cleanly after %d polls", poll_count)
    return True


def _has_valid_builtin_config(config: PipelineConfig) -> bool:
    """Check if the built-in health check strategy has required config."""
    strategy = config.health_check_strategy
    if strategy == "http":
        return bool(config.health_check_url)
    elif strategy == "tcp":
        return bool(config.health_check_host and config.health_check_port)
    elif strategy == "docker":
        return bool(config.health_check_container)
    elif strategy == "command":
        return bool(config.rollback_monitor_cmd)
    return False


def _poll_builtin_health(
    config: PipelineConfig,
    project_dir: str,
    timeout: int,
) -> tuple[bool, str]:
    """Use built-in health check strategy. Returns (ok, output)."""
    from darkfactory.deployment.health import run_health_check
    ok = run_health_check(
        config.health_check_strategy,
        url=config.health_check_url,
        host=config.health_check_host,
        port=config.health_check_port,
        container=config.health_check_container,
        project_dir=project_dir,
        timeout=timeout,
    )
    return (ok, "") if ok else (False, f"{config.health_check_strategy} health check failed")


def _poll_health(
    cmd: str,
    project_dir: str,
    env: dict[str, str],
    timeout: int,
) -> tuple[bool, str]:
    """Run the monitor command. Returns (ok, output).

    Exit code 0 = healthy, non-zero = breach.
    """
    try:
        result = run_subprocess(shlex.split(cmd), timeout=timeout, env=env, cwd=project_dir)
        output = result.stdout + result.stderr
        return result.exit_code == 0, output
    except Exception as e:
        return False, str(e)
