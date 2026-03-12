"""Barrier violation detection and semantic leakage checking.

Replaces lib/audit.sh — audit_check_output and validate_oracle_feedback.
"""

from __future__ import annotations

import logging
import re
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
) -> list[str]:
    """Scan agent output for references to restricted paths.

    Returns list of detected violations (path strings found).
    Emits BARRIER_VIOLATION events and logs to audit.
    """
    violations: list[str] = []
    for restricted in restricted_paths:
        if restricted in output:
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
]


def validate_oracle_feedback(
    feedback: str,
    audit_path: Path,
) -> list[str]:
    """Check Oracle feedback for semantic leakage patterns.

    Returns list of leakage descriptions found.
    """
    leaks: list[str] = []
    for pattern in _LEAKAGE_PATTERNS:
        matches = pattern.findall(feedback)
        if matches:
            desc = f"pattern {pattern.pattern!r}: {matches[:3]}"
            leaks.append(desc)
            audit_log(
                audit_path,
                f"SEMANTIC LEAKAGE in oracle feedback: {desc}",
            )

    if leaks:
        log.warning("oracle feedback contains %d leakage patterns", len(leaks))

    return leaks
