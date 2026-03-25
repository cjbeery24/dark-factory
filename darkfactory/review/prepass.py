"""Deterministic review pre-pass — FR-1 through FR-4.

Computes objective scores for Test Results Analysis (0-25),
Code Quality (0-20), and Security (0-15) without invoking an LLM.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from darkfactory.models.config import PipelineConfig
from darkfactory.review.patterns import SECURITY_PATTERNS
from darkfactory.scenarios.detection import get_sentinel_path, has_sentinel_file

log = logging.getLogger(__name__)

# Maximum source files to scan before stopping (EC-10).
_MAX_FILES = 500

# ---------------------------------------------------------------------------
# Data classes (Interface Contract §5)
# ---------------------------------------------------------------------------


@dataclass
class TestAnalysisResult:
    score: int  # 0-25
    scenario_passed: int
    scenario_failed: int
    scenario_total: int
    pass_rate: int  # 0-100
    satisfaction_score: int  # 0-100
    failure_pattern: str  # "none" | "isolated" | "systematic" | "sentinel"
    gate_outcomes: dict[str, bool] = field(default_factory=dict)
    sentinel_content: str = ""  # non-empty when failure_pattern == "sentinel"


@dataclass
class FunctionMetric:
    name: str
    file: str  # relative to project root
    line_count: int


@dataclass
class FileMetric:
    path: str  # relative to project root
    line_count: int


@dataclass
class CodeQualityResult:
    score: int  # 0-20
    lint_passed: bool
    long_functions: list[FunctionMetric] = field(default_factory=list)
    long_files: list[FileMetric] = field(default_factory=list)
    deep_nesting_files: list[str] = field(default_factory=list)


@dataclass
class SecurityFinding:
    file: str  # relative to project root
    line_number: int
    pattern: str  # e.g. "sql_injection"
    severity: str  # "HIGH" | "MEDIUM" | "LOW"
    matched_text: str  # up to 100 chars


@dataclass
class SecurityResult:
    score: int  # 0-15
    findings: list[SecurityFinding] = field(default_factory=list)
    language: str = ""


@dataclass
class ReviewPrePassResult:
    test_analysis: TestAnalysisResult
    code_quality: CodeQualityResult
    security: SecurityResult
    deterministic_total: int  # 0-60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_int(value: str) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def _read_env_file(path: Path) -> dict[str, str]:
    """Read a KEY=VALUE env file into a dict."""
    data: dict[str, str] = {}
    if not path.exists():
        return data
    with path.open() as f:
        for line in f:
            stripped = line.strip()
            if "=" in stripped:
                key, _, value = stripped.partition("=")
                data[key] = value
    return data


def _collect_source_files(project_dir: Path, source_dirs: list[str]) -> list[Path]:
    """Collect source files from source_dirs, up to _MAX_FILES."""
    files: list[Path] = []
    for sd in source_dirs:
        d = project_dir / sd
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if f.is_file():
                files.append(f)
                if len(files) >= _MAX_FILES:
                    log.warning("source file limit reached (%d), skipping remaining files", _MAX_FILES)
                    return files
    return files


def _read_file_text(path: Path) -> str | None:
    """Read file text, returning None on encoding/permission errors (EC-4)."""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError, OSError):
        log.debug("skipping unreadable file: %s", path)
        return None


# ---------------------------------------------------------------------------
# FR-1: Test Results Analysis
# ---------------------------------------------------------------------------

# Regex to extract failed scenario filenames from the gate summary.
_FAILURE_RE = re.compile(r"(?:FAIL|ERROR|FAILED)\s+.*?((?:0[1-8])[-_][^\s]+)")

# Gate outcome extraction patterns.
# Handles both markdown table format (| Build | PASS |) and simple (build: pass).
_GATE_OUTCOME_RE = re.compile(
    r"(?:\|\s*)?"
    r"(Build|Lint|Type\s*Check|Self[- ]?Tests|Blind\s+Scenarios?)"
    r"(?:\s*\|\s*|\s*[:\s]+)"
    r"(PASS(?:ED)?|FAIL(?:ED)?)",
    re.IGNORECASE,
)


def analyze_test_results(
    project_dir: Path,
    spec_name: str,
    min_satisfaction: int = 0,
) -> TestAnalysisResult:
    """FR-1: Parse quality gate output into test analysis score."""
    counts_path = project_dir / ".dark-factory" / "reports" / f"{spec_name}-scenario-counts.env"
    summary_path = project_dir / ".dark-factory" / "reports" / f"{spec_name}-quality-gate-summary.md"

    # Check for sentinel before reading counts — a sentinel means blind scenarios
    # were intentionally skipped, not that they failed.
    scenarios_dir = project_dir / ".dark-factory" / "scenarios" / spec_name
    sentinel_present = has_sentinel_file(scenarios_dir)
    sentinel_content = ""
    if sentinel_present:
        sentinel_content = get_sentinel_path(scenarios_dir).read_text()

    # Sentinel short-circuit: skip reading scenario counts entirely.
    # Satisfaction and pass-rate data come from scenario runs that didn't happen.
    if sentinel_present:
        gate_outcomes = _parse_gate_outcomes(summary_path)
        score = 25 + _compute_non_scenario_score(gate_outcomes, 0, 0)
        return TestAnalysisResult(
            score=max(score, 0),
            scenario_passed=0, scenario_failed=0, scenario_total=0,
            pass_rate=0, satisfaction_score=0,
            failure_pattern="sentinel",
            gate_outcomes=gate_outcomes,
            sentinel_content=sentinel_content,
        )

    # EC-1: missing counts file → all zeros, score 0.
    if not counts_path.exists():
        log.warning("scenario counts file not found: %s", counts_path)
        return TestAnalysisResult(
            score=0, scenario_passed=0, scenario_failed=0, scenario_total=0,
            pass_rate=0, satisfaction_score=0, failure_pattern="none",
        )

    data = _read_env_file(counts_path)
    scenario_passed = _safe_int(data.get("SCENARIO_PASSED", "0"))
    scenario_failed = _safe_int(data.get("SCENARIO_FAILED", "0"))
    scenario_total = _safe_int(data.get("SCENARIO_TOTAL", "0"))
    pass_rate = _safe_int(data.get("SCENARIO_RATE", "0"))
    satisfaction = _safe_int(data.get("SATISFACTION_SCORE", "0"))

    # Parse gate outcomes from the summary markdown.
    gate_outcomes = _parse_gate_outcomes(summary_path)

    # Compute score (starts at 25, deduct for low pass rate).
    score = 25
    if pass_rate < 80:
        score -= 10
    elif pass_rate < 95:
        score -= 5
    score += _compute_non_scenario_score(gate_outcomes, satisfaction, min_satisfaction)
    score = max(score, 0)

    failure_pattern = "none"
    if scenario_failed > 0:
        failure_pattern = _classify_failure_pattern(project_dir, spec_name, summary_path)

    return TestAnalysisResult(
        score=score,
        scenario_passed=scenario_passed,
        scenario_failed=scenario_failed,
        scenario_total=scenario_total,
        pass_rate=pass_rate,
        satisfaction_score=satisfaction,
        failure_pattern=failure_pattern,
        gate_outcomes=gate_outcomes,
    )


def _compute_non_scenario_score(
    gate_outcomes: dict[str, bool],
    satisfaction: int,
    min_satisfaction: int,
) -> int:
    """Compute the non-scenario portion of the test analysis score.

    Returns a value to be added to the scenario base (25).
    All deductions are negative, so this returns a value <= 0.
    """
    deduction = 0
    # Self-test failures (EC-9: default to False if gate not found).
    if not gate_outcomes.get("self-tests", False):
        deduction -= 5
    # Satisfaction below minimum.
    if min_satisfaction > 0 and satisfaction < min_satisfaction:
        deduction -= 3
    # Build or type check gate failed.
    if not gate_outcomes.get("build", False) or not gate_outcomes.get("typecheck", False):
        deduction -= 2
    return deduction


def _parse_gate_outcomes(summary_path: Path) -> dict[str, bool]:
    """Extract gate pass/fail from the quality gate summary markdown."""
    outcomes: dict[str, bool] = {}
    if not summary_path.exists():
        return outcomes

    text = _read_file_text(summary_path)
    if text is None:
        return outcomes

    for m in _GATE_OUTCOME_RE.finditer(text):
        gate_name = m.group(1).lower().replace(" ", "")
        # Normalize names.
        if "typecheck" in gate_name or "type" in gate_name:
            gate_name = "typecheck"
        elif "self" in gate_name:
            gate_name = "self-tests"
        elif "blind" in gate_name:
            gate_name = "blind-scenarios"
        passed = m.group(2).lower().startswith("pass")
        outcomes[gate_name] = passed

    return outcomes


def _classify_failure_pattern(
    project_dir: Path,
    spec_name: str,
    summary_path: Path,
) -> str:
    """Classify failure pattern as 'systematic' or 'isolated'."""
    text = _read_file_text(summary_path) if summary_path.exists() else None
    if text is None:
        return "isolated"

    # Extract failed scenario filenames.
    failed_files = _FAILURE_RE.findall(text)
    if not failed_files:
        return "isolated"

    # Group by 2-digit layer prefix.
    layer_counts: dict[str, int] = {}
    for fname in failed_files:
        prefix = fname[:2]
        layer_counts[prefix] = layer_counts.get(prefix, 0) + 1

    total_failures = len(failed_files)
    # Systematic if any single layer accounts for >50% of all failures.
    for count in layer_counts.values():
        if count / total_failures > 0.5:
            return "systematic"

    return "isolated"


# ---------------------------------------------------------------------------
# FR-2: Code Quality
# ---------------------------------------------------------------------------

# Function detection patterns by language.
_FUNC_PYTHON_RE = re.compile(r"^(\s*)def\s+(\w+)")
_FUNC_NODE_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)"
    r"|^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[^=])\s*=>"
)
_FUNC_GO_RE = re.compile(r"^(\s*)func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)")
_FUNC_RUST_RE = re.compile(r"^(\s*)(?:pub\s+)?(?:async\s+)?fn\s+(\w+)")

# Nesting thresholds.
_NESTING_SPACES = 20  # 5 levels × 4 spaces
_NESTING_TABS = 5


def analyze_code_quality(
    project_dir: Path,
    source_dirs: list[str],
    gate_summary_path: Path,
) -> CodeQualityResult:
    """FR-2: Compute code quality score from lint results and complexity metrics."""
    # EC-3: no source files → score 20.
    source_files = _collect_source_files(project_dir, source_dirs)

    # Lint status from gate summary (EC-9: default to False if not found).
    lint_passed = False
    if gate_summary_path.exists():
        outcomes = _parse_gate_outcomes(gate_summary_path)
        lint_passed = outcomes.get("lint", False)
    else:
        # EC-2: gate summary missing → assume lint failed.
        log.warning("gate summary file not found: %s", gate_summary_path)
        lint_passed = False

    score = 20
    if not lint_passed:
        score -= 5

    # Determine language from file extensions.
    lang = _detect_language(source_files)

    long_functions: list[FunctionMetric] = []
    long_files: list[FileMetric] = []
    deep_nesting_files: list[str] = []

    for fpath in source_files:
        text = _read_file_text(fpath)
        if text is None:
            continue

        lines = text.splitlines()
        rel_path = str(fpath.relative_to(project_dir))

        # File length (>500 lines).
        if len(lines) > 500:
            long_files.append(FileMetric(path=rel_path, line_count=len(lines)))

        # Function length (>50 lines).
        funcs = _detect_long_functions(lines, lang, rel_path)
        long_functions.extend(funcs)

        # Nesting depth (>5 levels).
        if _has_deep_nesting(lines):
            deep_nesting_files.append(rel_path)

    # Deductions.
    func_deduction = min(len(long_functions), 5)
    file_deduction = min(len(long_files), 3)
    nesting_deduction = min(len(deep_nesting_files), 2)

    score -= func_deduction + file_deduction + nesting_deduction
    score = max(0, min(score, 20))

    log.debug(
        "code quality deductions: lint=%s, long_funcs=%d, long_files=%d, deep_nesting=%d",
        not lint_passed, func_deduction, file_deduction, nesting_deduction,
    )

    return CodeQualityResult(
        score=score,
        lint_passed=lint_passed,
        long_functions=long_functions,
        long_files=long_files,
        deep_nesting_files=deep_nesting_files,
    )


def _detect_language(files: list[Path]) -> str:
    """Best-effort language detection from file extensions."""
    ext_counts: dict[str, int] = {}
    for f in files:
        ext = f.suffix.lower()
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

    ext_to_lang = {
        ".py": "python",
        ".js": "node", ".ts": "node", ".tsx": "node", ".jsx": "node",
        ".go": "go",
        ".rs": "rust",
    }

    best_lang = ""
    best_count = 0
    for ext, count in ext_counts.items():
        lang = ext_to_lang.get(ext, "")
        if lang and count > best_count:
            best_lang = lang
            best_count = count

    return best_lang


def _detect_long_functions(
    lines: list[str],
    lang: str,
    rel_path: str,
) -> list[FunctionMetric]:
    """Detect functions exceeding 50 lines."""
    results: list[FunctionMetric] = []

    if lang == "python":
        results = _detect_python_functions(lines, rel_path)
    elif lang == "node":
        results = _detect_brace_functions(lines, rel_path, _FUNC_NODE_RE)
    elif lang == "go":
        results = _detect_brace_functions(lines, rel_path, _FUNC_GO_RE)
    elif lang == "rust":
        results = _detect_brace_functions(lines, rel_path, _FUNC_RUST_RE)
    # Unsupported languages: skip (no deduction).

    return [f for f in results if f.line_count > 50]


def _detect_python_functions(lines: list[str], rel_path: str) -> list[FunctionMetric]:
    """Detect Python function boundaries using indentation."""
    functions: list[FunctionMetric] = []
    i = 0
    while i < len(lines):
        m = _FUNC_PYTHON_RE.match(lines[i])
        if m:
            indent = len(m.group(1))
            name = m.group(2)
            start = i
            i += 1
            # Count until dedent to same or lesser indentation.
            while i < len(lines):
                line = lines[i]
                # Skip blank lines.
                if not line.strip():
                    i += 1
                    continue
                current_indent = len(line) - len(line.lstrip())
                if current_indent <= indent:
                    break
                i += 1
            functions.append(FunctionMetric(name=name, file=rel_path, line_count=i - start))
        else:
            i += 1
    return functions


def _detect_brace_functions(
    lines: list[str],
    rel_path: str,
    pattern: re.Pattern[str],
) -> list[FunctionMetric]:
    """Detect brace-delimited function boundaries."""
    functions: list[FunctionMetric] = []
    i = 0
    while i < len(lines):
        m = pattern.match(lines[i])
        if m:
            # Get function name from first non-None group (after indent group if present).
            name = ""
            for g in m.groups():
                if g is not None and not g.isspace() and g != "":
                    name = g.strip()
                    break
            if not name:
                name = "<anonymous>"

            start = i
            # Find opening brace.
            brace_depth = 0
            found_open = False
            while i < len(lines):
                for ch in lines[i]:
                    if ch == "{":
                        brace_depth += 1
                        found_open = True
                    elif ch == "}":
                        brace_depth -= 1
                if found_open and brace_depth <= 0:
                    i += 1
                    break
                i += 1
            functions.append(FunctionMetric(name=name, file=rel_path, line_count=i - start))
        else:
            i += 1
    return functions


def _has_deep_nesting(lines: list[str]) -> bool:
    """Check if any line has indentation exceeding 5 levels."""
    for line in lines:
        if not line.strip():
            continue
        # Count leading spaces.
        stripped = line.lstrip(" ")
        spaces = len(line) - len(stripped)
        if spaces >= _NESTING_SPACES:
            return True
        # Count leading tabs.
        stripped_tabs = line.lstrip("\t")
        tabs = len(line) - len(stripped_tabs)
        if tabs >= _NESTING_TABS:
            return True
    return False


# ---------------------------------------------------------------------------
# FR-3: Security Analysis
# ---------------------------------------------------------------------------


def analyze_security(
    project_dir: Path,
    source_dirs: list[str],
    language: str,
) -> SecurityResult:
    """FR-3: Scan source files for security anti-patterns."""
    patterns = SECURITY_PATTERNS.get(language)

    # EC-7: unsupported language.
    if patterns is None:
        return SecurityResult(score=15, findings=[], language=language)

    source_files = _collect_source_files(project_dir, source_dirs)

    # EC-3: no source files.
    if not source_files:
        return SecurityResult(score=15, findings=[], language=language)

    findings: list[SecurityFinding] = []

    for fpath in source_files:
        text = _read_file_text(fpath)
        if text is None:
            continue

        rel_path = str(fpath.relative_to(project_dir))
        for line_num, line in enumerate(text.splitlines(), start=1):
            for name, severity, pattern in patterns:
                for match in pattern.finditer(line):
                    findings.append(SecurityFinding(
                        file=rel_path,
                        line_number=line_num,
                        pattern=name,
                        severity=severity,
                        matched_text=match.group()[:100],
                    ))

    # Compute score.
    score = 15
    high_count = sum(1 for f in findings if f.severity == "HIGH")
    medium_count = sum(1 for f in findings if f.severity == "MEDIUM")

    score -= min(high_count * 5, 15)
    score -= min(medium_count * 2, 6)
    score = max(score, 0)

    # Log findings.
    for f in findings:
        log.warning("security finding: %s in %s:%d [%s]", f.pattern, f.file, f.line_number, f.severity)

    return SecurityResult(score=score, findings=findings, language=language)


# ---------------------------------------------------------------------------
# FR-4: Report Generation
# ---------------------------------------------------------------------------


def _write_prepass_env(path: Path, result: ReviewPrePassResult) -> None:
    """Write machine-readable .env companion file."""
    high_count = sum(1 for f in result.security.findings if f.severity == "HIGH")
    medium_count = sum(1 for f in result.security.findings if f.severity == "MEDIUM")

    content = (
        f"TEST_ANALYSIS_SCORE={result.test_analysis.score}\n"
        f"CODE_QUALITY_SCORE={result.code_quality.score}\n"
        f"SECURITY_SCORE={result.security.score}\n"
        f"DETERMINISTIC_TOTAL={result.deterministic_total}\n"
        f"SECURITY_HIGH_COUNT={high_count}\n"
        f"SECURITY_MEDIUM_COUNT={medium_count}\n"
        f"FAILURE_PATTERN={result.test_analysis.failure_pattern}\n"
    )
    path.write_text(content)


def _write_prepass_markdown(path: Path, spec_name: str, result: ReviewPrePassResult) -> None:
    """Write human-readable markdown report."""
    ta = result.test_analysis
    cq = result.code_quality
    sec = result.security

    now = datetime.now(timezone.utc).isoformat()

    gate_str = ", ".join(
        f"{k}={'pass' if v else 'fail'}" for k, v in sorted(ta.gate_outcomes.items())
    )

    lines = [
        "# Deterministic Review Pre-Pass",
        "",
        f"**Spec:** {spec_name}",
        f"**Date:** {now}",
        f"**Deterministic Total:** {result.deterministic_total}/60",
        "",
        f"## Test Results Analysis: {ta.score}/25",
    ]

    if ta.failure_pattern == "sentinel":
        lines += [
            "- Scenarios: SKIPPED — Scenario Architect declared not applicable",
            f"- Failure pattern: {ta.failure_pattern}",
            f"- Gate outcomes: {gate_str}",
            "",
            "### Sentinel Justification (Reviewer: evaluate this)",
            ta.sentinel_content.strip(),
            "",
            "The heuristic validator confirmed structure. Now evaluate semantically:",
            "- Could Format A/B/C/D have tested any FR in the spec?",
            "- Are all FRs genuinely covered by the named quality gate checks?",
            "- Flag any concern in your review — the Oracle will see it.",
            "",
        ]
    else:
        lines += [
            f"- Scenarios: {ta.scenario_passed}/{ta.scenario_total} ({ta.pass_rate}%)",
            f"- Satisfaction: {ta.satisfaction_score}%",
            f"- Failure pattern: {ta.failure_pattern}",
            f"- Gate outcomes: {gate_str}",
            "",
        ]

    lines += [
        f"## Code Quality: {cq.score}/20",
        f"- Lint: {'passed' if cq.lint_passed else 'failed'}",
    ]

    func_list = ", ".join(f"{f.name} ({f.file}:{f.line_count}L)" for f in cq.long_functions)
    lines.append(f"- Long functions (>50 lines): {len(cq.long_functions)} [{func_list}]")

    file_list = ", ".join(f"{f.path} ({f.line_count}L)" for f in cq.long_files)
    lines.append(f"- Long files (>500 lines): {len(cq.long_files)} [{file_list}]")

    nesting_list = ", ".join(cq.deep_nesting_files)
    lines.append(f"- Deep nesting (>5 levels): {len(cq.deep_nesting_files)} [{nesting_list}]")

    high_count = sum(1 for f in sec.findings if f.severity == "HIGH")
    medium_count = sum(1 for f in sec.findings if f.severity == "MEDIUM")

    lines.extend([
        "",
        f"## Security: {sec.score}/15",
        f"- HIGH findings: {high_count}",
        f"- MEDIUM findings: {medium_count}",
    ])

    if not SECURITY_PATTERNS.get(sec.language):
        lines.append(f"- No security patterns configured for language: {sec.language}")

    if sec.findings:
        lines.extend([
            "",
            "| File | Line | Pattern | Severity | Matched |",
            "|------|------|---------|----------|---------|",
        ])
        for f in sec.findings:
            escaped = f.matched_text.replace("|", "\\|")
            lines.append(f"| {f.file} | {f.line_number} | {f.pattern} | {f.severity} | `{escaped}` |")

    lines.append("")
    path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# FR-4 + FR-5: Orchestrator
# ---------------------------------------------------------------------------


def run_review_prepass(
    project_dir: Path,
    spec_name: str,
    config: PipelineConfig,
) -> ReviewPrePassResult:
    """Run all analyses and write report files."""
    gate_summary_path = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_name}-quality-gate-summary.md"
    )

    test_analysis = analyze_test_results(
        project_dir, spec_name,
        min_satisfaction=config.min_satisfaction_score,
    )

    code_quality = analyze_code_quality(
        project_dir, config.source_dirs, gate_summary_path,
    )

    security = analyze_security(
        project_dir, config.source_dirs, config.language,
    )

    deterministic_total = test_analysis.score + code_quality.score + security.score

    result = ReviewPrePassResult(
        test_analysis=test_analysis,
        code_quality=code_quality,
        security=security,
        deterministic_total=deterministic_total,
    )

    # Write reports.
    reports_dir = project_dir / ".dark-factory" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    _write_prepass_env(reports_dir / f"{spec_name}-review-prepass.env", result)
    _write_prepass_markdown(reports_dir / f"{spec_name}-review-prepass.md", spec_name, result)

    log.info(
        "review pre-pass: test_analysis=%d/25, code_quality=%d/20, security=%d/15, total=%d/60",
        test_analysis.score, code_quality.score, security.score, deterministic_total,
    )

    return result
