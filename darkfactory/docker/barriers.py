"""Information barrier configuration per agent role.

Replaces lib/barriers.sh configure_barriers.
Declares which directories are masked, which tools are denied,
and what barrier prompt is appended for each role.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from darkfactory.models.agent import AgentRole


@dataclass
class BarrierConfig:
    """Barrier settings for a single agent role."""
    masked_dirs: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    barrier_prompt: str = ""


# Static barrier directories (relative to project root).
_SCENARIOS = ".dark-factory/scenarios"
_REPORTS = ".dark-factory/reports"
_AUDIT = ".dark-factory/audit"
_STATE = ".dark-factory/state"
_GIT = ".git"


def configure_barriers(
    source_dirs: list[str],
) -> dict[AgentRole, BarrierConfig]:
    """Build barrier configs for all roles.

    Args:
        source_dirs: List of source directories to mask from
                     scenario-architect/evaluator roles.

    Returns:
        Mapping of AgentRole → BarrierConfig.
    """
    # Implementer: cannot see scenarios, reports, audit, state, or git.
    # Primary barrier is tmpfs masks. denied_tools is defense-in-depth only.
    # Cannot deny Read/Glob/Grep — Implementer needs them to read specs and code.
    implementer_masked = [_SCENARIOS, _REPORTS, _AUDIT, _STATE, _GIT]
    implementer_denied = [
        "WebSearch", "WebFetch",
    ]
    implementer_prompt = (
        "INFORMATION BARRIER: You MUST NOT access .dark-factory/scenarios/ "
        "or .dark-factory/reports/. These directories do not exist in your "
        "filesystem. Do not attempt to read, search, or reference test "
        "scenarios or quality reports. Implement based solely on the "
        "specification."
    )

    # Scenario architect: cannot see source code or git.
    sa_masked = [*source_dirs, _GIT]
    sa_denied = ["Read", "Glob", "Grep"]
    sa_prompt = (
        "INFORMATION BARRIER: You MUST NOT access source code directories. "
        "These directories do not exist in your filesystem. Generate "
        "scenarios based solely on the specification."
    )

    # Reviewer: cannot see scenarios, reports, or git.
    reviewer_masked = [_SCENARIOS, _REPORTS, _GIT]
    reviewer_prompt = (
        "INFORMATION BARRIER: You MUST NOT access .dark-factory/scenarios/ "
        "or .dark-factory/reports/. Review based on the specification, "
        "source code, and quality gate output."
    )

    # Evaluator: same as scenario architect.
    evaluator_masked = [*source_dirs, _GIT]
    evaluator_prompt = (
        "INFORMATION BARRIER: You MUST NOT access source code directories. "
        "Evaluate based on HTTP responses and behavioral scenarios only."
    )

    return {
        AgentRole.IMPLEMENTER: BarrierConfig(
            masked_dirs=implementer_masked,
            denied_tools=implementer_denied,
            barrier_prompt=implementer_prompt,
        ),
        AgentRole.SCENARIO_ARCHITECT: BarrierConfig(
            masked_dirs=sa_masked,
            denied_tools=sa_denied,
            barrier_prompt=sa_prompt,
        ),
        AgentRole.REVIEWER: BarrierConfig(
            masked_dirs=reviewer_masked,
            denied_tools=[],
            barrier_prompt=reviewer_prompt,
        ),
        AgentRole.ORACLE: BarrierConfig(
            masked_dirs=[],
            denied_tools=[],
            barrier_prompt="",
        ),
        AgentRole.SPEC_VALIDATOR: BarrierConfig(
            masked_dirs=[],
            denied_tools=[],
            barrier_prompt="",
        ),
        AgentRole.ESCALATION: BarrierConfig(
            masked_dirs=[],
            denied_tools=[],
            barrier_prompt="",
        ),
        AgentRole.EVALUATOR: BarrierConfig(
            masked_dirs=evaluator_masked,
            denied_tools=sa_denied,
            barrier_prompt=evaluator_prompt,
        ),
    }
