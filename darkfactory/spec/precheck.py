"""Deterministic spec pre-check — fast structural validation before LLM agents."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from darkfactory.spec.anchors import AnchorManifest
from darkfactory.spec.precision import PrecisionFinding, check_precision_gaps
from darkfactory.spec.shape_tokens import SHAPE_TOKENS
from darkfactory.spec.spec_model import (
    FencedBlock,
    Section,
    SpecModel,
    SpecModelParseError,
    Table,
    parse_spec_model,
)
from darkfactory.adapters.typescript.library_symbols import (
    LibrarySymbolFinding,
    check_library_symbols,
)
from darkfactory.adapters.typescript.stub_compile import (
    StubCompileFinding,
    check_stub_roundtrip,
)
from darkfactory.adapters.postgres.sql_lint import SqlLintFinding, lint_sql_fences
from darkfactory.adapters.postgres.sql_tag_syntax import (
    SqlTagSyntaxFinding,
    check_sql_tag_syntax,
)
from darkfactory.spec.blocking_ac_family import (
    BlockingAcFamilyFinding,
    check_blocking_ac_family,
)

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
    # Structural cause for the model-based check; defaults preserve
    # backwards compatibility with any existing consumers.
    kind: str = "MALFORMED_GWT"  # one of MALFORMED_GWT|TABLE_BROKEN_AT_AC|BOLD_PSEUDO_HEADING_COLLISION
    message: str = ""


@dataclass
class ServiceAdvisoryFinding:
    """Phase 4.4: advisory finding when spec references a service not in the contract."""
    service_keyword: str  # e.g. "PostgreSQL"
    line_number: int
    context: str


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
    # Model-derived fields (defaults preserve backwards compatibility).
    section_path: tuple[str, ...] = ()
    available_wire_contracts: list[str] = field(default_factory=list)
    would_break_table: bool = False


@dataclass
class TestabilityFinding:
    """Advisory: AC text suggests a side effect untestable in-process."""
    criterion_id: str
    line_number: int
    matched_pattern: str  # e.g. "process.exit"
    has_vehicle_annotation: bool
    message: str


@dataclass
class BlockingAcFinding:
    """Phase D (2026-04-20): Non-Negotiable Acceptance Criteria section issue.

    Blocking (hard-fail) finding. The spec author must declare which ACs
    are non-negotiable so SHIP_WITH_GAPS can veto on failing invariants.
    ``kind`` distinguishes the three failure modes for test attribution.
    """
    kind: str  # "MISSING_SECTION" | "EMPTY_WITHOUT_JUSTIFICATION" | "UNRESOLVED_ID"
    line_number: int  # 0 when the section itself is missing
    message: str
    id: str = ""  # populated for UNRESOLVED_ID


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
    service_advisories: list[ServiceAdvisoryFinding] = field(default_factory=list)
    testability: list[TestabilityFinding] = field(default_factory=list)
    precision: list[PrecisionFinding] = field(default_factory=list)
    # A2: deterministic SQL syntax findings (advisory only; validator may
    # disagree, in which case A2 is ground truth).
    sql_lint: list[SqlLintFinding] = field(default_factory=list)
    # Malformed ``sql`` tagged-template syntax (``sql'...'`` / ``sql"..."``
    # instead of backticks). FATAL — an LLM validator has demonstrated
    # variance on this class of defect (same spec bytes: 4 PASS + 1 FAIL
    # across 5 runs on 2026-04-21). Mechanical catch, deterministic veto.
    sql_tag_syntax: list[SqlTagSyntaxFinding] = field(default_factory=list)
    # A3 Layer 1: imports in spec §5 that aren't declared in any workspace
    # package.json (advisory only). Layer 2 (symbol existence against the
    # installed package's exports) currently lives in the same field —
    # distinguished by ``layer="symbol"``.
    library_symbols: list[LibrarySymbolFinding] = field(default_factory=list)
    # A6: spec §5 Interface Contract stubs that fail to typecheck against
    # the project's installed libraries via ``tsc --noEmit``. No-op when
    # tsc/tsconfig/node_modules aren't present.
    stub_compile: list[StubCompileFinding] = field(default_factory=list)
    # Phase D (2026-04-20): Non-Negotiable Acceptance Criteria section
    # structural findings. Non-empty list fails the spec hard — every
    # spec must declare which ACs veto SHIP_WITH_GAPS, or justify why
    # none are blocking.
    blocking_ac_issues: list[BlockingAcFinding] = field(default_factory=list)
    # Parsed {ac_id: True} map from the Non-Negotiable section. Consumed
    # by the orchestrator to populate SpecRun.ac_blocking. Empty when
    # the section is missing or carries only a justification.
    blocking_acs: dict[str, bool] = field(default_factory=dict)
    # §6 Edge Cases whose backtick-code tokens verbatim-overlap a §8a
    # blocking AC. Non-empty list is FATAL — the overlap is a silent
    # veto-escape surface for SHIP_WITH_GAPS. See
    # ``darkfactory.spec.blocking_ac_family`` module docstring for the
    # 4736bbd9 regression that motivated the rule.
    blocking_ac_family: list[BlockingAcFamilyFinding] = field(default_factory=list)
    summary: str = ""
    # Phase 0 (resilience rewrite): structural failures that block before any
    # agent can run. Only set on the three early-return paths in run_precheck:
    # oversize file, markdown parse error, empty file. Content findings
    # (placeholders, criteria, shape claims, anchor coverage) are advisory and
    # do NOT set fatal — they are surfaced to the Architect via
    # .dark-factory/precheck/<spec>.json. See docs/plans/2026-04-06-pipeline-resilience-rewrite.md.
    fatal: bool = False


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
    """Legacy text-based entrypoint — delegates to the model-based check.

    The regex-based implementation has been replaced; this wrapper preserves
    the historical signature for any external caller.
    """
    return check_acceptance_criteria_model(parse_spec_model(text))


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


def _shape_bearing(section: Section) -> bool:
    name = section.heading.text.strip().lower()
    canonicals = _SECTION_ALIASES.get(name, [name.title()])
    return any(c.lower() in _SHAPE_BEARING_SECTION_HEADINGS for c in canonicals) or (
        name in _SHAPE_BEARING_SECTION_HEADINGS
    )


def _section_subtree_blocks(model: SpecModel, section: Section) -> tuple[list[FencedBlock], list[Table]]:
    """Return all fenced blocks and tables that fall inside the section's
    line range — i.e., the section AND its sub-headings (recursive)."""
    start, end = section.heading.line_number, section.end_line
    fences = [fb for fb in model.all_fenced_blocks if start <= fb.line_number < end]
    tables = [t for t in model.all_tables if start <= t.line_number < end]
    return fences, tables


def check_shape_claims(model: SpecModel) -> list[ShapeClaimFinding]:
    """Model-based replacement for the regex shape-claim check.

    A shape-bearing section "satisfies" its shape tokens if it (recursively)
    contains a fenced code block whose language is json/yaml/empty (i.e., not
    bash/sh/etc.), OR if a table row or paragraph in the section references a
    defined wire contract by name.
    """
    findings: list[ShapeClaimFinding] = []
    contracts = model.wire_contract_names()  # includes heading-only contracts
    contract_names = list(contracts.keys())

    def _fence_satisfies(fb: FencedBlock) -> bool:
        lang = (fb.language or "").lower()
        if lang in ("json", "yaml", "yml", "toml", "jsonc"):
            return True
        if lang == "":
            content = fb.content.lstrip()
            if content.startswith("{") or content.startswith("["):
                return True
        return False

    def _ref_resolves(text: str) -> bool:
        m = _WIRE_REF_RE.search(text)
        if not m:
            return False
        name = m.group(1).strip().rstrip(".,;:)").lower()
        # The matched group could include trailing words; try progressively
        # shorter prefixes against the contracts map.
        if name in contracts:
            return True
        # Try truncating at sentence boundaries.
        for sep in (".", ",", ";", ":"):
            head = name.split(sep, 1)[0].strip()
            if head and head in contracts:
                return True
        return False

    for section in model.sections:
        if not _shape_bearing(section):
            continue

        sub_fences, sub_tables = _section_subtree_blocks(model, section)

        section_satisfied_by_fence = any(_fence_satisfies(fb) for fb in sub_fences)

        # Check if any table row or paragraph contains a resolving wire ref.
        section_satisfied_by_ref = False
        for t in sub_tables:
            for row in t.rows:
                if _ref_resolves(" | ".join(row.cells)):
                    section_satisfied_by_ref = True
                    break
            if section_satisfied_by_ref:
                break
        if not section_satisfied_by_ref:
            for p in section.paragraphs:
                if _ref_resolves(p.text):
                    section_satisfied_by_ref = True
                    break

        if section_satisfied_by_fence or section_satisfied_by_ref:
            continue

        # Scan paragraphs and table rows for shape tokens. Emit one finding
        # per offending line (per the existing test contract).
        seen_lines: set[int] = set()

        def _scan(text: str, line_no: int) -> None:
            if line_no in seen_lines:
                return
            for m in _SHAPE_TOKEN_RE.finditer(text):
                token = m.group(0)
                would_break = any(
                    t.line_number <= line_no <= t.end_line for t in section.tables
                )
                findings.append(ShapeClaimFinding(
                    section=section.heading.text.strip(),
                    line_number=line_no,
                    matched_token=token,
                    context=text.strip()[:120],
                    section_path=(section.heading.text.strip(),),
                    available_wire_contracts=contract_names,
                    would_break_table=would_break,
                ))
                seen_lines.add(line_no)
                return  # one finding per line

        for p in section.paragraphs:
            _scan(p.text, p.line_number)
        for t in section.tables:
            for row in t.rows:
                _scan(" | ".join(row.cells), row.line_number)

    return findings


def check_shape_claims_from_text(text: str) -> list[ShapeClaimFinding]:
    """Thin wrapper preserving the legacy ``_check_shape_claims(text)`` API."""
    model = parse_spec_model(text)
    return check_shape_claims(model)


def check_acceptance_criteria_model(model: SpecModel) -> list[CriteriaFinding]:
    """Model-based AC check distinguishing three structural failure modes.

    Operates only on the section whose heading resolves (via alias) to
    "Acceptance Criteria".
    """
    ac_section: Section | None = None
    for s in model.sections:
        name = s.heading.text.strip().lower()
        canonicals = _SECTION_ALIASES.get(name, [])
        if "Acceptance Criteria" in canonicals or name == "acceptance criteria":
            ac_section = s
            break
    if ac_section is None:
        return []

    findings: list[CriteriaFinding] = []
    bold_collision_ids: set[str] = set()

    # 1) BOLD_PSEUDO_HEADING_COLLISION — paragraph (not in any table) whose
    # text matches **AC-N ...**.
    bold_re = re.compile(r"\*\*\s*(AC-\d+)\b[^*]*\*\*", re.IGNORECASE)
    for p in ac_section.paragraphs:
        m = bold_re.search(p.raw or p.text)
        if not m:
            continue
        ac_id = m.group(1).upper()
        bold_collision_ids.add(ac_id)
        findings.append(CriteriaFinding(
            criterion_id=ac_id,
            line_number=p.line_number,
            missing_keywords=list(_GWT_KEYWORDS),
            kind="BOLD_PSEUDO_HEADING_COLLISION",
            message=(
                f"{ac_id} appears as a **bold pseudo-heading**, not a table row. "
                "The AC table parser cannot read it. Move the AC into the table or "
                "remove the bold formatting."
            ),
        ))

    # 2) TABLE_BROKEN_AT_AC — section has 2+ Tables AND any earlier table ends
    # with an AC-N row.
    table_broken_fired = False
    if len(ac_section.tables) >= 2:
        # Find AC-N rows in each table.
        ac_id_re = _AC_ID_RE
        last_ac_in_first = None
        for row in ac_section.tables[0].rows:
            joined = " ".join(row.cells)
            m = ac_id_re.search(joined)
            if m:
                last_ac_in_first = (m.group().upper(), row.line_number)
        # Find first AC in second table.
        first_ac_in_second = None
        for row in ac_section.tables[1].rows:
            joined = " ".join(row.cells)
            m = ac_id_re.search(joined)
            if m:
                first_ac_in_second = (m.group().upper(), row.line_number)
                break
        if last_ac_in_first and first_ac_in_second:
            table_broken_fired = True
            ac_id, line_no = first_ac_in_second
            if ac_id not in bold_collision_ids:
                findings.append(CriteriaFinding(
                    criterion_id=ac_id,
                    line_number=line_no,
                    missing_keywords=list(_GWT_KEYWORDS),
                    kind="TABLE_BROKEN_AT_AC",
                    message=(
                        f"The Acceptance Criteria table is split into two pieces — "
                        f"AC rows are straddling the break (last in first table: "
                        f"{last_ac_in_first[0]} at line {last_ac_in_first[1]}; first in second: "
                        f"{ac_id} at line {line_no}). Something between the two pieces "
                        f"(a fenced block, a paragraph, or a heading) is breaking the table. "
                        f"Move the interleaved content out of the table."
                    ),
                ))

    # 3) MALFORMED_GWT — table row whose first cell matches AC-N but cells
    # lack GIVEN/WHEN/THEN. If TABLE_BROKEN_AT_AC fired, suppress
    # MALFORMED_GWT for ALL rows in the second-and-later tables — those
    # rows are almost certainly victims of the broken split, not genuinely
    # malformed, and reporting them double-charges the spec author.
    for t_idx, table in enumerate(ac_section.tables):
        if table_broken_fired and t_idx >= 1:
            continue
        for row in table.rows:
            if not row.cells:
                continue
            first = row.cells[0].strip()
            m = _AC_ID_RE.search(first)
            if not m:
                continue
            ac_id = m.group().upper()
            if ac_id in bold_collision_ids:
                continue
            joined = " ".join(row.cells).upper()
            missing = [kw for kw in _GWT_KEYWORDS if kw not in joined]
            if missing:
                # Skip if already reported as TABLE_BROKEN_AT_AC.
                if any(f.criterion_id == ac_id and f.kind == "TABLE_BROKEN_AT_AC" for f in findings):
                    continue
                findings.append(CriteriaFinding(
                    criterion_id=ac_id,
                    line_number=row.line_number,
                    missing_keywords=missing,
                    kind="MALFORMED_GWT",
                    message=(
                        f"{ac_id} (line {row.line_number}): missing {', '.join(missing)}"
                    ),
                ))

    return findings


# ---------------------------------------------------------------------------
# Phase D (2026-04-20): Non-Negotiable Acceptance Criteria
# ---------------------------------------------------------------------------

# Section-name aliases for the Non-Negotiable section. Mirrors the set in
# ``darkfactory.spec.blocking_acs`` — keep in lockstep.
_NON_NEG_SECTION_NAMES = {
    "non-negotiable acceptance criteria",
    "non negotiable acceptance criteria",
    "blocking acceptance criteria",
}

# Prefix re for spec IDs — same family as _EMBEDDED_TAG_RE in iteration.py.
_BLOCKING_ID_PREFIXES = "FR|EC|NFR|ERR|AC|SC|INT|SEC|INV|UC|BR|DR|TR|PR"
_BLOCKING_ID_IN_TEXT_RE = re.compile(
    rf"\b((?:{_BLOCKING_ID_PREFIXES})-\d+)\b", re.IGNORECASE,
)


def _find_non_negotiable_section(model: SpecModel) -> Section | None:
    for section in model.sections:
        name = section.heading.text.strip().lower()
        stripped = re.sub(r"^\s*\d+[a-z]?\.\s*", "", name)
        if stripped in _NON_NEG_SECTION_NAMES or name in _NON_NEG_SECTION_NAMES:
            return section
    return None


def _collect_all_declared_ids(model: SpecModel) -> set[str]:
    """Gather every AC / FR / EC / NFR / ... ID that appears in the spec.

    Used by the Non-Negotiable validator to verify that listed IDs
    actually resolve to something the spec defines. Extraction is done
    over table rows and paragraphs across all sections — any textual
    occurrence of an ID in canonical form counts, because the pipeline
    attributes failing tests by the same prefix-number pattern.
    """
    ids: set[str] = set()
    for table in model.all_tables:
        for row in table.rows:
            for cell in row.cells:
                for match in _BLOCKING_ID_IN_TEXT_RE.finditer(cell):
                    ids.add(match.group(1).upper())
    for section in model.sections:
        for paragraph in section.paragraphs:
            for match in _BLOCKING_ID_IN_TEXT_RE.finditer(paragraph.text):
                ids.add(match.group(1).upper())
    return ids


def _collect_declared_ids_excluding(
    model: SpecModel, exclude: Section,
) -> set[str]:
    """Gather declared IDs from every section except ``exclude``.

    Used by ``check_non_negotiable_section`` to verify that IDs listed
    under Non-Negotiable resolve to a definition *elsewhere* in the spec —
    a self-reference within the Non-Negotiable section itself doesn't
    count as resolution.
    """
    ids: set[str] = set()
    for table in model.all_tables:
        for row in table.rows:
            for cell in row.cells:
                for match in _BLOCKING_ID_IN_TEXT_RE.finditer(cell):
                    ids.add(match.group(1).upper())
    for section in model.sections:
        if section is exclude:
            continue
        for paragraph in section.paragraphs:
            for match in _BLOCKING_ID_IN_TEXT_RE.finditer(paragraph.text):
                ids.add(match.group(1).upper())
    return ids


def check_non_negotiable_section(
    model: SpecModel,
) -> tuple[list[BlockingAcFinding], dict[str, bool]]:
    """Validate the Non-Negotiable Acceptance Criteria section.

    Returns the list of findings (empty → passed) and the parsed
    ``{ac_id: True}`` map. Three hard-fail modes:

    1. ``MISSING_SECTION`` — section absent entirely.
    2. ``EMPTY_WITHOUT_JUSTIFICATION`` — section present but carries no
       IDs and no paragraph beginning with "justification".
    3. ``UNRESOLVED_ID`` — listed ID doesn't appear anywhere else in
       the spec (AC-table, EC-table, FR section, etc.).

    The caller (``run_precheck``) adds findings to
    ``PreCheckResult.blocking_ac_issues`` and sets ``passed=False`` when
    the list is non-empty. Phase D ships no grace period — every
    existing spec must be migrated (``scripts/migrate_specs_add_blocking.py``).
    """
    section = _find_non_negotiable_section(model)
    if section is None:
        return [BlockingAcFinding(
            kind="MISSING_SECTION",
            line_number=0,
            message=(
                "Spec is missing the '## Non-Negotiable Acceptance Criteria' "
                "section. Declare which AC / EC / FR IDs must pass before SHIP "
                "(list them as bullets), or state a justification paragraph "
                "explaining why no AC is blocking. Empty acceptance is a "
                "deliberate choice, not a default — the validator requires "
                "the author to make it explicitly."
            ),
        )], {}

    # Collect IDs from paragraph text. (markdown-it list items render as
    # paragraphs in SpecModel — see blocking_acs.parse_blocking_acs note.)
    listed_ids: dict[str, int] = {}  # id -> source line number
    has_justification = False
    for paragraph in section.paragraphs:
        if paragraph.text.strip().lower().startswith("justification"):
            has_justification = True
        for match in _BLOCKING_ID_IN_TEXT_RE.finditer(paragraph.text):
            ac_id = match.group(1).upper()
            listed_ids.setdefault(ac_id, paragraph.line_number)

    findings: list[BlockingAcFinding] = []
    if not listed_ids and not has_justification:
        findings.append(BlockingAcFinding(
            kind="EMPTY_WITHOUT_JUSTIFICATION",
            line_number=section.heading.line_number,
            message=(
                f"'Non-Negotiable Acceptance Criteria' section (line "
                f"{section.heading.line_number}) is empty — no AC IDs listed "
                "and no 'Justification:' paragraph. Either list the IDs that "
                "must pass before SHIP, or write a 'Justification: ...' "
                "paragraph explaining why no AC is blocking."
            ),
        ))

    if listed_ids:
        declared_elsewhere = _collect_declared_ids_excluding(model, section)
        for ac_id, line_no in listed_ids.items():
            if ac_id not in declared_elsewhere:
                findings.append(BlockingAcFinding(
                    kind="UNRESOLVED_ID",
                    line_number=line_no,
                    message=(
                        f"{ac_id} in Non-Negotiable section (line {line_no}) "
                        f"does not match any AC / EC / FR / NFR defined "
                        "elsewhere in the spec. Fix the ID or add the "
                        "corresponding row."
                    ),
                    id=ac_id,
                ))

    parsed = {ac_id: True for ac_id in listed_ids}
    return findings, parsed


# ---------------------------------------------------------------------------
# Phase 4.4: Service advisory — detect service references not in the contract
# ---------------------------------------------------------------------------

# Keyword → canonical service name used in environment contract.
# Case-insensitive match against spec text.
_SERVICE_KEYWORDS: dict[str, str] = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "pg_isready": "postgres",
    "redis": "redis",
    "memcached": "memcached",
    "mongodb": "mongodb",
    "mongo": "mongodb",
    "elasticsearch": "elasticsearch",
    "rabbitmq": "rabbitmq",
    "kafka": "kafka",
    "mysql": "mysql",
    "mariadb": "mariadb",
    "mailhog": "mailhog",
    "localstack": "localstack",
    "minio": "minio",
}

# Pre-compile a single regex that matches any keyword as a whole word.
_SERVICE_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_SERVICE_KEYWORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def check_service_references(
    text: str,
    current_services: frozenset[str],
) -> list[ServiceAdvisoryFinding]:
    """Scan spec text for service references not in the current environment contract.

    Returns advisory findings — the architect may declare the service, so this
    is informational, not blocking.
    """
    findings: list[ServiceAdvisoryFinding] = []
    seen_canonical: set[str] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in _SERVICE_KEYWORD_RE.finditer(line):
            keyword = m.group(0).lower()
            canonical = _SERVICE_KEYWORDS.get(keyword)
            if canonical is None or canonical in current_services or canonical in seen_canonical:
                continue
            seen_canonical.add(canonical)
            context = line.strip()[:120]
            findings.append(ServiceAdvisoryFinding(
                service_keyword=m.group(0),
                line_number=lineno,
                context=context,
            ))
    return findings


# ---------------------------------------------------------------------------
# Testability check — detect ACs that observe in-process-untestable side effects
# ---------------------------------------------------------------------------

# Patterns that indicate subprocess-level side effects.  Match whole words in
# AC / FR text.  Advisory only — the LLM validator makes the blocking call.
_SUBPROCESS_SIDE_EFFECT_RE = re.compile(
    r"\b("
    r"process\.exit|sys\.exit|os\.Exit|os\._exit"
    r"|SIGTERM|SIGINT|signal\.signal|process\.kill"
    r"|cluster\.fork|new\s+Worker"
    r")\b",
    re.IGNORECASE,
)


def check_testability(
    model: SpecModel,
) -> tuple[list[TestabilityFinding], list[str]]:
    """Scan AC table rows for side effects untestable in-process.

    Returns ``(findings, subprocess_ac_ids)`` where ``subprocess_ac_ids`` are
    AC-N IDs annotated with ``vehicle: subprocess`` in a 3rd table column.
    """
    # Find the Acceptance Criteria section.
    ac_section: Section | None = None
    for s in model.sections:
        name = s.heading.text.strip().lower()
        canonicals = _SECTION_ALIASES.get(name, [])
        if "Acceptance Criteria" in canonicals or name == "acceptance criteria":
            ac_section = s
            break
    if ac_section is None:
        return [], []

    findings: list[TestabilityFinding] = []
    subprocess_ac_ids: list[str] = []

    for table in ac_section.tables:
        for row in table.rows:
            if not row.cells:
                continue
            first = row.cells[0].strip()
            m = _AC_ID_RE.search(first)
            if not m:
                continue
            ac_id = m.group().upper()
            joined = " ".join(row.cells)

            # Check for vehicle annotation in 3rd column (index 2).
            has_vehicle = False
            if len(row.cells) >= 3:
                vehicle_cell = row.cells[2].strip().lower()
                if "subprocess" in vehicle_cell:
                    has_vehicle = True
                    subprocess_ac_ids.append(ac_id)

            # Scan for subprocess side-effect patterns.
            side_effect = _SUBPROCESS_SIDE_EFFECT_RE.search(joined)
            if side_effect and not has_vehicle:
                findings.append(TestabilityFinding(
                    criterion_id=ac_id,
                    line_number=row.line_number,
                    matched_pattern=side_effect.group(0),
                    has_vehicle_annotation=False,
                    message=(
                        f"{ac_id} observes '{side_effect.group(0)}' which is "
                        f"untestable in-process. Either rewrite the FR so the "
                        f"side effect is triggered inside a callable function, "
                        f"or annotate {ac_id} with vehicle: subprocess and add "
                        f"a Tier 1 command contract."
                    ),
                ))

    return findings, subprocess_ac_ids


# ---------------------------------------------------------------------------
# FR-5: Run all checks and produce aggregate pre-check result
# ---------------------------------------------------------------------------

_MAX_SIZE = 1_048_576  # 1MB


def run_precheck(spec_path: Path, *, project_dir: Path | None = None) -> PreCheckResult:
    """FR-5: Run all checks and return aggregate result.

    ``project_dir`` is optional — when provided, the service advisory check
    compares spec text against the current environment contract to warn about
    undeclared service references. When omitted, the check is skipped.
    """
    if spec_path.stat().st_size > _MAX_SIZE:
        log.warning("spec file exceeds 1MB: %s", spec_path)
        empty_structure = SpecStructureResult(
            sections=[SectionResult(name=n, status=SectionStatus.MISSING) for n in _REQUIRED_SECTIONS],
            score=0, passed=False,
        )
        return PreCheckResult(
            passed=False, structure=empty_structure,
            summary="spec file exceeds 1MB size limit",
            fatal=True,
        )

    text = spec_path.read_text(encoding="utf-8")

    # Hard-fail escape hatch: if the markdown parser cannot understand the
    # spec, every check fails together — no per-check fallback to the regex
    # path. Partial coverage is the failure mode this rewrite eliminates.
    try:
        model = parse_spec_model(text)
    except SpecModelParseError as exc:
        empty_structure = SpecStructureResult(
            sections=[SectionResult(name=n, status=SectionStatus.MISSING) for n in _REQUIRED_SECTIONS],
            score=0, passed=False,
        )
        return PreCheckResult(
            passed=False, structure=empty_structure,
            summary=f"spec parser could not understand this file: {exc}",
            fatal=True,
        )

    if not text.strip():
        empty_structure = SpecStructureResult(
            sections=[SectionResult(name=n, status=SectionStatus.MISSING) for n in _REQUIRED_SECTIONS],
            score=0, passed=False,
        )
        return PreCheckResult(
            passed=False, structure=empty_structure,
            summary="spec file is empty",
            fatal=True,
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

    criteria_issues = check_acceptance_criteria_model(model)
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

    shape_claims = check_shape_claims(model)
    log.debug("shape claims: %d unresolved", len(shape_claims))

    anchor_manifest = extract_anchor_manifest(text)
    anchor_intent_issues = check_anchor_intent_coverage(anchor_manifest)
    log.debug(
        "anchor manifest: FR=%d AC=%d EC=%d, intent gaps=%d",
        len(anchor_manifest.fr_ids), len(anchor_manifest.ac_ids),
        len(anchor_manifest.ec_ids), len(anchor_intent_issues),
    )

    blocking_ac_issues, blocking_acs_parsed = check_non_negotiable_section(model)
    log.debug(
        "non-negotiable AC section: %d issues, %d IDs declared blocking",
        len(blocking_ac_issues), len(blocking_acs_parsed),
    )

    # §6 EC vs §8a AC family-coverage check — FATAL. See module docstring
    # for 4736bbd9 regression context. Silent no-op when §8a is empty
    # (handled inside ``check_blocking_ac_family``). Pass raw ``text``
    # because ``TableRow.cells`` strips backticks during inline tokenization.
    blocking_ac_family_findings = check_blocking_ac_family(model, raw_text=text)
    if blocking_ac_family_findings:
        log.warning(
            "blocking_ac_family: %d EC(s) re-state §8a blocking invariants",
            len(blocking_ac_family_findings),
        )
        for baf in blocking_ac_family_findings:
            log.warning(
                "blocking_ac_family: %s (line %d) overlaps %s — shared: %s",
                baf.ec_id, baf.ec_line_number, baf.blocking_ac_id,
                ", ".join(baf.shared_tokens),
            )

    # Phase 4.4: service advisory check (advisory only, never blocks).
    service_advisories: list[ServiceAdvisoryFinding] = []
    if project_dir is not None:
        try:
            from darkfactory.architecture.runtime import load_environment
            env = load_environment(project_dir)
            current_services = frozenset(env.services)
        except Exception:  # noqa: BLE001 — advisory, never fail precheck
            current_services = frozenset()
        service_advisories = check_service_references(text, current_services)
        if service_advisories:
            log.debug("service advisories: %d undeclared references",
                      len(service_advisories))

    # Testability check (advisory only, never blocks).
    testability_findings, subprocess_ac_ids = check_testability(model)
    if subprocess_ac_ids:
        anchor_manifest.subprocess_ac_ids = subprocess_ac_ids
    if testability_findings:
        log.debug("testability advisories: %d findings", len(testability_findings))
        for tf in testability_findings:
            log.warning(
                "testability: %s observes '%s' (untestable in-process)",
                tf.criterion_id, tf.matched_pattern,
            )

    # Precision gap check (advisory only, never blocks).
    precision_findings = check_precision_gaps(model)
    if precision_findings:
        log.debug("precision advisories: %d findings", len(precision_findings))
        for pf in precision_findings:
            log.warning(
                "precision: %s in %s (line %d) — %s",
                pf.pattern_id, pf.section, pf.line_number, pf.matched_text,
            )

    # Malformed ``sql`` tagged-template syntax — FATAL. The Drizzle /
    # postgres.js ``sql`` identifier is always a tagged template; writing
    # ``sql'SELECT ...'`` or ``sql"SELECT ..."`` is never valid
    # JavaScript. This rule exists because the LLM validator showed
    # variance on identical spec bytes (2026-04-21 Phase E: 4 PASS + 1
    # FAIL across 5 runs on the same sha256). Mechanical catch,
    # deterministic veto; sets ``fatal=True`` below.
    sql_tag_syntax_findings = check_sql_tag_syntax(model, raw_text=text)
    if sql_tag_syntax_findings:
        log.warning(
            "sql_tag_syntax: %d malformed `sql` tagged-template(s)",
            len(sql_tag_syntax_findings),
        )
        for stf in sql_tag_syntax_findings:
            log.warning(
                "sql_tag_syntax: line %d — `sql%s…%s` in %s",
                stf.line_number, stf.quote_char, stf.quote_char,
                "/".join(stf.section_path) or "<root>",
            )

    # A2: deterministic SQL syntax lint (advisory only, never blocks).
    # Findings here are ground truth; the LLM validator's "invalid SQL"
    # claims that contradict A2 are hallucinations.
    # TODO(A2-followup): consume sql_lint findings (and sql_fences_present)
    # to actively deflate validator "invalid SQL" hits. Today the field is
    # populated and logged but no downstream caller reads it. See
    # docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md.
    sql_findings = lint_sql_fences(model)
    if sql_findings:
        log.warning(
            "sql_lint: %d SQL fence(s) failed to parse", len(sql_findings),
        )
        for sf in sql_findings:
            log.warning(
                "sql_lint: line %d (%s) — %s",
                sf.line_number, "/".join(sf.section_path) or "<root>", sf.error,
            )

    # A3: library-symbol existence check (advisory only, never blocks).
    # Layer 1 fires whenever ``project_dir`` is provided; Layer 2 is a
    # graceful no-op until ``node_modules`` resolution lands.
    library_findings = check_library_symbols(model, project_dir=project_dir)
    if library_findings:
        log.warning(
            "library_symbols: %d undeclared import(s) in spec §5",
            len(library_findings),
        )
        for lf in library_findings:
            log.warning(
                "library_symbols: line %d — %s [%s]",
                lf.line_number, lf.message, lf.layer,
            )

    # A6: stub round-trip (advisory only, never blocks). Runs only when
    # tsc + tsconfig + node_modules are present; otherwise a logged no-op.
    # Gate on A3: if library_symbols emitted ANY finding (Layer 1
    # "undeclared dependency" today; Layer 2 "missing export" once
    # implemented), tsc will either emit a cascade of ``cannot find
    # module`` errors (masking real typing issues) or fail at a symbol
    # that A3 already identified more cheaply. Either way, A6's output
    # is less actionable than A3's, so we skip A6 until A3 is clean.
    if library_findings:
        log.info(
            "A6: skipping stub round-trip — A3 has %d undeclared import(s); "
            "fix those first for meaningful tsc output",
            len(library_findings),
        )
        stub_findings: list[StubCompileFinding] = []
    else:
        stub_findings = check_stub_roundtrip(model, project_dir=project_dir)
        if stub_findings:
            log.warning(
                "stub_compile: %d tsc error(s) on spec §5 stubs",
                len(stub_findings),
            )
            for cf in stub_findings:
                log.warning(
                    "stub_compile: %s line %s [%s] — %s",
                    cf.filepath, cf.stub_line, cf.tsc_code, cf.message,
                )

    # Determine pass/fail: FAIL if structure incomplete, placeholders found,
    # criteria malformed, or unresolved shape claims. Ambiguities, FR count,
    # scenario intents, contract issues, and testability are warnings only.
    # ``sql_tag_syntax_findings`` sets ``fatal=True`` (not just
    # ``passed=False``) because the LLM validator has shown variance on
    # this defect class — advisory-only would let the same stochastic
    # PASS/FAIL back in.
    passed = (
        structure.passed
        and not placeholders
        and not criteria_issues
        and not shape_claims
        and not anchor_intent_issues
        and not blocking_ac_issues
        and not blocking_ac_family_findings
        and not sql_tag_syntax_findings
    )
    fatal = bool(sql_tag_syntax_findings) or bool(blocking_ac_family_findings)

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

    if sql_tag_syntax_findings:
        summary_parts.append(
            f"Malformed `sql` tagged-template: {len(sql_tag_syntax_findings)} "
            f"occurrence(s) — FATAL"
        )
        for stf in sql_tag_syntax_findings[:10]:
            summary_parts.append(
                f"  - line {stf.line_number}: `sql{stf.quote_char}…{stf.quote_char}` "
                f"in {'/'.join(stf.section_path) or '<root>'}"
            )
            summary_parts.append(f"      {stf.context}")
            summary_parts.append(
                f"      Fix: replace the {stf.quote_char} with a backtick "
                f"(Drizzle/postgres.js `sql` is always a tagged template)."
            )
        if len(sql_tag_syntax_findings) > 10:
            summary_parts.append(
                f"  ... and {len(sql_tag_syntax_findings) - 10} more"
            )
        issues += len(sql_tag_syntax_findings)

    if blocking_ac_issues:
        summary_parts.append(
            f"Non-Negotiable Acceptance Criteria: {len(blocking_ac_issues)} issue(s)"
        )
        for finding in blocking_ac_issues[:10]:
            loc = f"line {finding.line_number}" if finding.line_number else "section missing"
            summary_parts.append(f"  - [{finding.kind}] {loc}: {finding.message}")
        if len(blocking_ac_issues) > 10:
            summary_parts.append(f"  ... and {len(blocking_ac_issues) - 10} more")
        issues += len(blocking_ac_issues)

    if blocking_ac_family_findings:
        summary_parts.append(
            f"Blocking-AC family coverage: {len(blocking_ac_family_findings)} "
            f"EC(s) re-state §8a blocking invariant(s) — FATAL"
        )
        for baf in blocking_ac_family_findings[:10]:
            summary_parts.append(
                f"  - {baf.ec_id} ↔ {baf.blocking_ac_id} (line {baf.ec_line_number}): "
                f"shared {', '.join(baf.shared_tokens)}"
            )
            summary_parts.append(f"      {baf.message}")
        if len(blocking_ac_family_findings) > 10:
            summary_parts.append(
                f"  ... and {len(blocking_ac_family_findings) - 10} more"
            )
        issues += len(blocking_ac_family_findings)

    if ambiguities:
        summary_parts.append(f"Ambiguity warnings: {len(ambiguities)}")
        for a in ambiguities[:5]:
            summary_parts.append(f"  - line {a.line_number}: \"{a.word}\" in: {a.context}")
        if len(ambiguities) > 5:
            summary_parts.append(f"  ... and {len(ambiguities) - 5} more")

    if service_advisories:
        summary_parts.append(
            f"Service advisories: {len(service_advisories)} service(s) referenced "
            f"but not in environment contract"
        )
        for sa in service_advisories:
            summary_parts.append(
                f"  - line {sa.line_number}: \"{sa.service_keyword}\" — "
                f"architect will need to declare this service"
            )

    if testability_findings:
        summary_parts.append(
            f"Testability advisories: {len(testability_findings)} AC(s) observe "
            f"side effects untestable in-process"
        )
        for tf in testability_findings:
            summary_parts.append(
                f"  - {tf.criterion_id} (line {tf.line_number}): "
                f"'{tf.matched_pattern}' — {tf.message}"
            )

    if precision_findings:
        summary_parts.append(
            f"Precision gap advisories: {len(precision_findings)} potential "
            f"divergence point(s)"
        )
        for pf in precision_findings:
            summary_parts.append(
                f"  - line {pf.line_number}: [{pf.pattern_id}] "
                f"{pf.matched_text} in {pf.section}"
            )
            summary_parts.append(f"      {pf.message}")

    if library_findings:
        summary_parts.append(
            f"Library symbol advisories: {len(library_findings)} undeclared "
            f"import(s) in spec §5 Interface Contract"
        )
        for lf in library_findings:
            summary_parts.append(
                f"  - line {lf.line_number}: imports {lf.import_specifier!r} "
                f"but {lf.imported_package!r} is not a declared dependency"
            )

    if stub_findings:
        summary_parts.append(
            f"Stub round-trip advisories: {len(stub_findings)} tsc error(s) "
            f"on spec §5 Interface Contract stubs"
        )
        for cf in stub_findings[:10]:
            # Lead with the spec line — that's what the human will edit
            # to fix the finding. The stub path and tsc code are diagnostic
            # context pointing back into the originating fence.
            summary_parts.append(
                f"  - spec line {cf.spec_line_number} "
                f"({cf.filepath}:{cf.stub_line}) [{cf.tsc_code}]: "
                f"{cf.message}"
            )
        if len(stub_findings) > 10:
            summary_parts.append(
                f"  ... and {len(stub_findings) - 10} more"
            )

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
        service_advisories=service_advisories,
        testability=testability_findings,
        precision=precision_findings,
        sql_lint=sql_findings,
        sql_tag_syntax=sql_tag_syntax_findings,
        library_symbols=library_findings,
        stub_compile=stub_findings,
        blocking_ac_issues=blocking_ac_issues,
        blocking_acs=blocking_acs_parsed,
        blocking_ac_family=blocking_ac_family_findings,
        summary=summary,
        fatal=fatal,
    )
