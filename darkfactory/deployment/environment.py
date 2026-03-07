"""Ephemeral environment provisioning, health verification, and teardown."""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.deployment.command import run_command
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import run

log = logging.getLogger(__name__)

_BACKOFF_CAP = 30  # max inter-attempt delay seconds
_COMPOSE_NAME_RE = re.compile(r'[^a-z0-9_-]')


def _sanitize_compose_name(name: str) -> str:
    """Sanitize a string for use as a Docker Compose project name."""
    return _COMPOSE_NAME_RE.sub("-", name.lower())[:48]


@dataclass
class EnvironmentInfo:
    """Details of a provisioned ephemeral environment."""
    base_url: str = ""
    compose_project: str = ""
    container_ids: list[str] = field(default_factory=list)


def provision(
    config: PipelineConfig,
    project_dir: str,
    spec_name: str,
    run_id: str,
) -> EnvironmentInfo | None:
    """Provision an ephemeral environment.

    Strategy (priority order):
    1. deploy_compose_file -> docker-compose up
    2. deploy_cmd -> legacy command
    3. Neither -> skip (no deployment configured)
    """
    if config.deploy_compose_file:
        return _provision_compose(config, project_dir, spec_name, run_id, config.deploy_compose_file)

    if config.deploy_cmd:
        return _provision_legacy(config, project_dir)

    return None


def _provision_compose(
    config: PipelineConfig,
    project_dir: str,
    spec_name: str,
    run_id: str,
    compose_file: str,
) -> EnvironmentInfo:
    safe_name = _sanitize_compose_name(spec_name)
    compose_project = f"df-{safe_name}-{run_id[:8]}"

    env_vars: dict[str, str] = {
        "DF_SPEC_NAME": spec_name,
        "DF_RUN_ID": run_id,
        "DF_PROJECT_DIR": project_dir,
        "DF_BASE_BRANCH": config.git_base_branch,
    }
    if config.deploy_env:
        env_vars.update(config.deploy_env)

    compose_path = Path(project_dir) / compose_file
    if not compose_path.exists():
        raise FileNotFoundError(f"compose file not found: {compose_path}")

    cmd = [
        "docker-compose", "-f", str(compose_path),
        "-p", compose_project,
        "up", "-d",
    ]

    run_env = dict(os.environ)
    run_env.update(env_vars)

    result = run(cmd, timeout=120, env=run_env, cwd=project_dir)
    if result.exit_code != 0:
        raise RuntimeError(f"docker-compose up failed: {result.stderr}")

    log.info("provisioned compose environment: project=%s", compose_project)

    return EnvironmentInfo(
        base_url=config.deploy_base_url,
        compose_project=compose_project,
    )


def _provision_legacy(
    config: PipelineConfig,
    project_dir: str,
) -> EnvironmentInfo:
    if config.deploy_cmd_allow_shell:
        log.warning("deploy_cmd_allow_shell=true is deprecated; use a script file")
        result = run(["bash", "-c", config.deploy_cmd], timeout=120)
        if result.exit_code != 0:
            raise RuntimeError(f"deploy_cmd failed: {result.stderr}")
    else:
        result_proc = run_command(config.deploy_cmd, project_dir, timeout=120)
        if result_proc.returncode != 0:
            raise RuntimeError(f"deploy_cmd failed (exit {result_proc.returncode})")

    return EnvironmentInfo(base_url=config.deploy_base_url)


def health_check(
    base_url: str,
    endpoint: str = "/health",
    timeout: int = 60,
    max_retries: int = 10,
) -> bool:
    """Poll health endpoint with exponential backoff under a wall-clock deadline."""
    if not base_url:
        return False

    url = f"{base_url}{endpoint}"
    deadline = time.monotonic() + timeout
    attempt = 0

    while time.monotonic() < deadline and attempt < max_retries:
        result = run(
            ["curl", "--connect-timeout", "5", "--max-time", "10",
             "-sf", "-o", "/dev/null", url],
            timeout=15,
        )
        if result.exit_code == 0:
            log.info("health check passed: %s (attempt %d)", url, attempt + 1)
            return True

        attempt += 1
        delay = min(2 ** (attempt - 1), _BACKOFF_CAP)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(delay, remaining))

    log.warning("health check timed out after %ds: %s", timeout, url)
    return False


def teardown(
    config: PipelineConfig,
    project_dir: str,
    compose_project: str | None = None,
    run_id: str = "",
) -> None:
    """Deterministic teardown of ephemeral environment."""
    if compose_project:
        result = run(
            ["docker-compose", "-p", compose_project,
             "down", "--volumes", "--remove-orphans"],
            timeout=60,
        )
        if result.exit_code != 0:
            log.warning("compose teardown failed: %s", result.stderr)

    if config.deploy_teardown_cmd:
        if config.deploy_cmd_allow_shell:
            run(["bash", "-c", config.deploy_teardown_cmd], timeout=30)
        else:
            try:
                run_command(config.deploy_teardown_cmd, project_dir, timeout=30)
            except Exception as e:
                log.warning("teardown command failed: %s", e)

    if run_id:
        # Find and remove orphaned containers by label.
        ps_result = run(
            ["docker", "ps", "-aq", "--filter", f"label=df-deploy-{run_id}"],
            timeout=10,
        )
        container_ids = ps_result.stdout.strip().split()
        if container_ids and container_ids[0]:
            run(["docker", "rm", "-f"] + container_ids, timeout=30)

    log.info("teardown complete (project=%s)", compose_project or "legacy")
