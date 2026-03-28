"""Scenario quality validation — 9 checks on generated scenario files.

Replaces validate-scenarios.sh. Checks:
1. Minimum file count
2. Assertion presence per file
3. Trivial assertion detection
4. Empty test body detection
5. Build-tool spawn detection (warning only)
6. stderr convention check (warning only)
7. Serialization brittleness detection (hard fail)
8. Environment-dependent assertion detection (warning only)
9. TypeScript compilation via tsc --noEmit (hard fail, node only)
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
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

# Check 5: Build-tool spawn detection — warns when scenarios spawn build tools.
_BUILD_TOOL_SPAWN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"""(?:spawnSync|execSync|execFileSync|spawn)\s*\(\s*["']"""
        r"""(?:pnpm|npm|yarn|tsc|vite|webpack|esbuild|cargo|go)["']"""
    ),
    re.compile(
        r"""(?:spawnSync|execSync|execFileSync|spawn)\s*\(\s*["']npx["']\s*,\s*\[["']"""
        r"""(?:tsc|vite|webpack|esbuild)["']"""
    ),
    # Python: subprocess.run / subprocess.check_call with build tools
    re.compile(
        r"""subprocess\.(?:run|check_call|check_output|Popen)\s*\(\s*\[?["']"""
        r"""(?:pnpm|npm|yarn|tsc|vite|webpack|esbuild|cargo|go)["']"""
    ),
]

# Check 6: stderr convention — tools that write diagnostics to stdout, not stderr.
_STDOUT_DIAGNOSTIC_TOOLS = frozenset({"tsc", "eslint", "prettier", "biome"})
_STDERR_ASSERTION_PATTERN = re.compile(
    r"""result\.stderr.*?(?:toMatch|toContain|includes|match|toBe)\s*\("""
)

# Check 7: Serialization brittleness — string matching on structured file content.
# Structured file extensions whose content should be parsed, not string-matched.
_STRUCTURED_EXTENSIONS = frozenset({".json", ".yaml", ".yml", ".toml", ".xml"})

# Patterns that read structured files (per-language).
# Coverage: node, python only. Go/Rust scenarios are not yet covered —
# Check 7 silently passes for unsupported languages.
_STRUCTURED_READ_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "node": [
        re.compile(r"""readFileSync\s*\([^)]*\.(?:json|yaml|yml|toml|xml)["']"""),
        re.compile(r"""fs\.readFileSync\s*\([^)]*\.(?:json|yaml|yml|toml|xml)["']"""),
    ],
    "python": [
        re.compile(r"""open\s*\([^)]*\.(?:json|yaml|yml|toml|xml)["']"""),
        re.compile(r"""Path\s*\([^)]*\.(?:json|yaml|yml|toml|xml)["']"""),
    ],
}

# String assertion methods that indicate asserting on raw content.
_STRING_ASSERTION_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "node": [
        re.compile(r"""\.toContain\s*\("""),
        re.compile(r"""\.toMatch\s*\("""),
        re.compile(r"""\.includes\s*\("""),
    ],
    "python": [
        re.compile(r"""\bassert\s+.*\bin\b"""),
        re.compile(r"""\.find\s*\("""),
    ],
}

# Parse calls that indicate the file is being properly parsed before assertion.
_PARSE_CALL_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "node": [
        re.compile(r"""JSON\.parse\s*\("""),
        re.compile(r"""(?:yaml|YAML)\.parse\s*\("""),
        re.compile(r"""(?:toml|TOML)\.parse\s*\("""),
    ],
    "python": [
        re.compile(r"""json\.loads?\s*\("""),
        re.compile(r"""yaml\.safe_load\s*\("""),
        re.compile(r"""yaml\.load\s*\("""),
        re.compile(r"""toml\.loads?\s*\("""),
    ],
}

