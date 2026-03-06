"""Docker availability checks.

Replaces require_docker from docker.sh.
"""

from __future__ import annotations

import logging

from darkfactory.errors import DarkFactoryError
from darkfactory.utils.subprocess_runner import run

log = logging.getLogger(__name__)


def require_docker() -> None:
    """Verify Docker is installed and the daemon is running.

    Raises DarkFactoryError if Docker is not available.
    """
    result = run(["docker", "info"], timeout=10)
    if result.exit_code != 0:
        raise DarkFactoryError(
            "Docker is not available. Ensure Docker is installed "
            "and the daemon is running."
        )
    log.debug("docker is available")


def docker_version() -> str:
    """Return the Docker version string, or empty string on failure."""
    result = run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=5)
    if result.exit_code == 0:
        return result.stdout.strip()
    return ""
