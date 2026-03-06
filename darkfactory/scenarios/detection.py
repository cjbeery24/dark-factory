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
    """Parse spec validation output and check for PASS with score 100.

    Looks for a .status file first, falls back to parsing the .md report.

    Returns:
        (passed, reason) tuple.
    """
    status_file = spec_dir / f"{spec_name}.status"
    if status_file.exists():
        return _parse_status_file(status_file)

    # Fallback: parse the markdown report.
    report_file = spec_dir / f"{spec_name}.md"
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
    score = data.get("score", "0")
    divergence = data.get("divergence", "").upper()

    if verdict != "PASS":
        return False, f"verdict={verdict} (expected PASS)"
    try:
        score_int = int(score)
    except ValueError:
        return False, f"invalid score: {score}"
    if score_int < 100:
        return False, f"score={score_int} (expected 100)"
    if divergence == "HIGH":
        return False, f"divergence={divergence} (HIGH not allowed)"

    return True, "PASS"


def _parse_md_report(path: Path) -> tuple[bool, str]:
    """Fallback: parse verdict from markdown report."""
    content = path.read_text()
    if re.search(r'(?i)\bverdict\s*:\s*pass\b', content):
        if re.search(r'(?i)\bscore\s*:\s*100\b', content):
            if not re.search(r'(?i)\bdivergence\s*:\s*high\b', content):
                return True, "PASS (from markdown)"
    return False, "could not confirm PASS with score 100 from markdown"
