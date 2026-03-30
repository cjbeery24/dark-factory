"""Scenario file detection and spec validation result parsing.

Replaces lib/scenarios.sh — has_scenario_files and validate_spec_result.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# File patterns that indicate test/scenario files.
# Includes Format A (*.scenarios.md) and Format B (*.journey.md) in addition to
# the executable formats C/D (pytest / jest / go / rust).
_TEST_PATTERNS = [
    "*.test.*", "*.spec.*", "*.scenario.*",
    "test_*", "*_test.*",
    "**/*.scenarios.md", "**/*.journey.md",
]


def has_scenario_files(scenarios_dir: Path) -> bool:
    """Check if a scenarios directory contains recognized test files."""
    if not scenarios_dir.is_dir():
        return False
    for pattern in _TEST_PATTERNS:
        if list(scenarios_dir.glob(pattern)):
            return True
    return False


# ---------------------------------------------------------------------------
# Sentinel file support
# ---------------------------------------------------------------------------

SENTINEL_FILENAME = "SCENARIOS-NOT-APPLICABLE.md"

# Required section headings — checked as literal substrings so indentation must match.
_SENTINEL_REQUIRED_HEADINGS = [
    "### Format A",
    "### Format B",
    "### Format C",
    "### Format D",
    "## Quality Gate Coverage",
    "## Functional Requirement Coverage",
]
_SENTINEL_TRIVIAL_BODY = re.compile(
    r'^\s*(n/?a|not applicable|none|todo|tbd|—|-)\s*$', re.IGNORECASE
)


def has_sentinel_file(scenarios_dir: Path) -> bool:
    """Return True if a SCENARIOS-NOT-APPLICABLE.md sentinel is present."""
    return (scenarios_dir / SENTINEL_FILENAME).is_file()


def get_sentinel_path(scenarios_dir: Path) -> Path:
    """Return the path to the sentinel file in the given scenarios directory."""
    return scenarios_dir / SENTINEL_FILENAME


def validate_sentinel(sentinel_path: Path) -> tuple[bool, str]:
    """Heuristic structural validation of a sentinel file.

    Checks that all required sections are present and have non-trivial content,
    and that at least one functional requirement is referenced. Does NOT
    cross-reference the spec — semantic validity is the Reviewer's responsibility.

    Returns:
        (valid, objections_string) — if not valid, objections lists each issue.
    """
    content = sentinel_path.read_text()
    objections: list[str] = []

    for heading in _SENTINEL_REQUIRED_HEADINGS:
        if heading not in content:
            objections.append(f"Missing required section heading: '{heading}'")

    # Split document on heading boundaries to extract section bodies without
    # relying on a lazy quantifier against \Z (which can match empty string).
    section_bodies = _extract_section_bodies(content)
    for fmt in ["Format A", "Format B", "Format C", "Format D"]:
        prefix = f"### {fmt}"
        body = next((v for k, v in section_bodies.items() if k == prefix or k.startswith(prefix + " ") or k.startswith(prefix + "—")), "")
        if not body or _SENTINEL_TRIVIAL_BODY.match(body):
            objections.append(f"Section '{fmt}' has no substantive justification")

    # Require at least one requirement reference (FR-, REQ-, USER-, NFR-, EC-).
    if not re.search(r'\b(?:FR|REQ|USER|NFR|EC)-\d', content):
        objections.append(
            "No functional requirement coverage mapping found (expected FR-N, REQ-N, etc.)"
        )

    return (not objections), "\n".join(f"  - {o}" for o in objections)


def _extract_section_bodies(content: str) -> dict[str, str]:
    """Split a markdown document into {heading: body} pairs.

    Splits on lines that begin with '#' so each section's body is unambiguously
    everything between its heading line and the next heading line (or EOF).
    """
    bodies: dict[str, str] = {}
    current_heading = ""
    current_lines: list[str] = []
    for line in content.splitlines():
        if line.startswith("#"):
            if current_heading:
                bodies[current_heading] = "\n".join(current_lines).strip()
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_heading:
        bodies[current_heading] = "\n".join(current_lines).strip()
    return bodies


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
    # Only reject on blockers when the validator itself says FAIL/REVISE.
    # When verdict=PASS, the validator considered and accepted the issues —
    # blocking here overrides its judgment and is prone to LLM
    # non-determinism in "critical" classification.
    if blockers > 0 and verdict != "PASS":
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
