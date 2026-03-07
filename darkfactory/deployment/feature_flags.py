"""Feature flag lifecycle management via user-provided commands."""

from __future__ import annotations

import logging

from darkfactory.deployment.command import run_command
from darkfactory.models.config import PipelineConfig

log = logging.getLogger(__name__)


def _flag_name(spec_name: str) -> str:
    return f"df-{spec_name}"


def create_flag(config: PipelineConfig, spec_name: str, project_dir: str) -> bool:
    """Create a feature flag for the spec (default OFF).

    If no flag_create_cmd configured, skip (feature flags are optional).
    Returns True if created or skipped.
    """
    if not config.flag_create_cmd:
        return True

    env = {"FLAG_NAME": _flag_name(spec_name), "FLAG_DEFAULT": "off"}

    try:
        proc = run_command(config.flag_create_cmd, project_dir, env=env, timeout=60)
        return proc.returncode == 0
    except Exception as e:
        log.warning("flag create failed: %s", e)
        return False


def enable_flag(
    config: PipelineConfig,
    spec_name: str,
    project_dir: str,
    percent: int = 100,
) -> bool:
    """Enable the flag at the specified percentage."""
    if not config.flag_enable_cmd:
        return True

    env = {"FLAG_NAME": _flag_name(spec_name), "FLAG_PERCENT": str(percent)}

    try:
        proc = run_command(config.flag_enable_cmd, project_dir, env=env, timeout=60)
        return proc.returncode == 0
    except Exception as e:
        log.warning("flag enable failed: %s", e)
        return False


def disable_flag(config: PipelineConfig, spec_name: str, project_dir: str) -> bool:
    """Kill switch -- immediately disable the flag."""
    if not config.flag_disable_cmd:
        return True

    env = {"FLAG_NAME": _flag_name(spec_name)}

    try:
        proc = run_command(config.flag_disable_cmd, project_dir, env=env, timeout=60)
        return proc.returncode == 0
    except Exception as e:
        log.warning("flag disable failed: %s", e)
        return False


def check_flag(
    config: PipelineConfig, spec_name: str, project_dir: str,
) -> str | None:
    """Check current flag status. Returns stdout or None if unconfigured."""
    if not config.flag_check_cmd:
        return None

    env = {"FLAG_NAME": _flag_name(spec_name)}

    try:
        proc = run_command(
            config.flag_check_cmd, project_dir, env=env, timeout=30, capture=True,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
        return None
    except Exception as e:
        log.warning("flag check failed: %s", e)
        return None
