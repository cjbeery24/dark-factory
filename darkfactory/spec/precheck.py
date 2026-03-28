"""Deterministic spec pre-check — fast structural validation before LLM agents."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures (per spec section 5)
# ---------------------------------------------------------------------------

class SectionStatus(Enum):
    MISSING = "missing"
    EMPTY = "empty"
    PRESENT = "present"


@dataclass
class SectionResult:
    name: str
    status: SectionStatus
    line_number: int | None = None


@dataclass
class SpecStructureResult:
    sections: list[SectionResult]
    score: int
    passed: bool


@dataclass
class PlaceholderFinding:
    line_number: int
    matched_text: str
    pattern: str


@dataclass
class AmbiguityFinding:
    line_number: int
    word: str
    context: str


@dataclass
class CriteriaFinding:
    criterion_id: str
    line_number: int
    missing_keywords: list[str]


@dataclass
class PreCheckResult:
    passed: bool
    structure: SpecStructureResult
    placeholders: list[PlaceholderFinding] = field(default_factory=list)
    ambiguities: list[AmbiguityFinding] = field(default_factory=list)
    criteria_issues: list[CriteriaFinding] = field(default_factory=list)
    fr_count: int | None = None
    summary: str = ""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The 10 required checklist sections (feature template names).
_REQUIRED_SECTIONS: list[str] = [
    "Problem Statement",
    "Functional Requirements",
    "Non-Functional Requirements",
    "Constraints",
    "Interface Contract",
    "Edge Cases",
    "Error Handling",
    "Acceptance Criteria",
    "Examples",
    "Dependencies & Out of Scope",
]

# Alternate heading names that map to canonical required sections.
# Keys are lowercased heading text; values are list of canonical section names.
# A single heading can satisfy multiple checklist items (e.g., Risk Assessment
# covers both Edge Cases and Error Handling per EC-1).
_SECTION_ALIASES: dict[str, list[str]] = {
    # Feature template (canonical)
    "problem statement": ["Problem Statement"],
    "functional requirements": ["Functional Requirements"],
    "non-functional requirements": ["Non-Functional Requirements"],
    "constraints": ["Constraints"],
    "interface contract": ["Interface Contract"],
    "edge cases": ["Edge Cases"],
    "error handling": ["Error Handling"],
    "acceptance criteria": ["Acceptance Criteria"],
    "examples": ["Examples"],
    "dependencies": ["Dependencies & Out of Scope"],
    "out of scope": ["Dependencies & Out of Scope"],
    "dependencies & out of scope": ["Dependencies & Out of Scope"],
    # Refactor template (EC-1)
    "motivation": ["Problem Statement"],
    "scope": ["Functional Requirements"],
    "behavioral invariants": ["Acceptance Criteria"],
    "structural changes": ["Interface Contract"],
    "risk assessment": ["Edge Cases", "Error Handling"],
    # API template (EC-2)
    "business logic": ["Functional Requirements"],
    "request/responses": ["Interface Contract"],
    "requests/responses": ["Interface Contract"],
}

# Heading pattern: ## optionally followed by a number/letter and optional period, then text.
# The prefix group matches digits or a single letter (e.g., "1.", "A.", "1", "A")
# but not full words, to avoid consuming the heading text.
_HEADING_RE = re.compile(
    r"^##\s+(?:(?:[0-9]+|[A-Za-z])\.?\s+)?(.+?)\s*$",
)

# Placeholder patterns for FR-2.
_PLACEHOLDER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("PLACEHOLDER_COMMENT", re.compile(r"<!--\s*PLACEHOLDER", re.IGNORECASE)),
    ("PLACEHOLDER_BRACKET", re.compile(r"\[PLACEHOLDER\b", re.IGNORECASE)),
    ("TODO_BRACKET", re.compile(r"\[TODO\b", re.IGNORECASE)),
    ("TBD_BRACKET", re.compile(r"\[TBD\b", re.IGNORECASE)),
    ("ELLIPSIS_BRACKET", re.compile(r"\[\.\.\.\]")),
    # Template boilerplate from feature-spec.md and refactor-spec.md.
    ("TEMPLATE_BOILERPLATE", re.compile(
        r"\[(?:"
        r"Feature Name|name|YYYY-MM-DD"
        r"|Describe the current pain point in concrete terms"
        r"|Users, systems, or processes that experience this problem"
        r"|Quantify:.*?etc\.\]"
        r"|Describe the world after this feature is implemented"
        r"|Verb \+ Behavior"
        r"|Exact format with types"
        r"|Step-by-step logic"
        r"|State changes, events, logs, or .none."
        r"|Specific metric with target"
        r"|Specific controls"
        r"|Specific target"
        r"|Specific standard"
        r"|Logging/metrics requirements"
        r"|Specific condition"
        r"|Exact expected behavior"
        r"|Error condition"
        r"|Exact response"
        r"|Code\]"
        r"|What to log"
        r"|precondition"
        r"|action\]"
        r"|expected result"
        r"|exact input"
        r"|exact output(?:/error)?"
        r"|File/module path"
        r"|Service name"
        r"|Library name"
        r"|What it provides.*"
        r"|Thing [0-9]+ .* brief reason why"
        r"|What Is Being Refactored"
        r"|path\]"
        r"|What changes and why"
        r"|What is being extracted and where"
        r"|What is being simplified"
        r"|Old name .* New name"
        r"|Why this file is no longer needed"
        r"|Why this new file is needed"
        r"|Why this is explicitly excluded.*"
        r"|e\.g\.,\s"
        r")\]",
        re.IGNORECASE,
    )),
]

# Link pattern — `[text](url)` — used to exclude from placeholder scanning.
_LINK_RE = re.compile(r"\[[^\]]*\]\(")

# Ambiguous terms for FR-3.
_AMBIGUOUS_WORDS: list[str] = [
    "fast", "quick", "responsive", "secure", "scalable",
    "user-friendly", "intuitive",
    "should", "could", "might",
    r"etc\.", "and so on", "similar",
    "appropriate", "reasonable", "suitable",
]
_AMBIGUITY_RE = re.compile(
    r"\b(?:" + "|".join(_AMBIGUOUS_WORDS) + r")(?:\b|(?<=\.))",
    re.IGNORECASE,
)

# Acceptance criteria ID pattern.
_AC_ID_RE = re.compile(r"AC-(\d+)", re.IGNORECASE)

# GIVEN/WHEN/THEN keywords.
_GWT_KEYWORDS = ["GIVEN", "WHEN", "THEN"]


# ---------------------------------------------------------------------------
# Code fence tracking
# ---------------------------------------------------------------------------

def _build_code_fence_set(lines: list[str]) -> set[int]:
    """Return set of 0-based line indices that are inside code fences."""
    inside: set[int] = set()
    fence_open = False
    fence_backticks = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not fence_open:
            if stripped.startswith("```"):
                fence_backticks = len(stripped) - len(stripped.lstrip("`"))
                fence_open = True
                inside.add(i)
            elif stripped.startswith("~~~"):
                fence_backticks = len(stripped) - len(stripped.lstrip("~"))
                fence_open = True
                inside.add(i)
        else:
            inside.add(i)
            # Closing fence must have at least as many backticks/tildes.
            if stripped.startswith("`" * fence_backticks) and stripped.rstrip("`") == "" and len(stripped) >= fence_backticks:
                fence_open = False
            elif stripped.startswith("~" * fence_backticks) and stripped.rstrip("~") == "" and len(stripped) >= fence_backticks:
                fence_open = False

    return inside


# ---------------------------------------------------------------------------
# Section detection helpers
# ---------------------------------------------------------------------------

def _find_out_of_scope_range(lines: list[str]) -> tuple[int, int] | None:
    """Return (start, end) 0-based line range for the Out of Scope section."""
    start = None
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            heading_text = m.group(1).strip().lower()
            if heading_text == "out of scope":
                start = i
            elif start is not None:
                return (start, i)
    if start is not None:
        return (start, len(lines))
    return None


# ---------------------------------------------------------------------------
# FR-1: Parse spec into sections and score structural completeness
# ---------------------------------------------------------------------------

def check_structure(text: str) -> SpecStructureResult:
    """FR-1: Parse sections and score structural completeness."""
    lines = text.split("\n")
    code_fences = _build_code_fence_set(lines)

    # Track which required sections are found and where.
    found: dict[str, SectionResult] = {}

    # First pass: find all ## headings and map them.
    headings: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if i in code_fences:
            continue
        m = _HEADING_RE.match(line)
        if m:
            heading_text = m.group(1).strip()
            headings.append((i, heading_text))

    # Map headings to canonical names.
    for idx, (line_idx, heading_text) in enumerate(headings):
        lower = heading_text.lower()
        canonicals = _SECTION_ALIASES.get(lower)
        if canonicals is None:
            continue

        # Determine content range: from heading to next heading (or EOF).
        if idx + 1 < len(headings):
            end = headings[idx + 1][0]
        else:
            end = len(lines)

        # Check if section has substantive content.
        has_content = False
        for j in range(line_idx + 1, end):
            if j in code_fences:
                # Code fence content counts as content.
                has_content = True
                break
            content_line = lines[j].strip()
            if not content_line:
                continue
            if content_line.startswith("---"):
                continue
            if content_line.startswith("<!--") and content_line.endswith("-->"):
                inner = content_line[4:-3].strip()
                if not inner or inner.upper().startswith("PLACEHOLDER"):
                    continue
            if content_line.startswith("<!--"):
                continue
            if content_line.endswith("-->"):
                continue
            has_content = True
            break

        status = SectionStatus.PRESENT if has_content else SectionStatus.EMPTY

        # A single heading can satisfy multiple checklist items.
        for canonical in canonicals:
            if canonical not in found or (found[canonical].status == SectionStatus.EMPTY and status == SectionStatus.PRESENT):
                found[canonical] = SectionResult(
                    name=canonical,
                    status=status,
                    line_number=line_idx + 1,  # 1-based
                )

    # Build results for all 10 required sections.
    sections: list[SectionResult] = []
    for name in _REQUIRED_SECTIONS:
        if name in found:
            sections.append(found[name])
        else:
            sections.append(SectionResult(name=name, status=SectionStatus.MISSING))

    score = sum(10 for s in sections if s.status == SectionStatus.PRESENT)
    return SpecStructureResult(sections=sections, score=score, passed=score == 100)


# ---------------------------------------------------------------------------
# FR-2: Detect unresolved placeholder markers
# ---------------------------------------------------------------------------

def find_placeholders(text: str) -> list[PlaceholderFinding]:
    """FR-2: Detect unresolved placeholder markers."""
    lines = text.split("\n")
    code_fences = _build_code_fence_set(lines)
    findings: list[PlaceholderFinding] = []

    for i, line in enumerate(lines):
        if i in code_fences:
            continue

        for pattern_name, pattern in _PLACEHOLDER_PATTERNS:
            for m in pattern.finditer(line):
                matched = m.group()

                # Exclude markdown links: [text](url).
                # Check if the closing bracket is followed by '('.
                # Find the closing bracket position for this match.
                bracket_end = line.find("]", m.start())
                if bracket_end != -1 and bracket_end + 1 < len(line) and line[bracket_end + 1] == "(":
                    continue

                # Also do a simpler check: if the match itself looks like [text](
                match_end = m.end()
                if match_end < len(line) and line[match_end] == "(":
                    continue

                findings.append(PlaceholderFinding(
                    line_number=i + 1,  # 1-based
                    matched_text=matched[:80],
                    pattern=pattern_name,
                ))

    return findings


# ---------------------------------------------------------------------------
# FR-3: Detect ambiguous language without quantification
# ---------------------------------------------------------------------------

def find_ambiguities(text: str) -> list[AmbiguityFinding]:
    """FR-3: Detect ambiguous language without quantification."""
    lines = text.split("\n")
    code_fences = _build_code_fence_set(lines)
    out_of_scope = _find_out_of_scope_range(lines)
    findings: list[AmbiguityFinding] = []

    for i, line in enumerate(lines):
        if i in code_fences:
            continue

        for m in _AMBIGUITY_RE.finditer(line):
            word = m.group()

            # Exclude "should" in "Out of Scope" section (per FR-3 rule).
            if word.lower() == "should" and out_of_scope:
                start, end = out_of_scope
                if start <= i < end:
                    continue

            findings.append(AmbiguityFinding(
                line_number=i + 1,
                word=word,
                context=line.strip()[:120],
            ))

    return findings


# ---------------------------------------------------------------------------
# FR-4: Validate acceptance criteria format
# ---------------------------------------------------------------------------

def check_acceptance_criteria(text: str) -> list[CriteriaFinding]:
    """FR-4: Validate acceptance criteria GIVEN/WHEN/THEN format."""
    lines = text.split("\n")
    code_fences = _build_code_fence_set(lines)

    # Find the Acceptance Criteria section.
    ac_start = None
    ac_end = None
    headings: list[tuple[int, str]] = []

    for i, line in enumerate(lines):
        if i in code_fences:
            continue
        m = _HEADING_RE.match(line)
        if m:
            headings.append((i, m.group(1).strip()))

    for idx, (line_idx, heading_text) in enumerate(headings):
        lower = heading_text.lower()
        canonicals = _SECTION_ALIASES.get(lower, [])
        if "Acceptance Criteria" in canonicals or lower == "acceptance criteria":
            ac_start = line_idx
            if idx + 1 < len(headings):
                ac_end = headings[idx + 1][0]
            else:
                ac_end = len(lines)
            break

    if ac_start is None or ac_end is None:
        return []

    findings: list[CriteriaFinding] = []

    for i in range(ac_start, ac_end):
        if i in code_fences:
            continue
        line = lines[i]
        ac_match = _AC_ID_RE.search(line)
        if not ac_match:
            continue

        criterion_id = ac_match.group()
        line_upper = line.upper()

        missing: list[str] = []
        for kw in _GWT_KEYWORDS:
            if kw not in line_upper:
                missing.append(kw)

        if missing:
            findings.append(CriteriaFinding(
                criterion_id=criterion_id.upper(),
                line_number=i + 1,
                missing_keywords=missing,
            ))

    return findings


# ---------------------------------------------------------------------------
# FR count check: count functional requirements to flag overly broad specs
# ---------------------------------------------------------------------------

# Matches FR headings: ### FR-1: Name, ### FR-2: Name, etc.
_FR_HEADING_RE = re.compile(r"^#{2,3}\s+FR-\d+", re.IGNORECASE)


def check_fr_count(text: str) -> tuple[int, list[int]]:
    """Count FR-N headings in the spec.

    Returns (count, line_numbers) where line_numbers are 1-based.
    Scans the full document but skips code fences.
    """
    lines = text.split("\n")
    code_fences = _build_code_fence_set(lines)
    count = 0
    line_numbers: list[int] = []

    for i, line in enumerate(lines):
        if i in code_fences:
            continue
        if _FR_HEADING_RE.match(line):
            count += 1
            line_numbers.append(i + 1)  # 1-based

    return count, line_numbers


# ---------------------------------------------------------------------------
# FR-5: Run all checks and produce aggregate pre-check result
# ---------------------------------------------------------------------------

_MAX_SIZE = 1_048_576  # 1MB


def run_precheck(spec_path: Path) -> PreCheckResult:
    """FR-5: Run all checks and return aggregate result."""
    if spec_path.stat().st_size > _MAX_SIZE:
        log.warning("spec file exceeds 1MB: %s", spec_path)
        empty_structure = SpecStructureResult(
            sections=[SectionResult(name=n, status=SectionStatus.MISSING) for n in _REQUIRED_SECTIONS],
            score=0, passed=False,
        )
        return PreCheckResult(
            passed=False, structure=empty_structure,
            summary="spec file exceeds 1MB size limit",
        )

    text = spec_path.read_text(encoding="utf-8")

    if not text.strip():
        empty_structure = SpecStructureResult(
            sections=[SectionResult(name=n, status=SectionStatus.MISSING) for n in _REQUIRED_SECTIONS],
            score=0, passed=False,
        )
        return PreCheckResult(
            passed=False, structure=empty_structure,
            summary="spec file is empty",
        )

    structure = check_structure(text)
    log.debug("structure check: score=%d, %d/%d sections present",
              structure.score,
              sum(1 for s in structure.sections if s.status == SectionStatus.PRESENT),
              len(structure.sections))

    placeholders = find_placeholders(text)
    log.debug("placeholder check: %d findings", len(placeholders))

    ambiguities = find_ambiguities(text)
    log.debug("ambiguity check: %d findings", len(ambiguities))

    criteria_issues = check_acceptance_criteria(text)
    log.debug("criteria check: %d findings", len(criteria_issues))

    fr_count, fr_lines = check_fr_count(text)
    log.debug("FR count: %d", fr_count)

    # Determine pass/fail: FAIL if structure incomplete, placeholders found,
    # or criteria malformed. Ambiguities and FR count are warnings only.
    passed = structure.passed and not placeholders and not criteria_issues

    # Build summary.
    summary_parts: list[str] = []
    issues = 0

    if not structure.passed:
        missing = [s for s in structure.sections if s.status == SectionStatus.MISSING]
        empty = [s for s in structure.sections if s.status == SectionStatus.EMPTY]
        parts = []
        if missing:
            parts.append(f"{len(missing)} missing")
        if empty:
            parts.append(f"{len(empty)} empty")
        summary_parts.append(f"Sections: {', '.join(parts)} ({structure.score}/100)")
        for s in missing:
            summary_parts.append(f"  - MISSING: {s.name}")
        for s in empty:
            summary_parts.append(f"  - EMPTY: {s.name} (line {s.line_number})")
        issues += len(missing) + len(empty)

    if placeholders:
        summary_parts.append(f"Placeholders: {len(placeholders)} found")
        for p in placeholders[:10]:
            summary_parts.append(f"  - line {p.line_number}: {p.matched_text} [{p.pattern}]")
        if len(placeholders) > 10:
            summary_parts.append(f"  ... and {len(placeholders) - 10} more")
        issues += len(placeholders)

    if criteria_issues:
        summary_parts.append(f"Acceptance criteria: {len(criteria_issues)} malformed")
        for c in criteria_issues:
            summary_parts.append(f"  - {c.criterion_id} (line {c.line_number}): missing {', '.join(c.missing_keywords)}")
        issues += len(criteria_issues)

    if fr_count > 5:
        summary_parts.append(f"FR count: {fr_count} (max recommended: 5) — consider decomposing into sub-specs")

    if ambiguities:
        summary_parts.append(f"Ambiguity warnings: {len(ambiguities)}")
        for a in ambiguities[:5]:
            summary_parts.append(f"  - line {a.line_number}: \"{a.word}\" in: {a.context}")
        if len(ambiguities) > 5:
            summary_parts.append(f"  ... and {len(ambiguities) - 5} more")

    if passed:
        verdict = f"Pre-check passed. {issues} issues, {len(ambiguities)} warnings."
    else:
        verdict = f"Pre-check FAILED. {issues} issues, {len(ambiguities)} ambiguity warnings."

    summary = verdict + "\n" + "\n".join(summary_parts) if summary_parts else verdict

    log.info("pre-check %s for %s", "PASSED" if passed else "FAILED", spec_path.name)
    if ambiguities:
        for a in ambiguities:
            log.warning("ambiguous term \"%s\" on line %d: %s", a.word, a.line_number, a.context)

    return PreCheckResult(
        passed=passed,
        structure=structure,
        placeholders=placeholders,
        ambiguities=ambiguities,
        criteria_issues=criteria_issues,
        fr_count=fr_count,
        summary=summary,
    )
