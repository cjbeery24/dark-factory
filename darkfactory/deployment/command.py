"""Safe command execution with path validation.

Addresses Section 4.12 requirement: require *_cmd config values to be paths
to scripts, not arbitrary shell strings. Validate existence and reject shell
metacharacters before execution.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Reject shell metacharacters including redirects.
SHELL_METACHAR_RE = re.compile(r'[;|&`$()<>]')

# System paths exempt from project-dir containment check.
_ALLOWED_SYSTEM_PREFIXES = ("/usr/bin/", "/usr/local/bin/", "/bin/", "/usr/sbin/")


def validate_command(cmd: str, project_dir: str, field_name: str) -> Path:
    """Validate that a command config value is a safe script path.

    Extracts the script path (first whitespace-delimited token), resolves
    relative to project_dir, verifies it is a regular executable file,
    and rejects shell metacharacters.

    Path.resolve() follows symlinks, so a symlink pointing outside
    project_dir is caught by the containment check.

    Raises ValueError on validation failure.
    """
    if not cmd.strip():
        raise ValueError(f"{field_name}: command is empty")

    if SHELL_METACHAR_RE.search(cmd):
        raise ValueError(
            f"{field_name}: shell metacharacters are not allowed "
            f"(found in {cmd!r}). Use a script file instead."
        )

    tokens = cmd.split()
    script_token = tokens[0]

    script_path = Path(script_token)
    if not script_path.is_absolute():
        script_path = Path(project_dir) / script_path

    # resolve() follows symlinks — a symlink to outside project_dir is caught below.
    resolved = script_path.resolve()

    project_resolved = Path(project_dir).resolve()
    is_in_project = str(resolved).startswith(str(project_resolved) + os.sep) or resolved == project_resolved
    is_system = any(str(resolved).startswith(p) for p in _ALLOWED_SYSTEM_PREFIXES)

    if not is_in_project and not is_system:
        raise ValueError(
            f"{field_name}: script {resolved} is outside project directory "
            f"({project_resolved}) and not a system binary"
        )

    if not resolved.exists():
        raise ValueError(f"{field_name}: script not found: {resolved}")

    if not resolved.is_file():
        raise ValueError(f"{field_name}: {resolved} is not a regular file")

    if not os.access(resolved, os.X_OK):
        raise ValueError(f"{field_name}: {resolved} is not executable")

    return resolved


def run_command(
    cmd: str,
    project_dir: str,
    *,
    env: dict[str, str] | None = None,
    timeout: int = 120,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Execute a validated command safely.

    Validates the command first, then runs via subprocess.run with argv —
    NOT bash -c. Merges os.environ with provided env dict.
    """
    resolved = validate_command(cmd, project_dir, field_name="run_command")

    tokens = cmd.split()
    args = tokens[1:]
    argv = [str(resolved)] + args

    run_env = dict(os.environ)
    if env:
        run_env.update(env)

    log.debug("run_command: %s (timeout=%ds)", argv, timeout)

    return subprocess.run(
        argv,
        capture_output=capture,
        text=True,
        timeout=timeout,
        cwd=project_dir,
        env=run_env,
    )
