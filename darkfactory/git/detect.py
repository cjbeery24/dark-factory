"""Toolchain and timeout command detection.

Replaces lib/detect.sh — detect_toolchain and detect_timeout_cmd.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

# Toolchain detection: file → language mapping.
_TOOLCHAIN_FILES: list[tuple[str, str]] = [
    ("package.json", "node"),
    ("pyproject.toml", "python"),
    ("go.mod", "go"),
    ("Cargo.toml", "rust"),
]


def detect_toolchain(project_root: Path) -> str:
    """Detect the project language from manifest files.

    Returns one of: node, python, go, rust, unknown.
    """
    for filename, language in _TOOLCHAIN_FILES:
        if (project_root / filename).exists():
            log.debug("detected toolchain: %s (from %s)", language, filename)
            return language
    log.debug("no toolchain detected, returning 'unknown'")
    return "unknown"


def detect_timeout_cmd() -> str:
    """Find the timeout command (GNU coreutils).

    Returns 'timeout', 'gtimeout', or empty string if neither found.
    macOS ships without GNU timeout; Homebrew installs it as gtimeout.
    """
    if shutil.which("timeout"):
        return "timeout"
    if shutil.which("gtimeout"):
        return "gtimeout"
    log.warning("no timeout command found (install coreutils for timeout support)")
    return ""
