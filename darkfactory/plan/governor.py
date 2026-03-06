"""Resource governor — preflight checks and budget enforcement.

Replaces governor_preflight() and governor_check_budget_posthoc()
from dark-factory-plan.sh.
"""

from __future__ import annotations

import logging
import platform

from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import run

log = logging.getLogger(__name__)


def preflight(config: PipelineConfig) -> list[str]:
    """Run resource preflight checks. Returns list of warnings (empty = all OK).

    Checks:
    - Memory: >= 4GB per concurrent slot recommended.
    - Docker daemon responsive.
    """
    warnings: list[str] = []
    max_concurrent = config.max_concurrent_specs

    # Memory check.
    mem_gb = _get_memory_gb()
    if mem_gb > 0:
        required = max_concurrent * 4
        if mem_gb < required:
            warnings.append(
                f"{mem_gb}GB memory, {required}GB recommended "
                f"for {max_concurrent} concurrent specs"
            )

    # Docker check.
    result = run(["docker", "info"], timeout=10)
    if result.exit_code != 0:
        warnings.append("Docker daemon not running")

    return warnings


def check_budget_pre_dispatch(
    allocated_turns: int,
    per_spec_turns: int,
    plan_budget: int,
) -> bool:
    """Check if dispatching another spec would exceed the plan token budget.

    Returns True if within budget (OK to dispatch), False if would exceed.
    """
    if plan_budget <= 0:
        return True
    return (allocated_turns + per_spec_turns) <= plan_budget


def _get_memory_gb() -> int:
    """Get system memory in GB. Returns 0 if unavailable."""
    system = platform.system()
    if system == "Darwin":
        result = run(["sysctl", "-n", "hw.memsize"], timeout=5)
        if result.exit_code == 0 and result.stdout.strip():
            try:
                return int(result.stdout.strip()) // (1024 ** 3)
            except ValueError:
                pass
    elif system == "Linux":
        result = run(
            ["awk", "/MemTotal/ {print int($2/1048576)}", "/proc/meminfo"],
            timeout=5,
        )
        if result.exit_code == 0 and result.stdout.strip():
            try:
                return int(result.stdout.strip())
            except ValueError:
                pass
    return 0
