"""Framework integration into target projects.

Replaces scripts/integrate.sh (586 lines). Sets up Dark Factory in a
new or existing project: directories, config, Docker images, CLAUDE.md.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from darkfactory.config.loader import migrate_config
from darkfactory.git.detect import detect_toolchain
from darkfactory.utils.subprocess_runner import run

log = logging.getLogger(__name__)

# Required Dark Factory directories.
_REQUIRED_DIRS = [
    ".dark-factory/specs",
    ".dark-factory/scenarios",
    ".dark-factory/reviews",
    ".dark-factory/reports",
    ".dark-factory/audit",
    ".dark-factory/state",
    ".dark-factory/plans",
]

# Framework directories to install.
_FRAMEWORK_DIRS = [
    "agents",
    "templates",
    "docs",
    "skills",
]

# Language-specific defaults for config generation.
_LANGUAGE_DEFAULTS: dict[str, dict[str, str]] = {
    "node": {
        "build_cmd": "npm run build",
        "lint_cmd": "npm run lint",
        "typecheck_cmd": "npx tsc --noEmit",
        "test_cmd": "npm test",
        "source_dirs": "src",
        "test_dir": "tests",
    },
    "python": {
        "build_cmd": "",
        "lint_cmd": "ruff check .",
        "typecheck_cmd": "mypy .",
        "test_cmd": "python -m pytest",
        "source_dirs": "src",
        "test_dir": "tests",
    },
    "go": {
        "build_cmd": "go build ./...",
        "lint_cmd": "golangci-lint run",
        "typecheck_cmd": "go vet ./...",
        "test_cmd": "go test ./...",
        "source_dirs": ".",
        "test_dir": ".",
    },
    "rust": {
        "build_cmd": "cargo build",
        "lint_cmd": "cargo clippy",
        "typecheck_cmd": "",
        "test_cmd": "cargo test",
        "source_dirs": "src",
        "test_dir": "tests",
    },
}


def integrate(
    project_dir: Path,
    framework_dir: Path,
    *,
    language: str = "",
    force: bool = False,
    symlink: bool = False,
    dry_run: bool = False,
) -> bool:
    """Integrate Dark Factory into a project.

    Steps:
    1. Create .dark-factory directory structure.
    2. Install framework files (agents, templates, docs, skills).
    3. Detect language and generate config.
    4. Build Docker images.
    5. Update CLAUDE.md.
    6. Run validation.

    Returns True on success.
    """
    log.info("integrating Dark Factory into %s", project_dir)

    # Step 1: Create directories.
    _create_directories(project_dir, dry_run)

    # Step 2: Install framework files.
    _install_framework(project_dir, framework_dir, symlink=symlink, force=force, dry_run=dry_run)

    # Step 3: Language detection and config.
    if not language:
        language = detect_toolchain(project_dir)
    _generate_config(project_dir, language, dry_run)

    # Step 4: Build Docker images (non-fatal — integration succeeds without Docker).
    docker_ok = _build_docker_images(framework_dir, language, dry_run)
    if not docker_ok:
        log.warning("Docker image build failed — integration complete but pipeline will not run until images are built")

    # Step 5: Update CLAUDE.md.
    _update_claude_md(project_dir, dry_run)

    log.info("integration complete")
    return True


def _create_directories(project_dir: Path, dry_run: bool) -> None:
    """Create the .dark-factory directory structure."""
    for d in _REQUIRED_DIRS:
        path = project_dir / d
        if dry_run:
            log.info("[dry-run] would create %s", path)
        else:
            path.mkdir(parents=True, exist_ok=True)
            log.info("created %s", path)


def _install_framework(
    project_dir: Path,
    framework_dir: Path,
    *,
    symlink: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    """Install framework files into the project."""
    target = project_dir / "dark-factory"

    if target.exists() and not force:
        log.info("framework directory already exists: %s", target)
        return

    if dry_run:
        log.info("[dry-run] would install framework to %s", target)
        return

    if symlink:
        if target.exists():
            target.unlink() if target.is_symlink() else shutil.rmtree(target)
        target.symlink_to(framework_dir)
        log.info("symlinked %s → %s", target, framework_dir)
    else:
        for d in _FRAMEWORK_DIRS:
            src = framework_dir / d
            dst = target / d
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                log.info("installed %s", d)


def _generate_config(project_dir: Path, language: str, dry_run: bool) -> None:
    """Generate config.toml for the project."""
    config_path = project_dir / ".dark-factory" / "config.toml"
    if config_path.exists():
        log.info("config already exists: %s", config_path)
        return

    if dry_run:
        log.info("[dry-run] would generate config for language=%s", language)
        return

    defaults = _LANGUAGE_DEFAULTS.get(language, _LANGUAGE_DEFAULTS["node"])

    lines = [
        "[core]",
        f'language = "{language}"',
        f'source_dirs = ["{defaults["source_dirs"]}"]',
        f'test_dir = "{defaults["test_dir"]}"',
    ]
    if defaults.get("test_cmd"):
        lines.append(f'test_cmd = "{defaults["test_cmd"]}"')
    lines.append("")
    lines.append("[agents]")
    lines.append("max_turns = 200")
    lines.append("timeout = 600")
    lines.append("")
    lines.append("[gates]")
    if defaults.get("build_cmd"):
        lines.append(f'build_cmd = "{defaults["build_cmd"]}"')
    if defaults.get("lint_cmd"):
        lines.append(f'lint_cmd = "{defaults["lint_cmd"]}"')
    if defaults.get("typecheck_cmd"):
        lines.append(f'typecheck_cmd = "{defaults["typecheck_cmd"]}"')
    lines.append("")
    lines.append("[autonomy]")
    lines.append("level = 3")
    lines.append("max_iterations = 3")
    lines.append("")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("\n".join(lines) + "\n")
    log.info("generated config: %s (language=%s)", config_path, language)


def _build_docker_images(framework_dir: Path, language: str, dry_run: bool) -> bool:
    """Build Docker images for the language. Returns True on success."""
    build_script = framework_dir / "docker" / "build-images.sh"
    if not build_script.exists():
        log.warning("build script not found: %s", build_script)
        return False

    if dry_run:
        log.info("[dry-run] would build Docker images for %s", language)
        return True

    result = run(["bash", str(build_script), language], timeout=300)
    if result.exit_code != 0:
        log.warning("Docker image build failed: %s", result.stderr)
        return False

    log.info("built Docker images for %s", language)
    return True


def _update_claude_md(project_dir: Path, dry_run: bool) -> None:
    """Add Dark Factory section to CLAUDE.md if not already present."""
    claude_md = project_dir / "CLAUDE.md"

    if claude_md.exists():
        content = claude_md.read_text()
        if "dark factory" in content.lower():
            log.info("CLAUDE.md already contains Dark Factory section")
            return
    else:
        content = ""

    section = """
## Dark Factory

This project uses the Dark Factory specification-driven AI development framework.

### Critical Rules
- Implementation agents MUST NOT read `.dark-factory/scenarios/` or `.dark-factory/reports/`
- Scenario generation agents MUST NOT read implementation source code
- All code changes MUST begin with a validated specification
- Quality gates are non-negotiable
"""

    if dry_run:
        log.info("[dry-run] would update CLAUDE.md")
        return

    with claude_md.open("a") as f:
        f.write(section)
    log.info("updated CLAUDE.md with Dark Factory section")
