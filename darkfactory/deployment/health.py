"""Built-in health check strategies — HTTP, TCP, Docker, command.

Provides 4 reusable health check implementations that cover 80% of use
cases without requiring custom scripts.
"""

from __future__ import annotations

import logging
import shlex
import socket
import time

from darkfactory.utils.subprocess_runner import run as run_subprocess

log = logging.getLogger(__name__)

_BACKOFF_CAP = 30  # max inter-attempt delay seconds


def check_http(url: str, timeout: int = 60, max_retries: int = 10) -> bool:
    """HTTP GET health check with exponential backoff.

    Returns True if any attempt receives a 2xx response within the deadline.
    """
    if not url:
        log.warning("no health_check_url configured")
        return False

    deadline = time.monotonic() + timeout
    attempt = 0

    while time.monotonic() < deadline and attempt < max_retries:
        result = run_subprocess(
            ["curl", "--connect-timeout", "5", "--max-time", "10",
             "-sf", "-o", "/dev/null", url],
            timeout=15,
        )
        if result.exit_code == 0:
            log.info("http health check passed: %s (attempt %d)", url, attempt + 1)
            return True

        attempt += 1
        delay = min(2 ** (attempt - 1), _BACKOFF_CAP)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(delay, remaining))

    log.warning("http health check timed out after %ds: %s", timeout, url)
    return False


def check_tcp(host: str, port: int, timeout: int = 5) -> bool:
    """TCP connection health check.

    Returns True if a TCP connection can be established.
    """
    if not host or not port:
        log.warning("no health_check_host/port configured")
        return False

    try:
        with socket.create_connection((host, port), timeout=timeout):
            log.info("tcp health check passed: %s:%d", host, port)
            return True
    except (OSError, ConnectionRefusedError, TimeoutError) as e:
        log.warning("tcp health check failed: %s:%d — %s", host, port, e)
        return False


def check_docker(container: str, timeout: int = 5) -> bool:
    """Docker container health check via `docker inspect`.

    Returns True if the container's health status is "healthy".
    """
    if not container:
        log.warning("no health_check_container configured")
        return False

    result = run_subprocess(
        ["docker", "inspect", "--format", "{{.State.Health.Status}}", container],
        timeout=timeout,
    )
    status = result.stdout.strip()
    if result.exit_code == 0 and status == "healthy":
        log.info("docker health check passed: %s", container)
        return True

    log.warning("docker health check failed: %s (status=%s)", container, status)
    return False


def check_command(cmd: str, project_dir: str, timeout: int = 30) -> bool:
    """Arbitrary command health check. Exit 0 = healthy."""
    if not cmd:
        log.warning("no health check command configured")
        return False

    result = run_subprocess(shlex.split(cmd), timeout=timeout, cwd=project_dir)
    if result.exit_code == 0:
        log.info("command health check passed")
        return True

    log.warning("command health check failed (exit %d)", result.exit_code)
    return False


def run_health_check(
    strategy: str,
    *,
    url: str = "",
    host: str = "",
    port: int = 0,
    container: str = "",
    cmd: str = "",
    project_dir: str = "",
    timeout: int = 60,
) -> bool:
    """Dispatch to the appropriate health check strategy."""
    if strategy == "http":
        return check_http(url, timeout=timeout)
    elif strategy == "tcp":
        return check_tcp(host, port, timeout=timeout)
    elif strategy == "docker":
        return check_docker(container, timeout=timeout)
    elif strategy == "command":
        return check_command(cmd, project_dir, timeout=timeout)
    else:
        log.warning("unknown health check strategy: %s", strategy)
        return False
