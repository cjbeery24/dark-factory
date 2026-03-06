"""Scenario quality validation — 4 checks on generated scenario files.

Replaces validate-scenarios.sh. Checks:
1. Minimum file count
2. Assertion presence per file
3. Trivial assertion detection
4. Empty test body detection
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_MIN_FILES = 3
_MIN_ASSERTIONS_PER_FILE = 1

# Language-specific assertion patterns.
_ASSERTION_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "node": [re.compile(r"expect\("), re.compile(r"assert\(")],
    "python": [re.compile(r"assert "), re.compile(r"self\.assert"), re.compile(r"pytest\.raises")],
    "go": [re.compile(r"t\.Error"), re.compile(r"require\."), re.compile(r"assert\.")],
    "rust": [re.compile(r"assert!"), re.compile(r"assert_eq!")],
}

# Trivial assertion patterns (per-language).
_TRIVIAL_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "node": [
        re.compile(r"expect\(true\)"), re.compile(r"expect\(1\)\.toBe\(1\)"),
        re.compile(r"expect\(false\)\.toBe\(false\)"),
    ],
    "python": [
        re.compile(r"assert True"), re.compile(r"assert 1 == 1"),
        re.compile(r"self\.assertTrue\(True\)"),
    ],
    "go": [],
    "rust": [re.compile(r"assert!\(true\)"), re.compile(r"assert_eq!\(1, 1\)")],
}

# File glob patterns for discovering scenario files.
_FILE_PATTERNS: dict[str, list[str]] = {
    "node": ["**/*.test.ts", "**/*.test.js", "**/*.spec.ts", "**/*.spec.js"],
    "python": ["**/test_*.py", "**/*_test.py"],
    "go": ["**/*_test.go"],
    "rust": ["**/*.rs"],
}

# Empty body detection patterns.
_EMPTY_BODY_PATTERNS: dict[str, re.Pattern[str]] = {
    "node": re.compile(r"(it|test)\s*\([^)]*\)\s*=>\s*\{\s*\}", re.MULTILINE),
    "python": re.compile(r"def test_\w+\([^)]*\)\s*:\s*\n\s*(pass|\.\.\.)\s*$", re.MULTILINE),
    "go": re.compile(r"func Test\w+\([^)]*\)\s*\{\s*\}", re.MULTILINE),
    "rust": re.compile(r"fn test_\w+\(\)\s*\{\s*\}", re.MULTILINE),
}


@dataclass
class ValidationResult:
    """Result of scenario validation."""
    passed: bool = True
    file_count: int = 0
    checks: list[CheckResult] = field(default_factory=list)
    report: str = ""


@dataclass
class CheckResult:
    """Result of a single validation check."""
    name: str
    passed: bool
    detail: str = ""
    failing_files: list[str] = field(default_factory=list)


def validate_scenarios(
    spec_name: str,
    language: str,
    project_dir: Path,
    *,
    min_files: int = _MIN_FILES,
    min_assertions: int = _MIN_ASSERTIONS_PER_FILE,
) -> ValidationResult:
    """Run all 4 validation checks on scenario files.

    Returns ValidationResult with per-check details.
    """
    scenarios_dir = project_dir / ".dark-factory" / "scenarios" / spec_name
    result = ValidationResult()

    if not scenarios_dir.exists():
        result.passed = False
        result.checks.append(CheckResult(
            name="file_count", passed=False,
            detail="scenarios directory not found",
        ))
        return result

    # Discover scenario files.
    files = _discover_files(scenarios_dir, language)
    result.file_count = len(files)

    # Check 1: File count.
    check1 = CheckResult(name="file_count", passed=len(files) >= min_files,
                         detail=f"{len(files)} files (min {min_files})")
    result.checks.append(check1)

    if not files:
        result.passed = False
        return result

    # Check 2: Assertion presence.
    check2 = _check_assertions(files, language, min_assertions)
    result.checks.append(check2)

    # Check 3: Trivial assertions.
    check3 = _check_trivial(files, language)
    result.checks.append(check3)

    # Check 4: Empty test bodies.
    check4 = _check_empty_bodies(files, language)
    result.checks.append(check4)

    result.passed = all(c.passed for c in result.checks)

    # Build report.
    result.report = _build_report(spec_name, result)

    # Write report file.
    report_path = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_name}-scenario-validation.md"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(result.report)

    return result


def _discover_files(scenarios_dir: Path, language: str) -> list[Path]:
    """Discover scenario test files by language."""
    patterns = _FILE_PATTERNS.get(language, _FILE_PATTERNS["node"])
    files: list[Path] = []
    for pattern in patterns:
        files.extend(scenarios_dir.glob(pattern))
    return sorted(set(files))


def _check_assertions(
    files: list[Path], language: str, min_per_file: int,
) -> CheckResult:
    """Check each file has enough assertions."""
    patterns = _ASSERTION_PATTERNS.get(language, [])
    failing: list[str] = []

    for f in files:
        content = f.read_text()
        count = sum(len(p.findall(content)) for p in patterns)
        if count < min_per_file:
            failing.append(f.name)

    return CheckResult(
        name="assertions",
        passed=len(failing) == 0,
        detail=f"{len(failing)} files below minimum assertions",
        failing_files=failing,
    )


def _check_trivial(files: list[Path], language: str) -> CheckResult:
    """Check for files where ALL assertions are trivial."""
    patterns = _ASSERTION_PATTERNS.get(language, [])
    trivial = _TRIVIAL_PATTERNS.get(language, [])
    failing: list[str] = []

    if not trivial:
        return CheckResult(name="trivial", passed=True, detail="no trivial patterns for language")

    for f in files:
        content = f.read_text()
        real_count = sum(len(p.findall(content)) for p in patterns)
        trivial_count = sum(len(p.findall(content)) for p in trivial)
        if real_count > 0 and trivial_count >= real_count:
            failing.append(f.name)

    return CheckResult(
        name="trivial",
        passed=len(failing) == 0,
        detail=f"{len(failing)} files with all-trivial assertions",
        failing_files=failing,
    )


def _check_empty_bodies(files: list[Path], language: str) -> CheckResult:
    """Check for empty test bodies."""
    pattern = _EMPTY_BODY_PATTERNS.get(language)
    failing: list[str] = []

    if not pattern:
        return CheckResult(name="empty_bodies", passed=True, detail="no empty body pattern for language")

    for f in files:
        content = f.read_text()
        if pattern.search(content):
            failing.append(f.name)

    return CheckResult(
        name="empty_bodies",
        passed=len(failing) == 0,
        detail=f"{len(failing)} files with empty test bodies",
        failing_files=failing,
    )


def _build_report(spec_name: str, result: ValidationResult) -> str:
    """Build a markdown validation report."""
    lines = [f"# Scenario Validation: {spec_name}\n"]
    lines.append(f"Files found: {result.file_count}\n")
    lines.append("| Check | Status | Detail |")
    lines.append("|-------|--------|--------|")
    for check in result.checks:
        status = "PASS" if check.passed else "FAIL"
        lines.append(f"| {check.name} | {status} | {check.detail} |")
        if check.failing_files:
            for ff in check.failing_files:
                lines.append(f"|  | | - {ff} |")
    lines.append("")
    return "\n".join(lines)
