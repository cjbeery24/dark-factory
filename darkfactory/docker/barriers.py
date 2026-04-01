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
    write_dirs: list[str] = field(default_factory=list)


# Static barrier directories (relative to project root).
_SCENARIOS = ".dark-factory/scenarios"
_REPORTS = ".dark-factory/reports"
_AUDIT = ".dark-factory/audit"
_STATE = ".dark-factory/state"
_FAILURES = ".dark-factory/failures"
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
    implementer_masked = [_SCENARIOS, _REPORTS, _AUDIT, _STATE, _FAILURES, _GIT]
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
    # Cannot deny Read/Glob/Grep — Scenario Architect needs them to read the spec.
    # The primary barrier is tmpfs masks on source_dirs; those directories simply
    # don't exist inside the container.
    sa_masked = [*source_dirs, _GIT]
    sa_denied = ["WebSearch", "WebFetch"]
    sa_prompt = (
        "INFORMATION BARRIER: You MUST NOT access source code directories. "
        "These directories do not exist in your filesystem. Read the spec "
        "file and generate scenarios based solely on the specification. "
        "Do not attempt to read, search, or reference any implementation source code."
    )

    # Reviewer: cannot see scenarios, reports, or git.
    reviewer_masked = [_SCENARIOS, _REPORTS, _GIT]
    reviewer_prompt = (
        "INFORMATION BARRIER: You MUST NOT access .dark-factory/scenarios/ "
        "or .dark-factory/reports/. These directories do not exist in your filesystem. "
        "Quality gate metrics and pre-pass scores have been provided directly in your prompt — "
        "use those values. Review based on the specification, source code, and the "
        "metrics already included above."
    )

    # Evaluator: cannot see source code or git. WebFetch is allowed (exercises deployed app
    # over HTTP). WebSearch is blocked. denied_tools is independent — must NOT alias sa_denied
    # so changes to sa_denied don't silently affect the Evaluator.
    evaluator_masked = [*source_dirs, _GIT]
    evaluator_prompt = (
        "INFORMATION BARRIER: You MUST NOT access source code directories. "
        "Evaluate based on HTTP responses and behavioral scenarios only. "
        "You may use WebFetch to exercise the deployed application."
    )

    # Architect: cannot see scenarios, audit, state, failures, or git.
    # CAN read source code (needs codebase conventions) and reports (needs validation results).
    # Cannot use WebSearch/WebFetch — must derive structure from spec + existing code only.
    architect_masked = [_SCENARIOS, _AUDIT, _STATE, _FAILURES, _GIT]
    architect_denied = ["WebSearch", "WebFetch"]
    architect_prompt = (
        "INFORMATION BARRIER: You MUST NOT access .dark-factory/scenarios/, "
        ".dark-factory/audit/, .dark-factory/state/, .dark-factory/failures/, "
        "or .git/. These directories do not exist in your filesystem. Produce "
        "the architecture manifest based solely on the specification and "
        "existing source code."
    )

    return {
        AgentRole.ARCHITECT: BarrierConfig(
            masked_dirs=architect_masked,
            denied_tools=architect_denied,
            barrier_prompt=architect_prompt,
            write_dirs=[".dark-factory/architecture", ".dark-factory/reports"],
        ),
        AgentRole.IMPLEMENTER: BarrierConfig(
            masked_dirs=implementer_masked,
            denied_tools=implementer_denied,
            barrier_prompt=implementer_prompt,
        ),
        AgentRole.SCENARIO_ARCHITECT: BarrierConfig(
            masked_dirs=sa_masked,
            denied_tools=sa_denied,
            barrier_prompt=sa_prompt,
            write_dirs=[".dark-factory/scenarios", ".dark-factory/reports"],
        ),
        AgentRole.REVIEWER: BarrierConfig(
            masked_dirs=reviewer_masked,
            denied_tools=[],
            barrier_prompt=reviewer_prompt,
            write_dirs=[".dark-factory/reviews"],
        ),
        AgentRole.ORACLE: BarrierConfig(
            masked_dirs=[],
            denied_tools=[],
            barrier_prompt="",
            write_dirs=[".dark-factory/reviews"],
        ),
        AgentRole.SPEC_VALIDATOR: BarrierConfig(
            masked_dirs=[],
            denied_tools=[],
            barrier_prompt="",
            write_dirs=[".dark-factory/reviews"],
        ),
        AgentRole.ESCALATION: BarrierConfig(
            masked_dirs=[],
            denied_tools=[],
            barrier_prompt="",
        ),
        AgentRole.EVALUATOR: BarrierConfig(
            masked_dirs=evaluator_masked,
            denied_tools=["WebSearch"],
            barrier_prompt=evaluator_prompt,
            write_dirs=[".dark-factory/reports"],
        ),
    }
