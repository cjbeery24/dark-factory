"""Container lifecycle — run, stop, cleanup.

Replaces _execute_container, cleanup_containers from docker.sh.
"""

from __future__ import annotations

import logging
import re

from darkfactory.audit.logger import audit_log
from darkfactory.utils.subprocess_runner import RunResult, run

log = logging.getLogger(__name__)

_CREDENTIAL_RE = re.compile(
    r'(CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|'
    r'AWS_SECRET_ACCESS_KEY|GITHUB_TOKEN|'
    r'[A-Z_]*(?:SECRET|TOKEN|KEY|PASSWORD|CREDENTIAL)[A-Z_]*)=[^\s]+',
    re.IGNORECASE,
)


def _redact_cmd(cmd_str: str) -> str:
    """Redact credential values from a command string."""
    return _CREDENTIAL_RE.sub(lambda m: f'{m.group(1)}=REDACTED', cmd_str)


def execute_container(
    docker_args: list[str],
    image: str,
    cmd: list[str],
    *,
    timeout: int | None = None,
    dry_run: bool = False,
    audit_path: str = "",
    input_text: str | None = None,
) -> RunResult:
    """Run a Docker container and return the result.

    Args:
        docker_args: Pre-built Docker CLI args (from args.py).
        image: Docker image name.
        cmd: Command to run inside the container.
        timeout: Timeout in seconds.
        dry_run: If True, log but don't execute.
        audit_path: Path to audit log for logging the command.

    Returns:
        RunResult with exit code and captured output.
    """
    full_cmd = ["docker", "run", "--rm", *docker_args, image, *cmd]
    redacted = _redact_cmd(' '.join(full_cmd))
    log.debug("container cmd: %s", redacted)

    if audit_path:
        from pathlib import Path
        audit_log(Path(audit_path), f"CONTAINER: {redacted}")

    if input_text is not None:
        full_cmd.insert(full_cmd.index("--rm") + 1, "-i")
    return run(full_cmd, timeout=timeout, dry_run=dry_run, input_text=input_text)


def stop_container(container_id: str, *, timeout: int = 10, dry_run: bool = False) -> None:
    """Stop a running container."""
    run(["docker", "stop", "-t", str(timeout), container_id], dry_run=dry_run)


def remove_container(container_id: str, *, dry_run: bool = False) -> None:
    """Force-remove a container."""
    run(["docker", "rm", "-f", container_id], dry_run=dry_run)


def cleanup_containers(run_id: str, *, dry_run: bool = False) -> None:
    """Stop and remove all containers associated with a run ID.

    Uses Docker label filtering to find containers.
    """
    result = run(
        ["docker", "ps", "-q", "--filter", f"label=df-run-id={run_id}"],
    )
    if result.exit_code == 0 and result.stdout.strip():
        container_ids = result.stdout.strip().split("\n")
        for cid in container_ids:
            cid = cid.strip()
            if cid:
                stop_container(cid, dry_run=dry_run)
                remove_container(cid, dry_run=dry_run)
                log.debug("cleaned up container: %s", cid)
