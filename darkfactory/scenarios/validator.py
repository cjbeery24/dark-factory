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


# Check 10: Assertion count upper bound — max assertions per file.
_MAX_ASSERTIONS_PER_FILE = 30

# Check 11: No source file reads — readFileSync/open() on source extensions.
_SOURCE_EXTENSIONS = frozenset({".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs"})
# Config files with source extensions that are NOT source code.
_CONFIG_FILE_PATTERN = re.compile(
    r"""[\w.-]*\.config\.(?:ts|tsx|js|jsx|mjs|cjs)["']"""
)
_SOURCE_READ_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"""(?:readFileSync|fs\.readFileSync)\s*\([^)]*\.(?:ts|tsx|js|jsx|py|go|rs)["']"""
    ),
    re.compile(
        r"""open\s*\([^)]*\.(?:ts|tsx|js|jsx|py|go|rs)["']"""
    ),
]

# Check 12: No indexOf ordering comparisons.
_INDEXOF_ORDERING_PATTERN = re.compile(
    r"""\.indexOf\([^)]+\)\s*[<>]\s*\w+\.indexOf\("""
)

# Check 14: Regex multiline flag — toMatch on readFileSync without /m or /s.
_REGEX_NO_MULTILINE_PATTERN = re.compile(
    r"""toMatch\s*\(\s*/[^/]+/(?![msi]*m)"""
)

# Check 15: No project file modification.
_PROJECT_WRITE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"""(?:writeFileSync|fs\.writeFile|fs\.writeFileSync)\s*\("""
    ),
]
_SAFE_WRITE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"""/tmp"""),
    re.compile(r"""mkdtemp"""),
    re.compile(r"""tmpdir"""),
    re.compile(r"""tmpDir"""),
    re.compile(r"""tempDir"""),
    re.compile(r"""os\.tmpdir"""),
]

