"""Container lifecycle — run, stop, cleanup.

Replaces _execute_container, cleanup_containers from docker.sh.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from darkfactory.audit.logger import audit_log
from darkfactory.errors import ConfigError, TransientFailure
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
    audit_path: str | Path = "",
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
        audit_log(Path(audit_path), f"CONTAINER: {redacted}")

    if input_text is not None:
        full_cmd.insert(full_cmd.index("--rm") + 1, "-i")
    return run(full_cmd, timeout=timeout, dry_run=dry_run, input_text=input_text)


def start_container(
    docker_args: list[str],
    image: str,
    cmd: list[str],
    *,
    name: str | None = None,
    dry_run: bool = False,
    audit_path: str | Path = "",
) -> str:
    """Start a container in detached mode (-d). Returns the container ID.

    Unlike ``execute_container``, this does NOT pass ``--rm`` — the container
    persists until explicitly stopped.

    Args:
        docker_args: Pre-built Docker CLI args (from args.py). Do NOT include
            ``--name`` here; pass it via the ``name`` parameter instead.
        image: Docker image name.
        cmd: Command to run inside the container.
        name: Container name. Added as ``--name`` in the command. Pass ``None``
            to omit naming; passing an empty string is treated the same as None.
        dry_run: If True, log but don't execute. Returns '' on dry run.
        audit_path: Path to audit log for logging the command.

    Returns:
        Container ID (from Docker stdout) or '' in dry_run mode.
    """
    full_cmd = ["docker", "run", "-d", *docker_args]
    if name:
        full_cmd.extend(["--name", name])
    full_cmd.extend([image, *cmd])
    redacted = _redact_cmd(' '.join(full_cmd))
    log.debug("start container: %s", redacted)

    if audit_path:
        audit_log(Path(audit_path), f"START_CONTAINER: {redacted}")

    result = run(full_cmd, dry_run=dry_run)
    if result.exit_code != 0:
        output = result.stderr or result.stdout
        # parens required: !r must apply to the full expression, not just `image`
        raise RuntimeError(f"Failed to start container {(name or image)!r}: {output}")
    return result.stdout.strip()


def stop_container(container_id: str, *, timeout: int = 10, dry_run: bool = False) -> None:
    """Stop a running container."""
    run(["docker", "stop", "-t", str(timeout), container_id], dry_run=dry_run)


def remove_container(container_id: str, *, dry_run: bool = False) -> None:
    """Force-remove a container."""
    run(["docker", "rm", "-f", container_id], dry_run=dry_run)


def check_docker_image(image_name: str) -> None:
    """Raise if image_name is not available on the Docker host.

    Distinguishes daemon-not-running (TransientFailure — retryable) from
    image-missing (ConfigError — user must build the image) so error messages
    are actionable. Used as a pre-flight check before container execution.
    """
    daemon_check = run(["docker", "info"])
    if daemon_check.exit_code != 0:
        raise TransientFailure(
            "Docker daemon is not running. Start Docker Desktop (or Docker Engine) "
            "and then retry."
        )
    result = run(["docker", "image", "inspect", image_name])
    if result.exit_code != 0:
        # Extract language from image name for the build hint (dark-factory-{lang}).
        # Assumes the dark-factory-{lang} naming convention holds.
        lang = image_name.removeprefix("dark-factory-")
        raise ConfigError(
            f"Docker image '{image_name}' is not available. "
            f"Build it first:\n  bash docker/build-images.sh {lang}"
        )


def check_language_image(language: str, spec_name: str = "") -> None:
    """Fail early if the dark-factory-{language} image is not available.

    Per-spec language overrides mean the resolved config.language may differ
    from the global config. This check runs once per invoke_agent / run_quality_gate
    call so per-spec overrides are caught before Docker tries to pull the image.
    """
    try:
        check_docker_image(f"dark-factory-{language}")
    except ConfigError as e:
        if spec_name:
            raise ConfigError(f"Spec '{spec_name}': {e}") from e
        raise


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
