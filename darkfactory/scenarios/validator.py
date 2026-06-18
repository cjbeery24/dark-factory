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
# Phase 5 Stage 4 (2026-04-25): node `assert\(` extended to `assert\w*\(`
# so contract-helper calls like `assertUnsetGucIsNullish(...)` /
# `assertTxGucRoundTrips(...)` count as assertions. Without this, a
# scenario file that delegates all its assertion logic to
# `_contracts/<adapter>/` helpers (the pattern `_check_contract_helper_
# usage` actively encourages) registers as zero assertions and fails
# `_check_assertions`. The two checks contradicted each other.
_ASSERTION_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "node": [re.compile(r"expect\("), re.compile(r"assert\w*\(")],
    "python": [re.compile(r"assert "), re.compile(r"self\.assert"), re.compile(r"pytest\.raises")],
    "go": [re.compile(r"t\.Error"), re.compile(r"require\."), re.compile(r"assert\.")],
    "rust": [re.compile(r"assert!"), re.compile(r"assert_eq!")],
}

# Per-language multiplier on the assertion-upper-bound cap (Sprint:breadth).
# The cap (scenario_max_assertions_per_file, default 30) is tuned to the node
# baseline where one logical check is ONE `expect(result).toEqual({...})`. Idiomatic
# pytest asserts each property separately (`assert r["ok"] is True`, `assert
# r["cents"] == 123456`, `assert "error" not in r` = 3 assertions for one logical
# check), so the SAME coverage counts ~2-3x more `assert ` statements per file.
# A flat global cap therefore false-rejects well-organized python files (T2.0:
# 50/36/45 vs cap 30). Scale python by 2x (conservative end of the 2-3x density
# ratio) so the cap measures comparable granularity across stacks; the global cap
# stays the single tunable knob. Languages absent here keep the 1x baseline.
# (Same family as the JS-coupled anchor-coverage grammar — a node-tuned mechanism
# that needed per-language treatment, not a python carve-out.)
_ASSERTION_CAP_MULTIPLIER: dict[str, int] = {"python": 2}

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
    # S0(b): structured compile outcome for Check 9 (typescript_compilation).
    # Retires the fragile ``"skipped" in detail`` substring coupling the S41
    # timeout-salvage backstop relied on (orchestrator._step_scenario_generation).
    # One of: "" (not a compile check), "passed" (tsc type-checked the bodies,
    # zero code/syntax errors), "skipped" (tsc could NOT validate — node types
    # unresolvable / tsc unavailable / hung — bodies NEVER checked, the genuine
    # no-op), "failed" (real code errors), "syntax" (TS1xxx parser-class errors —
    # a SIGKILL-truncated or otherwise malformed file). Only "passed" (and a
    # non-TS "not_applicable", encoded as "passed" with the "no .ts files"
    # detail) is safe to ship from the salvage path.
    compile_status: str = ""


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
        check9 = _check_typescript_compilation(
            files, scenarios_dir, manifest_path, project_dir=project_dir,
        )
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
    seen_specifiers: set[str] = set()
    for ep in entry_points:
        specifier = ep.get("specifier", "")
        exports = ep.get("exports", [])
        if not specifier:
            continue
        # S0(b): emit the `declare module` for EVERY extension form the test
        # might write the import as. tsc with moduleResolution:bundler matches a
        # `declare module '<spec>'` against the import specifier as-written, so a
        # manifest specifier of ``.../db/index`` does NOT satisfy an import of
        # ``.../db/index.ts`` (and vice-versa), leaving the import unresolved
        # (TS2307 → bucketed import-error → the imported symbol becomes ``any``,
        # masking real type errors in its usage). Generate the bare specifier
        # plus its ``.ts``/``.js`` variants (stripping any extension first so we
        # don't double-suffix) and the ``.js`` form a TS file may use for an ESM
        # import of a ``.ts`` source. Dedup so two entry_points that normalize to
        # the same base don't emit a duplicate `declare module` (TS2649).
        base = re.sub(r"\.(ts|tsx|js|jsx|mjs|cjs|mts|cts)$", "", specifier)
        variants = [base, f"{base}.ts", f"{base}.js"]
        for variant in variants:
            if variant in seen_specifiers:
                continue
            seen_specifiers.add(variant)
            lines.append(f"declare module '{variant}' {{")
            for sym in exports:
                if sym == "default":
                    # `default` is reserved — `export const default` is invalid TS
                    # (TS1389). Emit the default-export form instead. The default
                    # is intentionally value-shaped (`any`) so it resolves for
                    # `import x from` and `import { default as x }` alike; do NOT
                    # narrow it to a type, or default exports become blocking again.
                    lines.append("  const _df_default: any;")
                    lines.append("  export default _df_default;")
                else:
                    # Emit as `const` — works for both value and type usage with `any`.
                    lines.append(f"  export const {sym}: any;")
            lines.append("}")
            lines.append("")

    if len(lines) <= 3:  # Only the header, no modules
        return None

    stub_path = scenarios_dir / "__df_entry_point_stubs.d.ts"
    stub_path.write_text("\n".join(lines), encoding="utf-8")
    return stub_path


