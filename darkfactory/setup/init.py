"""Project initialization — zero-config bootstrap for dark-factory.

Creates .dark-factory/ directory structure, detects project characteristics,
generates a working config.toml, and validates prerequisites.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from darkfactory.setup.detect import DetectionResult, detect_project

log = logging.getLogger(__name__)


@dataclass
class InitResult:
    """Result of project initialization."""

    success: bool = False
    config_path: str = ""
    language: str = ""
    source_dirs: list[str] | None = None
    test_dir: str = ""
    framework: str = ""
    errors: list[str] | None = None

    def __post_init__(self) -> None:
        if self.source_dirs is None:
            self.source_dirs = []
        if self.errors is None:
            self.errors = []


# Directories created during init.
_REQUIRED_DIRS = [
    ".dark-factory",
    ".dark-factory/specs",
    ".dark-factory/scenarios",
    ".dark-factory/reviews",
    ".dark-factory/reports",
    ".dark-factory/audit",
    ".dark-factory/state",
    ".dark-factory/plans",
]


def init_project(
    project_dir: Path,
    *,
    non_interactive: bool = False,
    language: str = "",
) -> InitResult:
    """Initialize a Dark Factory project.

    Creates directory structure, detects project characteristics,
    generates config.toml, and validates prerequisites.

    Args:
        project_dir: Project root directory.
        non_interactive: Skip interactive prompts, use detected defaults.
        language: Override language detection (for --non-interactive).

    Returns:
        InitResult with success status and detected characteristics.
    """
    result = InitResult()

    # Step 1: Validate prerequisites.
    prereq_errors = check_prerequisites()
    if prereq_errors:
        result.errors = prereq_errors
        return result

    # Step 2: Detect project structure.
    detection = detect_project(project_dir)

    # Apply language override if provided.
    if language:
        detection.language = language

    # Step 3: Interactive confirmation (if not --non-interactive).
    if not non_interactive and detection.language != "unknown":
        _print_detection(detection)
        if sys.stdin.isatty():
            answer = input("\nProceed with these settings? [Y/n] ").strip().lower()
            if answer and answer not in ("y", "yes"):
                print("Aborted.")
                return result
    elif detection.language == "unknown" and not language:
        if result.errors is None:
            result.errors = []
        result.errors.append(
            "Could not detect project language. "
            "Use --language to specify: dark-factory init --language python"
        )
        return result

    # Step 4: Create directory structure.
    _create_directories(project_dir)

    # Step 5: Generate config.toml.
    config_path = project_dir / ".dark-factory" / "config.toml"
    if config_path.exists() and not non_interactive and sys.stdin.isatty():
        answer = input(f"\n{config_path} already exists. Overwrite? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Keeping existing config.")
            result.success = True
            result.config_path = str(config_path)
            _populate_result(result, detection)
            return result

    _write_config(config_path, detection)

    # Step 6: Populate result.
    result.success = True
    result.config_path = str(config_path)
    _populate_result(result, detection)

    return result


def check_prerequisites() -> list[str]:
    """Check that all prerequisites are installed.

    Returns a list of actionable error messages (empty = all good).
    """
    from darkfactory.utils.subprocess_runner import run

    errors: list[str] = []

    # Docker.
    docker_which = run(["which", "docker"], timeout=5)
    if docker_which.exit_code != 0:
        errors.append(
            "Docker not installed.\n"
            "  macOS:  brew install --cask docker\n"
            "  Linux:  curl -fsSL https://get.docker.com | sh"
        )
    else:
        docker_info = run(["docker", "info"], timeout=10)
        if docker_info.exit_code != 0:
            errors.append(
                "Docker daemon not running.\n"
                "  macOS:  open -a Docker\n"
                "  Linux:  sudo systemctl start docker"
            )

    # Claude Code.
    claude_which = run(["which", "claude"], timeout=5)
    if claude_which.exit_code != 0:
        errors.append(
            "Claude Code not installed.\n"
            "  Install: npm install -g @anthropic-ai/claude-code"
        )

    # Git.
    git_which = run(["which", "git"], timeout=5)
    if git_which.exit_code != 0:
        errors.append(
            "Git not installed.\n"
            "  macOS:  xcode-select --install\n"
            "  Linux:  sudo apt install git  (or equivalent)"
        )
    else:
        # Check if inside a git repo.
        git_check = run(["git", "rev-parse", "--is-inside-work-tree"], timeout=5)
        if git_check.exit_code != 0:
            errors.append(
                "Not inside a git repository.\n"
                "  Run: git init"
            )

    # Python version (we need 3.11+ for tomllib).
    if sys.version_info < (3, 11):
        errors.append(
            f"Python {sys.version_info.major}.{sys.version_info.minor} detected, but 3.11+ required.\n"
            "  Upgrade: https://www.python.org/downloads/"
        )

    return errors


def _print_detection(detection: DetectionResult) -> None:
    """Print detected project characteristics."""
    print(f"\nDetected: {detection.language.capitalize()} project")
    if detection.source_dirs:
        print(f"  Source dirs: {', '.join(detection.source_dirs)}")
    if detection.test_dir:
        print(f"  Test dir:    {detection.test_dir}")
    if detection.framework:
        print(f"  Framework:   {detection.framework}")
    if detection.build_cmd:
        print(f"  Build:       {detection.build_cmd}")
    if detection.lint_cmd:
        print(f"  Lint:        {detection.lint_cmd}")
    if detection.test_cmd:
        print(f"  Test:        {detection.test_cmd}")


def _create_directories(project_dir: Path) -> None:
    """Create the .dark-factory directory structure."""
    for dir_path in _REQUIRED_DIRS:
        full_path = project_dir / dir_path
        full_path.mkdir(parents=True, exist_ok=True)
    print("\nDirectory structure created: .dark-factory/")


def _write_config(config_path: Path, detection: DetectionResult) -> None:
    """Generate and write config.toml from detection results."""
    lines: list[str] = []

    # Header comment.
    lines.append("# Dark Factory configuration")
    lines.append("# Generated by `dark-factory init`")
    lines.append("")

    # [core]
    lines.append("[core]")
    lines.append(f'language = "{detection.language}"')
    if detection.source_dirs:
        dirs_str = ", ".join(f'"{d}"' for d in detection.source_dirs)
        lines.append(f"source_dirs = [{dirs_str}]")
    if detection.test_dir:
        lines.append(f'test_dir = "{detection.test_dir}"')
    if detection.test_cmd:
        lines.append(f'test_cmd = "{detection.test_cmd}"')
    lines.append("")

    # [gates]
    if detection.build_cmd or detection.lint_cmd or detection.typecheck_cmd:
        lines.append("[gates]")
        if detection.build_cmd:
            lines.append(f'build_cmd = "{detection.build_cmd}"')
        if detection.lint_cmd:
            lines.append(f'lint_cmd = "{detection.lint_cmd}"')
        if detection.typecheck_cmd:
            lines.append(f'typecheck_cmd = "{detection.typecheck_cmd}"')
        lines.append("")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("\n".join(lines) + "\n")
    print(f"Config written: {config_path}")


def _populate_result(result: InitResult, detection: DetectionResult) -> None:
    """Copy detection results into InitResult."""
    result.language = detection.language
    result.source_dirs = detection.source_dirs
    result.test_dir = detection.test_dir
    result.framework = detection.framework
