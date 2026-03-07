"""Post-merge rollback monitor — poll health after merge, trigger cascade on breach."""

from __future__ import annotations

import logging
import os
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
        log.warning("no rollback_monitor_cmd configured — skipping monitor")
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
        ok, output = _poll_health(cmd, project_dir, poll_env, cmd_timeout)

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
        result = run_subprocess(cmd.split(), timeout=timeout, env=env, cwd=project_dir)
        output = result.stdout + result.stderr
        return result.exit_code == 0, output
    except Exception as e:
        return False, str(e)