def _resolve_project_node_types(
    project_dir: Path | None, manifest_path: Path | None,
) -> Path | None:
    """S0(b): locate the project's installed ``@types/node`` package directory.

    Check 9 runs HOST-side (``npx tsc`` with ``cwd=scenarios_dir``). In a pnpm
    monorepo, ``@types/node`` is NOT hoisted to a node_modules the scenarios dir
    can walk up to — it lives under a workspace app or under ``.pnpm/``. When tsc
    can't resolve it, ``"types":[...,"node"]`` makes tsc emit a single
    ``TS2688 Cannot find type definition file for 'node'`` and BAIL before
    type-checking any body — Check 9 silently no-ops (the latent hole this sprint
    fixes). We resolve the project's OWN copy (zero version skew vs. a vendored
    pin) and symlink it check-local so the default resolution walk finds it.

    Search order (most-specific first):
      1. ``<project>/node_modules/@types/node`` (flat / hoisted)
      2. the entry-point app's ``node_modules/@types/node`` (the app the
         scenarios import from, inferred from manifest entry_point specifiers)
      3. ``<project>/node_modules/.pnpm/@types+node@*/node_modules/@types/node``
      4. a bounded ``<project>/*/node_modules/@types/node`` (per-app, non-pnpm)

    Returns the ``@types/node`` package dir, or None when no copy is installed
    (then Check 9 degrades to the genuine "skipped" no-op, as before — but now
    it still runs the syntax-only pass that catches truncation).
    """
    if project_dir is None:
        return None

    def _valid(p: Path) -> bool:
        # A usable @types/node has its declaration entry point.
        return (p / "index.d.ts").is_file()

    candidates: list[Path] = []

    # 1. flat / hoisted
    candidates.append(project_dir / "node_modules" / "@types" / "node")

    # 2. entry-point app's node_modules (parse the app dir from a manifest
    #    specifier like ``../../../apps/api/src/db/index``).
    if manifest_path is not None and manifest_path.is_file():
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            eps = (manifest.get("test_integration") or {}).get("entry_points", [])
            for ep in eps:
                spec = ep.get("specifier", "") if isinstance(ep, dict) else ""
                m = re.search(r"(apps|packages)/([^/]+)/", spec)
                if m:
                    app_dir = project_dir / m.group(1) / m.group(2)
                    candidates.append(app_dir / "node_modules" / "@types" / "node")
        except Exception:  # noqa: BLE001 — best-effort; fall through to globs.
            pass

    # 3. pnpm virtual store. We only need *a* valid @types/node to unblock the
    # TS2688 bail (we are not type-checking against a specific node version — the
    # 32 real errors are version-independent implicit-any/cast/spread bugs), and
    # the app-specific copy (candidate #2) already takes precedence. But when the
    # store holds multiple versions and no app owner was identified, pick the
    # HIGHEST by semver (not lexical — `sorted()` puts 9.x above 22.x) so the
    # choice is deterministic and recent (Codex/Linus nit). first-valid-wins.
    pnpm_store = project_dir / "node_modules" / ".pnpm"
    if pnpm_store.is_dir():
        def _ver_key(p: Path) -> tuple:
            # parent chain: .../@types+node@<ver>/node_modules/@types/node
            name = p.parents[2].name  # "@types+node@22.19.21"
            ver = name.split("@")[-1]
            parts = []
            for seg in ver.split("."):
                num = re.match(r"\d+", seg)
                parts.append(int(num.group()) if num else 0)
            return tuple(parts)
        candidates.extend(sorted(
            pnpm_store.glob("@types+node@*/node_modules/@types/node"),
            key=_ver_key, reverse=True,
        ))

    # 4. bounded per-app scan (one level deep; avoids an unbounded ** walk)
    for child in sorted(project_dir.glob("*/node_modules/@types/node")):
        candidates.append(child)
    for child in sorted(project_dir.glob("*/*/node_modules/@types/node")):
        candidates.append(child)

    for cand in candidates:
        if _valid(cand):
            return cand
    return None


