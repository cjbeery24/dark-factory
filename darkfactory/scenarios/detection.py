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
# All patterns are recursive — test-engineers organize executable scenarios in
# subdirs (e.g. acceptance/, edge-cases/); this must match bash has_scenario_files
# (scripts/lib/scenarios.sh) which uses find and walks subdirs by default.
_TEST_PATTERNS = [
    "**/*.test.*", "**/*.spec.*", "**/*.scenario.*",
    "**/test_*", "**/*_test.*",
    "**/*.scenarios.md", "**/*.journey.md",
]


def has_scenario_files(scenarios_dir: Path) -> bool:
    """Check if a scenarios directory contains recognized test files."""
    if not scenarios_dir.is_dir():
        return False
    # is_file() filter: pathlib's recursive glob matches directories too
    # (e.g. a lone `test_helpers/` subdir), but bash has_scenario_files
    # uses `find -type f`; match that semantics to avoid false positives.
    for pattern in _TEST_PATTERNS:
        if any(p.is_file() for p in scenarios_dir.glob(pattern)):
            return True
    return False


# Code extensions a test/scenario file would plausibly use. The misnamed-file
# detector is SCOPED to these (F Move 2c, 2026-06-16): a stray non-code artifact
# the TE legitimately writes (COVERAGE-GAPS.md, a plain doc) must NOT be flagged
# as a "misnamed scenario" — that would trigger spurious corrective retries.
# Markdown scenario formats ARE recognized when correctly named
# (``*.scenarios.md`` / ``*.journey.md`` / ``*.scenario.*`` are in
# ``_TEST_PATTERNS``); a plain ``.md`` is ambiguous with a doc, so we do NOT
# flag it (false-negative-safe: it falls through to the legacy empty-retry
# path). The observed failure (run 2f013c56) was plain ``.js`` files.
_MISNAMED_CODE_EXTS = frozenset({
    ".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx", ".mts", ".cts", ".py",
})
# Non-test artifacts the TE legitimately writes into scenarios/ — never flagged.
# (The sentinel literal is duplicated from SENTINEL_FILENAME below — that
# constant is defined further down the module, after this load-time frozenset.)
_MISNAMED_EXCLUDE_NAMES = frozenset({"SCENARIOS-NOT-APPLICABLE.md", "COVERAGE-GAPS.md"})
_MISNAMED_EXCLUDE_TOPDIRS = frozenset({"_contracts", "_df_setup"})


def find_misnamed_scenario_files(
    scenarios_dir: Path, *, tier1_prefix: str = "00-tier1-"
) -> list[str]:
    """Return code-extension files in *scenarios_dir* that match NO
    ``_TEST_PATTERNS`` — the (scoped) complement of ``has_scenario_files``.

    F Move 2c (2026-06-16): the test-engineer intermittently writes
    valid-content scenario files under a non-test name (run 2f013c56 wrote
    ``fr-1-workspace-install.js`` instead of ``*.test.ts``); the runner won't
    collect them and ``has_scenario_files`` (correctly) returns False. This
    detector identifies exactly those files so the warm-session retry can feed
    a deterministic "rename to a recognized pattern" corrective.

    Scoped + excluded to stay false-positive-free (a false positive here
    triggers spurious corrective retries):
      * only ``_MISNAMED_CODE_EXTS`` extensions (plain ``.md`` docs are ambiguous
        with legitimate non-test artifacts → not flagged; markdown scenarios are
        recognized by ``_TEST_PATTERNS`` when correctly named);
      * never the tier-1 prefix, ``_contracts``/``_df_setup`` helper dirs, the
        sentinel, ``COVERAGE-GAPS.md``, or dotfiles;
      * never anything already matching ``_TEST_PATTERNS`` (shares that list, so
        it can never drift from ``has_scenario_files``).

    Returns sorted repo-relative names. Empty when nothing applies — which the
    caller treats as "truly empty", routing to the legacy clear-and-regenerate.
    """
    if not scenarios_dir.is_dir():
        return []
    recognized: set[Path] = set()
    for pattern in _TEST_PATTERNS:
        recognized.update(p for p in scenarios_dir.glob(pattern) if p.is_file())
    misnamed: list[str] = []
    for p in scenarios_dir.rglob("*"):
        if not p.is_file() or p in recognized:
            continue
        if p.suffix not in _MISNAMED_CODE_EXTS:
            continue
        rel = p.relative_to(scenarios_dir)
        if rel.parts[0].startswith(tier1_prefix):
            continue
        if _MISNAMED_EXCLUDE_TOPDIRS & set(rel.parts):
            continue
        if p.name in _MISNAMED_EXCLUDE_NAMES or p.name.startswith("."):
            continue
        misnamed.append(str(rel))
    return sorted(misnamed)


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

    try:
        blockers = int(blockers_raw)
    except ValueError:
        return False, f"invalid blockers value: {blockers_raw}"

    if divergence == "HIGH":
        log.warning(
            "divergence=%s — proceeding (barriers catch real ambiguity downstream)",
            divergence,
        )

    # Blockers is the authoritative gate signal. The LLM validator sometimes
    # writes ``verdict=PASS`` alongside ``blockers>0`` (hedging in prose like
    # "CONDITIONAL PASS" or "PASS with 1 critical fix required"). We treat
    # blockers as ground truth and fail on them directly — no "re-run
    # validation" loop, since re-running rarely resolves LLM instruction
    # hedging and just burns tokens.
    if blockers > 0:
        return False, f"blockers={blockers} (critical issues must be resolved)"
    if verdict != "PASS":
        return False, f"verdict={verdict} (expected PASS)"

    return True, "PASS"


def _parse_md_report(path: Path) -> tuple[bool, str]:
    """Fallback: parse verdict from markdown report."""
    content = path.read_text()
    if not re.search(r'(?i)\bverdict\s*:\s*pass\b', content):
        return False, "could not confirm PASS verdict from markdown"
    if re.search(r'(?i)\bdivergence\s*:\s*high\b', content):
        log.warning("divergence=HIGH in markdown — proceeding (barriers catch real ambiguity)")

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
