"""Scenario test execution — run scenario tests against implementation.

Replaces run-scenarios.sh.
"""

from __future__ import annotations

import logging
from pathlib import Path

from darkfactory.git.detect import detect_toolchain
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import RunResult, run

log = logging.getLogger(__name__)


def run_scenarios(
    spec_name: str,
    config: PipelineConfig,
    project_dir: Path,
    *,
    timeout: int = 0,
    dry_run: bool = False,
) -> RunResult:
    """Run scenario tests for a spec.

    Priority for test command:
    1. config.scenario_test_cmd (with {SCENARIOS_DIR} substitution)
    2. Auto-detect based on toolchain

    Returns RunResult with exit code and output.
    """
    scenarios_dir = project_dir / ".dark-factory" / "scenarios" / spec_name

    if not scenarios_dir.exists():
        log.warning("no scenarios directory: %s", scenarios_dir)
        return RunResult(exit_code=2, stdout="", stderr="no scenarios directory")

    cmd = _build_test_cmd(config, project_dir, scenarios_dir)
    if not cmd:
        log.warning("could not determine test command")
        return RunResult(exit_code=2, stdout="", stderr="no test command")

    effective_timeout = timeout or config.gate_timeout or 300
    return run(cmd, cwd=str(project_dir), timeout=effective_timeout)


def _build_test_cmd(
    config: PipelineConfig,
    project_dir: Path,
    scenarios_dir: Path,
) -> list[str]:
    """Build the scenario test command."""
    if config.scenario_test_cmd:
        expanded = config.scenario_test_cmd.replace(
            "{SCENARIOS_DIR}", str(scenarios_dir),
        )
        return ["bash", "-c", expanded]

    toolchain = detect_toolchain(project_dir)
    rel_scenarios = str(scenarios_dir.relative_to(project_dir))

    if toolchain == "node":
        if (project_dir / "vitest.config.ts").exists() or (project_dir / "vitest.config.js").exists():
            return ["npx", "vitest", "run", rel_scenarios]
        return ["npx", "jest", "--no-coverage", rel_scenarios]
    elif toolchain == "python":
        return ["python3", "-m", "pytest", rel_scenarios, "-v"]
    elif toolchain == "go":
        return ["go", "test", f"./{rel_scenarios}/..."]
    elif toolchain == "rust":
        return ["cargo", "test"]
    return []