# tsc error codes for genuine PARSE/SYNTAX failures (TS1xxx range). These fire
# even when module/type resolution also fails (verified: a SIGKILL-truncated
# file emits TS1160 with or without @types/node present), so they are the
# reliable signal that a salvaged file is truncated garbage rather than a clean
# file with unresolved cross-barrier imports. NEVER soft-passed.
_SYNTAX_ERROR_RE = re.compile(r"error TS1\d{3}\b")
_IMPORT_ERROR_MARKERS = (
    "Cannot find type definition file for",
    "Cannot find module",
)


def _check_typescript_compilation(
    files: list[Path],
    scenarios_dir: Path,
    manifest_path: Path | None = None,
    project_dir: Path | None = None,
) -> CheckResult:
    """Check 9: Compile TypeScript scenarios with tsc --noEmit.

    Catches non-existent assertion methods, import failures, type misuse, and
    syntax errors before scenarios are executed.  Zero false positives — if tsc
    rejects a body, it will crash at runtime.

    LOAD-BEARING CONTRACT (S0(b), supersedes the S41 substring coupling): the
    outcome is reported in the STRUCTURED ``compile_status`` field, not by a
    ``"skipped"`` substring in ``detail``. The TE timeout-salvage backstop in
    ``orchestrator._step_scenario_generation`` reads ``compile_status`` and ships
    a salvaged (possibly truncated) result ONLY when it is ``"passed"``. Values:
      - ``"passed"``  — tsc resolved node types and type-checked the bodies with
        zero code/syntax errors (unresolved imports to BARRIER-MASKED project
        source are expected and allowed). Safe to ship.
      - ``"skipped"`` — tsc could NOT validate the bodies (``@types/node`` not
        installed → TS2688 bail; or tsc unavailable / hung). The genuine no-op;
        NOT a compile backstop. Bodies were never checked.
      - ``"failed"``  — real scenario code errors (implicit-any, bad casts, …).
      - ``"syntax"``  — TS1xxx parser-class errors → a SIGKILL-truncated or
        otherwise malformed file. Caught even when types are unresolved.
    ``detail`` keeps a human-readable summary (and, for skips, still contains the
    word "skipped" for log readers) but is NO LONGER the backstop's contract.

    S0(b) fix: resolves the project's installed ``@types/node`` and symlinks it
    check-local (no ``typeRoots`` override — that breaks ``vitest/globals``
    resolution) so tsc actually type-checks node-importing specs in pnpm
    monorepos instead of bailing on TS2688. When a manifest_path is provided,
    also generates ``declare module`` stubs for entry_point specifiers.

    Only runs when .ts files are present.
    """
    ts_files = [f for f in files if f.suffix == ".ts"]
    if not ts_files:
        return CheckResult(
            name="typescript_compilation", passed=True, detail="no .ts files",
            compile_status="passed",
        )

    # Resolve the project's own @types/node and symlink it check-local so the
    # default resolution walk from scenarios_dir finds it (vitest/globals keeps
    # resolving via the existing ancestor walk — do NOT set typeRoots).
    node_types = _resolve_project_node_types(project_dir, manifest_path)
    node_types_link: Path | None = None
    types_root: Path | None = None
    # Self-link guard: in the degenerate case where the resolved @types/node IS
    # the check-local link path (only possible if scenarios_dir == project_dir,
    # which the real pipeline never does — scenarios live at
    # project/.dark-factory/scenarios/<spec>), do NOT try to symlink a dir onto
    # itself; node types are already in place (Linus nit).
    _link_target = scenarios_dir / "node_modules" / "@types" / "node"
    if node_types is not None and node_types.resolve() == _link_target.resolve():
        node_types_link = None  # already resolvable check-local; nothing to do
    elif node_types is not None:
        try:
            types_root = scenarios_dir / "node_modules" / "@types"
            types_root.mkdir(parents=True, exist_ok=True)
            node_types_link = types_root / "node"
            if node_types_link.exists() or node_types_link.is_symlink():
                node_types_link.unlink()
            node_types_link.symlink_to(node_types, target_is_directory=True)
        except OSError as exc:
            log.warning("could not link @types/node for tsc check: %s", exc)
            node_types_link = None
            node_types = None

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
            # Explicit target/lib so the discriminator is real code errors, not
            # ES5-default lib gaps (e.g. TS2468 "Cannot find global value
            # 'Promise'"). Verified lib-neutral on the spec-4 corpus.
            "target": "es2022",
            "lib": ["es2022"],
            "module": "esnext",
            "moduleResolution": "bundler",
            "types": ["vitest/globals", "node"],
            "skipLibCheck": True,
        },
        "include": include_files,
    }

    tsconfig_path = scenarios_dir / "tsconfig.check.json"
    tsconfig_path.write_text(json.dumps(tsconfig), encoding="utf-8")

    def _result(passed: bool, detail: str, status: str,
                failing: list[str] | None = None) -> CheckResult:
        return CheckResult(
            name="typescript_compilation", passed=passed, detail=detail,
            failing_files=failing or [], compile_status=status,
        )

    try:
        proc = subprocess.run(
            ["npx", "tsc", "--project", str(tsconfig_path)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(scenarios_dir),
        )
        if proc.returncode == 0:
            return _result(True, "all files compile", "passed")

        combined = proc.stdout + proc.stderr
        if "This is not the tsc command" in combined:
            return _result(True, "tsc not available (skipped)", "skipped")

        # tsc emits errors as "error TSxxxx: ..." or "file.ts(1,1): error TSxxxx: ..."
        error_lines = [
            l for l in combined.splitlines()
            if l.strip().startswith("error ") or ": error " in l
        ]
        syntax_errors = [l for l in error_lines if _SYNTAX_ERROR_RE.search(l)]
        import_errors = [
            l for l in error_lines
            if not _SYNTAX_ERROR_RE.search(l)
            and any(m in l for m in _IMPORT_ERROR_MARKERS)
        ]
        code_errors = [
            l for l in error_lines
            if not _SYNTAX_ERROR_RE.search(l)
            and not any(m in l for m in _IMPORT_ERROR_MARKERS)
        ]

        def _failing_files(lines: list[str]) -> list[str]:
            out: set[str] = set()
            for line in lines:
                if ".ts(" in line:
                    out.add(line.split("(")[0].strip())
            return sorted(out)

        # 1. Syntax/parser-class (TS1xxx) errors ALWAYS fail. This bucket is the
        #    reliable signal for a SIGKILL-truncated/malformed file (TS1005,
        #    TS1160 — fire even when types are unresolved, which is what makes the
        #    salvage backstop meaningful). It also catches semantic TS1xxx codes
        #    (TS1192 "no default export", TS1259 esModuleInterop) — those are real
        #    bugs worth failing on too, so the bucket is "syntax" (not "truncated"
        #    — the file may be malformed rather than chopped). The orchestrator
        #    backstop only checks ``!= "passed"``, so the precise label is for
        #    humans, not control flow.
        if syntax_errors:
            return _result(
                False,
                "tsc found syntax/parser errors (file may be truncated or "
                "malformed):\n" + "\n".join(syntax_errors)[:2000],
                "syntax", _failing_files(syntax_errors),
            )
        # 2. Real code errors — fail even if import errors also present.
        if code_errors:
            return _result(
                False,
                "tsc found scenario code errors:\n" + "\n".join(code_errors)[:2000],
                "failed", _failing_files(code_errors),
            )
        # 3. Only import/module-resolution errors remain. If we could NOT resolve
        #    node types, tsc bailed (TS2688) BEFORE type-checking the bodies —
        #    this is the genuine no-op skip (bodies never checked). If we DID
        #    resolve node types, the bodies WERE checked and the only residue is
        #    imports to barrier-masked project source / uninstalled deps — that
        #    is a real pass.
        if import_errors:
            if node_types is None:
                return _result(
                    True,
                    "type definitions not available (skipped — @types/node "
                    "unresolved, tsc did not type-check bodies)",
                    "skipped",
                )
            return _result(
                True,
                "all bodies type-check; only barrier-masked imports unresolved",
                "passed",
            )
        # 4. Non-zero exit with no recognized error lines — report raw output.
        return _result(
            False, f"tsc --noEmit failed:\n{proc.stdout[:2000]}", "failed",
        )
    except FileNotFoundError:
        return _result(True, "tsc not available (skipped)", "skipped")
    except subprocess.TimeoutExpired:
        return _result(True, "tsc timed out (skipped)", "skipped")
    finally:
        tsconfig_path.unlink(missing_ok=True)
        if stub_path is not None:
            stub_path.unlink(missing_ok=True)
        if node_types_link is not None:
            node_types_link.unlink(missing_ok=True)
            # Remove the node_modules/@types scaffolding we created, deepest-
            # first and ONLY while empty — scenarios_dir is Implementer-readable,
            # so don't leave stray (empty) node_modules litter the gate's vitest
            # or a human debugger would trip over (Linus nit). break on a non-
            # empty dir (a pre-existing real node_modules) — never rmtree.
            if types_root is not None:
                for d in (types_root, types_root.parent):
                    try:
                        d.rmdir()
                    except OSError:
                        break


# --- Checks 10-16: Tier 2 only ---


def _check_assertion_upper_bound(
    files: list[Path], language: str, max_per_file: int,
) -> CheckResult:
    """Check 10: Assertion count upper bound.

    Files with more than the effective cap (``max_per_file`` × the language's
    ``_ASSERTION_CAP_MULTIPLIER``, default 1×) assertions are likely overly
    granular and fragile.  Hard fail.
    """
    patterns = _ASSERTION_PATTERNS.get(language, [])
    # Scale the (node-baseline) cap by the language's assertion-density factor so
    # the check measures comparable test granularity across stacks rather than
    # raw `assert ` count (see _ASSERTION_CAP_MULTIPLIER).
    effective_cap = max_per_file * _ASSERTION_CAP_MULTIPLIER.get(language, 1)
    failing: list[str] = []

    for f in files:
        content = f.read_text()
        count = sum(len(p.findall(content)) for p in patterns)
        if count > effective_cap:
            failing.append(f"{f.name} ({count} assertions)")

    return CheckResult(
        name="assertion_upper_bound",
        passed=len(failing) == 0,
        detail=f"{len(failing)} file(s) exceed {effective_cap} assertions",
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