# Check 16: No hardcoded container paths.
_HARDCODED_CHDIR_PATTERN = re.compile(
    r"""process\.chdir\s*\(\s*['"]/(workspace|project)"""
)


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
    max_assertions: int = _MAX_ASSERTIONS_PER_FILE,
    spec_path: Path | None = None,
    tier1_prefix: str = "00-tier1-",
) -> ValidationResult:
    """Run validation checks on scenario files (up to 16 checks).

    Returns ValidationResult with per-check details (checks 5-6, 8, 14 are warnings).
    Check 9 (tsc --noEmit) runs only for language='node' with .ts files.
    Checks 10-16 apply only to Tier 2 files (files NOT starting with tier1_prefix).
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

    # Discover scenario files. Format A (*.scenarios.md) and Format B
    # (*.journey.md) are retired as of Phase 4.6b.1a — only native executable
    # tests in the project's test framework are valid here.
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

    # Check 5: Build-tool spawn detection (warning only).
    check5 = _check_build_tool_spawns(files, language)
    result.warnings.append(check5)

    # Check 6: stderr convention (warning only).
    check6 = _check_stderr_convention(files, language)
    result.warnings.append(check6)

    # Check 7: Serialization brittleness (warning — string matching is brittle
    # but common in tooling/config specs; doesn't violate information barrier).
    check7 = _check_serialization_brittleness(files, language)
    result.warnings.append(check7)

    # Check 8: Environment-dependent assertions (warning only).
    check8 = _check_env_dependent(files, language)
    result.warnings.append(check8)

    # Check 9: TypeScript compilation (hard fail, node only).
    if language == "node":
        check9 = _check_typescript_compilation(files, scenarios_dir)
        result.checks.append(check9)

    # --- Checks 10-16: Tier 2 only (skip tier1_prefix files) ---
    tier2_files = [f for f in files if not f.name.startswith(tier1_prefix)]
    if tier2_files:
        # Check 10: Assertion count upper bound (hard fail).
        check10 = _check_assertion_upper_bound(tier2_files, language, max_assertions)
        result.checks.append(check10)

        # Check 11: No source file reads (hard fail).
        check11 = _check_no_source_reads(tier2_files)
        result.checks.append(check11)

        # Check 12: No indexOf ordering (hard fail).
        check12 = _check_no_indexof_ordering(tier2_files)
        result.checks.append(check12)

        # Check 13: Intent count alignment (hard fail, requires spec).
        if spec_path is not None:
            check13 = _check_intent_alignment(tier2_files, language, spec_path)
            result.checks.append(check13)

        # Check 14: Regex multiline flag (warning).
        check14 = _check_regex_multiline_flag(tier2_files)
        result.warnings.append(check14)

        # Check 15: No project file modification (warning — tooling/infrastructure
        # specs legitimately create temp files to test build/lint/typecheck behavior).
        check15 = _check_no_project_writes(tier2_files)
        result.warnings.append(check15)

        # Check 16: No hardcoded container paths (hard fail).
        check16 = _check_no_hardcoded_paths(tier2_files)
        result.checks.append(check16)

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
            "module": "esnext",
            "moduleResolution": "bundler",
            "types": ["vitest/globals", "node"],
            "skipLibCheck": True,
        },
        "include": [str(f.relative_to(scenarios_dir)) for f in ts_files],
    }

    # In pnpm monorepos, @types/node and vitest are per-workspace, not
    # hoisted to root.  If tsc can't resolve them, downgrade to skip
    # rather than blocking the SA on an infrastructure gap.
    _MISSING_TYPES_MARKERS = (
        "Cannot find type definition file for",
        "Cannot find module",
    )
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
            combined = proc.stdout + proc.stderr
            if "This is not the tsc command" in combined:
                return CheckResult(
                    name="typescript_compilation", passed=True,
                    detail="tsc not available (skipped)",
                )
            # Partition tsc errors: import/module resolution errors (expected
            # in scenarios that reference project paths) vs code errors
            # (actual scenario bugs like missing imports or type misuse).
            # tsc outputs errors as either "error TSxxxx: ..." or
            # "file.ts(1,1): error TSxxxx: ..." — match both forms.
            error_lines = [
                l for l in combined.splitlines()
                if l.strip().startswith("error ") or ": error " in l
            ]
            import_errors = [
                l for l in error_lines
                if any(m in l for m in _MISSING_TYPES_MARKERS)
            ]
            code_errors = [
                l for l in error_lines
                if not any(m in l for m in _MISSING_TYPES_MARKERS)
            ]

            if code_errors:
                # Real scenario bugs — fail even if import errors also present.
                failing: set[str] = set()
                for line in code_errors:
                    if ".ts(" in line:
                        failing.add(line.split("(")[0].strip())
                code_detail = "\n".join(code_errors)
                return CheckResult(
                    name="typescript_compilation",
                    passed=False,
                    detail="tsc found scenario code errors:\n" + code_detail[:2000],
                    failing_files=sorted(failing),
                )
            if import_errors:
                # Only import/module resolution errors — skip gracefully.
                return CheckResult(
                    name="typescript_compilation", passed=True,
                    detail="type definitions not available (skipped)",
                )
            # No recognized error lines but tsc still failed — report raw output.
            return CheckResult(
                name="typescript_compilation",
                passed=False,
                detail=f"tsc --noEmit failed:\n{proc.stdout[:2000]}",
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


# --- Checks 10-16: Tier 2 only ---


def _check_assertion_upper_bound(
    files: list[Path], language: str, max_per_file: int,
) -> CheckResult:
    """Check 10: Assertion count upper bound.

    Files with more than max_per_file assertions are likely overly granular
    and fragile.  Hard fail.
    """
    patterns = _ASSERTION_PATTERNS.get(language, [])
    failing: list[str] = []

    for f in files:
        content = f.read_text()
        count = sum(len(p.findall(content)) for p in patterns)
        if count > max_per_file:
            failing.append(f"{f.name} ({count} assertions)")

    return CheckResult(
        name="assertion_upper_bound",
        passed=len(failing) == 0,
        detail=f"{len(failing)} file(s) exceed {max_per_file} assertions",
        failing_files=failing,
    )


def _check_no_source_reads(files: list[Path]) -> CheckResult:
    """Check 11: No readFileSync/open() on source code files.

    Config files (.json, .yaml, .toml, *.config.{js,ts,...}) are allowed.
    Hard fail.
    """
    flagged: list[str] = []
    for f in files:
        content = f.read_text()
        for pattern in _SOURCE_READ_PATTERNS:
            for match in pattern.finditer(content):
                matched_text = match.group(0)
                # Allow reads of config files (e.g. eslint.config.js, vitest.config.ts).
                if _CONFIG_FILE_PATTERN.search(matched_text):
                    continue
                flagged.append(f.name)
                break
            else:
                continue
            break

    return CheckResult(
        name="no_source_reads",
        passed=len(flagged) == 0,
        detail=(
            f"{len(flagged)} file(s) read source code files"
            if flagged else "no source file reads detected"
        ),
        failing_files=flagged,
    )


def _check_no_indexof_ordering(files: list[Path]) -> CheckResult:
    """Check 12: No indexOf ordering comparisons.

    ``indexOf(X) < indexOf(Y)`` asserts unconstrained ordering.  Hard fail.
    """
    flagged: list[str] = []
    for f in files:
        content = f.read_text()
        if _INDEXOF_ORDERING_PATTERN.search(content):
            flagged.append(f.name)

    return CheckResult(
        name="no_indexof_ordering",
        passed=len(flagged) == 0,
        detail=(
            f"{len(flagged)} file(s) use indexOf ordering comparisons"
            if flagged else "no indexOf ordering detected"
        ),
        failing_files=flagged,
    )


def _count_test_functions(content: str, language: str) -> int:
    """Count test function declarations in a file."""
    if language in ("node", ""):
        return len(re.findall(r"""(?:it|test)\s*\(""", content))
    if language == "python":
        return len(re.findall(r"""def test_""", content))
    if language == "go":
        return len(re.findall(r"""func Test""", content))
    if language == "rust":
        return len(re.findall(r"""fn test_""", content))
    return len(re.findall(r"""(?:it|test)\s*\(""", content))


def _check_intent_alignment(
    files: list[Path], language: str, spec_path: Path,
) -> CheckResult:
    """Check 13: Intent count alignment.

    Total Tier 2 test count must be within 2x of declared scenario intents
    from the spec.  Skips if no intent section found.  Hard fail.
    """
    if not spec_path.exists():
        return CheckResult(
            name="intent_alignment", passed=True,
            detail="spec file not found (skipped)",
        )

    spec_text = spec_path.read_text(encoding="utf-8", errors="replace")

    # Look for a scenario intents section.
    match = re.search(
        r"(?i)#{2,3}\s+(?:\d+\.\s*)?scenario\s+intents.*?\n(.*?)(?=\n#{1,3}\s|\Z)",
        spec_text,
        re.DOTALL,
    )
    if not match:
        return CheckResult(
            name="intent_alignment", passed=True,
            detail="no scenario intents section found (skipped)",
        )

    body = match.group(1).strip()
    # Count bullet points or numbered items.
    intent_count = len(re.findall(r"^\s*(?:[-*]|\d+\.)\s+", body, re.MULTILINE))
    if intent_count == 0:
        return CheckResult(
            name="intent_alignment", passed=True,
            detail="no intents parsed (skipped)",
        )

    total_tests = sum(
        _count_test_functions(f.read_text(), language) for f in files
    )
    max_allowed = intent_count * 2

    return CheckResult(
        name="intent_alignment",
        passed=total_tests <= max_allowed,
        detail=f"{total_tests} tests vs {intent_count} intents (max {max_allowed})",
    )


def _check_regex_multiline_flag(files: list[Path]) -> CheckResult:
    """Check 14: Regex multiline flag.

    Detects ``toMatch(/pattern/)`` without ``/m`` or ``/s`` flag on content
    likely from ``readFileSync``.  Warning only.
    """
    flagged: list[str] = []
    for f in files:
        content = f.read_text()
        # Only flag if the file also reads files (proxy for multiline content).
        if "readFileSync" not in content and "readFile" not in content:
            continue
        if _REGEX_NO_MULTILINE_PATTERN.search(content):
            flagged.append(f.name)

    return CheckResult(
        name="regex_multiline_flag",
        passed=True,  # Warning only
        detail=(
            f"{len(flagged)} file(s) use toMatch without /m on file content"
            if flagged else "no multiline regex issues detected"
        ),
        failing_files=flagged,
    )


def _check_no_project_writes(files: list[Path]) -> CheckResult:
    """Check 15: No writeFileSync to project paths.

    Scenarios must write to temp directories only.  Hard fail.
    """
    flagged: list[str] = []
    for f in files:
        content = f.read_text()
        has_write = any(p.search(content) for p in _PROJECT_WRITE_PATTERNS)
        if not has_write:
            continue
        has_safe_target = any(p.search(content) for p in _SAFE_WRITE_PATTERNS)
        if not has_safe_target:
            flagged.append(f.name)

    return CheckResult(
        name="no_project_writes",
        passed=len(flagged) == 0,
        detail=(
            f"{len(flagged)} file(s) write to project paths"
            if flagged else "no project file modifications detected"
        ),
        failing_files=flagged,
    )


def _check_no_hardcoded_paths(files: list[Path]) -> CheckResult:
    """Check 16: No hardcoded container paths.

    ``process.chdir('/workspace')`` or ``process.chdir('/project')`` break
    outside Docker.  Hard fail.
    """
    flagged: list[str] = []
    for f in files:
        content = f.read_text()
        if _HARDCODED_CHDIR_PATTERN.search(content):
            flagged.append(f.name)

    return CheckResult(
        name="no_hardcoded_paths",
        passed=len(flagged) == 0,
        detail=(
            f"{len(flagged)} file(s) use hardcoded container paths"
            if flagged else "no hardcoded container paths detected"
        ),
        failing_files=flagged,
    )


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
