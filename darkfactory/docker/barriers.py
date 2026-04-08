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

# .dark-factory/precheck/ holds the spec pre-check advisory consumed by the
# Architect. It is intentionally NOT in any role's masked_dirs list:
#  - Architect: needs read access (advisory is its primary input alongside the spec).
#  - Implementer: derived purely from spec content the Implementer already reads,
#    so masking adds no information protection. Explicit allow.
#  - Scenario Architect / Reviewer / others: the file is harmless to them; they
#    have no reason to read it but no risk if they do.
# See docs/plans/2026-04-06-pipeline-resilience-rewrite.md Phase 0 / Phase 1.


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

    # Defect Classifier: reads project :ro, spec, scenarios, and reports to tag
    # failing tests. Masked: audit/state/failures/git (same barrier surface as
    # Reviewer) but with scenarios and reports READABLE — the classifier's
    # entire job is reading failing test bodies and the gate report.
    classifier_masked = [_AUDIT, _STATE, _FAILURES, _GIT]
    classifier_denied = ["WebSearch", "WebFetch"]
    classifier_prompt = (
        "INFORMATION BARRIER: You MUST NOT access .dark-factory/audit/, "
        ".dark-factory/state/, .dark-factory/failures/, or .git/. Your single "
        "job is to classify each failing test into one of the allowed "
        "categories. Do not make ship/no-ship decisions."
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
        AgentRole.DEFECT_CLASSIFIER: BarrierConfig(
            masked_dirs=classifier_masked,
            denied_tools=classifier_denied,
            barrier_prompt=classifier_prompt,
        ),
        AgentRole.EVALUATOR: BarrierConfig(
            masked_dirs=evaluator_masked,
            denied_tools=["WebSearch"],
            barrier_prompt=evaluator_prompt,
            write_dirs=[".dark-factory/reports"],
        ),
    }


def live_barrier_for(role: AgentRole, config) -> BarrierConfig:
    """Phase 4.5.3: rebuild a single role's BarrierConfig from live config.

    Use this at every container-launch / safety-check site instead of
    indexing into a cached ``barriers`` dict. The cached dict is built
    once at orchestrator startup and goes stale the moment the
    architect amends ``layout.source_dirs`` mid-run; ``configure_barriers``
    is pure and the only input is ``source_dirs``, so a fresh per-call
    rebuild is the simplest way to make staleness impossible.

    Takes ``config`` as ``Any`` to avoid an import cycle with
    ``models.config``.
    """
    return configure_barriers(config.source_dirs)[role]
