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
_HOLDOUTS = ".dark-factory/holdouts"
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
    # Phase 4.6b.1: Implementer CAN now read the Test Engineer's visible
    # AC-aligned tests under .dark-factory/scenarios/ (they are the spec
    # predicates made executable). The QA Holdout's adversarial tests
    # live under .dark-factory/holdouts/ which remains masked.
    implementer_masked = [_HOLDOUTS, _REPORTS, _AUDIT, _STATE, _FAILURES, _GIT]
    implementer_denied = [
        "WebSearch", "WebFetch",
    ]
    implementer_prompt = (
        "INFORMATION BARRIER: You MUST NOT access .dark-factory/holdouts/ "
        "or .dark-factory/reports/. These directories do not exist in your "
        "filesystem. You MAY read the visible tests under "
        ".dark-factory/scenarios/<spec>/ — those are the Test Engineer's "
        "AC-aligned executable contracts derived from the spec. Do not "
        "attempt to read or reference adversarial holdout tests."
    )

    # Scenario architect: cannot see source code or git.
    # Cannot deny Read/Glob/Grep — Scenario Architect needs them to read the spec.
    # The primary barrier is tmpfs masks on source_dirs; those directories simply
    # don't exist inside the container.
    sa_masked = [*source_dirs, _FAILURES, _GIT]
    sa_denied = ["WebSearch", "WebFetch"]
    sa_prompt = (
        "INFORMATION BARRIER: You MUST NOT access source code directories. "
        "These directories do not exist in your filesystem. Read the spec "
        "file and generate scenarios based solely on the specification. "
        "Do not attempt to read, search, or reference any implementation source code."
    )

    # Reviewer: cannot see scenarios, reports, or git.
    reviewer_masked = [_SCENARIOS, _HOLDOUTS, _REPORTS, _FAILURES, _GIT]
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
    evaluator_masked = [*source_dirs, _FAILURES, _GIT]
    evaluator_prompt = (
        "INFORMATION BARRIER: You MUST NOT access source code directories. "
        "Evaluate based on HTTP responses and behavioral scenarios only. "
        "You may use WebFetch to exercise the deployed application."
    )

    # Test Engineer (Phase 4.6b.1): writes AC-aligned visible tests the
    # Implementer CAN read. Source code + holdouts + git are masked. The
    # TE must not see adversarial holdouts (they are the QA Holdout's
    # independent output) or source code.
    te_masked = [*source_dirs, _HOLDOUTS, _FAILURES, _GIT]
    te_denied = ["WebSearch", "WebFetch"]
    te_prompt = (
        "INFORMATION BARRIER: You MUST NOT access source code directories "
        "or .dark-factory/holdouts/. These directories do not exist in "
        "your filesystem. Write AC-aligned tests to "
        ".dark-factory/scenarios/<spec>/ only. Every test must carry "
        "exactly one in-manifest tag ([AC-N], [FR-N], or [EC-N]). "
        "No adversarial exploration — that belongs to the QA Holdout agent."
    )

    # QA Holdout (Phase 4.6b.1): writes adversarial blind tests the
    # Implementer CANNOT read. Source code + TE's visible scenarios + git
    # are masked. The QA Holdout explores the spec surface independently;
    # reading TE's visible tests would bias the exploration toward
    # already-covered anchors and defeat the split.
    qa_masked = [*source_dirs, _SCENARIOS, _FAILURES, _GIT]
    qa_denied = ["WebSearch", "WebFetch"]
    qa_prompt = (
        "INFORMATION BARRIER: You MUST NOT access source code directories "
        "or .dark-factory/scenarios/. These directories do not exist in "
        "your filesystem. Write adversarial holdout tests to "
        ".dark-factory/holdouts/<spec>/ only. Every test must be tagged "
        "[adversarial] or [holdout] — never [AC-N]/[FR-N]/[EC-N]. The "
        "Implementer cannot see your output."
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

    # Proposal Rehearsal (C2): writes ONE proposal test per AC/FR/EC so
    # the host can typecheck them before full scenario generation. Same
    # source-code barrier as the Test Engineer (it must not bias toward
    # existing code); scenarios dir is also masked because it may hold
    # stale output from a prior run and the proposal author shouldn't
    # pattern-match on it. Output comes back via stdout JSON — no
    # write_dirs — so the agent can't scatter files into the project.
    pr_masked = [*source_dirs, _SCENARIOS, _HOLDOUTS, _FAILURES, _GIT]
    pr_denied = ["WebSearch", "WebFetch"]
    pr_prompt = (
        "INFORMATION BARRIER: You MUST NOT access source code directories, "
        ".dark-factory/scenarios/, or .dark-factory/holdouts/. These do not "
        "exist in your filesystem. You MUST NOT write files — the project "
        "is read-only to you. Emit your proposal tests in a single JSON "
        "object between the sentinels BEGIN_PROPOSALS and END_PROPOSALS at "
        "the END of your reply."
    )

    # Spec Validator: cannot see source code or git history.
    #
    # The validator evaluates spec TEXT — completeness rubric, ambiguity
    # detection, Precision Analysis (3 maximally-different implementation
    # sketches), Free Variables, substrate-primitive completeness, wire-
    # accurate predicate language. Every check that legitimately consults
    # project metadata (library symbols, SQL lint, stub compile) runs
    # host-side in ``darkfactory.spec.precheck`` BEFORE the validator
    # container starts; findings reach the LLM as structured JSON under
    # ``.dark-factory/precheck/<spec>.json``.
    #
    # Source access is NOT merely unnecessary — it is actively harmful.
    # Precision Analysis's premise is "three max-different implementations
    # from the spec alone"; peeking at real source collapses its sketches
    # to the status quo and defeats the divergence signal. Determinism is
    # a gate-level requirement — see the 2026-04-16 PASS-then-FAIL
    # incident (``docs/pipeline-run-reports/``) for the concrete reproducer.
    validator_masked = [*source_dirs, _FAILURES, _GIT]
    validator_denied = ["WebSearch", "WebFetch"]
    validator_prompt = (
        "INFORMATION BARRIER: You MUST NOT access source code directories "
        "or .git/. These directories do not exist in your filesystem. "
        "Evaluate the spec based solely on its text and the deterministic "
        "precheck findings at .dark-factory/precheck/<spec>.json. Do not "
        "attempt to read, search, or reference any implementation source "
        "code — the Precision Analysis is adversarial by design and "
        "peeking at real code defeats it."
    )

    # Architect: cannot see scenarios, audit, state, failures, or git.
    # CAN read source code (needs codebase conventions) and reports (needs validation results).
    # Cannot use WebSearch/WebFetch — must derive structure from spec + existing code only.
    architect_masked = [_SCENARIOS, _HOLDOUTS, _AUDIT, _STATE, _FAILURES, _GIT]
    architect_denied = ["WebSearch", "WebFetch"]
    architect_prompt = (
        "INFORMATION BARRIER: You MUST NOT access .dark-factory/scenarios/, "
        ".dark-factory/audit/, .dark-factory/state/, .dark-factory/failures/, "
        "or .git/. These directories do not exist in your filesystem. Produce "
        "the architecture manifest based solely on the specification and "
        "existing source code."
    )

    # Substrate Owner (Phase 5 Stage 3, 2026-04-25): owns the
    # "is the substrate ready before code runs?" question. Reads
    # installed deps + existing files + architect manifest + spec
    # §4a. Emits idempotent shell commands that seed tool-prerequisite
    # gaps. Same access profile as the Architect (CAN read source) —
    # both are pre-implementer infrastructure roles. Tighter write
    # surface: only its own state file + a report.
    substrate_owner_masked = [
        _SCENARIOS, _HOLDOUTS, _AUDIT, _STATE, _FAILURES, _GIT,
    ]
    substrate_owner_denied = ["WebSearch", "WebFetch"]
    substrate_owner_prompt = (
        "INFORMATION BARRIER: You MUST NOT access .dark-factory/scenarios/, "
        ".dark-factory/holdouts/, .dark-factory/audit/, .dark-factory/state/, "
        ".dark-factory/failures/, or .git/. These directories do not exist "
        "in your filesystem. Identify substrate-prep gaps from the project's "
        "installed dependencies, existing files, the architect manifest, and "
        "the spec's §4a Negative Space block alone."
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
            masked_dirs=validator_masked,
            denied_tools=validator_denied,
            barrier_prompt=validator_prompt,
            write_dirs=[".dark-factory/reviews"],
        ),
        AgentRole.ESCALATION: BarrierConfig(
            masked_dirs=[],
            denied_tools=[],
            barrier_prompt="",
        ),
        AgentRole.TEST_ENGINEER: BarrierConfig(
            masked_dirs=te_masked,
            denied_tools=te_denied,
            barrier_prompt=te_prompt,
            write_dirs=[".dark-factory/scenarios", ".dark-factory/reports"],
        ),
        AgentRole.QA_HOLDOUT: BarrierConfig(
            masked_dirs=qa_masked,
            denied_tools=qa_denied,
            barrier_prompt=qa_prompt,
            write_dirs=[".dark-factory/holdouts", ".dark-factory/reports"],
        ),
        AgentRole.DEFECT_CLASSIFIER: BarrierConfig(
            masked_dirs=classifier_masked,
            denied_tools=classifier_denied,
            barrier_prompt=classifier_prompt,
            write_dirs=[".dark-factory/reports"],
        ),
        AgentRole.EVALUATOR: BarrierConfig(
            masked_dirs=evaluator_masked,
            denied_tools=["WebSearch"],
            barrier_prompt=evaluator_prompt,
            write_dirs=[".dark-factory/reports"],
        ),
        AgentRole.PROPOSAL_REHEARSAL: BarrierConfig(
            masked_dirs=pr_masked,
            denied_tools=pr_denied,
            barrier_prompt=pr_prompt,
            # Intentionally no write_dirs — proposals come back via stdout
            # JSON, the host writes scratch files, tsc runs host-side.
        ),
        AgentRole.SUBSTRATE_OWNER: BarrierConfig(
            masked_dirs=substrate_owner_masked,
            denied_tools=substrate_owner_denied,
            barrier_prompt=substrate_owner_prompt,
            # Writes substrate-prep.json under .dark-factory/architecture/
            # substrate-prep/<spec>.json (adjacent to architect manifest;
            # not masked, so the agent can actually write there) and a
            # human-readable report. Source code is read via the project
            # mount; substrate-owner never writes to project source.
            write_dirs=[".dark-factory/architecture", ".dark-factory/reports"],
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
