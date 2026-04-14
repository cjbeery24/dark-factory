"""Environment validation — checks Dark Factory setup completeness.

Replaces scripts/validate-setup.sh. Validates directory structure,
framework files, information barriers, configuration, Docker, and tools.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import run

log = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Result of a single validation check."""
    status: str  # "pass", "fail", "warn"
    message: str


@dataclass
class ValidationReport:
    """Full validation report."""
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "pass")

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "fail")

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "warn")

    @property
    def passed(self) -> bool:
        return self.fail_count == 0

    def add_pass(self, msg: str) -> None:
        self.checks.append(CheckResult("pass", msg))

    def add_fail(self, msg: str) -> None:
        self.checks.append(CheckResult("fail", msg))

    def add_warn(self, msg: str) -> None:
        self.checks.append(CheckResult("warn", msg))


def validate_setup(
    project_dir: Path,
    config: PipelineConfig | None = None,
) -> ValidationReport:
    """Run all validation checks and return a report."""
    report = ValidationReport()

    _check_directories(report, project_dir)
    _check_framework_files(report, project_dir, config)
    _check_config(report, project_dir, config)
    _check_claude_md(report, project_dir)
    _check_docker(report, config)
    _check_tools(report, config)

    return report


def _check_directories(report: ValidationReport, project_dir: Path) -> None:
    """Check required directory structure."""
    required = [
        ".dark-factory",
        ".dark-factory/specs",
        ".dark-factory/scenarios",
        ".dark-factory/reviews",
        ".dark-factory/reports",
    ]
    optional = [
        ".dark-factory/audit",
        ".dark-factory/state",
        ".dark-factory/plans",
        ".dark-factory/architecture",
    ]

    for d in required:
        if (project_dir / d).is_dir():
            report.add_pass(f"{d}/ exists")
        else:
            report.add_fail(f"{d}/ missing")

    for d in optional:
        if (project_dir / d).is_dir():
            report.add_pass(f"{d}/ exists")
        else:
            report.add_warn(f"{d}/ missing (will be created on first pipeline run)")


def _check_framework_files(
    report: ValidationReport,
    project_dir: Path,
    config: PipelineConfig | None,
) -> None:
    """Check agent definitions and core scripts."""
    df_home = config.dark_factory_home if config else "dark-factory"
    framework_dir = project_dir / df_home

    if not framework_dir.is_dir():
        # Check if running from within the dark-factory repo.
        if (project_dir / "agents").is_dir():
            framework_dir = project_dir
            report.add_pass("running from within dark-factory repo")
        else:
            report.add_fail(f"framework directory ({df_home}/) missing")
            return

    agent_files = [
        "agents/architect.md",
        "agents/implementer.md",
        "agents/test-engineer.md",
        "agents/qa-holdout.md",
        "agents/spec-validator.md",
        "agents/reviewer.md",
        "agents/oracle.md",
        "agents/proposal-rehearsal.md",
    ]
    for f in agent_files:
        if (framework_dir / f).is_file():
            report.add_pass(f"{df_home}/{f}")
        else:
            report.add_fail(f"{df_home}/{f} missing")



def _check_config(
    report: ValidationReport,
    project_dir: Path,
    config: PipelineConfig | None,
) -> None:
    """Check configuration."""
    toml_path = project_dir / ".dark-factory" / "config.toml"
    sh_path = project_dir / ".dark-factory" / "config.sh"

    if toml_path.is_file():
        report.add_pass("config.toml exists")
    elif sh_path.is_file():
        report.add_warn("config.sh exists (consider migrating to config.toml)")
    else:
        report.add_warn("no config file found — defaults will be used")

    if config:
        lang = config.language
        if lang in ("node", "python", "go", "rust"):
            report.add_pass(f"language={lang} (recognized)")
        elif lang == "unknown":
            report.add_warn("language=unknown — auto-detection will be used")
        else:
            report.add_warn(f"language={lang} (not standard: node|python|go|rust)")


def _check_claude_md(report: ValidationReport, project_dir: Path) -> None:
    """Check CLAUDE.md presence and barrier language."""
    claude_md = project_dir / "CLAUDE.md"
    if not claude_md.is_file():
        report.add_fail("CLAUDE.md not found")
        return

    report.add_pass("CLAUDE.md exists")
    content = claude_md.read_text().lower()

    if "dark factory" in content:
        report.add_pass("CLAUDE.md mentions Dark Factory")
    else:
        report.add_fail("CLAUDE.md does not mention Dark Factory")

    if "scenarios" in content and any(
        phrase in content for phrase in ("never", "must not", "never read", "must not read")
    ):
        report.add_pass("CLAUDE.md contains information barrier rules")
    else:
        report.add_warn("CLAUDE.md may be missing strong information barrier language")


def _check_docker(report: ValidationReport, config: PipelineConfig | None) -> None:
    """Check Docker availability and images."""
    import platform

    result = run(["docker", "info"], timeout=10)
    if result.exit_code != 0:
        # Check if docker is installed at all.
        which = run(["which", "docker"], timeout=5)
        if which.exit_code != 0:
            if platform.system() == "Darwin":
                report.add_fail("Docker not installed — fix: brew install --cask docker")
            else:
                report.add_fail("Docker not installed — fix: curl -fsSL https://get.docker.com | sh")
        else:
            if platform.system() == "Darwin":
                report.add_fail("Docker daemon not running — fix: open -a Docker")
            else:
                report.add_fail("Docker daemon not running — fix: sudo systemctl start docker")
        return

    report.add_pass("Docker installed and running")

    lang = config.language if config else "node"
    for image in [f"dark-factory-{lang}", "dark-factory-base"]:
        result = run(["docker", "image", "inspect", image], timeout=10)
        if result.exit_code == 0:
            report.add_pass(f"Docker image {image} found")
        else:
            report.add_fail(f"Docker image {image} not built — fix: bash docker/build-images.sh {lang}")


def _check_tools(report: ValidationReport, config: PipelineConfig | None) -> None:
    """Check required tool availability."""
    import platform

    _TOOL_INSTALL_HINTS: dict[str, dict[str, str]] = {
        "claude": {
            "Darwin": "npm install -g @anthropic-ai/claude-code",
            "Linux": "npm install -g @anthropic-ai/claude-code",
        },
        "git": {
            "Darwin": "xcode-select --install",
            "Linux": "sudo apt install git",
        },
    }

    os_name = platform.system()
    for tool, hints in _TOOL_INSTALL_HINTS.items():
        result = run(["which", tool], timeout=5)
        if result.exit_code == 0:
            report.add_pass(f"{tool} found")
        else:
            fix = hints.get(os_name, hints.get("Linux", ""))
            report.add_fail(f"{tool} not found — fix: {fix}")

    # Timeout command (macOS uses gtimeout).
    timeout_found = False
    for cmd in ("timeout", "gtimeout"):
        result = run(["which", cmd], timeout=5)
        if result.exit_code == 0:
            report.add_pass(f"{cmd} found")
            timeout_found = True
            break
    if not timeout_found:
        if platform.system() == "Darwin":
            report.add_warn("timeout/gtimeout not found — fix: brew install coreutils")
        else:
            report.add_warn("timeout/gtimeout not found — no execution time limits")
