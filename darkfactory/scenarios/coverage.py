"""Scenario coverage —

Two unrelated coverage concerns live in this module:

1. ``affected_specs`` — selective regression: cross-reference changed files
   against the registry's per-spec target_files.

2. ``compute_coverage`` — anchor coverage: parse generated scenario files for
   ``@covers`` tags, ``[FR-N]`` inline markers, and fallback inferences from
   filename / describe / it / assertion text. Used by the post-scenario-gen
   gate to verify every declared FR/AC/EC anchor maps to >=1 scenario before
   any Implementer run is dispatched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.spec.anchors import AnchorManifest
from darkfactory.state.registry import list_specs, read_target_files


def affected_specs(project_dir: Path, changed_files: set[str]) -> list[str]:
    """Return spec names whose target_files overlap with changed_files."""
    if not changed_files:
        return []

    result = []
    for spec_name in list_specs(project_dir):
        target_files = read_target_files(project_dir, spec_name)
        if target_files & changed_files:
            result.append(spec_name)
    return sorted(result)


# ---------------------------------------------------------------------------
# Anchor coverage (post-scenario-gen gate)
# ---------------------------------------------------------------------------


@dataclass
class CoverageReport:
    """Per-anchor scenario coverage results.

    - per_anchor_counts: anchor_id -> number of scenario files crediting it.
    - uncovered_anchors: anchors with zero scenarios — hard-floor trigger.
    - weakly_covered: anchors with some coverage but fewer scenarios than
      declared intents (0 < count < declared_intents) — Oracle prior only,
      NOT a gate trigger.
    - duplicate_tags_by_anchor: anchors tagged by >1 distinct file (sanity
      warning, not a failure — useful for catching architect drift).
    """
    per_anchor_counts: dict[str, int] = field(default_factory=dict)
    uncovered_anchors: list[str] = field(default_factory=list)
    weakly_covered: list[str] = field(default_factory=list)
    duplicate_tags_by_anchor: dict[str, int] = field(default_factory=dict)
    # Phase 4.3: bugfix floor — True iff at least one scenario file
    # carries a ``@regression`` tag. Consumed by
    # ``check_spec_type_floor`` to gate bugfix specs.
    regression_scenario_present: bool = False


# Match @covers FR-1, AC-7 (commas/spaces, single-line).
_COVERS_TAG_RE = re.compile(r"@covers\s+([A-Za-z0-9,\s\-]+)", re.IGNORECASE)
# Match anchor IDs anywhere (FR-1, AC-7, EC-2). Word-boundary anchored.
_ANCHOR_RE = re.compile(r"\b(FR|AC|EC)-(\d+)\b", re.IGNORECASE)
# Match describe/it/test("...") / describe/it/test('...') call sites and
# capture the string literal argument. Used to scope inline [FR-N] marker
# credits to test-name strings only (not random body comments).
_DESCRIBE_CALL_RE = re.compile(
    r"""\b(?:describe|it|test)\s*\(\s*(?:"([^"\\]*(?:\\.[^"\\]*)*)"|'([^'\\]*(?:\\.[^'\\]*)*)')""",
    re.IGNORECASE,
)
# Filename variant: filenames often embed anchors as "FR-5_login.spec.js"
# where "_" is a word char and breaks \b. Match without trailing word-boundary
# but require a non-digit (or end-of-string) sentinel after the digits to
# avoid matching "FR-12" as "FR-1".
_FILENAME_ANCHOR_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])(FR|AC|EC)-(\d+)(?=$|[^0-9])",
    re.IGNORECASE,
)
# Match the @untestable escape-hatch marker (retry-2 escalation signal).
_UNTESTABLE_RE = re.compile(
    r"@untestable\s+((?:FR|AC|EC)-\d+)\s*:?\s*(.*)", re.IGNORECASE,
)

# Phase 4.3: bugfix specs must include at least one scenario that
# reproduces the regression. The credit is keyed to an explicit
# ``@regression`` tag in the file body. A filename heuristic was
# considered and rejected: substrings like ``no_regression`` or scratch
# files with ``regression`` in their name would silently satisfy the
# floor without an actual repro scenario. Authors must opt in
# explicitly via the tag.
_REGRESSION_TAG_RE = re.compile(r"@regression\b", re.IGNORECASE)

# File extensions to scan. Language-agnostic regex over file text — no AST.
_SCENARIO_EXTENSIONS = (".js", ".ts", ".jsx", ".tsx", ".py", ".mjs", ".cjs")

# Compound extensions for markdown scenario formats. Matched against the
# full filename (case-insensitive endswith) because Path.suffix only returns
# the last suffix (`.md`), which would be too broad. Documented in
# agents/scenario-architect.md as Format A (HTTP, .scenarios.md) and
# Format B (Browser, .journey.md); both use markdown header conventions
# for anchor declaration.
_MARKDOWN_SCENARIO_SUFFIXES = (".scenarios.md", ".journey.md")

# Match `# Requirement: FR-1, AC-2` (or `# Requirements:` plural).
# - Anchored to start-of-line ((?m)^).
# - Allows leading whitespace before `#` (some editors auto-indent).
# - Captures everything after the colon for anchor extraction.
# - Single-line; the header is one line per the architect contract.
_MARKDOWN_REQUIREMENT_RE = re.compile(
    r"(?m)^\s*#\s*Requirements?\s*:\s*(.+)$",
    re.IGNORECASE,
)
# Match any markdown header line (``#``, ``##``, ``###`` ...) and capture the
# header text. Used to scope inline ``[FR-N]`` / ``FR-N`` anchor credit to
# header lines only — mirrors the discipline of ``_DESCRIBE_CALL_RE`` for
# code files (credit is restricted to test-name strings, not random body
# prose). TE-format headers like ``# Scenario: ... [FR-1]`` and
# ``## Test: ... [AC-3]`` are credited via this path.
_MARKDOWN_HEADER_RE = re.compile(r"(?m)^\s*#{1,6}\s+(.+)$")


def _normalise_anchor(kind: str, num: str) -> str:
    return f"{kind.upper()}-{int(num)}"


def _extract_anchors_from_text(text: str) -> set[str]:
    """Return the set of anchor IDs credited by a single scenario file.

    Credit rules (any one is sufficient):
    - Explicit ``@covers FR-1, AC-7`` tag in JSDoc / docstring.
    - Inline ``[FR-3]`` or ``FR-3`` marker inside a
      ``describe(...)``/``it(...)``/``test(...)`` string literal.

    NOTE: random prose mentions of ``FR-3`` elsewhere in the file body do
    NOT credit the anchor. Filename-based credit is handled by the caller.
    """
    anchors: set[str] = set()

    # Explicit @covers tags — parse the comma-separated list and capture
    # any anchor IDs in it. We use the broader anchor regex on the captured
    # group rather than splitting on commas, so trailing punctuation,
    # whitespace, and partial mentions all resolve correctly.
    for tag_match in _COVERS_TAG_RE.finditer(text):
        for m in _ANCHOR_RE.finditer(tag_match.group(1)):
            anchors.add(_normalise_anchor(m.group(1), m.group(2)))

    # Inline markers restricted to describe/it/test string literal arguments.
    for call_match in _DESCRIBE_CALL_RE.finditer(text):
        literal = call_match.group(1) or call_match.group(2) or ""
        for m in _ANCHOR_RE.finditer(literal):
            anchors.add(_normalise_anchor(m.group(1), m.group(2)))

    return anchors


def _is_markdown_scenario(path: Path) -> bool:
    """True iff the path's filename matches a known markdown scenario suffix.

    Centralised so that ``_iter_scenario_files`` and ``compute_coverage``
    cannot drift: a new entry in ``_MARKDOWN_SCENARIO_SUFFIXES`` automatically
    affects both iteration and dispatch.
    """
    name_lower = path.name.lower()
    return any(name_lower.endswith(s) for s in _MARKDOWN_SCENARIO_SUFFIXES)


def _extract_anchors_from_markdown_scenario(text: str) -> set[str]:
    """Return the set of anchor IDs credited by a markdown scenario file.

    Credit rules (any one is sufficient):
    - ``# Requirement: FR-N[, AC-N]`` header (legacy Scenario Architect
      format A/B).
    - ``@covers FR-N, AC-N`` tag anywhere in the file (mirrors the
      code-file extractor — TE emits this form under
      ``# Scenario:`` headers).
    - Anchor IDs appearing in any markdown header line, including bracketed
      forms like ``# Scenario: ... [FR-1]`` and ``## Test: ... [AC-3]``
      (Phase 4.6b.1 Test Engineer format).

    Body prose mentions of FR-N/AC-N/EC-N that are not in a header or
    ``@covers`` tag do NOT credit the anchor — same discipline as the
    code-file extractor's restriction to ``@covers`` tags and describe()
    literals.

    Caveat: the regex is line-oriented and does not understand fenced code
    blocks. A literal ``# Requirement: FR-1`` inside a ```` ``` ```` fence
    would be credited. Revisit if a real false credit appears.
    """
    anchors: set[str] = set()

    # Legacy `# Requirement: FR-N` / `# Requirements: ...` headers.
    for header_match in _MARKDOWN_REQUIREMENT_RE.finditer(text):
        line = header_match.group(1)
        for m in _ANCHOR_RE.finditer(line):
            anchors.add(_normalise_anchor(m.group(1), m.group(2)))

    # Explicit `@covers FR-1, AC-7` tags — TE places these under
    # `# Scenario:` headers.
    for tag_match in _COVERS_TAG_RE.finditer(text):
        for m in _ANCHOR_RE.finditer(tag_match.group(1)):
            anchors.add(_normalise_anchor(m.group(1), m.group(2)))

    # Any anchor ID (bracketed or bare) inside a markdown header line.
    # Scoped to headers only so that body prose mentions don't count.
    for header_match in _MARKDOWN_HEADER_RE.finditer(text):
        header_text = header_match.group(1)
        for m in _ANCHOR_RE.finditer(header_text):
            anchors.add(_normalise_anchor(m.group(1), m.group(2)))

    return anchors


def _extract_untestable_markers(text: str) -> dict[str, str]:
    """Return anchor_id -> reason for any @untestable markers in the file."""
    markers: dict[str, str] = {}
    for m in _UNTESTABLE_RE.finditer(text):
        anchor = m.group(1).upper()
        # Normalise the numeric tail.
        am = _ANCHOR_RE.match(anchor)
        if am:
            anchor = _normalise_anchor(am.group(1), am.group(2))
        markers[anchor] = m.group(2).strip()
    return markers


def _iter_scenario_files(scenarios_dir: Path):
    """Yield every scenario file under scenarios_dir matching known patterns.

    Recognized:
    - Code-test extensions in ``_SCENARIO_EXTENSIONS`` (.js, .ts, .py, ...)
    - ``.spec.`` / ``.test.`` in the suffix chain (jest/mocha/vitest convention)
    - Markdown scenario formats in ``_MARKDOWN_SCENARIO_SUFFIXES``
      (``.scenarios.md``, ``.journey.md``) — agent-emitted Format A / Format B
    """
    if not scenarios_dir.exists():
        return
    for path in scenarios_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in _SCENARIO_EXTENSIONS:
            yield path
            continue
        suffix_chain = "".join(path.suffixes).lower()
        if ".spec." in suffix_chain or ".test." in suffix_chain:
            yield path
            continue
        if _is_markdown_scenario(path):
            yield path
            continue


def compute_coverage(
    scenarios_dir: Path,
    manifest: AnchorManifest,
) -> CoverageReport:
    """Compute per-anchor scenario coverage for the gate.

    Walks every scenario file under ``scenarios_dir`` and credits each anchor
    via @covers tag, [FR-N] inline marker, or any literal anchor ID found in
    the file body or filename. Returns a structured ``CoverageReport`` with
    uncovered, weakly-covered, and duplicate-tag findings. Pure / safe to
    call repeatedly.
    """
    declared = list(manifest.fr_ids) + list(manifest.ac_ids) + list(manifest.ec_ids)
    per_anchor_files: dict[str, set[str]] = {a: set() for a in declared}
    regression_scenario_present = False

    for path in _iter_scenario_files(scenarios_dir):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _is_markdown_scenario(path):
            anchors = _extract_anchors_from_markdown_scenario(text)
        else:
            anchors = _extract_anchors_from_text(text)
        # Filename fallback inference — credit any anchor ID in the filename.
        for m in _FILENAME_ANCHOR_RE.finditer(path.name):
            anchors.add(_normalise_anchor(m.group(1), m.group(2)))
        for anchor in anchors:
            if anchor in per_anchor_files:
                per_anchor_files[anchor].add(str(path))
        # Phase 4.3: regression scenario detection — once any file
        # earns the tag, the bugfix floor is satisfied. Checked on
        # every file so a single regression scenario is enough.
        if not regression_scenario_present and _REGRESSION_TAG_RE.search(text):
            regression_scenario_present = True

    per_anchor_counts = {a: len(files) for a, files in per_anchor_files.items()}
    uncovered_anchors = [a for a in declared if per_anchor_counts.get(a, 0) == 0]

    weakly_covered: list[str] = []
    for anchor in declared:
        intents = manifest.declared_intents.get(anchor, 0)
        count = per_anchor_counts.get(anchor, 0)
        # Under-covered relative to declared intent: has some coverage but
        # fewer scenarios than declared intents.
        if 0 < count < intents:
            weakly_covered.append(anchor)

    duplicate_tags_by_anchor = {
        a: c for a, c in per_anchor_counts.items() if c > 1
    }

    return CoverageReport(
        per_anchor_counts=per_anchor_counts,
        uncovered_anchors=uncovered_anchors,
        weakly_covered=weakly_covered,
        duplicate_tags_by_anchor=duplicate_tags_by_anchor,
        regression_scenario_present=regression_scenario_present,
    )


# ---------------------------------------------------------------------------
# Phase 4.3: spec-type-aware coverage floor
# ---------------------------------------------------------------------------


def check_spec_type_floor(
    report: CoverageReport,
    spec_type: str,
) -> tuple[bool, str]:
    """Apply the Phase-4.3 spec-type-specific coverage floor.

    Assumes the caller has already run the anchor-coverage gate (empty
    ``uncovered_anchors``). This returns the *additional* floor on top
    of that baseline:

    - ``bugfix``: at least one scenario must carry an ``@regression``
      tag in its body. Bugfix
      specs exist precisely because a regression slipped through; the
      pipeline cannot ship a bugfix without a scenario that would have
      caught the bug pre-fix.
    - ``bootstrap``: currently no additional floor beyond per-anchor
      coverage. The locked plan calls for "100% of FRs that touch
      ``environment.toml``" but identifying which FRs those are
      requires a spec-reading pass that Phase 4.3 defers. A TODO
      below tracks the remaining work.
    - ``feature`` / unknown: no additional floor.

    Returns ``(passes, reason)``. ``passes=True`` means the floor is
    satisfied; ``reason`` is empty. ``passes=False`` means the floor is
    violated and ``reason`` is a short, user-facing sentence suitable
    for logging and for feeding back into a scenario-regen prompt.
    """
    if spec_type == "bugfix":
        if not report.regression_scenario_present:
            return (
                False,
                "bugfix spec must include at least one scenario with an "
                "`@regression` tag in its body that reproduces the bug "
                "being fixed",
            )
    # bootstrap / feature / unknown: no extra floor today.
    # TODO(phase-4.3-followup): bootstrap should enforce 100% coverage
    # for FRs that touch environment.toml. Requires correlating the
    # spec's FR text with environment-contract paths, which this module
    # does not yet parse.
    return True, ""