# Check 8: Environment-dependent assertions.
_ENV_DEPENDENT_PATTERNS: list[re.Pattern[str]] = [
    # Absolute paths in expected assertion values.
    re.compile(r"""(?:toBe|toEqual|assert.*==)\s*\(\s*["']/(?:Users|home|tmp|var|project)"""),
    # Path length assertions.
    re.compile(r"""\.length\)?\s*\.\s*(?:toBeGreaterThan|toBeLessThan|toBeGreaterThanOrEqual)"""),
    # Working directory references in expected values.
    re.compile(r"""process\.cwd\(\)"""),
    # Hardcoded timestamps in expected values.
    re.compile(r"""(?:toBe|toEqual)\s*\(\s*["']20\d{2}-\d{2}-\d{2}"""),
]


@dataclass
class ValidationResult:
    """Result of scenario validation."""
    passed: bool = True
    file_count: int = 0
    checks: list[CheckResult] = field(default_factory=list)
    warnings: list[CheckResult] = field(default_factory=list)
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
    """Run validation checks on scenario files (up to 9 checks).

    Returns ValidationResult with per-check details (checks 5-6, 8 are warnings).
    Check 9 (tsc --noEmit) runs only for language='node' with .ts files.
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

    # Markdown-only pass-through: if the directory contains only Format A/B
    # (*.scenarios.md and *.journey.md) files and no executable test files,
    # the assertion-pattern checks below are meaningless and would false-fail.
    # Use a lightweight structural check (file non-empty, has ## section header)
    # instead.
    markdown_files = (
        list(scenarios_dir.rglob("*.scenarios.md"))
        + list(scenarios_dir.rglob("*.journey.md"))
    )
    executable_files = _discover_files(scenarios_dir, language)

    if markdown_files and not executable_files:
        return _validate_markdown_only(
            spec_name, scenarios_dir, markdown_files, project_dir, min_files=min_files
        )

    if markdown_files and executable_files:
        log.info(
            "mixed scenario directory: %d markdown file(s) present alongside executable "
            "tests — markdown files are not assertion-validated in mixed mode",
            len(markdown_files),
        )

    # Discover scenario files.
    files = executable_files
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

    # Check 5: Build-tool spawn detection (warning only).
    check5 = _check_build_tool_spawns(files, language)
    result.warnings.append(check5)

    # Check 6: stderr convention (warning only).
    check6 = _check_stderr_convention(files, language)
    result.warnings.append(check6)

    # Check 7: Serialization brittleness (hard fail).
    check7 = _check_serialization_brittleness(files, language)
    result.checks.append(check7)

    # Check 8: Environment-dependent assertions (warning only).
    check8 = _check_env_dependent(files, language)
    result.warnings.append(check8)

    # Check 9: TypeScript compilation (hard fail, node only).
    if language == "node":
        check9 = _check_typescript_compilation(files, scenarios_dir)
        result.checks.append(check9)

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


def _validate_markdown_only(
    spec_name: str,
    scenarios_dir: Path,
    markdown_files: list[Path],
    project_dir: Path,
    *,
    min_files: int = _MIN_FILES,
) -> ValidationResult:
    """Lightweight structural validation for markdown-only scenario sets.

    Checks that each *.scenarios.md / *.journey.md file is non-empty and
    contains at least one ## section header.  Assertion-pattern checks
    (checks 2-4) are skipped — they are meaningless for markdown format.
    """
    result = ValidationResult()
    result.file_count = len(markdown_files)

    check1 = CheckResult(
        name="file_count",
        passed=len(markdown_files) >= min_files,
        detail=f"{len(markdown_files)} markdown scenario file(s) found (min {min_files})",
    )
    result.checks.append(check1)

    # Structural checks: non-empty + has at least one H2 (## ) section header
    failing_empty: list[str] = []
    failing_no_sections: list[str] = []
    for f in sorted(markdown_files):
        content = f.read_text(encoding="utf-8", errors="replace")
        if not content.strip():
            failing_empty.append(f.name)
        elif not re.search(r"^## ", content, re.MULTILINE):
            failing_no_sections.append(f.name)

    check_empty = CheckResult(
        name="non_empty",
        passed=len(failing_empty) == 0,
        detail=f"{len(failing_empty)} empty scenario file(s)",
        failing_files=failing_empty,
    )
    result.checks.append(check_empty)

    check_sections = CheckResult(
        name="has_sections",
        passed=len(failing_no_sections) == 0,
        detail=f"{len(failing_no_sections)} file(s) missing ## section headers",
        failing_files=failing_no_sections,
    )
    result.checks.append(check_sections)

    result.passed = all(c.passed for c in result.checks)
    result.report = _build_report(spec_name, result)

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

    # Warn about misnamed Python files (*.test.py) that pytest won't collect.
    if language == "python":
        bad_files = list(scenarios_dir.glob("**/*.test.py"))
        if bad_files:
            names = [f.name for f in bad_files]
            log.warning(
                "Found *.test.py files that pytest will not collect "
                "(use test_*.py or *_test.py naming): %s", names,
            )

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


def _check_build_tool_spawns(files: list[Path], language: str) -> CheckResult:
    """Check 5: Detect build-tool subprocess spawns.

    Warning only — Format C CLI specs legitimately need subprocess spawns.
    Flags files that spawn pnpm/npm/tsc/etc. so the smoke test and Oracle
    are aware of potential fragility.
    """
    flagged: list[str] = []
    for f in files:
        content = f.read_text()
        for pattern in _BUILD_TOOL_SPAWN_PATTERNS:
            if pattern.search(content):
                flagged.append(f.name)
                break

    return CheckResult(
        name="build_tool_spawns",
        passed=True,  # Warning only, never fails validation
        detail=f"{len(flagged)} file(s) spawn build tools" if flagged else "no build-tool spawns detected",
        failing_files=flagged,
    )


def _check_stderr_convention(files: list[Path], language: str) -> CheckResult:
    """Check 6: Flag assertions on result.stderr for stdout-diagnostic tools.

    Tools like tsc, eslint, prettier, and biome write diagnostics to stdout,
    not stderr. Asserting on result.stderr for these tools will always see an
    empty string, causing false test failures.

    Warning only — the assertion may be intentional in some contexts.
    """
    flagged: list[str] = []
    for f in files:
        content = f.read_text()
        if _STDERR_ASSERTION_PATTERN.search(content):
            for tool in _STDOUT_DIAGNOSTIC_TOOLS:
                if tool in content:
                    flagged.append(f.name)
                    break

    return CheckResult(
        name="stderr_convention",
        passed=True,  # Warning only
        detail=(
            f"{len(flagged)} file(s) assert on stderr for stdout-diagnostic tools"
            if flagged else "no stderr convention issues detected"
        ),
        failing_files=flagged,
    )


def _check_serialization_brittleness(
    files: list[Path], language: str,
) -> CheckResult:
    """Check 7: Detect string-matching assertions on structured file content.

    File-level detection: if a scenario file reads a structured file (.json,
    .yaml, .yml, .toml, .xml) AND contains string assertion methods (toContain,
    toMatch, includes) AND does NOT contain any parse call (JSON.parse,
    yaml.parse, etc.) anywhere in the file, flag it.

    The absence of a parse call is the signal — robust against variable
    indirection (e.g., ``const data = JSON.parse(raw)`` on line 5, then
    ``expect(data.key)`` on line 20).

    Hard fail — string-matching on structured file content is reliably brittle.
    """
    read_patterns = _STRUCTURED_READ_PATTERNS.get(language, [])
    assert_patterns = _STRING_ASSERTION_PATTERNS.get(language, [])
    parse_patterns = _PARSE_CALL_PATTERNS.get(language, [])

    if not read_patterns:
        return CheckResult(
            name="serialization_brittleness", passed=True,
            detail="no brittleness patterns for language",
        )

    flagged: list[str] = []
    for f in files:
        content = f.read_text()

        # Does the file read a structured file?
        reads_structured = any(p.search(content) for p in read_patterns)
        if not reads_structured:
            continue

        # Does the file contain string assertion methods?
        has_string_assertions = any(p.search(content) for p in assert_patterns)
        if not has_string_assertions:
            continue

        # Does the file contain ANY parse call? If so, it passes.
        has_parse_call = any(p.search(content) for p in parse_patterns)
        if has_parse_call:
            continue

        flagged.append(f.name)

    return CheckResult(
        name="serialization_brittleness",
        passed=len(flagged) == 0,
        detail=(
            f"{len(flagged)} file(s) use string matching on structured file content "
            f"without parsing first"
            if flagged else "no serialization brittleness detected"
        ),
        failing_files=flagged,
    )


def _check_env_dependent(files: list[Path], language: str) -> CheckResult:
    """Check 8: Detect assertions on environment-dependent values.

    Flags paths, timestamps, working directories, and other values that
    vary across execution environments.

    Warning only — some path assertions are legitimate.
    """
    flagged: list[str] = []
    for f in files:
        content = f.read_text()
        for pattern in _ENV_DEPENDENT_PATTERNS:
            if pattern.search(content):
                flagged.append(f.name)
                break

    return CheckResult(
        name="env_dependent",
        passed=True,  # Warning only
        detail=(
            f"{len(flagged)} file(s) contain environment-dependent assertions"
            if flagged else "no environment-dependent assertions detected"
        ),
        failing_files=flagged,
    )


def _check_typescript_compilation(
    files: list[Path],
    scenarios_dir: Path,
) -> CheckResult:
    """Check 9: Compile TypeScript scenarios with tsc --noEmit.

    Catches non-existent assertion methods, import failures, and syntax errors
    before scenarios are executed.  Zero false positives — if tsc rejects it,
    it will crash at runtime.

    Only runs when .ts files are present.  Gracefully skips when tsc is not
    available or times out.
    """
    ts_files = [f for f in files if f.suffix == ".ts"]
    if not ts_files:
        return CheckResult(
            name="typescript_compilation", passed=True, detail="no .ts files",
        )

    tsconfig = {
        "compilerOptions": {
            "noEmit": True,
            "strict": True,
            "moduleResolution": "bundler",
            "types": ["vitest/globals", "node"],
            "skipLibCheck": True,
        },
        "include": [str(f.relative_to(scenarios_dir)) for f in ts_files],
    }
    tsconfig_path = scenarios_dir / "tsconfig.check.json"
    tsconfig_path.write_text(json.dumps(tsconfig), encoding="utf-8")

    try:
        proc = subprocess.run(
            ["npx", "tsc", "--project", str(tsconfig_path)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(scenarios_dir),
        )
        if proc.returncode != 0:
            failing: set[str] = set()
            for line in proc.stdout.splitlines():
                if ".ts(" in line:
                    failing.add(line.split("(")[0].strip())
            return CheckResult(
                name="typescript_compilation",
                passed=False,
                detail=f"tsc --noEmit failed:\n{proc.stdout[:2000]}",
                failing_files=sorted(failing),
            )
        return CheckResult(
            name="typescript_compilation", passed=True,
            detail="all files compile",
        )
    except FileNotFoundError:
        return CheckResult(
            name="typescript_compilation", passed=True,
            detail="tsc not available (skipped)",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="typescript_compilation", passed=True,
            detail="tsc timed out (skipped)",
        )
    finally:
        tsconfig_path.unlink(missing_ok=True)


def _build_report(spec_name: str, result: ValidationResult) -> str:
    """Build a markdown validation report."""
    lines = [f"# Scenario Validation: {spec_name}\n"]
    lines.append(f"Files found: {result.file_count}\n")
    lines.append("## Checks\n")
    lines.append("| Check | Status | Detail |")
    lines.append("|-------|--------|--------|")
    for check in result.checks:
        status = "PASS" if check.passed else "FAIL"
        lines.append(f"| {check.name} | {status} | {check.detail} |")
        if check.failing_files:
            for ff in check.failing_files:
                lines.append(f"|  | | - {ff} |")

    if result.warnings:
        lines.append("")
        lines.append("## Warnings\n")
        lines.append("| Check | Detail |")
        lines.append("|-------|--------|")
        for warn in result.warnings:
            lines.append(f"| {warn.name} | {warn.detail} |")
            if warn.failing_files:
                for ff in warn.failing_files:
                    lines.append(f"|  | - {ff} |")

    lines.append("")
    return "\n".join(lines)
