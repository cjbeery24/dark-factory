"""Deterministic preflight — zero-cost environment validation.

Phase 7 of the config unification plan. Runs before any agent work to
catch broken configurations at $0 cost (no containers, no builds).
Gated behind ``PipelineConfig.preflight_enabled`` for escape hatch.

Checks the effective PipelineConfig (after environment overlay) rather
than the raw environment contract, since PipelineConfig is what agents
and the quality gate actually read. PipelineConfig.__post_init__ already
validates structural invariants (language in allowed set, etc.); this
module catches operational gaps that waste agent money.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from darkfactory.models.config import PipelineConfig

log = logging.getLogger(__name__)


def _extract_binary(cmd: str) -> str | None:
    """Extract the binary name from a shell command string.

    Strips ``cd <dir> &&`` prefixes, then returns the first token.
    This handles simple commands and cd-prefixed commands correctly,
    but will return shell builtins (``export``, ``source``) or env
    assignments for other compound commands — acceptable since binary
    checks are warnings only.

    Returns None if the command is empty or unparseable.
    """
    if not cmd:
        return None
    # Handle 'cd <dir> && <actual_cmd>' prefix.
    if "&&" in cmd:
        parts = cmd.split("&&", 1)
        first_part = parts[0].strip()
        if first_part.split()[0] == "cd":
            cmd = parts[1].strip()
    tokens = cmd.split()
    return tokens[0] if tokens else None


def run_preflight_check(
    config: PipelineConfig,
    project_dir: Path,
) -> list[str]:
    """Validate effective pipeline configuration before any agent work.

    Zero cost: no container startup, no installs, no builds — just
    structural validation and binary existence checks.

    Returns a list of error strings. Empty list = all checks pass.
    """
    errors: list[str] = []

    # 1. At least one gate command must exist, or the quality gate
    #    has nothing to run and agent money is wasted.
    if not config.build_cmd and not config.test_cmd:
        errors.append(
            "Both build_cmd and test_cmd are empty. "
            "The quality gate needs at least one. The Architect should "
            "declare them, or run `dark-factory init` to auto-detect."
        )

    # 2. Binary existence checks for host-visible commands.
    #    Commands run inside Docker containers, so a missing host binary
    #    is a WARNING (logged), not a hard error.
    _COMMAND_CHECKS = (
        ("build_cmd", config.build_cmd),
        ("lint_cmd", config.lint_cmd),
        ("typecheck_cmd", config.typecheck_cmd),
        ("test_cmd", config.test_cmd),
    )
    for label, cmd in _COMMAND_CHECKS:
        if not cmd:
            continue
        binary = _extract_binary(cmd)
        if binary and not shutil.which(binary):
            log.warning(
                "preflight: %s = %r — binary %r not found on host "
                "(may still be available inside the Docker container)",
                label, cmd, binary,
            )

    # 3. Services require Docker on the host.
    if config.services:
        if not shutil.which("docker"):
            errors.append(
                "Services declared but `docker` not found on PATH. "
                "Install Docker: https://docs.docker.com/get-docker/"
            )

    return errors
