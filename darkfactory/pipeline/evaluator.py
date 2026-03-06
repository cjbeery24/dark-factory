"""Evaluator deployment lifecycle — deploy, health check, teardown.

Replaces step 4.5 of dark-factory.sh.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import run

log = logging.getLogger(__name__)

_HEALTH_CHECK_INTERVAL = 1  # seconds


def deploy(config: PipelineConfig) -> bool:
    """Run the deploy command. Returns True on success."""
    if not config.deploy_cmd:
        return False

    result = run(["bash", "-c", config.deploy_cmd], timeout=120)
    if result.exit_code != 0:
        log.warning("deploy failed: %s", result.stderr)
        return False

    log.info("deploy succeeded")
    return True


def health_check(
    config: PipelineConfig,
    *,
    timeout: int = 60,
) -> bool:
    """Poll the health endpoint until it responds. Returns True on success."""
    if not config.deploy_base_url:
        return False

    url = f"{config.deploy_base_url}{config.deploy_health_endpoint}"
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        result = run(
            ["curl", "--connect-timeout", "5", "--max-time", "10",
             "-sf", "-o", "/dev/null", url],
            timeout=15,
        )
        if result.exit_code == 0:
            log.info("health check passed: %s", url)
            return True
        time.sleep(_HEALTH_CHECK_INTERVAL)

    log.warning("health check timed out after %ds: %s", timeout, url)
    return False


def teardown(config: PipelineConfig) -> None:
    """Run the teardown command if configured."""
    if not config.deploy_teardown_cmd:
        return
    result = run(["bash", "-c", config.deploy_teardown_cmd], timeout=30)
    if result.exit_code != 0:
        log.warning("teardown failed: %s", result.stderr)
