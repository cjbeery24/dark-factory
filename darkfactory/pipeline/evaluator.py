"""Evaluator deployment lifecycle — deploy, health check, teardown.

DEPRECATED: This module is a thin compatibility shim. New code should use
darkfactory.deployment.environment directly. These functions are retained
for callers in iteration.py until Phase 6 Tier 6 wires the post-ship pipeline.
"""

from __future__ import annotations

import logging

from darkfactory.deployment.environment import (
    health_check as _health_check,
    provision as _provision,
    teardown as _teardown,
)
from darkfactory.models.config import PipelineConfig

log = logging.getLogger(__name__)


def deploy(config: PipelineConfig, project_dir: str = ".") -> bool:
    """Run the deploy command. Returns True on success.

    Delegates to darkfactory.deployment.environment.provision().
    """
    if not config.deploy_cmd and not config.deploy_compose_file:
        return False

    try:
        _provision(config, project_dir, spec_name="evaluator", run_id="eval")
        return True
    except Exception as e:
        log.warning("deploy failed: %s", e)
        return False


def health_check(
    config: PipelineConfig,
    *,
    timeout: int = 60,
) -> bool:
    """Poll the health endpoint until it responds. Returns True on success."""
    if not config.deploy_base_url:
        return False

    return _health_check(
        config.deploy_base_url,
        endpoint=config.deploy_health_endpoint,
        timeout=timeout,
    )


def teardown(config: PipelineConfig, project_dir: str = ".") -> None:
    """Run the teardown command if configured."""
    _teardown(config, project_dir=project_dir)
