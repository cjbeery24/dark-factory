"""Barrier violation detection and semantic leakage checking.

Replaces lib/audit.sh — audit_check_output and validate_oracle_feedback.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.audit.logger import audit_log
from darkfactory.events.types import emit_barrier_violation

log = logging.getLogger(__name__)


def check_output_for_barriers(
    output: str,
    restricted_paths: list[str],
    role: str,
    audit_path: Path,
    events_file: Path,
    spec_name: str,
    run_id: str,
    *,
    excluded_text: str = "",
    subtractable_paths: list[str] | None = None,
) -> list[str]:
    """Scan agent output for references to restricted paths.

    Returns list of detected violations (path strings found).
    Emits BARRIER_VIOLATION events and logs to audit.

    ``excluded_text`` is the text the agent was legitimately GIVEN (its prompt,
    spec body, architect manifest, and agent definition). A masked **source**
    dir that appears verbatim in that given text is not a barrier read — the dir
    is tmpfs-invisible inside the container, so the agent cannot have read it; it
    is echoing a path string from its own inputs (e.g. the manifest's
    ``specifier`` it is told to copy verbatim, or a path the spec names). This
    mirrors ``source_leak_scan.py``'s spec-text subtraction — it is the
    high-precision counterpart of this blunt substring check.

    ``subtractable_paths`` bounds which restricted paths the subtraction applies
    to — ONLY the source dirs. Holdouts / ``.git`` are never subtracted: the
    agent's legitimate inputs do not contain those strings, so any echo of them
    is a real signal that must still fire. When ``subtractable_paths`` is None,
    nothing is subtractable and the check is the original blunt substring match.

    Limitations:
    - Subtraction applies ONLY to MULTI-SEGMENT source dirs (those containing a
      path separator, e.g. ``apps/api/src``). A bare single-segment dir like
      ``src`` / ``app`` / ``lib`` is too generic — the token appears in prose and
      unrelated paths throughout any agent's inputs, so subtracting it would
      over-suppress and silently swallow real violations. Single-segment source
      dirs therefore keep the blunt always-fire behavior (a tolerable FP rather
      than a silent false-negative); the precise ``source_leak_scan.py`` (real
      file paths) + the Docker tmpfs mask remain the real defenses for them.
    - The subtraction is a substring check (matching the blunt detection match).
      ``excluded_text`` is assembled host-side from the agent's own inputs
      (prompt/spec/manifest/agent-def), never from agent OUTPUT, so an agent
      cannot mutate it to mask its own leak.
    """
    subtractable = set(subtractable_paths or [])
    violations: list[str] = []
    for restricted in restricted_paths:
        if restricted not in output:
            continue
        # Suppress ONLY a source-dir echo that also appears in the agent's given
        # inputs — not a read. Holdouts/.git (not in subtractable) always fire.
        # Restrict subtraction to MULTI-SEGMENT source dirs ('/' present): a bare
        # single-segment dir ('src') is too generic to subtract without
        # over-suppressing real violations (Codex gate, option b).
        if (
            restricted in subtractable
            and "/" in restricted
            and excluded_text
            and restricted in excluded_text
        ):
            audit_log(
                audit_path,
                f"BARRIER ECHO SUPPRESSED: {role} output references "
                f"{restricted}, but it appears in the agent's given inputs "
                f"(prompt/spec/manifest/agent-def) — not a source read",
            )
            continue
        violations.append(restricted)
        audit_log(
            audit_path,
            f"BARRIER VIOLATION: {role} output references {restricted}",
        )
        emit_barrier_violation(
            events_file, spec=spec_name, run_id=run_id,
            role=role, path=restricted,
        )

    if violations:
        log.warning(
            "barrier violations detected in %s output: %s",
            role, violations,
        )

    return violations


# Patterns that indicate semantic leakage in Oracle feedback.
_LEAKAGE_PATTERNS = [
    # Scenario filenames (NN-*.test.*)
    re.compile(r'\b\d{2}-[\w-]+\.(?:test|spec|scenario)\.\w+'),
    # Python scenario filenames (test_NN_*.py)
    re.compile(r'\btest_\d{2}_\w+\.py\b'),
    # Test assertion calls (expect, assert, should)
    re.compile(r'\b(?:expect|assert|should)\s*[.(]'),
    # Test DSL (describe, it, test blocks)
    re.compile(r'\b(?:describe|it|test)\s*\(\s*["\']'),
    # Raw output (stack traces, line numbers from test runners)
    re.compile(r'at\s+\w+\s+\(.*:\d+:\d+\)'),
    # Layer references (01-, 02-, etc. with scenario context)
    re.compile(r'\b(?:layer|scenario)\s+\d{2}-'),
    # Scenario count leakage: "64/73 passed", "9/73 failed", "73 tests"
    re.compile(r'\b\d+\s*/\s*\d+\s*(?:passed|failed|scenarios?|tests?)\b', re.IGNORECASE),
    # Percentage leakage: "87% pass rate", "13% fail"
    re.compile(r'\b\d+%\s*(?:pass|fail)', re.IGNORECASE),
    # Quantified scenario references with explicit count: "all 73 scenarios"
    # (bare "all scenarios" with no count and no verb is generic English and
    # leaks no information — see 4.6b.1g and Run 11 report.)
    re.compile(r'\b(?:all|every|none|no)\s+\d+\s+scenarios?\b', re.IGNORECASE),
    # Verb-based scenario leakage without a count: "no scenarios passed",
    # "all scenarios failed" — still leaks pass/fail distribution.
    re.compile(r'\b(?:all|every|none|no)\s+scenarios?\s+(?:passed|failed|pass|fail)\b', re.IGNORECASE),
]


@dataclass
class FeedbackValidationResult:
    """Result of Oracle feedback validation."""
    violations: list[str] = field(default_factory=list)

    @property
    def is_compliant(self) -> bool:
        """True when no leakage violations found."""
        return len(self.violations) == 0

    @property
    def violation_summary(self) -> str:
        """One-line summary for re-prompt injection."""
        if not self.violations:
            return ""
        return (
            f"Feedback violated {len(self.violations)} barrier rule(s): "
            f"{'; '.join(self.violations[:3])}"
        )


def validate_oracle_feedback(
    feedback: str,
    audit_path: Path,
) -> FeedbackValidationResult:
    """Check Oracle feedback for semantic leakage patterns.

    Returns FeedbackValidationResult with violation details and compliance flag.
    """
    result = FeedbackValidationResult()
    for pattern in _LEAKAGE_PATTERNS:
        matches = pattern.findall(feedback)
        if matches:
            desc = f"pattern {pattern.pattern!r}: {matches[:3]}"
            result.violations.append(desc)
            audit_log(
                audit_path,
                f"SEMANTIC LEAKAGE in oracle feedback: {desc}",
            )

    if result.violations:
        log.warning("oracle feedback contains %d leakage patterns", len(result.violations))

    # Missing CLASSIFICATIONS block is a validation violation for NO-SHIP
    # feedback.  SHIP decisions have nothing to classify, so the block is
    # only required when the Oracle is rejecting the implementation.
    first_line = feedback.strip().split("\n", 1)[0].strip().upper()
    is_ship = (
        first_line == "SHIP"
        or first_line.startswith("SHIP ")
        or first_line.startswith("SHIP:")
    )
    if not is_ship and "<!-- CLASSIFICATIONS:" not in feedback:
        result.violations.append("missing required <!-- CLASSIFICATIONS: {...} --> block")
        log.warning("oracle feedback missing CLASSIFICATIONS block — triggering re-prompt")
        audit_log(audit_path, "CLASSIFICATIONS block absent — added to violations")

    return result
