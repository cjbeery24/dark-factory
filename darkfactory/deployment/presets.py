"""Deployment presets — named configurations for common deployment targets.

Presets populate empty deploy/rollback/health command fields in PipelineConfig
so users can write `preset = "docker-compose"` instead of configuring every
command individually.
"""

from __future__ import annotations

import logging
from pathlib import Path

from darkfactory.models.config import PipelineConfig

log = logging.getLogger(__name__)


def apply_preset(config: PipelineConfig, project_dir: str) -> None:
    """Apply a named deployment preset to config, filling empty command fields.

    Already-populated fields are never overwritten — the preset only provides
    defaults for fields the user has not explicitly set.
    """
    preset = config.deploy_preset
    if preset == "none":
        return

    if preset == "docker-compose":
        _apply_docker_compose(config, project_dir)
    elif preset == "k8s-argo":
        _apply_k8s_argo(config, project_dir)
    else:
        log.warning("unknown deploy preset %r — ignored", preset)


def _apply_docker_compose(config: PipelineConfig, project_dir: str) -> None:
    """Apply docker-compose preset defaults."""
    # Compose file.
    if not config.deploy_compose_file:
        for candidate in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            if (Path(project_dir) / candidate).exists():
                config.deploy_compose_file = candidate
                break

    compose_file = config.deploy_compose_file or "docker-compose.yml"

    # Teardown command.
    if not config.deploy_teardown_cmd:
        config.deploy_teardown_cmd = f"docker compose -f {compose_file} down --remove-orphans"

    # Health check defaults.
    if not config.health_check_url:
        base = config.deploy_base_url or "http://localhost:8080"
        endpoint = config.deploy_health_endpoint or "/health"
        config.health_check_url = f"{base}{endpoint}"

    # Deploy and rollback commands from scripts/ if they exist.
    scripts_dir = Path(project_dir) / "scripts" / "deploy" / "docker-compose"
    if not config.deploy_cmd:
        deploy_script = scripts_dir / "deploy.sh"
        if deploy_script.exists():
            config.deploy_cmd = str(deploy_script)

    if not config.deploy_rollback_cmd:
        rollback_script = scripts_dir / "rollback.sh"
        if rollback_script.exists():
            config.deploy_rollback_cmd = str(rollback_script)


def _apply_k8s_argo(config: PipelineConfig, project_dir: str) -> None:
    """Apply k8s-argo preset defaults."""
    # Health check defaults.
    if not config.health_check_url:
        base = config.deploy_base_url or "http://localhost:8080"
        endpoint = config.deploy_health_endpoint or "/health"
        config.health_check_url = f"{base}{endpoint}"

    # Deploy and rollback commands from scripts/ if they exist.
    scripts_dir = Path(project_dir) / "scripts" / "deploy" / "k8s-argo"
    if not config.deploy_cmd:
        deploy_script = scripts_dir / "deploy.sh"
        if deploy_script.exists():
            config.deploy_cmd = str(deploy_script)

    if not config.deploy_rollback_cmd:
        rollback_script = scripts_dir / "rollback.sh"
        if rollback_script.exists():
            config.deploy_rollback_cmd = str(rollback_script)
