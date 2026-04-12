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
from darkfactory.scenarios.known_gaps import (
    build_pytest_conftest_source,
    build_vitest_setup_source,
    collect_skippable_names,
)
from darkfactory.utils.subprocess_runner import RunResult, run

log = logging.getLogger(__name__)

# Filenames for the generated skip shims. Both live directly under
# ``.dark-factory/scenarios/`` so a single shim covers every scenario
# dir in the invocation (including symlinked archive dirs from the
# smoke probe). The vitest file is hidden and doesn't match the default
# test-file glob, so it can't be mistaken for a test.
_VITEST_SKIP_SHIM = ".df-known-gaps-skip.mjs"
_PYTEST_SKIP_SHIM = "conftest.py"

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

    # Materialize a known-gaps skip shim across every dir in the run
    # set *before* building the test command. The shim's path is
    # injected via --setupFiles for vitest or picked up by pytest's
    # conftest discovery. Categories on the skip list are
    # intentionally narrow (ENV_ISSUE / BRITTLE / FLAKE only) — see
    # darkfactory.scenarios.known_gaps.
    toolchain = detect_toolchain(project_dir)
    skip_shim = _materialize_skip_shim(
        project_dir, [scenarios_dir, *extra_dirs], toolchain,
    )

    cmd = _build_test_cmd(
        config, project_dir, scenarios_dir, extra_dirs,
        toolchain=toolchain, skip_shim=skip_shim,
    )
    if not cmd:
        log.warning("could not determine test command")
        return RunResult(exit_code=2, stdout="", stderr="no test command")

    effective_timeout = timeout or config.gate_timeout or 300
    return run(cmd, cwd=str(project_dir), timeout=effective_timeout)


def _materialize_skip_shim(
    project_dir: Path,
    all_dirs: list[Path],
    toolchain: str | None,
) -> Path | None:
    """Write a language-specific skip shim for the known-gaps list.

    Returns the path to the generated shim, or ``None`` when there are
    no skippable gaps (or the toolchain has no shim implementation).
    The shim lives under ``.dark-factory/scenarios/`` so one file
    covers every scenario directory (new + archived) in the invocation.
    """
    skippable = collect_skippable_names(all_dirs)
    if not skippable:
        return None

    scenarios_root = project_dir / ".dark-factory" / "scenarios"
    scenarios_root.mkdir(parents=True, exist_ok=True)

    if toolchain == "node":
        shim_path = scenarios_root / _VITEST_SKIP_SHIM
        shim_path.write_text(build_vitest_setup_source(skippable))
        log.info(
            "known-gaps: wrote vitest skip shim (%d test%s) to %s",
            len(skippable), "" if len(skippable) == 1 else "s", shim_path,
        )
        return shim_path
    if toolchain == "python":
        shim_path = scenarios_root / _PYTEST_SKIP_SHIM
        shim_path.write_text(build_pytest_conftest_source(skippable))
        log.info(
            "known-gaps: wrote pytest conftest (%d test%s) to %s",
            len(skippable), "" if len(skippable) == 1 else "s", shim_path,
        )
        return shim_path
    log.info(
        "known-gaps: %d skippable name(s) but no shim for toolchain=%s",
        len(skippable), toolchain,
    )
    return None


def _build_test_cmd(
    config: PipelineConfig,
    project_dir: Path,
    scenarios_dir: Path,
    extra_dirs: list[Path],
    *,
    toolchain: str | None = None,
    skip_shim: Path | None = None,
) -> list[str]:
    """Build the scenario test command, optionally with archived dirs.

    For ``config.scenario_test_cmd``, ``{SCENARIOS_DIR}`` is substituted
    with a space-joined list of all directories so user templates like
    ``npx vitest run {SCENARIOS_DIR}`` keep working with multiple dirs.

    When ``skip_shim`` is provided it's injected into the auto-detected
    toolchain's setup-file slot so the known-gaps skip list takes
    effect. Custom ``scenario_test_cmd`` templates are left alone —
    they own their own setup files.
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

    if toolchain is None:
        toolchain = detect_toolchain(project_dir)
    rel_dirs = [str(d.relative_to(project_dir)) for d in all_dirs]

    if toolchain == "node":
        if (project_dir / "vitest.config.ts").exists() or (project_dir / "vitest.config.js").exists():
            cmd = ["npx", "vitest", "run"]
            if skip_shim is not None:
                cmd += ["--setupFiles", str(skip_shim.relative_to(project_dir))]
            cmd += rel_dirs
            return cmd
        # jest: --setupFilesAfterEach is not a CLI flag; users relying
        # on jest get the conftest-style path once the known-gaps
        # writer ships for jest. MVC scope is vitest + pytest.
        return ["npx", "jest", "--no-coverage", *rel_dirs]
    elif toolchain == "python":
        # pytest auto-discovers conftest.py from the directories it
        # walks, so the skip_shim path does not need to be passed on
        # the CLI — writing it to .dark-factory/scenarios/conftest.py
        # is sufficient.
        return ["python3", "-m", "pytest", *rel_dirs, "-v"]
    elif toolchain == "go":
        return ["go", "test", *[f"{d}/..." for d in rel_dirs]]
    elif toolchain == "rust":
        return ["cargo", "test"]
    return []
