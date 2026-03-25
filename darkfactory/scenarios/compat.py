"""Scenario format compatibility checker.

Detects which scenario formats are present and validates that the configured
toolchain and commands can actually execute them. Returns hard errors (pipeline
must stop) and warnings (logged but non-blocking).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.models.config import PipelineConfig


@dataclass
class CompatResult:
    """Result of a scenario format compatibility check."""

    hard_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def check_format_compatibility(scenarios_dir: Path, config: PipelineConfig) -> CompatResult:
    """Check whether the scenario formats present can be executed by the configured toolchain.

    Detects which formats are present in *scenarios_dir* and cross-references them
    against the toolchain and configured commands in *config*. Returns a
    :class:`CompatResult` with ``hard_errors`` (pipeline must stop) and
    ``warnings`` (logged, non-blocking).

    Rules
    -----
    - ``scenario_test_cmd`` bypasses ALL format-incompatibility checks.
    - ``toolchain == "unknown"``: emit warnings for potentially incompatible formats,
      no hard errors.
    - Format A (``.scenarios.md``): hard error when Python toolchain and no runner
      configured; warning for non-Python toolchain and no runner configured.
    - Format B (``.journey.md``): same conditions as Format A but for browser runner.
    - Format C Python (``test_*.py`` / ``*_test.py``): hard error when toolchain is
      node/go/rust and ``scenario_test_cmd`` is empty.
    """
    result = CompatResult()

    if not scenarios_dir.is_dir():
        return result

    # scenario_test_cmd bypasses all format-incompatibility checks.
    if config.scenario_test_cmd:
        return result

    toolchain = (config.language or "unknown").lower()
    has_test_server = bool(config.test_server_cmd)
    has_browser_server = bool(config.browser_server_cmd)

    # Detect which formats are present.
    has_format_a = _has_glob(scenarios_dir, "**/*.scenarios.md")
    has_format_b = _has_glob(scenarios_dir, "**/*.journey.md")
    has_python_tests = _has_glob(scenarios_dir, "**/test_*.py") or _has_glob(scenarios_dir, "**/*_test.py")
    has_ts_tests = (
        _has_glob(scenarios_dir, "**/*.test.ts")
        or _has_glob(scenarios_dir, "**/*.spec.ts")
        or _has_glob(scenarios_dir, "**/*.test.tsx")
        or _has_glob(scenarios_dir, "**/*.spec.tsx")
    )

    # Format A — HTTP Behavioral Scenarios (.scenarios.md)
    if has_format_a:
        no_http_runner = not has_test_server and not config.test_base_url
        if no_http_runner:
            if toolchain == "python":
                result.hard_errors.append(
                    "Format A (.scenarios.md) scenarios are present but no HTTP runner is configured: "
                    "test_server_cmd, test_base_url are all empty. "
                    "Set test_server_cmd or test_base_url in config, or set scenario_test_cmd to provide a custom runner."
                )
            elif toolchain != "unknown":
                result.warnings.append(
                    f"Format A (.scenarios.md) scenarios are present but test_server_cmd and test_base_url are empty "
                    f"(toolchain={toolchain}). These scenarios will be SKIPPED by the quality gate unless "
                    f"test_server_cmd or test_base_url is configured."
                )
            else:
                # toolchain == "unknown"
                result.warnings.append(
                    "Format A (.scenarios.md) scenarios are present but test_server_cmd and test_base_url are empty "
                    "(toolchain could not be detected). These scenarios may be SKIPPED by the quality gate."
                )

    # Format B — Browser Journey Scenarios (.journey.md)
    if has_format_b:
        no_browser_runner = not has_browser_server and not config.browser_base_url
        if no_browser_runner:
            if toolchain == "python":
                result.hard_errors.append(
                    "Format B (.journey.md) scenarios are present but no browser runner is configured: "
                    "browser_server_cmd and browser_base_url are all empty. "
                    "Set browser_server_cmd in config, or set scenario_test_cmd to provide a custom runner."
                )
            elif toolchain != "unknown":
                result.warnings.append(
                    f"Format B (.journey.md) scenarios are present but browser_server_cmd is empty "
                    f"(toolchain={toolchain}). These scenarios will be SKIPPED by the quality gate unless "
                    f"browser_server_cmd is configured."
                )
            else:
                result.warnings.append(
                    "Format B (.journey.md) scenarios are present but browser_server_cmd is empty "
                    "(toolchain could not be detected). These scenarios may be SKIPPED by the quality gate."
                )

    # Format C Python — test_*.py / *_test.py
    if has_python_tests:
        incompatible_toolchains = ("node", "go", "rust")
        if toolchain in incompatible_toolchains:
            result.hard_errors.append(
                f"Python scenario files (test_*.py / *_test.py) are present but the toolchain is "
                f"'{toolchain}' — pytest is not available in this container. "
                f"Options: (1) Regenerate scenarios as TypeScript (*.test.ts) for node, "
                f"(2) Set scenario_test_cmd in config to provide a custom runner, "
                f"(3) Add SCENARIOS-NOT-APPLICABLE.md to opt out."
            )

    # Format D TypeScript — *.test.ts / *.spec.ts / *.test.tsx / *.spec.tsx
    if has_ts_tests:
        if toolchain == "python":
            result.hard_errors.append(
                "TypeScript scenario files (*.test.ts / *.spec.ts) are present but the toolchain is "
                "'python' — a Node.js runner is not available in this container. "
                "Regenerate scenarios as Python (test_*.py) for python toolchains, "
                "or set scenario_test_cmd in config to provide a custom runner."
            )
        elif toolchain == "go":
            result.hard_errors.append(
                "TypeScript scenario files (*.test.ts / *.spec.ts) are present but the toolchain is "
                "'go' — a Node.js runner is not available in this container. "
                "Regenerate scenarios using Go test files (*_test.go), "
                "or set scenario_test_cmd in config to provide a custom runner."
            )
        elif toolchain == "rust":
            result.hard_errors.append(
                "TypeScript scenario files (*.test.ts / *.spec.ts) are present but the toolchain is "
                "'rust' — a Node.js runner is not available in this container. "
                "Regenerate scenarios using Rust tests, "
                "or set scenario_test_cmd in config to provide a custom runner."
            )
        # toolchain == "node": TypeScript is correct — no error.
        # toolchain == "unknown": no error.

    return result


def _has_glob(directory: Path, pattern: str) -> bool:
    """Return True if *directory* contains at least one file matching *pattern*."""
    return next(directory.glob(pattern), None) is not None
