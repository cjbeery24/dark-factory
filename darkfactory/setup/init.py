"""Project initialization — zero-config bootstrap for dark-factory.

Creates .dark-factory/ directory structure, detects project characteristics,
generates a working config.toml, and validates prerequisites.
"""

from __future__ import annotations

import logging
import shutil
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
    ".dark-factory/shipped-scenarios",
]


def init_project(
    project_dir: Path,
    *,
    non_interactive: bool = False,
    language: str = "",
    update: bool = False,
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
    _create_directories(project_dir, update=update)

    # Step 5: Copy framework files into project.
    _copy_framework_files(project_dir, update=update)

    # Step 6: Install skills globally.
    _install_skills_globally()

    # Step 7: Generate config.toml.
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

    # Step 7b: Write bootstrap delta (operational config → delta log).
    _write_bootstrap_delta(project_dir, detection)

    # Step 8: Populate result.
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


_DARK_FACTORY_GITIGNORE = """\
# Framework files — managed by dark-factory, refreshed via `dark-factory init --update`.
# Do not commit these; they are not project customizations.
agents/
templates/

# Runtime artifacts — generated during pipeline runs, not for version control.
scenarios/
audit/*
state/*
reports/*
metrics.jsonl
events.jsonl
events.jsonl.lock
worktrees/

# Ephemeral per-run artifacts in reviews/ — feedback, oracle output, escalation
# reports are only useful during the iteration loop. Validation files and
# review.md stay (review.md is copied to shipped-scenarios/<spec>/ on ship).
reviews/*-feedback-iter*.md
reviews/*-oracle-output-iter*.md
reviews/*-escalation.md

# Holdouts are adversarial scenarios written blind — not validated against
# shipped code. Skip unless building a regression suite.
holdouts/

# Advisory pre-check findings — stale after implementation, derivable.
precheck/

# Preserve directory placeholders for ignored directories.
!audit/.gitkeep
!state/.gitkeep
!reports/.gitkeep

# shipped-scenarios/ is tracked — it contains the validated test suite,
# code review, defect registry, and metadata for each shipped spec.
# See docs/POST-SHIP-COMMIT.md for the complete post-ship commit guide.
"""


def _create_directories(project_dir: Path, *, update: bool = False) -> None:
    """Create the .dark-factory directory structure."""
    for dir_path in _REQUIRED_DIRS:
        full_path = project_dir / dir_path
        full_path.mkdir(parents=True, exist_ok=True)

    gitignore_path = project_dir / ".dark-factory" / ".gitignore"
    if not gitignore_path.exists() or update:
        gitignore_path.write_text(_DARK_FACTORY_GITIGNORE, encoding="utf-8")

    # shipped-scenarios/ is tracked; create a .gitkeep so the empty dir is committed.
    gitkeep = project_dir / ".dark-factory" / "shipped-scenarios" / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.touch()

    print("\nDirectory structure created: .dark-factory/")


def _write_config(config_path: Path, detection: DetectionResult) -> None:
    """Generate and write config.toml from detection results.

    Phase 3: config.toml holds only pipeline tuning and the language
    field (needed for Docker image selection before the env contract
    loads). All operational config (commands, source_dirs, etc.) goes
    into the bootstrap delta via _write_bootstrap_delta().
    """
    lines: list[str] = []

    # Header comment.
    lines.append("# Dark Factory configuration")
    lines.append("# Generated by `dark-factory init`")
    lines.append("# Operational config (commands, dirs) lives in the environment")
    lines.append("# contract. See .dark-factory/architecture/environment.toml")
    lines.append("")

    # [core] — language only (needed for Docker image selection).
    lines.append("[core]")
    lines.append(f'language = "{detection.language}"')
    lines.append("")

    # [enterprise] — commented out by default.
    lines.append("# [enterprise]")
    lines.append("# enabled = true  # Uncomment to enable: deployment, Slack/webhook notifications, trust scoring, anomaly detection, OTEL")
    lines.append("")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("\n".join(lines) + "\n")
    print(f"Config written: {config_path}")


def _write_bootstrap_delta(project_dir: Path, detection: DetectionResult) -> None:
    """Write detection results as a bootstrap delta in the environment delta log.

    This replaces the old approach of writing operational fields into
    config.toml's [gates] section. The bootstrap delta is the seed for
    the environment contract — the Architect can override any field via
    subsequent deltas.
    """
    from darkfactory.architecture.amend import _next_delta_number, _render_delta
    from darkfactory.architecture.deltas import hash_view, materialize
    from darkfactory.architecture.environment import EnvironmentContract

    # Build ops from detection results.
    # Map: (DetectionResult field, environment contract path)
    _DETECT_TO_BOOTSTRAP = (
        ("language", "runtime.language"),
        ("package_manager", "runtime.package_manager"),
        ("build_cmd", "commands.build"),
        ("lint_cmd", "commands.lint"),
        ("typecheck_cmd", "commands.typecheck"),
        ("test_cmd", "commands.test"),
        ("setup_cmd", "commands.setup"),
        ("server_cmd", "commands.server_start"),
    )

    ops: list[tuple[str, object]] = []
    for detect_field, env_path in _DETECT_TO_BOOTSTRAP:
        value = getattr(detection, detect_field, "")
        if value and value != "unknown":
            ops.append((env_path, value))

    # source_dirs and test_dir use list/string → list paths.
    if detection.source_dirs:
        ops.append(("layout.source_dirs", detection.source_dirs))
    if detection.test_dir:
        ops.append(("layout.test_dirs", [detection.test_dir]))

    if not ops:
        return

    # Env path uniqueness: all keys in _DETECT_TO_BOOTSTRAP map to distinct
    # env paths, so sort is safe even with mixed value types.
    ops.sort()

    arch_dir = project_dir / ".dark-factory" / "architecture"
    deltas_dir = arch_dir / "environment.deltas"
    env_path = arch_dir / "environment.toml"
    deltas_dir.mkdir(parents=True, exist_ok=True)

    # Guard against re-init: if deltas already exist, skip bootstrap.
    # The user can amend the environment via the Architect or by editing
    # deltas directly. Re-running init should not corrupt the delta chain.
    from darkfactory.architecture.deltas import list_deltas
    if list_deltas(deltas_dir):
        log.info("bootstrap delta: skipping — deltas already exist in %s", deltas_dir)
        return

    parent_hash = hash_view(EnvironmentContract())
    n = _next_delta_number(deltas_dir)
    filename = f"{n:04d}-bootstrap-from-detection.toml"
    delta_path = deltas_dir / filename

    # Snapshot for rollback (same pattern as amend.py).
    env_snapshot: bytes | None
    if env_path.exists():
        env_snapshot = env_path.read_bytes()
    else:
        env_snapshot = None

    delta_path.write_text(
        _render_delta("bootstrap-from-detection", parent_hash, ops),
        encoding="utf-8",
    )
    log.info("bootstrap delta: wrote %s with %d op(s)", delta_path.name, len(ops))

    try:
        materialize(deltas_dir, env_path)
    except Exception:
        if env_snapshot is not None:
            env_path.write_bytes(env_snapshot)
        elif env_path.exists():
            env_path.unlink()
        delta_path.unlink(missing_ok=True)
        raise

    print(f"Environment contract written: {env_path}")


def _copy_framework_files(project_dir: Path, *, update: bool = False) -> None:
    """Copy agents/ and templates/ from the framework into .dark-factory/.

    This lets skills read agent definitions and spec templates from the local
    project instead of the installed package path, avoiding out-of-project
    permission prompts in Claude Code.
    """
    import darkfactory
    framework_dir = Path(darkfactory.__file__).parent.parent

    copied: list[str] = []
    skipped: list[str] = []

    for dirname in ("agents", "templates"):
        src = framework_dir / dirname
        dst = project_dir / ".dark-factory" / dirname
        if not src.is_dir():
            log.warning("framework directory not found: %s", src)
            continue
        if dst.exists():
            if update:
                shutil.rmtree(dst)
            else:
                skipped.append(dirname)
                continue
        shutil.copytree(src, dst)
        copied.append(dirname)

    if copied:
        print(f"Framework files copied: {', '.join(f'.dark-factory/{d}/' for d in copied)}")
    if skipped:
        print(f"Framework files already present (use --update to refresh): {', '.join(skipped)}")


def _install_skills_globally() -> None:
    """Symlink dark-factory skills into ~/.claude/skills/ so they're available in every project."""
    import darkfactory
    framework_skills = Path(darkfactory.__file__).parent.parent / ".claude" / "skills"
    if not framework_skills.is_dir():
        log.warning("skills directory not found: %s", framework_skills)
        return

    global_skills = Path.home() / ".claude" / "skills"
    global_skills.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for skill_dir in sorted(framework_skills.iterdir()):
        if not skill_dir.is_dir() or not skill_dir.name.startswith("df-"):
            continue
        link = global_skills / skill_dir.name
        if link.is_symlink() and link.resolve() == skill_dir.resolve():
            continue  # already up to date
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(skill_dir)
        installed.append(skill_dir.name)

    if installed:
        print(f"Skills installed globally: {', '.join(installed)}")
    else:
        print("Skills already up to date in ~/.claude/skills/")


def _populate_result(result: InitResult, detection: DetectionResult) -> None:
    """Copy detection results into InitResult."""
    result.language = detection.language
    result.source_dirs = detection.source_dirs
    result.test_dir = detection.test_dir
    result.framework = detection.framework
