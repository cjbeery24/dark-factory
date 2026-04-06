"""Deterministic spec pre-check — fast structural validation before LLM agents."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from darkfactory.spec.anchors import AnchorManifest
from darkfactory.spec.shape_tokens import SHAPE_TOKENS

__all__ = ["AnchorManifest"]  # re-exported for backwards compatibility

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
class ScenarioIntentResult:
    present: bool
    fr_intent_counts: dict[str, int] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


@dataclass
class ContractCheckResult:
    present: bool
    count: int = 0
    invariant_count: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class ShapeClaimFinding:
    section: str
    line_number: int
    matched_token: str
    context: str


@dataclass
class PreCheckResult:
    passed: bool
    structure: SpecStructureResult
    placeholders: list[PlaceholderFinding] = field(default_factory=list)
    ambiguities: list[AmbiguityFinding] = field(default_factory=list)
    criteria_issues: list[CriteriaFinding] = field(default_factory=list)
    fr_count: int | None = None
    scenario_intents: ScenarioIntentResult | None = None
    contracts: ContractCheckResult | None = None
    shape_claims: list[ShapeClaimFinding] = field(default_factory=list)
    anchor_manifest: AnchorManifest | None = None
    anchor_intent_issues: list[str] = field(default_factory=list)
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
    "scenario intent": ["Scenario Intent"],
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
# Scenario Intent check: verify per-FR intent declarations exist
# ---------------------------------------------------------------------------

# Matches table rows like "| 1 | intent text | layer |" — at least 3 columns.
_INTENT_ROW_RE = re.compile(r"^\|\s*\d+\s*\|.+\|.+\|")

# Matches any ### sub-heading inside the Scenario Intent section.
# Templates use varied sub-heading styles (FR-N, HTTP methods, free-text groups).
_INTENT_GROUP_RE = re.compile(r"^###\s+\S")


def check_scenario_intents(text: str) -> ScenarioIntentResult:
    """Check the Scenario Intent section for per-FR intent declarations.

    Returns a ScenarioIntentResult with presence flag, per-group intent counts,
    and issues (e.g., groups with fewer than 5 intents).
    """
    lines = text.split("\n")
    code_fences = _build_code_fence_set(lines)

    # Find the Scenario Intent section.
    si_start: int | None = None
    si_end: int | None = None
    headings: list[tuple[int, str]] = []

    for i, line in enumerate(lines):
        if i in code_fences:
            continue
        m = _HEADING_RE.match(line)
        if m:
            headings.append((i, m.group(1).strip()))

    for idx, (line_idx, heading_text) in enumerate(headings):
        lower = heading_text.lower()
        if lower == "scenario intent":
            si_start = line_idx
            # Find next ## heading to bound the section.
            for j in range(idx + 1, len(headings)):
                next_line = headings[j][0]
                # Only stop at ## level, not ### sub-sections.
                if lines[next_line].startswith("## ") and not lines[next_line].startswith("### "):
                    si_end = next_line
                    break
            if si_end is None:
                si_end = len(lines)
            break

    if si_start is None:
        return ScenarioIntentResult(present=False, issues=["Scenario Intent section not found"])

    # Scan sub-headings (### FR-1, ### POST /path, ### Cross-Cutting) within the section.
    groups: list[tuple[str, int, int]] = []  # (label, start, end)
    sub_heads: list[tuple[int, str]] = []

    for i in range(si_start + 1, si_end):
        if i in code_fences:
            continue
        if _INTENT_GROUP_RE.match(lines[i]):
            # Use the full heading text after "### ".
            label = lines[i].lstrip("#").strip()
            sub_heads.append((i, label))

    for idx, (line_idx, label) in enumerate(sub_heads):
        end = sub_heads[idx + 1][0] if idx + 1 < len(sub_heads) else si_end
        groups.append((label, line_idx, end))

    # If no sub-headings, treat the whole section as one group.
    if not groups:
        groups = [("(ungrouped)", si_start, si_end)]

    fr_intent_counts: dict[str, int] = {}
    issues: list[str] = []

    for label, start, end in groups:
        count = 0
        for i in range(start, end):
            if i in code_fences:
                continue
            if _INTENT_ROW_RE.match(lines[i].strip()):
                count += 1
        fr_intent_counts[label] = count

        is_cross_cutting = "cross-cutting" in label.lower()
        if count == 0 and not is_cross_cutting:
            issues.append(f"{label}: no intents declared")
        elif count < 5 and not is_cross_cutting:
            issues.append(f"{label}: {count} intents (minimum 5)")

    return ScenarioIntentResult(
        present=True,
        fr_intent_counts=fr_intent_counts,
        issues=issues,
    )


# ---------------------------------------------------------------------------
# Anchor manifest: enumerate every FR/AC/EC ID declared in the spec
# ---------------------------------------------------------------------------

# Matches "FR-1", "AC-12", "EC-3" — case-insensitive, word-boundary anchored.
_ANCHOR_ID_RE = re.compile(r"\b(FR|AC|EC)-(\d+)\b", re.IGNORECASE)


def extract_anchor_manifest(text: str) -> AnchorManifest:
    """Enumerate every FR/AC/EC anchor declared in the spec.

    Walks the document and collects:
    - FR-N IDs from headings AND the Functional Requirements section body
      (handles both "### FR-1: Login" and inline "FR-1" mentions in tables).
    - AC-N IDs from any line in the Acceptance Criteria section.
    - EC-N IDs from any line in the Edge Cases / Error Handling sections.
    - declared_intents: per-anchor row count in the Scenario Intent section.

    Pure / file-IO-free. Caller (orchestrator) is responsible for persisting
    the manifest as JSON.
    """
    lines = text.split("\n")
    code_fences = _build_code_fence_set(lines)

    # Collect all top-level headings.
    headings: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if i in code_fences:
            continue
        m = _HEADING_RE.match(line)
        if m:
            headings.append((i, m.group(1).strip()))

    def _section_range(canonical: str) -> tuple[int, int] | None:
        for idx, (line_idx, heading_text) in enumerate(headings):
            lower = heading_text.lower()
            canonicals = _SECTION_ALIASES.get(lower, [])
            if canonical in canonicals:
                end = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
                return (line_idx, end)
        return None

    def _collect(section: str, prefix: str) -> list[str]:
        rng = _section_range(section)
        if rng is None:
            return []
        seen: dict[str, None] = {}
        for j in range(rng[0], rng[1]):
            if j in code_fences:
                continue
            for m in _ANCHOR_ID_RE.finditer(lines[j]):
                kind = m.group(1).upper()
                if kind != prefix:
                    continue
                anchor = f"{kind}-{int(m.group(2))}"
                seen.setdefault(anchor, None)
        return list(seen.keys())

    fr_ids = _collect("Functional Requirements", "FR")
    ac_ids = _collect("Acceptance Criteria", "AC")

    # EC anchors may appear in Edge Cases or Error Handling sections.
    ec_seen: dict[str, None] = {}
    for sec in ("Edge Cases", "Error Handling"):
        rng = _section_range(sec)
        if rng is None:
            continue
        for j in range(rng[0], rng[1]):
            if j in code_fences:
                continue
            for m in _ANCHOR_ID_RE.finditer(lines[j]):
                if m.group(1).upper() != "EC":
                    continue
                anchor = f"EC-{int(m.group(2))}"
                ec_seen.setdefault(anchor, None)
    ec_ids = list(ec_seen.keys())

    # declared_intents: count rows in the Scenario Intent section per anchor.
    declared_intents: dict[str, int] = {}
    si_rng = _section_range("Scenario Intent")
    if si_rng is not None:
        # Scan sub-headings to find the active anchor context.
        current_anchor: str | None = None
        for j in range(si_rng[0], si_rng[1]):
            if j in code_fences:
                continue
            line = lines[j]
            if _INTENT_GROUP_RE.match(line):
                # Sub-heading like "### FR-1: ..." — capture anchor IDs in label.
                m = _ANCHOR_ID_RE.search(line)
                current_anchor = (
                    f"{m.group(1).upper()}-{int(m.group(2))}" if m else None
                )
                continue
            if _INTENT_ROW_RE.match(line.strip()):
                # Credit the current sub-heading anchor (context) AND any
                # inline anchor IDs found on the row.
                row_anchors: set[str] = set()
                if current_anchor:
                    row_anchors.add(current_anchor)
                for m in _ANCHOR_ID_RE.finditer(line):
                    row_anchors.add(
                        f"{m.group(1).upper()}-{int(m.group(2))}"
                    )
                for a in row_anchors:
                    declared_intents[a] = declared_intents.get(a, 0) + 1

    return AnchorManifest(
        fr_ids=fr_ids,
        ac_ids=ac_ids,
        ec_ids=ec_ids,
        declared_intents=declared_intents,
    )


def check_anchor_intent_coverage(
    manifest: AnchorManifest,
) -> list[str]:
    """Precheck rule: every FR-N and AC-N must appear in Scenario Intent.

    EC anchors are NOT enforced at precheck — they're caught by the
    post-scenario-gen coverage gate. Returns a list of issue strings;
    empty list means pass.
    """
    issues: list[str] = []
    intent_set = set(manifest.declared_intents.keys())
    for fr in manifest.fr_ids:
        if fr not in intent_set:
            issues.append(f"{fr} declared but missing from Scenario Intent section")
    for ac in manifest.ac_ids:
        if ac not in intent_set:
            issues.append(f"{ac} declared but missing from Scenario Intent section")
    return issues


# ---------------------------------------------------------------------------
# FR count check: count functional requirements to flag overly broad specs
# ---------------------------------------------------------------------------

# Matches FR headings: ### FR-1: Name, ### FR-2: Name, etc.
_FR_HEADING_RE = re.compile(r"^#{2,3}\s+FR-\d+", re.IGNORECASE)


def check_fr_count(text: str) -> tuple[int, list[int]]:
    """Count FR-N headings under the Functional Requirements section.

    Returns (count, line_numbers) where line_numbers are 1-based.
    Only counts within the Functional Requirements section to avoid
    double-counting FR headings reused in Scenario Intent tables.
    """
    lines = text.split("\n")
    code_fences = _build_code_fence_set(lines)

    # Find the Functional Requirements section boundaries.
    fr_start: int | None = None
    fr_end: int | None = None

    for i, line in enumerate(lines):
        if i in code_fences:
            continue
        m = _HEADING_RE.match(line)
        if m:
            heading = m.group(1).strip().lower()
            canonicals = _SECTION_ALIASES.get(heading, [])
            if "Functional Requirements" in canonicals and fr_start is None:
                fr_start = i
            elif fr_start is not None and line.startswith("## ") and not line.startswith("### "):
                fr_end = i
                break

    if fr_start is None:
        return 0, []
    if fr_end is None:
        fr_end = len(lines)

    count = 0
    line_numbers: list[int] = []

    for i in range(fr_start, fr_end):
        if i in code_fences:
            continue
        if _FR_HEADING_RE.match(lines[i]):
            count += 1
            line_numbers.append(i + 1)  # 1-based

    return count, line_numbers


# ---------------------------------------------------------------------------
# Contract validation (Phase 3)
# ---------------------------------------------------------------------------


def check_contracts(text: str) -> ContractCheckResult:
    """Validate Tier 1 contract YAML blocks in the Acceptance Criteria section.

    Delegates parsing to contracts.py and surfaces any errors. Contract
    validation is a warning, not a hard failure — specs without contracts
    are valid (they just won't have Tier 1 deterministic tests).
    """
    from darkfactory.spec.contracts import parse_contracts

    parse_result = parse_contracts(text)

    if not parse_result.contracts and not parse_result.errors:
        return ContractCheckResult(present=False)

    invariant_count = sum(1 for c in parse_result.contracts if c.invariant)

    return ContractCheckResult(
        present=bool(parse_result.contracts),
        count=len(parse_result.contracts),
        invariant_count=invariant_count,
        errors=parse_result.errors,
    )


# ---------------------------------------------------------------------------
# Shape-claim check: serialized cross-boundary structures must have a literal
# example. Without this, two LLM agents reading the same spec each invent a
# different shape and the resulting scenarios fight the implementation.
# ---------------------------------------------------------------------------

# Sections that must back any shape-token mention with a literal example
# (a fenced code block in the same section, or a Wire Contracts reference).
_SHAPE_BEARING_SECTION_HEADINGS: set[str] = {
    "functional requirements",
    "acceptance criteria",
    "interface contract",
    "error handling",
    "wire contracts",
}

# Word-boundary regex over SHAPE_TOKENS. Some tokens are multi-word phrases;
# re.escape handles spaces fine, and \b at both ends prevents jsonp/payloads
# false positives.
_SHAPE_TOKEN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in SHAPE_TOKENS) + r")\b",
    re.IGNORECASE,
)

# Matches an explicit Wire Contracts reference like
# "see Wire Contracts: Error Envelope".
_WIRE_REF_RE = re.compile(r"wire contracts?\s*:\s*([^\n]+)", re.IGNORECASE)

# Matches a sub-heading inside the Wire Contracts section: "### <name>".
_WIRE_NAME_HEADING_RE = re.compile(r"^#{2,4}\s+(.+?)\s*$")


def _check_shape_claims(text: str) -> list[ShapeClaimFinding]:
    """Find shape tokens in shape-bearing sections that lack a literal example.

    Algorithm:
    1. Walk every ## heading. For each whose text matches a shape-bearing
       section, slice the section body up to the next ## heading.
    2. Determine whether the section contains at least one fenced code block
       (section-membership rule, not a line window: a long FR section with a
       fenced block at the top correctly satisfies tokens 50 lines down).
    3. Build the set of named contracts defined in the Wire Contracts section
       (sub-headings above fenced blocks).
    4. For each non-fenced line in a shape-bearing section, search for any
       SHAPE_TOKENS token. A match is satisfied if:
         - the section contains at least one fenced code block, OR
         - the line contains "wire contracts: <name>" and <name> is defined.
    5. Otherwise emit a ShapeClaimFinding.
    """
    lines = text.split("\n")
    code_fences = _build_code_fence_set(lines)

    # Find every ## heading.
    headings: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if i in code_fences:
            continue
        m = _HEADING_RE.match(line)
        if m:
            headings.append((i, m.group(1).strip()))

    # First pass: collect Wire Contracts named-contract definitions.
    wire_contract_names: set[str] = set()
    for idx, (line_idx, heading_text) in enumerate(headings):
        if heading_text.strip().lower() != "wire contracts":
            continue
        end = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
        # A "named contract" is a sub-heading (### / ####) immediately
        # followed (within 5 non-blank lines) by a fenced code block.
        for j in range(line_idx + 1, end):
            if j in code_fences:
                continue
            sub = _WIRE_NAME_HEADING_RE.match(lines[j])
            if not sub:
                continue
            # Skip the top-level "## Wire Contracts" line itself.
            if not lines[j].lstrip().startswith("###"):
                continue
            # Look ahead for a fenced block.
            for k in range(j + 1, min(j + 20, end)):
                if k in code_fences:
                    wire_contract_names.add(sub.group(1).strip().lower())
                    break

    # Second pass: scan each shape-bearing section.
    findings: list[ShapeClaimFinding] = []
    for idx, (line_idx, heading_text) in enumerate(headings):
        section_key = heading_text.strip().lower()
        # Resolve template aliases (e.g., "Request/Responses" → "Interface
        # Contract") so API/refactor templates aren't silently exempt.
        canonicals = _SECTION_ALIASES.get(section_key, [heading_text.strip()])
        if not any(c.lower() in _SHAPE_BEARING_SECTION_HEADINGS for c in canonicals):
            if section_key not in _SHAPE_BEARING_SECTION_HEADINGS:
                continue
        end = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)

        # Section-membership rule: does this section contain ANY fenced block?
        section_has_fence = any(
            k in code_fences for k in range(line_idx + 1, end)
        )

        for j in range(line_idx + 1, end):
            if j in code_fences:
                continue
            line = lines[j]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for m in _SHAPE_TOKEN_RE.finditer(line):
                token = m.group(0)
                # Satisfied: section has a fenced example anywhere in it.
                if section_has_fence:
                    continue
                # Satisfied: line references a defined wire contract by name.
                ref = _WIRE_REF_RE.search(line)
                if ref:
                    name = ref.group(1).strip().lower()
                    # Allow trailing punctuation in the reference.
                    name = name.rstrip(".,;:)")
                    if name in wire_contract_names:
                        continue
                findings.append(ShapeClaimFinding(
                    section=heading_text.strip(),
                    line_number=j + 1,
                    matched_token=token,
                    context=stripped[:120],
                ))
                # One finding per line is enough — multiple tokens on the
                # same line all share the same fix.
                break

    return findings


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

    scenario_intents = check_scenario_intents(text)
    log.debug("scenario intents: present=%s, groups=%d, issues=%d",
              scenario_intents.present,
              len(scenario_intents.fr_intent_counts),
              len(scenario_intents.issues))

    contracts = check_contracts(text)
    log.debug("contracts: present=%s, count=%d, errors=%d",
              contracts.present, contracts.count, len(contracts.errors))

    shape_claims = _check_shape_claims(text)
    log.debug("shape claims: %d unresolved", len(shape_claims))

    anchor_manifest = extract_anchor_manifest(text)
    anchor_intent_issues = check_anchor_intent_coverage(anchor_manifest)
    log.debug(
        "anchor manifest: FR=%d AC=%d EC=%d, intent gaps=%d",
        len(anchor_manifest.fr_ids), len(anchor_manifest.ac_ids),
        len(anchor_manifest.ec_ids), len(anchor_intent_issues),
    )

    # Determine pass/fail: FAIL if structure incomplete, placeholders found,
    # criteria malformed, or unresolved shape claims. Ambiguities, FR count,
    # scenario intents, and contract issues are warnings only.
    passed = (
        structure.passed
        and not placeholders
        and not criteria_issues
        and not shape_claims
        and not anchor_intent_issues
    )

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

    if scenario_intents.issues:
        summary_parts.append(f"Scenario Intent warnings: {len(scenario_intents.issues)}")
        for issue in scenario_intents.issues:
            summary_parts.append(f"  - {issue}")
    elif not scenario_intents.present:
        summary_parts.append("Scenario Intent section: not found")

    if contracts.errors:
        summary_parts.append(f"Contract errors: {len(contracts.errors)}")
        for err in contracts.errors:
            summary_parts.append(f"  - {err}")
    elif contracts.present:
        active = contracts.count - contracts.invariant_count
        summary_parts.append(
            f"Contracts: {contracts.count} found ({active} active, {contracts.invariant_count} invariant)"
        )

    if shape_claims:
        summary_parts.append(
            f"Unresolved shape claims: {len(shape_claims)} "
            f"(mention serialized structure without literal example)"
        )
        for sc in shape_claims[:10]:
            summary_parts.append(
                f"  - line {sc.line_number}: shape token \"{sc.matched_token}\" "
                f"used in {sc.section} without a literal example."
            )
            summary_parts.append(
                f"      Fix: add a fenced code block in the same section showing "
                f"the literal shape, or add `Wire Contracts: <name>` and define "
                f"<name> in the Wire Contracts section."
            )
            summary_parts.append(f"      context: {sc.context}")
        if len(shape_claims) > 10:
            summary_parts.append(f"  ... and {len(shape_claims) - 10} more")
        issues += len(shape_claims)

    if anchor_intent_issues:
        summary_parts.append(
            f"Scenario Intent anchor coverage: {len(anchor_intent_issues)} missing"
        )
        for issue in anchor_intent_issues[:10]:
            summary_parts.append(f"  - {issue}")
        if len(anchor_intent_issues) > 10:
            summary_parts.append(f"  ... and {len(anchor_intent_issues) - 10} more")
        issues += len(anchor_intent_issues)

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
        scenario_intents=scenario_intents,
        contracts=contracts,
        shape_claims=shape_claims,
        anchor_manifest=anchor_manifest,
        anchor_intent_issues=anchor_intent_issues,
        summary=summary,
    )
