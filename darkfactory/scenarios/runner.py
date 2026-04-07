"""Scenario test execution — run scenario tests against implementation.

Replaces run-scenarios.sh.

Phase 5.2: when ``spec_type`` is ``feature`` or ``bugfix``, the scenario
set is the union of (this spec's new scenarios) and (every previously-
shipped spec's archived scenarios under
``.dark-factory/shipped-scenarios/``). Bootstrap and refactor specs run
new scenarios only — bootstrap because the archive is empty by
definition, refactor per the plan's spec-type matrix.

**Scope:** this module is invoked from ``dark-factory scenarios`` (CLI)
and from ``darkfactory.scenarios.regression``. The pipeline's
iteration-loop blind-scenario gate runs through ``quality-gate.sh``
inside Docker, which is **not** yet archive-aware — that wiring lands
in Phase 5.4 when the gate report grows the new/regression split. So
running ``dark-factory scenarios <feat>`` today exercises archived
scenarios; an end-to-end ``dark-factory run <feat>`` does not yet.
"""

from __future__ import annotations

import logging
import shlex
from pathlib import Path

from darkfactory.git.detect import detect_toolchain
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import RunResult, run

log = logging.getLogger(__name__)

# spec_types that pull in archived shipped scenarios. Bootstrap is excluded
# because the archive is empty when it runs; refactor is excluded because
# its scenario set is already meant to fully cover the refactored surface
# and bundling unrelated archive scenarios would muddy attribution.
_ARCHIVE_INCLUSIVE_SPEC_TYPES: frozenset[str] = frozenset(("feature", "bugfix"))


def discover_shipped_scenario_dirs(
    project_dir: Path,
    *,
    exclude: str | None = None,
) -> list[Path]:
    """Return per-spec archive directories under ``shipped-scenarios/``.

    Sorted by spec_id for deterministic command construction. Hidden
    entries (e.g., ``.registry-meta``) are skipped. ``exclude`` lets the
    caller drop the spec currently under test so it isn't double-run.
    """
    root = project_dir / ".dark-factory" / "shipped-scenarios"
    if not root.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if exclude and child.name == exclude:
            continue
        out.append(child)
    return out


def run_scenarios(
    spec_name: str,
    config: PipelineConfig,
    project_dir: Path,
    *,
    timeout: int = 0,
    dry_run: bool = False,
    spec_type: str | None = None,
) -> RunResult:
    """Run scenario tests for a spec.

    Priority for test command:
    1. config.scenario_test_cmd (with {SCENARIOS_DIR} substitution)
    2. Auto-detect based on toolchain

    When ``spec_type`` is feature or bugfix, archived shipped scenarios
    are unioned into the same test invocation so a regression in any
    previously-shipped spec fails the run. Bootstrap/refactor (and
    callers that don't pass spec_type) keep the legacy single-dir
    behavior.

    Returns RunResult with exit code and output.
    """
    scenarios_dir = project_dir / ".dark-factory" / "scenarios" / spec_name

    if not scenarios_dir.exists():
        log.warning("no scenarios directory: %s", scenarios_dir)
        return RunResult(exit_code=2, stdout="", stderr="no scenarios directory")

    extra_dirs: list[Path] = []
    if spec_type in _ARCHIVE_INCLUSIVE_SPEC_TYPES:
        extra_dirs = discover_shipped_scenario_dirs(project_dir, exclude=spec_name)
        if extra_dirs:
            log.info(
                "scenario runner: including %d archived spec(s) in regression union",
                len(extra_dirs),
            )

    cmd = _build_test_cmd(config, project_dir, scenarios_dir, extra_dirs)
    if not cmd:
        log.warning("could not determine test command")
        return RunResult(exit_code=2, stdout="", stderr="no test command")

    effective_timeout = timeout or config.gate_timeout or 300
    return run(cmd, cwd=str(project_dir), timeout=effective_timeout)


def _build_test_cmd(
    config: PipelineConfig,
    project_dir: Path,
    scenarios_dir: Path,
    extra_dirs: list[Path],
) -> list[str]:
    """Build the scenario test command, optionally with archived dirs.

    For ``config.scenario_test_cmd``, ``{SCENARIOS_DIR}`` is substituted
    with a space-joined list of all directories so user templates like
    ``npx vitest run {SCENARIOS_DIR}`` keep working with multiple dirs.
    """
    all_dirs = [scenarios_dir, *extra_dirs]

    if config.scenario_test_cmd:
        # shlex.quote each path so directories with spaces or shell
        # metacharacters don't split into multiple bash args. This is the
        # `bash -c` branch — anything we splice in is interpreted by the
        # shell, so an unquoted "/Users/some name/project" becomes two
        # tokens and the test runner sees a nonexistent path.
        joined = " ".join(shlex.quote(str(d)) for d in all_dirs)
        if '"{SCENARIOS_DIR}"' in config.scenario_test_cmd or "'{SCENARIOS_DIR}'" in config.scenario_test_cmd:
            log.warning(
                "scenario_test_cmd wraps {SCENARIOS_DIR} in quotes; multi-dir "
                "expansion will collapse archived dirs into one arg. Drop the "
                "quotes — paths are already shell-quoted by the runner.",
            )
        expanded = config.scenario_test_cmd.replace("{SCENARIOS_DIR}", joined)
        return ["bash", "-c", expanded]

    toolchain = detect_toolchain(project_dir)
    rel_dirs = [str(d.relative_to(project_dir)) for d in all_dirs]

    if toolchain == "node":
        if (project_dir / "vitest.config.ts").exists() or (project_dir / "vitest.config.js").exists():
            return ["npx", "vitest", "run", *rel_dirs]
        return ["npx", "jest", "--no-coverage", *rel_dirs]
    elif toolchain == "python":
        return ["python3", "-m", "pytest", *rel_dirs, "-v"]
    elif toolchain == "go":
        return ["go", "test", *[f"{d}/..." for d in rel_dirs]]
    elif toolchain == "rust":
        return ["cargo", "test"]
    return []
