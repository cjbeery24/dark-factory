"""Battery config parsing + validation (pure, unit-testable).

A battery declares which (target, spec, stack) combinations to run and how many
times. The battery owns project-SOURCE reset (``reset_cmd``); the runner owns
framework reset (state wipe, --fresh, orphan-container cleanup).

TOML shape:

    runs = 3                      # N runs per (spec, stack); default 1
    [[target]]
    path = "/abs/path/to/project"
    stack = "typescript-postgres"
    reset_cmd = "git reset --hard <pin> && git clean -fd -e .dark-factory/specs"
    setup_cmd = ""               # optional, run ONCE per target before its runs
    specs = ["spec-a", "spec-b"]
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.errors import ConfigError


@dataclass
class BatteryTarget:
    path: Path
    stack: str
    reset_cmd: str
    specs: list[str]
    setup_cmd: str = ""
    # Best-effort warning surface: a target that enables enterprise makes
    # exit codes 1/2 ambiguous in the outcome classifier.
    enterprise_warning: bool = False


@dataclass
class Battery:
    runs: int = 1
    targets: list[BatteryTarget] = field(default_factory=list)
    # Optional per-run wall-clock cap (seconds). When set, a `dark-factory run`
    # that exceeds it is killed and recorded as an ERROR row so one hung spec
    # cannot wedge the whole battery. None = no cap (operator's choice).
    run_timeout_s: int | None = None


def _check_target_enterprise(path: Path) -> bool:
    """Best-effort: does the target's config.toml enable enterprise? (warn only)."""
    cfg = path / ".dark-factory" / "config.toml"
    if not cfg.exists():
        return False
    try:
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    # enterprise_enabled may live at top level or under a section; check both.
    if data.get("enterprise_enabled") is True:
        return True
    for section in data.values():
        if isinstance(section, dict) and section.get("enterprise_enabled") is True:
            return True
    return False


def parse_battery(toml_text: str) -> Battery:
    """Parse + validate a battery TOML string. Raises ConfigError on any problem.

    Validation: ``runs`` >= 1; >=1 target; each target has an existing path, a
    non-empty stack, a non-empty reset_cmd (reset is MANDATORY — a benchmark
    that doesn't reset measures drift), and >=1 spec.
    """
    try:
        data = tomllib.loads(toml_text)
    except ValueError as exc:
        raise ConfigError(f"invalid battery TOML: {exc}") from exc

    runs = data.get("runs", 1)
    # bool is a subclass of int in Python — reject it explicitly so `runs = true`
    # does not silently become 1.
    if isinstance(runs, bool) or not isinstance(runs, int) or runs < 1:
        raise ConfigError(f"battery 'runs' must be an integer >= 1, got {runs!r}")

    run_timeout_s = data.get("run_timeout_s")
    if run_timeout_s is not None and (
        isinstance(run_timeout_s, bool)
        or not isinstance(run_timeout_s, int)
        or run_timeout_s < 1
    ):
        raise ConfigError(
            f"battery 'run_timeout_s' must be a positive integer or omitted, got {run_timeout_s!r}"
        )

    raw_targets = data.get("target", [])
    if not raw_targets:
        raise ConfigError("battery must declare at least one [[target]]")

    targets: list[BatteryTarget] = []
    for i, t in enumerate(raw_targets):
        where = f"target[{i}]"
        path_str = t.get("path", "")
        if not path_str:
            raise ConfigError(f"{where}: missing 'path'")
        path = Path(path_str)
        if not path.exists():
            raise ConfigError(f"{where}: path does not exist: {path}")
        stack = t.get("stack", "")
        if not stack:
            raise ConfigError(f"{where}: missing 'stack'")
        reset_cmd = t.get("reset_cmd", "")
        if not reset_cmd:
            raise ConfigError(
                f"{where}: missing 'reset_cmd' — reset is mandatory "
                "(a benchmark that does not reset measures drift, not the spec)"
            )
        specs = t.get("specs", [])
        if not specs:
            raise ConfigError(f"{where}: 'specs' must be a non-empty list")
        targets.append(
            BatteryTarget(
                path=path,
                stack=stack,
                reset_cmd=reset_cmd,
                specs=list(specs),
                setup_cmd=t.get("setup_cmd", ""),
                enterprise_warning=_check_target_enterprise(path),
            )
        )

    return Battery(runs=runs, targets=targets, run_timeout_s=run_timeout_s)


def load_battery(path: Path) -> Battery:
    """Load + parse a battery TOML file."""
    if not path.exists():
        raise ConfigError(f"battery file not found: {path}")
    return parse_battery(path.read_text(encoding="utf-8"))
