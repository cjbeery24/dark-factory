"""Scenario file detection and spec validation result parsing.

Replaces lib/scenarios.sh — has_scenario_files and validate_spec_result.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# File patterns that indicate test/scenario files.
_TEST_PATTERNS = [
    "*.test.*", "*.spec.*", "*.scenario.*",
    "test_*", "*_test.*",
]


def has_scenario_files(scenarios_dir: Path) -> bool:
    """Check if a scenarios directory contains any test files."""
    if not scenarios_dir.is_dir():
        return False
    for pattern in _TEST_PATTERNS:
        if list(scenarios_dir.glob(pattern)):
            return True
    # Also check for any files at all (some frameworks use different naming).
    return any(scenarios_dir.iterdir())


def validate_spec_result(spec_dir: Path, spec_name: str) -> tuple[bool, str]:
    """Parse spec validation output and check for PASS with no critical blockers.

    Looks for a .status file first, falls back to parsing the .md report.

    Returns:
        (passed, reason) tuple.
    """
    status_file = spec_dir / f"{spec_name}-validation.status"
    if status_file.exists():
        return _parse_status_file(status_file)

    # Fallback: parse the markdown report.
    report_file = spec_dir / f"{spec_name}-validation.md"
    if report_file.exists():
        return _parse_md_report(report_file)

    return False, "no validation output found"


def _parse_status_file(path: Path) -> tuple[bool, str]:
    """Parse a key=value status file."""
    data: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if "=" in line:
            key, _, value = line.partition("=")
            data[key.strip()] = value.strip()

    verdict = data.get("verdict", "").upper()
    divergence = data.get("divergence", "").upper()
    blockers_raw = data.get("blockers", "0")

    if verdict != "PASS":
        return False, f"verdict={verdict} (expected PASS)"
    if divergence == "HIGH":
        return False, f"divergence={divergence} (HIGH not allowed)"
    try:
        blockers = int(blockers_raw)
    except ValueError:
        return False, f"invalid blockers value: {blockers_raw}"
    if blockers > 0:
        return False, f"blockers={blockers} (critical issues must be resolved)"

    return True, "PASS"


def _parse_md_report(path: Path) -> tuple[bool, str]:
    """Fallback: parse verdict from markdown report."""
    content = path.read_text()
    if not re.search(r'(?i)\bverdict\s*:\s*pass\b', content):
        return False, "could not confirm PASS verdict from markdown"
    if re.search(r'(?i)\bdivergence\s*:\s*high\b', content):
        return False, "divergence=HIGH (HIGH not allowed)"
    # Check for critical issues section with actual content.
    critical_match = re.search(
        r'(?i)##\s*critical\s+issues?\s*\(must\s+fix\)\s*\n(.*?)(?=\n##|\Z)',
        content, re.DOTALL,
    )
    if critical_match:
        critical_body = critical_match.group(1).strip()
        # If the section has any non-trivial content (not just "None" or empty), block.
        if critical_body and not re.match(r'^[-*\s]*none\.?\s*$', critical_body, re.IGNORECASE):
            return False, "critical issues present (must be resolved)"
    return True, "PASS (from markdown)"
