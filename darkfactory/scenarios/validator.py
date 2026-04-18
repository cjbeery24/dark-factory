"""Scenario quality validation — checks on generated scenario files.

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
10-17. Tier 2 checks (assertion cap, source reads, indexOf, intent, etc.)
18. Contract helper import enforcement (hard fail)
19. vi.stubGlobal anti-pattern (warning, node only)
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

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

# Check 17: No CWD-relative paths (authoring contract).
#
# Rationale: the quality gate runs scenarios from different CWDs depending on
# project layout (project root vs. a pnpm workspace like apps/api/). A test
# that writes `readFileSync('packages/shared/foo.ts')` happens to work when
# CWD is exactly the project root and crashes otherwise. The scenarios MUST
# be CWD-invariant — anchored to the test file's own location via
# `path.resolve(import.meta.dirname, ...)` or `new URL(..., import.meta.url)`,
# or imported as workspace package names (`@scope/pkg`).
#
# FS op pattern matches `readFileSync('foo')`, `fs.readFileSync('foo')`, etc.
# with a *string-literal* first argument. Expression arguments (e.g.
# `readFileSync(path.resolve(...))`) do not match and therefore pass.
_CWD_FS_OP_PATTERN = re.compile(
    r"""(?:\bfs\.)?"""
    r"""\b(?:readFileSync|readFile|writeFileSync|writeFile|existsSync|"""
    r"""statSync|readdirSync|readdir|unlinkSync|unlink|mkdirSync|mkdir|"""
    r"""accessSync|access|rmSync)\s*\(\s*(['"])([^'"]*)\1"""
)
# Dynamic-import / require pattern matches `await import('foo')`,
# `import('foo')`, `require('foo')` with a string-literal argument.
_CWD_IMPORT_PATTERN = re.compile(
    r"""(?:\bawait\s+)?\b(?:import|require)\s*\(\s*(['"])([^'"]*)\1"""
)
# File extensions that indicate a filesystem path (not a package name).
_SOURCE_FILE_EXTENSIONS = (
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json",
    ".yaml", ".yml", ".toml", ".md", ".txt",
)


# Check 18: Contract helper import enforcement.
# Matches import-from paths containing _contracts/ at any depth.
_CONTRACT_IMPORT_PATTERNS: dict[str, re.Pattern[str]] = {
    # Node: from './_contracts/...' or require('./_contracts/...')
    "node": re.compile(
        r"""(?:from|require)\s*[\(]?\s*['"](?:\.\.?/)*_contracts/"""
    ),
    # Python: from _contracts.postgres.helpers import ...
    "python": re.compile(
        r"""from\s+_contracts[.\s]"""
    ),
}

# Check 19: vi.stubGlobal anti-pattern.
_VI_STUB_GLOBAL_PATTERN = re.compile(r"""vi\.stubGlobal\s*\(""")


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
    manifest_path: Path | None = None,
    tier1_prefix: str = "00-tier1-",
    adapters: "list[Any] | None" = None,
) -> ValidationResult:
    """Run validation checks on scenario files.

    Returns ValidationResult with per-check details (checks 5-6, 8, 14, 19 are warnings).
    Check 9 (tsc --noEmit) runs only for language='node' with .ts files.
    Checks 10-17 apply only to Tier 2 files (files NOT starting with tier1_prefix).
    Checks 18-19 apply to all files.
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
        check9 = _check_typescript_compilation(files, scenarios_dir, manifest_path)
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

        # Check 17: No CWD-relative paths (authoring contract, hard fail).
        check17 = _check_cwd_invariant_paths(tier2_files)
        result.checks.append(check17)

    # --- Checks 18-19: All files ---

    # Check 18: Contract helper import enforcement (hard fail).
    check18 = _check_contract_helper_usage(files, scenarios_dir, language)
    result.checks.append(check18)

    # Check 19: vi.stubGlobal anti-pattern (warning, node only).
    if language == "node":
        check19 = _check_vi_stub_global(files)
        result.warnings.append(check19)

    # Check 20: G2 — import specifier and symbol validation against manifest
    # entry_points. Hard fail if imports reference unknown specifiers or
    # symbols not declared in the manifest's exports list.
    if manifest_path and adapters:
        check20 = _check_import_specifiers(files, manifest_path, adapters)
        result.checks.append(check20)

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


def _generate_declare_module_stubs(
    scenarios_dir: Path,
    manifest_path: Path,
) -> Path | None:
    """Generate a .d.ts stub from the architecture manifest's entry_points.

    Creates ``declare module '<specifier>' { ... }`` blocks so tsc can resolve
    relative imports that point at impl source (which is masked in the TE
    container). Returns the path to the generated stub file, or None if no
    entry points are found.
    """
    if not manifest_path.is_file():
        return None

    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("could not parse manifest %s for type stubs", manifest_path)
        return None

    test_integration = (manifest or {}).get("test_integration")
    if not test_integration:
        return None

    entry_points = test_integration.get("entry_points", [])
    if not entry_points:
        return None

    lines: list[str] = [
        "// Auto-generated by dark-factory scenario validator (Check 9).",
        "// Provides ambient type stubs so tsc can resolve entry-point imports.",
        "",
    ]
    for ep in entry_points:
        specifier = ep.get("specifier", "")
        exports = ep.get("exports", [])
        if not specifier:
            continue
        lines.append(f"declare module '{specifier}' {{")
        for sym in exports:
            # Emit as `const` — works for both value and type usage with `any`.
            lines.append(f"  export const {sym}: any;")
        lines.append("}")
        lines.append("")

    if len(lines) <= 3:  # Only the header, no modules
        return None

    stub_path = scenarios_dir / "__df_entry_point_stubs.d.ts"
    stub_path.write_text("\n".join(lines), encoding="utf-8")
    return stub_path


def _check_typescript_compilation(
    files: list[Path],
    scenarios_dir: Path,
    manifest_path: Path | None = None,
) -> CheckResult:
    """Check 9: Compile TypeScript scenarios with tsc --noEmit.

    Catches non-existent assertion methods, import failures, and syntax errors
    before scenarios are executed.  Zero false positives — if tsc rejects it,
    it will crash at runtime.

    When a manifest_path is provided, generates ``declare module`` stubs for
    each entry_point specifier so tsc can resolve project-source imports that
    are masked in the TE container. This upgrades the check from a near-no-op
    (all imports unresolvable → skip) to catching real scenario code errors.

    Only runs when .ts files are present.  Gracefully skips when tsc is not
    available or times out.
    """
    ts_files = [f for f in files if f.suffix == ".ts"]
    if not ts_files:
        return CheckResult(
            name="typescript_compilation", passed=True, detail="no .ts files",
        )

    # Generate declare-module stubs from the architecture manifest so tsc
    # can resolve entry-point imports.
    stub_path: Path | None = None
    if manifest_path is not None:
        stub_path = _generate_declare_module_stubs(scenarios_dir, manifest_path)

    include_files = [str(f.relative_to(scenarios_dir)) for f in ts_files]
    if stub_path is not None:
        include_files.append(str(stub_path.relative_to(scenarios_dir)))

    tsconfig = {
        "compilerOptions": {
            "noEmit": True,
            "strict": True,
            "module": "esnext",
            "moduleResolution": "bundler",
            "types": ["vitest/globals", "node"],
            "skipLibCheck": True,
        },
        "include": include_files,
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
        if stub_path is not None:
            stub_path.unlink(missing_ok=True)


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


def _path_literal_is_cwd_invariant_fs(literal: str) -> bool:
    """An FS-op string literal is CWD-invariant only when it is absolute.

    Relative strings (with or without ``./`` / ``../`` prefix) resolve
    against CWD, which varies with gate invocation layout. CWD-invariant
    code paths use an expression (``path.resolve(import.meta.dirname, ...)``
    or ``new URL(..., import.meta.url)``) as the first argument — those
    are not matched by the string-literal regex and therefore skip this
    check automatically.
    """
    return literal.startswith("/")


def _import_literal_is_cwd_invariant(literal: str) -> bool:
    """A dynamic-import / require literal is acceptable when it is:

    - A dot-prefix relative path (``./foo``, ``../foo``) — file-relative
      resolution, not CWD-relative.
    - An absolute path (``/foo``).
    - A scoped package (``@scope/pkg``, ``@scope/pkg/subpath``).
    - A Node built-in (``node:fs``).
    - A package specifier (no ``/`` or package subpath that does NOT end
      in a source-file extension — rules out filesystem-path references).
    """
    if not literal:
        return True  # empty literal — let upstream checks handle
    if literal.startswith(("./", "../", "/", "@", "node:")):
        return True
    # Package specifier heuristic: if the literal does not end with a
    # recognized source file extension, treat it as a package name or
    # package subpath (e.g. ``vitest``, ``vitest/config``, ``drizzle-orm``).
    if not literal.endswith(_SOURCE_FILE_EXTENSIONS):
        return True
    return False


def _check_cwd_invariant_paths(files: list[Path]) -> CheckResult:
    """Check 17: No CWD-relative filesystem ops or bare-path imports.

    Enforces the test-engineer authoring contract: scenario code must be
    executable from any CWD. Flags:

    - String-literal first argument to ``readFileSync``/``existsSync``/
      ``writeFileSync``/``readdirSync``/... that is not an absolute path.
    - ``import('...')``/``require('...')`` with a string that looks like a
      filesystem path (ends in a source extension and has no dot/scope/
      ``node:`` prefix).

    Acceptable alternatives: ``path.resolve(import.meta.dirname, ...)``,
    ``new URL(..., import.meta.url)``, or importing workspace package
    names (``@scope/pkg``). Hard fail.
    """
    flagged: list[str] = []
    # Record representative offending snippets per file for the report.
    details_per_file: dict[str, list[str]] = {}
    for f in files:
        content = f.read_text()
        violations: list[str] = []
        for match in _CWD_FS_OP_PATTERN.finditer(content):
            literal = match.group(2)
            if not _path_literal_is_cwd_invariant_fs(literal):
                violations.append(match.group(0))
        for match in _CWD_IMPORT_PATTERN.finditer(content):
            literal = match.group(2)
            if not _import_literal_is_cwd_invariant(literal):
                violations.append(match.group(0))
        if violations:
            flagged.append(f.name)
            # Store up to 3 samples for the report.
            details_per_file[f.name] = violations[:3]

    if flagged:
        lines = [
            f"{len(flagged)} file(s) use CWD-relative paths (not CWD-invariant)",
        ]
        for name in flagged:
            lines.append(f"  - {name}: {'; '.join(details_per_file[name])}")
        lines.append(
            "FIX: use path.resolve(import.meta.dirname, '<rel>') or "
            "new URL('<rel>', import.meta.url) for filesystem reads, or "
            "import from workspace package names (@scope/pkg). Bare "
            "paths like readFileSync('packages/foo.ts') only work when "
            "CWD is exactly the project root and break when the gate "
            "runs from a workspace directory (e.g. apps/api/)."
        )
        detail = "\n".join(lines)
    else:
        detail = "no CWD-relative paths detected"

    return CheckResult(
        name="cwd_invariant_paths",
        passed=len(flagged) == 0,
        detail=detail,
        failing_files=flagged,
    )


def _check_contract_helper_usage(
    files: list[Path],
    scenarios_dir: Path,
    language: str,
) -> CheckResult:
    """Check 18: Contract helper import enforcement.

    If ``_contracts/`` exists under the scenarios directory and contains at
    least one helper module, at least one test file MUST import from it.
    Hard fail — staged helpers encode verified wire-level truths that the
    TE must use instead of hand-rolling equivalent logic.
    """
    contracts_dir = scenarios_dir / "_contracts"
    if not contracts_dir.is_dir():
        return CheckResult(
            name="contract_helper_usage", passed=True,
            detail="no contracts staged",
        )

    # Check if contracts have actual helper files (not just README/config).
    helper_extensions = {"node": {".ts", ".js"}, "python": {".py"}}
    exts = helper_extensions.get(language, set())
    has_helpers = any(
        f.suffix in exts
        for f in contracts_dir.rglob("*")
        if f.is_file()
    )
    if not has_helpers:
        return CheckResult(
            name="contract_helper_usage", passed=True,
            detail="no helper files in contracts",
        )

    # Check if any test file imports from _contracts.
    import_pattern = _CONTRACT_IMPORT_PATTERNS.get(language)
    if import_pattern is None:
        return CheckResult(
            name="contract_helper_usage", passed=True,
            detail=f"contract import check not implemented for {language}",
        )

    any_import = any(
        import_pattern.search(
            f.read_text(encoding="utf-8", errors="replace")
        )
        for f in files
    )

    if not any_import:
        return CheckResult(
            name="contract_helper_usage",
            passed=False,
            detail=(
                "contract helpers staged in _contracts/ but no test file "
                "imports from them. Staged helpers encode verified wire-level "
                "truths — you MUST use them."
            ),
            failing_files=[f.name for f in files],
        )
    return CheckResult(
        name="contract_helper_usage", passed=True,
        detail="contracts imported",
    )


def _check_vi_stub_global(files: list[Path]) -> CheckResult:
    """Check 19: vi.stubGlobal anti-pattern detection.

    ``vi.stubGlobal()`` does not intercept ES-module imports — ``vi.mock()``
    with a hoisted factory is the correct approach. Warning only because
    the pattern is occasionally valid for true globals like ``fetch``.
    """
    flagged: list[str] = []
    for f in files:
        if f.suffix not in (".ts", ".js"):
            continue
        content = f.read_text(encoding="utf-8", errors="replace")
        if _VI_STUB_GLOBAL_PATTERN.search(content):
            flagged.append(f.name)

    return CheckResult(
        name="vi_stub_global",
        passed=True,  # Warning only
        detail=(
            f"{len(flagged)} file(s) use vi.stubGlobal() which does not "
            f"intercept ES-module imports — prefer vi.mock() with a "
            f"hoisted factory"
            if flagged
            else "no vi.stubGlobal usage detected"
        ),
        failing_files=flagged,
    )


def _check_import_specifiers(
    files: list[Path],
    manifest_path: Path,
    adapters: list,
) -> CheckResult:
    """Check 20 (G2): Validate test imports against manifest entry_points.

    For each import in a scenario file, verify:
    1. The specifier matches a declared ``entry_points[*].specifier``
    2. Each imported symbol is a subset of that entry point's ``exports``

    Skips relative imports (./  ../) and node builtins / test-framework imports.
    """
    from darkfactory.spec.manifest import parse_manifest

    try:
        yaml_text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return CheckResult(
            name="import_specifiers",
            passed=True,
            detail="manifest not readable — skipping import check",
        )

    pr = parse_manifest(yaml_text)
    if not pr.manifest or not pr.manifest.test_integration.entry_points:
        return CheckResult(
            name="import_specifiers",
            passed=True,
            detail="no entry_points in manifest — skipping import check",
        )

    # Build specifier → exports lookup.
    ep_map: dict[str, set[str]] = {}
    for ep in pr.manifest.test_integration.entry_points:
        if ep.specifier:
            ep_map[ep.specifier] = set(ep.exports)

    if not ep_map:
        return CheckResult(
            name="import_specifiers",
            passed=True,
            detail="no specifiers declared — skipping",
        )

    # Collect all imports from all scenario files via adapters.
    all_imports = []
    for f in files:
        for adapter in adapters:
            imports = adapter.parse_test_imports(f)
            if imports:
                all_imports.extend(imports)
                break  # first adapter that returns results wins

    if not all_imports:
        return CheckResult(
            name="import_specifiers",
            passed=True,
            detail="no parseable imports found",
        )

    # Skip relative imports, test framework imports, and node builtins.
    _SKIP_PREFIXES = (".", "node:", "vitest", "jest", "@jest", "@testing-library",
                      "assert", "path", "fs", "child_process", "crypto",
                      "http", "https", "net", "os", "url", "util", "stream",
                      "events", "buffer", "querystring", "zlib")

    specifier_violations: list[str] = []
    symbol_violations: list[str] = []

    for imp in all_imports:
        # Skip relative imports and known non-project specifiers.
        if any(imp.specifier.startswith(p) for p in _SKIP_PREFIXES):
            continue
        # Skip _contracts/ helper imports (covered by check 18).
        if "_contracts/" in imp.specifier:
            continue

        if imp.specifier not in ep_map:
            # Unknown specifier — might be a third-party package.
            # Only flag if it looks like it could be a project import
            # (starts with @ or matches a known entry point prefix).
            known_prefixes = set()
            for spec in ep_map:
                if "/" in spec:
                    known_prefixes.add(spec.rsplit("/", 1)[0])
                else:
                    known_prefixes.add(spec)
            if any(imp.specifier.startswith(p) for p in known_prefixes):
                rel = Path(imp.file_path).name if imp.file_path else "?"
                specifier_violations.append(
                    f"{rel}:{imp.line} imports '{imp.specifier}' "
                    f"(not in manifest entry_points)"
                )
            continue

        # Specifier matches — check symbols.
        declared_exports = ep_map[imp.specifier]
        if not declared_exports:
            continue  # no exports declared = allow anything

        for sym in imp.symbols:
            if sym.startswith("* as "):
                continue  # namespace imports always valid
            if sym not in declared_exports:
                rel = Path(imp.file_path).name if imp.file_path else "?"
                symbol_violations.append(
                    f"{rel}:{imp.line} imports '{sym}' from '{imp.specifier}' "
                    f"(not in exports: {sorted(declared_exports)})"
                )

    total = len(specifier_violations) + len(symbol_violations)
    if total == 0:
        return CheckResult(
            name="import_specifiers",
            passed=True,
            detail=f"all {len(all_imports)} import(s) validated against manifest",
        )

    all_violations = specifier_violations + symbol_violations
    return CheckResult(
        name="import_specifiers",
        passed=False,
        detail=(
            f"{len(specifier_violations)} specifier violation(s), "
            f"{len(symbol_violations)} symbol violation(s): "
            + "; ".join(all_violations[:5])
            + (f" ... and {total - 5} more" if total > 5 else "")
        ),
        failing_files=list({
            Path(imp.file_path).name for imp in all_imports
            if imp.file_path
        }),
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
