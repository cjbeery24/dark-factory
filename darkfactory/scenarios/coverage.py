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

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from darkfactory.spec.anchors import AnchorManifest
from darkfactory.scenarios.vehicle_classifier import VEHICLE_TIERS
from darkfactory.state.registry import list_specs, read_target_files

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from darkfactory.adapters.base import EcosystemAdapter
    from darkfactory.scenarios.vehicle_classifier import VehicleSuggestion


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


# Match an explicit "covers" marker in either idiom:
#   - JSDoc/docstring   @covers FR-1, AC-7                  (whitespace after word)
#   - pytest decorator  @pytest.mark.covers("FR-1", "AC-2")  (@…covers( … ))
# BOTH forms require the leading `@` — that's what makes the tag an explicit,
# intentional coverage marker. Anchoring the pytest form to the DECORATOR
# (`@(?:\w+\.)*covers(`) is load-bearing at this ship-gate: a bare `.covers(`
# would false-credit ANY method call named `covers` (`query.covers("FR-1")`,
# `poly.covers(...)` from shapely, a commented-out `# x.covers("FR-9")`) —
# crediting an anchor with ZERO real test, the worst failure here. The decorator
# anchor rejects those (no `@`) and the look-alikes `discovers(`/`recovers(`
# (no leading `@…`). The captured run is scanned by _credited_tokens below,
# which discards the surrounding quotes/commas.
# NOTE the ``#`` in the capture class: an orphan-intent handle ``FR-2#3`` must
# survive into the captured group (without it the class stops at ``#`` and only
# the bare ``FR-2`` is credited — the wired-but-inert failure Neo flagged).
# The capture is bounded to a SINGLE LINE (`[ \t]`, not `\s`): a newline-spanning
# class let a ``#`` on the tag line swallow the *next* line's prose, false-
# crediting a stray anchor after a ``#`` comment (Linus). A real handle's ``#``
# has no surrounding whitespace and stays on the tag's own line, so it survives.
_COVERS_TAG_RE = re.compile(
    r"""(?:@covers[ \t]+|@(?:\w+\.)*covers[ \t]*\()([A-Za-z0-9, \t\-"'#]+)""",
    re.IGNORECASE,
)
# Anchor OR orphan-intent handle: FR-2, AC-7, or FR-2#3 (parent#index). The
# ``#(\d+)`` group is optional so this also matches a bare anchor. Used by the
# credit path; a handle credits BOTH itself and its parent (see _credited_tokens).
_ANCHOR_OR_HANDLE_RE = re.compile(r"\b(FR|AC|EC)-(\d+)(?:#(\d+))?\b", re.IGNORECASE)
# Match describe/it/test("...") / describe/it/test('...') call sites and
# capture the string literal argument. Used to scope inline [FR-N] marker
# credits to test-name strings only (not random body comments).
_DESCRIBE_CALL_RE = re.compile(
    r"""\b(?:describe|it|test)\s*\(\s*(?:"([^"\\]*(?:\\.[^"\\]*)*)"|'([^'\\]*(?:\\.[^'\\]*)*)')""",
    re.IGNORECASE,
)
# Pytest analogue of _DESCRIBE_CALL_RE: credit inline [FR-N] markers in the
# FIRST docstring line of a `def test_*(...):` function — the convention
# agents/test-engineer.md teaches for Python. Scoped to the test-function
# boundary (def test_\w+ + its opening docstring) so module docstrings, body
# comments, and non-test helper docstrings do NOT credit — mirroring the
# describe/it/test "credit only inside the test boundary" discipline.
# NOTE: a PREFIXED docstring (r\"\"\"…\"\"\", f\"\"\"…\"\"\") is intentionally not
# matched — the quotes no longer follow the colon directly. This is a
# false-NEGATIVE (safe direction; the TE prompt teaches a plain triple-quoted
# docstring), never a false-credit.
_PYTEST_TEST_DOCSTRING_RE = re.compile(
    r"""def\s+test_\w+\s*\([^)]*\)\s*:\s*["']{3}([^\n]*)""",
)
# Filename variant: filenames often embed anchors as "FR-5_login.spec.js"
# where "_" is a word char and breaks \b. Match without trailing word-boundary
# but require a non-digit (or end-of-string) sentinel after the digits to
# avoid matching "FR-12" as "FR-1".
_FILENAME_ANCHOR_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])(FR|AC|EC)-(\d+)(?=$|[^0-9])",
    re.IGNORECASE,
)
# Match the @untestable escape-hatch marker (retry-2 escalation signal). The
# optional ``#\d+`` lets an orphan-intent handle (FR-2#3) be waived the same way.
_UNTESTABLE_RE = re.compile(
    r"@untestable\s+((?:FR|AC|EC)-\d+(?:#\d+)?)\s*:?\s*(.*)", re.IGNORECASE,
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


def _credited_tokens(s: str) -> set[str]:
    """Anchors AND orphan-intent handles credited by a string.

    A handle ``FR-2#3`` credits BOTH the handle and its parent ``FR-2`` — a
    scenario exercising the specific orphan intent necessarily exercises the
    parent FR. A bare ``FR-2`` credits ONLY ``FR-2``: a generic FR test must not
    silently satisfy a specific orphan-intent obligation (that is the whole point
    of the handle). Normalises the numeric tails (``FR-02#03`` → ``FR-2#3``).
    """
    out: set[str] = set()
    for m in _ANCHOR_OR_HANDLE_RE.finditer(s):
        anchor = _normalise_anchor(m.group(1), m.group(2))
        out.add(anchor)
        if m.group(3) is not None:
            out.add(f"{anchor}#{int(m.group(3))}")
    return out


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
    # any anchor IDs (or FR-N#M handles) in it. We scan the captured group
    # rather than splitting on commas, so trailing punctuation, whitespace,
    # and partial mentions all resolve correctly.
    for tag_match in _COVERS_TAG_RE.finditer(text):
        anchors |= _credited_tokens(tag_match.group(1))

    # Inline markers restricted to describe/it/test string literal arguments.
    for call_match in _DESCRIBE_CALL_RE.finditer(text):
        literal = call_match.group(1) or call_match.group(2) or ""
        anchors |= _credited_tokens(literal)

    # Pytest: [FR-N] markers in the first docstring line of a def test_*()
    # function (the python TE convention; same test-boundary scoping as above).
    for ds_match in _PYTEST_TEST_DOCSTRING_RE.finditer(text):
        anchors |= _credited_tokens(ds_match.group(1))

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
        anchors |= _credited_tokens(header_match.group(1))

    # Explicit `@covers FR-1, AC-7` tags — TE places these under
    # `# Scenario:` headers.
    for tag_match in _COVERS_TAG_RE.finditer(text):
        anchors |= _credited_tokens(tag_match.group(1))

    # Any anchor ID (bracketed or bare) inside a markdown header line.
    # Scoped to headers only so that body prose mentions don't count.
    for header_match in _MARKDOWN_HEADER_RE.finditer(text):
        anchors |= _credited_tokens(header_match.group(1))

    return anchors


def _extract_untestable_markers(text: str) -> dict[str, str]:
    """Return anchor_id -> reason for any @untestable markers in the file.

    Normalises the numeric tail and PRESERVES an orphan-intent handle suffix
    (``@untestable FR-2#3`` → key ``FR-2#3``) so a handle can be waived exactly
    like an anchor.
    """
    markers: dict[str, str] = {}
    for m in _UNTESTABLE_RE.finditer(text):
        raw = m.group(1).upper()
        am = _ANCHOR_OR_HANDLE_RE.match(raw)
        if am:
            anchor = _normalise_anchor(am.group(1), am.group(2))
            if am.group(3) is not None:
                anchor = f"{anchor}#{int(am.group(3))}"
        else:
            anchor = raw
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


# --- Behavioral-credit rule (close the lexical-credit hole) ------------------
# Lexical credit (a ``[AC-N]`` token in a test name) is NECESSARY but NOT
# sufficient: a "source-grep" scenario that ``readFileSync``s the impl source and
# asserts on its TEXT — or an empty test — earns full credit while verifying no
# behavior (F-1d FR-2 routing shipped exactly this way). Ground-truth tell: a real
# behavioral test IMPORTS the unit under test; a source-grep imports only stdlib /
# test-framework and reads source as data. So under this rule a Tier-2 code
# scenario must import >=1 PROJECT module to credit. The project-vs-not decision
# is made by the ADAPTER (``parse_test_imports`` — same ground-truth the
# SCENARIO_FINGERPRINT uses), NOT a regex in this module: ``compute_coverage``
# stays adapter-agnostic and consumes a precomputed ``behavioral_files`` set.
# This is a FLOOR-level necessary condition (an import-then-grep still passes);
# the blind-holdout gate is the sufficient behavioral layer. Tier-1 contract
# files (deterministic ``00-tier1-`` prefix) assert config CONTENT and are EXEMPT;
# markdown scenarios (prose, not imports) are EXEMPT.
_TIER1_PREFIXES = ("00-tier1-", "00_tier1_")


def _is_tier1_contract(path: Path) -> bool:
    """Tier-1 contract files — emitted with a deterministic ``00-tier1-`` prefix
    by the contract generators — legitimately assert config/file CONTENT and are
    exempt from the project-import requirement. Anchored to the generator's prefix,
    not a free-text name match, so a Tier-2 file cannot bypass by naming itself."""
    return path.name.startswith(_TIER1_PREFIXES)


def _is_project_specifier(specifier: str, project_prefixes: tuple[str, ...]) -> bool:
    """A specifier that resolves to the project's own source (the unit under test),
    NOT stdlib / test-framework / third-party. Relative imports always qualify;
    path-aliases / workspace-scopes / source-dir roots are supplied by the caller
    (derived from the project's config), so alias (``@/x``) and workspace-package
    (``@scope/pkg``) and path-mapped (``apps/...``) project imports are not
    false-denied."""
    if specifier.startswith("."):  # ./ ../ . ..  (relative — JS/TS and Python)
        return True
    return any(specifier.startswith(p) for p in project_prefixes)


def compute_behavioral_files(
    scenarios_dir: Path,
    adapters,
    project_prefixes: tuple[str, ...] = (),
) -> set[str]:
    """Scenario file paths that import >=1 PROJECT module (ground-truth via the
    adapters' ``parse_test_imports``). Source-greps / non-importing fakes are
    absent from the returned set and so credit nothing under the behavioral rule.
    Best-effort: an adapter that raises on a file simply doesn't contribute it.
    """
    # NOTE (residual false-deny class): parse_test_imports is regex/line-oriented.
    # Dynamic ``import('../x')``, bare side-effect ``import '../x'``, and re-exports
    # ``export ... from '../x'`` ARE now detected (prereq 1). What remains: a
    # multi-line import statement, or a second dynamic import on the same line, is
    # not split; and ``import type`` is intentionally excluded (no runtime value).
    behavioral: set[str] = set()
    for path in _iter_scenario_files(scenarios_dir):
        for adapter in adapters:
            try:
                imports = adapter.parse_test_imports(path)
            except Exception:  # noqa: BLE001 — one bad parse must not deny the rest
                # Visible, not silent: a swallowed parse error means this file is
                # denied behavioral credit (a potential false-deny) — surface it.
                log.warning(
                    "behavioral-credit: parse_test_imports failed for %s — file "
                    "denied behavioral credit (may be a false-deny)", path,
                    exc_info=True,
                )
                continue
            if any(_is_project_specifier(i.specifier, project_prefixes) for i in imports):
                behavioral.add(str(path))
                break
    return behavioral


def project_prefixes_from_manifest(manifest_path: Path) -> tuple[str, ...]:
    """Project-import specifier prefixes derived from the ARCHITECT manifest's
    ``test_integration.entry_points[*].specifier`` — the SAME manifest-anchored
    ground-truth the validator's import-specifier check is built from (its
    ``ep_map`` keys). NOT a fresh tsconfig/package.json parse: the soundness
    requirement is that this set comes from the declared entry points, so the
    "is this a project import" question has one authority, not two guesses.

    For each declared specifier we admit BOTH the full specifier (an exact
    ``@/app`` import) AND its dir-prefix.

    DELIBERATE DIVERGENCE from validator.py's ``known_prefixes`` (validator.py
    ~1827): the validator's dir-prefix is ``spec.rsplit('/', 1)[0]`` (no trailing
    slash) → ``@/app`` yields ``@``. We add the trailing slash → ``@/``. This is
    NOT a bug-for-bug mirror: the validator's polarity is FLAG-a-violation (a
    loose ``@`` that also matches a third-party ``@scope/pkg`` is fine — it only
    *suspects* an undeclared project import and cross-checks ep_map). OUR polarity
    is GRANT-credit; a loose ``@`` would wrongly credit a test that imports only
    ``@testing-library/react`` as behavioral project coverage. The trailing slash
    enforces the path-segment boundary so ``@/sub/x`` (project alias) credits but
    ``@scope/pkg`` (third-party) does not — and ``apps/api/src/`` does not match a
    sibling ``apps/api/src-utils/``. The tightening is load-bearing for a credit gate.

    Returns ``()`` on a missing/unparseable manifest OR a manifest with no declared
    entry_points — the behavioral rule then credits only relative imports (still
    sound, just narrower), never crediting third-party.
    """
    try:
        from darkfactory.spec.manifest import parse_manifest

        result = parse_manifest(manifest_path.read_text(encoding="utf-8"))
    except OSError:
        return ()
    if result.manifest is None:
        return ()
    prefixes: set[str] = set()
    for ep in result.manifest.test_integration.entry_points:
        spec = ep.specifier
        if not spec:
            continue
        prefixes.add(spec)  # exact declared specifier (e.g. ``@/app``)
        if "/" in spec:
            # dir-prefix WITH trailing slash (``@/``, ``apps/api/src/``) — the
            # path-segment boundary that keeps a third-party ``@scope/pkg`` out of
            # the credit set. See the docstring's DELIBERATE DIVERGENCE note.
            prefixes.add(spec.rsplit("/", 1)[0] + "/")
    return tuple(sorted(prefixes))


def compute_coverage(
    scenarios_dir: Path,
    manifest: AnchorManifest,
    *,
    require_behavioral_credit: bool = False,
    behavioral_files: set[str] | None = None,
    adapters=None,
    project_prefixes: tuple[str, ...] = (),
    runtime_boundary_anchors: frozenset[str] | set[str] = frozenset(),
) -> CoverageReport:
    """Compute per-anchor scenario coverage for the gate.

    Walks every scenario file under ``scenarios_dir`` and credits each anchor
    via @covers tag, [FR-N] inline marker, or any literal anchor ID found in
    the file body or filename. Returns a structured ``CoverageReport`` with
    uncovered, weakly-covered, and duplicate-tag findings. Pure / safe to
    call repeatedly.

    Behavioral-credit (opt-in, ``require_behavioral_credit``): a Tier-2 scenario
    credits an anchor only if it imports >=1 project module (ground-truth via the
    adapters). Callers pass ``adapters`` + ``project_prefixes`` and let this
    function derive ``behavioral_files`` from its OWN ``scenarios_dir`` walk — so
    the path keys are identical to the credit loop's by construction (no two-caller
    drift). Tests may instead inject ``behavioral_files`` directly. Default OFF =
    pre-existing lexical behavior, unchanged.
    """
    # Derive behavioral_files from this same scenarios_dir (identical path keys to
    # the credit loop below) rather than trusting a caller to pre-walk it. Only
    # when the rule is on, nothing was injected, and adapters are available.
    if require_behavioral_credit and behavioral_files is None and adapters:
        behavioral_files = compute_behavioral_files(
            scenarios_dir, adapters, project_prefixes
        )
    # Declared obligations = every FR/AC/EC anchor PLUS the orphan-intent handles
    # (FR-N#M) for blocking-layer unmapped §9 intents. A handle is anchor-shaped,
    # so it rides this whole credit / uncovered / remediation path unchanged: an
    # uncredited handle lands in uncovered_anchors exactly like a bare anchor.
    declared = (
        list(manifest.fr_ids)
        + list(manifest.ac_ids)
        + list(manifest.ec_ids)
        + manifest.orphan_obligation_handles()
    )
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
        # Behavioral-credit rule (opt-in, default OFF — corpus-validate before
        # flipping the default). A source-grep / non-importing Tier-2 scenario is
        # NOT in behavioral_files → it credits nothing AND cannot satisfy the
        # bugfix-regression floor (both gated consistently). Tier-1 contract files
        # and markdown scenarios are exempt.
        file_creditable = (
            not require_behavioral_credit
            or _is_markdown_scenario(path)
            or _is_tier1_contract(path)
            or (behavioral_files is not None and str(path) in behavioral_files)
        )
        for anchor in anchors:
            if anchor not in per_anchor_files:
                continue
            # Per-anchor exemption (distinct from the per-FILE exemptions above):
            # a runtime-boundary anchor — behavioral but with no importable unit —
            # is credited by its covering scenario even from a non-importing file.
            # Gated per-anchor INSIDE the loop (not a 4th disjunct on file_creditable),
            # so a non-importing file credits ONLY its runtime-boundary anchors, not
            # every other anchor it happens to tag.
            if file_creditable or anchor in runtime_boundary_anchors:
                per_anchor_files[anchor].add(str(path))
        # Phase 4.3: regression scenario detection — gated on FILE creditability,
        # NOT the runtime-boundary exemption. A bugfix regression test reproduces a
        # real bug, so it exercises real behavior and imports the unit; it is never
        # a no-import runtime-boundary scenario, so the two sets are disjoint by
        # nature and need no extra guard.
        if (
            file_creditable
            and not regression_scenario_present
            and _REGRESSION_TAG_RE.search(text)
        ):
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


# Anchor-ID form: FR-N / AC-N / EC-N, OR an orphan-intent handle FR-N#M (the
# forms the manifest declares / demands). The optional ``#\d+`` lets a substrate
# `covers = ["FR-1#1"]` credit an orphan handle — without it the handle is
# silently dropped and an RLS-style orphan can never be substrate-covered (the
# FR-1 chicken-and-egg Neo flagged).
_CREDIT_ANCHOR_RE = re.compile(r"^(?:FR|AC|EC)-\d+(?:#\d+)?$")


def architect_credited_anchors(
    manifest_path: Path,
    substrate_manifest_path: Path,
) -> dict[str, str]:
    """Anchors the ARCHITECT declared covered by a NON-scenario vehicle.

    ``compute_coverage`` only credits Tier-2 scenario *files*. But many anchors
    are legitimately verified by other vehicles the architect declares — the
    deterministic forbidden-pattern scanner, or a substrate primitive — or are
    forward-references that are unverifiable at this build-phase scope. Without
    crediting them, the coverage floor demands an impossible Tier-2 scenario and
    aborts a spec that is correct for its scope. Returns ``{anchor_id: reason}``.

    Two architect-produced signals are honored:

    1. ``forbidden_patterns`` keys in the architect manifest YAML — the anchor
       is enforced by the forbidden-pattern scanner (a Tier-1 vehicle); no
       Tier-2 scenario is needed.
    2. ``covers = ["AC-N", ...]`` on a substrate-manifest primitive (e.g.
       ``[[tenant_isolation]]``) — the architect declared which spec anchors
       that primitive governs. With ``verifiable = false`` the anchor is a
       forward-reference unverifiable at this scope (e.g. an F-2+ RLS table not
       yet introduced); either way the primitive, not a Tier-2 scenario, is the
       vehicle.

    SAFETY (load-bearing): this reads ONLY architect-produced files. The
    architect is a source-reading role and is NOT barrier-restricted against the
    spec/source, so its declarations are trustworthy. It deliberately does NOT
    consult the Test Engineer's ``COVERAGE-GAPS.md`` — the TE is
    barrier-restricted and controls that file, so honoring its self-declared
    category would let a barrier'd agent waive its own coverage obligation (the
    ``d2fecbf`` class). An anchor the architect did not list stays uncovered: the
    floor fails CLOSED on architect omission, never open.
    """
    credited: dict[str, str] = {}

    # 1. forbidden-pattern scanner vehicle (anchor-keyed in the YAML manifest).
    try:
        from darkfactory.spec.manifest import parse_manifest

        result = parse_manifest(manifest_path.read_text(encoding="utf-8"))
        if result.manifest is not None:
            for anchor in result.manifest.forbidden_patterns:
                if _CREDIT_ANCHOR_RE.match(anchor):
                    credited.setdefault(anchor, "forbidden-pattern scanner (Tier-1)")
    except OSError:
        pass

    # 2. substrate-primitive ``covers`` lists (architect-declared anchor sets).
    import tomllib

    try:
        data = tomllib.loads(substrate_manifest_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        data = {}
    for primitive, entries in (data.items() if isinstance(data, dict) else []):
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            covers = entry.get("covers")
            if not isinstance(covers, list):
                continue
            reason = (
                f"substrate:{primitive} (verifiable=false — deferred)"
                if entry.get("verifiable") is False
                else f"substrate:{primitive}"
            )
            for anchor in covers:
                if isinstance(anchor, str) and _CREDIT_ANCHOR_RE.match(anchor):
                    credited.setdefault(anchor, reason)
    return credited


@dataclass(frozen=True)
class DeferralConsistencyFinding:
    """A structural inconsistency between a ``verifiable=false`` substrate
    primitive and the spec's deferral/blocking declarations.

    Background (Neo F+E2 ruling, 2026-06-21; bb spec-4 FR-2 escalation).
    ``verifiable=false`` is an ASYMMETRIC deferral: it suppresses gate
    substrate-verification (adapter side, ``adapter.py`` skips the entry)
    but only propagates to the coverage floor — crediting the governed
    anchors as deferred — when the primitive ALSO declares a ``covers``
    list (consumed by :func:`architect_credited_anchors`). When ``covers``
    is absent, the deferral is half-applied: the gate knows the capability
    is unverifiable this phase, but the AC/scenario layer does not, so any
    AC asserting that capability gets a scenario that cannot pass at this
    build-phase scope.

    Two kinds:

    - ``INERT_DEFERRAL`` (F): a ``verifiable=false`` primitive lacks a
      ``covers`` key. The forcing function — the author/architect must
      declare which anchors the primitive governs (``[]`` if none) so the
      deferral is symmetric.
    - ``BLOCKING_OVER_DEFERRED`` (E2): an anchor a ``verifiable=false``
      primitive declares it ``covers`` is ALSO in the spec's
      Non-Negotiable (Blocking) set. A Blocking anchor cannot be satisfied
      by a primitive that is unverifiable this phase — re-scope the anchor
      to a later build phase, or make the primitive ``verifiable=true``.

    F surfaces; E2 adjudicates. F alone would only relocate the
    contradiction (covers declared, but still Blocking-over-deferred); the
    pair resolves it.
    """

    kind: str
    primitive: str
    anchor: str | None
    message: str


def check_substrate_deferral_consistency(
    substrate_manifest_path: Path,
    blocking_anchors: set[str],
    live_scenario_anchors: set[str] | None = None,
) -> list[DeferralConsistencyFinding]:
    """F + E2 + E3: detect asymmetric / contradictory ``verifiable=false``
    substrate deferrals in the architect-produced substrate manifest.

    ``blocking_anchors`` is the spec's Non-Negotiable / Blocking anchor-id
    set (e.g. from :func:`~darkfactory.spec.precheck.check_non_negotiable_section`).
    ``live_scenario_anchors`` is the set of anchors the Test Engineer still
    targets for a Tier-2 scenario — i.e. the persisted ``AnchorManifest``'s
    ``ac_ids | ec_ids | fr_ids`` (``@untestable`` / ``vehicle: subprocess``
    anchors are ALREADY excluded from those lists). Pass ``None`` to skip E3.

    Returns one finding per violation (empty → consistent):

    - **F** (``INERT_DEFERRAL``) — a ``verifiable=false`` primitive with no
      ``covers`` key (``covers`` is not a list).
    - **E2** (``BLOCKING_OVER_DEFERRED``) — a ``verifiable=false`` primitive
      whose ``covers`` list names an anchor that is also Blocking. A Blocking
      anchor cannot be satisfied by a deferred primitive (re-scope it).
    - **E3** (``LIVE_SCENARIO_OVER_DEFERRED``) — a ``verifiable=false``
      primitive whose ``covers`` list names an anchor that is STILL a live
      Test-Engineer scenario target. ``covers`` credits the anchor as deferred
      at the floor, but the TE doesn't read ``covers`` — it will write a
      scenario the deferred capability cannot satisfy this phase. This makes
      the framework NON-SILENT on every deferred-but-still-tested anchor, not
      just the Blocking ones: mark the anchor ``@untestable`` /
      forward-reference (excludes it from scenario generation), or drop it from
      ``covers`` if it is not actually deferred.

    E2 and E3 are orthogonal and may BOTH fire for one anchor (Blocking AND
    live): they point at different edits (de-block vs exclude-from-scenarios).

    Scope: ``verifiable=false`` ONLY. ``verifiable=true`` (gate-verified, no
    asymmetry) and ``verifiable`` absent (defaults to verify) are untouched.
    Gated on boolean identity (``is False``), not truthiness — the same
    lesson as :func:`architect_credited_anchors` and the adapter's ``force``
    key (a string sentinel must not route through the wrong branch). Reads
    ONLY the architect-produced substrate manifest; a missing/unparseable
    file yields no findings (the caller's other gates own that failure).
    """
    import tomllib

    try:
        data = tomllib.loads(substrate_manifest_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []

    findings: list[DeferralConsistencyFinding] = []
    for primitive, entries in (data.items() if isinstance(data, dict) else []):
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("verifiable") is not False:
                continue  # scope: verifiable=false only
            label = entry.get("name") or entry.get("tenant_table") or primitive
            covers = entry.get("covers")
            if not isinstance(covers, list):
                findings.append(DeferralConsistencyFinding(
                    kind="INERT_DEFERRAL",
                    primitive=primitive,
                    anchor=None,
                    message=(
                        f"substrate primitive [[{primitive}]] ({label!r}) is "
                        f"verifiable=false but declares no `covers` key. The "
                        f"deferral is asymmetric: gate substrate-verification "
                        f"is skipped, but no spec anchor is credited as "
                        f"deferred at the coverage floor — so any AC/EC "
                        f"asserting this capability gets a scenario that "
                        f"cannot pass at this build-phase scope. Declare "
                        f"`covers = [\"AC-N\", ...]` listing the anchors this "
                        f"primitive governs (`covers = []` if it governs none)."
                    ),
                ))
                continue
            for anchor in covers:
                # A covers entry must be a CANONICAL anchor id, because
                # architect_credited_anchors only credits exact ``FR/AC/EC-\d+``
                # strings (case-sensitive, _CREDIT_ANCHOR_RE). A malformed or
                # non-canonical entry (``14``, ``"AC-014"``, lowercase
                # ``"ac-14"``) silently fails to credit — recreating the inert
                # asymmetry F is meant to eliminate — AND would slip past the E2
                # intersection (the Blocking set is canonical-uppercase). Flag it.
                if not isinstance(anchor, str) or not _CREDIT_ANCHOR_RE.match(anchor):
                    findings.append(DeferralConsistencyFinding(
                        kind="MALFORMED_COVERS",
                        primitive=primitive,
                        anchor=None,
                        message=(
                            f"substrate primitive [[{primitive}]] ({label!r}) is "
                            f"verifiable=false and lists covers entry {anchor!r}, "
                            f"which is not a canonical anchor id (e.g. \"AC-14\", "
                            f"\"EC-5\", \"FR-2\"). The coverage floor credits only "
                            f"canonical ids, so this entry defers nothing — the "
                            f"anchor it was meant to govern still gets an "
                            f"unsatisfiable scenario. Fix the covers entry."
                        ),
                    ))
                    continue
                if anchor in blocking_anchors:
                    findings.append(DeferralConsistencyFinding(
                        kind="BLOCKING_OVER_DEFERRED",
                        primitive=primitive,
                        anchor=anchor,
                        message=(
                            f"{anchor} is in the Non-Negotiable (Blocking) set, "
                            f"but its governing substrate primitive "
                            f"[[{primitive}]] ({label!r}) is verifiable=false — "
                            f"deferred / unverifiable at this build-phase scope. "
                            f"A Blocking anchor cannot be satisfied by a deferred "
                            f"primitive. Resolve the contradiction: re-scope "
                            f"{anchor} to the build phase where this primitive "
                            f"becomes verifiable=true, or drop {anchor} from the "
                            f"Non-Negotiable set if it is not truly blocking at "
                            f"this phase."
                        ),
                    ))
                if live_scenario_anchors is not None and anchor in live_scenario_anchors:
                    findings.append(DeferralConsistencyFinding(
                        kind="LIVE_SCENARIO_OVER_DEFERRED",
                        primitive=primitive,
                        anchor=anchor,
                        message=(
                            f"{anchor} is governed by the verifiable=false "
                            f"(deferred) substrate primitive [[{primitive}]] "
                            f"({label!r}) yet is still a live Test-Engineer "
                            f"scenario target — the TE will write a scenario the "
                            f"deferred capability cannot satisfy at this "
                            f"build-phase scope (no passing scenario exists yet). "
                            f"Mark {anchor} @untestable / forward-reference in "
                            f"the AC table (excludes it from scenario "
                            f"generation), or drop {anchor} from this primitive's "
                            f"covers if it is not actually deferred."
                        ),
                    ))
    return findings


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


# ---------------------------------------------------------------------------
# COVERAGE-GAPS.md parsing and classification (architect-amend flow)
# ---------------------------------------------------------------------------


GAP_CATEGORY_ARCHITECT = "architect-entry-point"
GAP_CATEGORY_SPEC_VEHICLE = "spec-vehicle-mismatch"
GAP_CATEGORY_RUNTIME = "runtime-untestable"
GAP_CATEGORY_MISSING = "category-missing"

_VALID_GAP_CATEGORIES = frozenset({
    GAP_CATEGORY_ARCHITECT,
    GAP_CATEGORY_SPEC_VEHICLE,
    GAP_CATEGORY_RUNTIME,
})

_GAP_HEADING_RE = re.compile(r"^##\s+((?:AC|FR|EC)-\d+)\b", re.IGNORECASE | re.MULTILINE)
_GAP_FIELD_RE = re.compile(
    r"^-\s+\*{0,2}(Category|Reason|Needs)(?:\s+module)?\*{0,2}:\*{0,2}\s+(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# Trailing bold markers on the value ("value**") are an authoring tic rather
# than a semantic difference — strip them before interpreting the field.
_TRAILING_BOLD_RE = re.compile(r"\s*\*{1,2}\s*$")


@dataclass
class CoverageGap:
    """One entry in COVERAGE-GAPS.md as emitted by the test-engineer."""

    anchor_id: str
    reason: str
    category: str | None = None  # None = field missing (TE drift)
    needs: str | None = None     # module path for architect-entry-point


def parse_coverage_gaps_markdown(gaps_md_path: Path) -> list[CoverageGap]:
    """Parse ``COVERAGE-GAPS.md`` into a list of :class:`CoverageGap`.

    Handles ``## AC-N``, ``## FR-N``, ``## EC-N`` headings (with or
    without a trailing colon). Reads ``- **Category:**``, ``- **Reason:**``,
    ``- **Needs:**`` bullet lines (tolerant of missing bold markers).
    Returns ``[]`` on missing file.

    Category is one of the four named constants in this module; anything
    else (including a missing field) becomes ``None`` and the orchestrator
    will classify the gap as ``category-missing`` (no auto-routing).
    """
    if not gaps_md_path.exists():
        return []
    try:
        text = gaps_md_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    # Split into sections keyed by anchor headings. Use finditer so we can
    # slice body text between consecutive heading starts.
    headings = list(_GAP_HEADING_RE.finditer(text))
    if not headings:
        return []

    gaps: list[CoverageGap] = []
    for i, match in enumerate(headings):
        anchor_id = match.group(1).upper()
        body_start = match.end()
        body_end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        body = text[body_start:body_end]

        fields: dict[str, str] = {}
        for fm in _GAP_FIELD_RE.finditer(body):
            key = fm.group(1).lower()
            value = _TRAILING_BOLD_RE.sub("", fm.group(2).strip())
            # First wins (in case TE duplicates a field).
            fields.setdefault(key, value)

        category_raw = fields.get("category")
        category: str | None = None
        if category_raw:
            # Tolerant matching: normalise whitespace / case.
            normalised = category_raw.strip().lower()
            if normalised in _VALID_GAP_CATEGORIES:
                category = normalised

        gaps.append(CoverageGap(
            anchor_id=anchor_id,
            reason=fields.get("reason", ""),
            category=category,
            needs=fields.get("needs") or None,
        ))
    return gaps


def classify_gaps(
    gaps: list[CoverageGap],
    uncovered_anchors: list[str],
) -> dict[str, list[CoverageGap]]:
    """Partition gaps into buckets, scoped to anchors that block the floor.

    Returns a dict with four keys (always present, possibly empty):
      - ``architect-entry-point`` — orchestrator will invoke the architect
      - ``spec-vehicle-mismatch`` — orchestrator surfaces for human fix
      - ``runtime-untestable`` — orchestrator proceeds (TE should waive)
      - ``category-missing`` — orchestrator emits a drift warning

    Only gaps whose ``anchor_id`` appears in ``uncovered_anchors`` are
    included — gaps the TE logged but the floor report doesn't care about
    (e.g., already waived or duplicate-tagged) are dropped.
    """
    uncovered_set = {a.upper() for a in uncovered_anchors}
    buckets: dict[str, list[CoverageGap]] = {
        GAP_CATEGORY_ARCHITECT: [],
        GAP_CATEGORY_SPEC_VEHICLE: [],
        GAP_CATEGORY_RUNTIME: [],
        GAP_CATEGORY_MISSING: [],
    }
    for gap in gaps:
        if gap.anchor_id not in uncovered_set:
            continue
        if gap.category is None:
            buckets[GAP_CATEGORY_MISSING].append(gap)
        elif gap.category in _VALID_GAP_CATEGORIES:
            buckets[gap.category].append(gap)
        else:
            # Unreachable — parse sets category to None for invalid values.
            buckets[GAP_CATEGORY_MISSING].append(gap)
    return buckets


# The fixed, ecosystem-blind menu of verification vehicles a spec author can
# declare to satisfy the coverage floor for an anchor a Tier-2 scenario cannot
# cover. Generic on purpose — these are framework-level spec signals, not
# per-stack knowledge — so the same menu serves every ecosystem (breadth) and
# nothing here is curated against any one spec (non-curation). The author reads
# each anchor's TE reason (shown as context) and picks; the framework never
# guesses the vehicle from the TE's free-text prose (that mis-routes).
# Ordered by VERIFICATION IMPACT (Neo Phase-1: the menu must not present these as
# equivalent — they sit on a trust ladder from preserving to waiving).
_FLOOR_VEHICLE_MENU = (
    "  [PRESERVES verification]",
    "  1. `vehicle: runtime-boundary` — ONLY if the unit-under-test is a "
    "runtime/framework-injected boundary with NO importable project module "
    "(e.g. an injected global like `import.meta.env`). This does NOT waive "
    "verification: the anchor STILL requires a passing behavioral scenario and a "
    "blind holdout — it waives ONLY the behavioral-credit import requirement.",
    "  [PRESERVES verification ONLY if gated this phase]",
    "  2. Substrate `covers` — if a substrate primitive (a GUC / RLS policy / "
    "migration the architect declares in `## Substrate Primitives`) verifies "
    "this anchor, add `covers = [\"<anchor>\"]` to that primitive. Using "
    "`verifiable = false` (object exists only in a later build phase) DEFERS "
    "verification — it is not verified now.",
    "  [REDUCES verification — drops the blind-holdout net]",
    "  3. `vehicle: subprocess` — if it is a CLI / startup / connection-failure "
    "behavior, set the Vehicle column to `subprocess` AND add a Tier-1 command "
    "contract. The anchor is REMOVED from scenario+holdout scoring and verified "
    "only by that one visible contract, so the contract MUST assert a behavioral "
    "outcome (a nonzero exit on a bad input, or a literal string in a file the "
    "command produces) and NAME the anchor in `covers: [\"<anchor>\"]` — a "
    "vacuous contract is a hard validation failure (FATAL).",
    "  [WAIVES verification]",
    "  4. `@untestable` — if it is enforced by a non-scenario gate phase "
    "(e.g. the type-checker or a forbidden-pattern scanner) or is not "
    "observable from the running app, mark the anchor `@untestable` with a "
    "reason naming the enforcing vehicle.",
    "  5. Re-scope — if the anchor is a documentation/guidance constraint with "
    "no observable predicate, rewrite the AC to assert an observable behavior.",
)


_H2_RE = re.compile(r"^##\s+(.*)$")
_ANY_HEADING_RE = re.compile(r"^#{1,6}\s")
_ANCHOR_ROW_RE = re.compile(r"^\|\s*((?:AC|EC|FR)-\d+)\s*\|(.+)$", re.IGNORECASE)
_FR_HEADING_RE = re.compile(r"^#{2,4}\s*(FR-\d+)\b:?\s*(.*)$", re.IGNORECASE)
# H2 section names (lowercased substring match) where anchor DEFINITIONS live.
# Scoping to these prevents picking an anchor id out of a Scenario-Intent /
# holdout table that happens to appear earlier (Codex review: first-match on a
# non-definition table would feed the classifier the wrong text).
_AC_SECTION_HINTS = ("acceptance criteria", "edge case", "error handling")
_FR_SECTION_HINTS = ("functional requirement",)


def extract_anchor_texts(spec_text: str, anchors: list[str]) -> dict[str, str]:
    """Map each requested anchor id → its spec text (the AC/EC table-row criterion
    or the FR heading + first body line). Used to feed the Phase-1 vehicle
    classifier the anchor's ACTUAL description, not just the (often-absent) TE gap
    reason — the in-situ floor abort revealed the gap-reason-only input is too thin
    (anchor-id-only → the classifier rightly ABSTAINs).

    Section-SCOPED: anchor rows are read only under an Acceptance-Criteria /
    Edge-Case / Error-Handling H2, and FR headings only under a Functional-
    Requirements H2 — so an anchor id appearing first in some other table is not
    mis-extracted. Best-effort + dependency-light; a missing/out-of-section anchor
    simply has no entry (→ the classifier ABSTAINs → static menu, safe)."""
    if not isinstance(spec_text, str):
        return {}
    want = {str(a).strip().upper() for a in (anchors or []) if a is not None}
    if not want:
        return {}
    out: dict[str, str] = {}
    lines = spec_text.splitlines()
    section = ""
    for i, line in enumerate(lines):
        h2 = _H2_RE.match(line)
        if h2:
            section = h2.group(1).strip().lower()
        in_ac = any(h in section for h in _AC_SECTION_HINTS)
        in_fr = any(h in section for h in _FR_SECTION_HINTS)
        if in_ac:
            row = _ANCHOR_ROW_RE.match(line)
            if row:
                aid = row.group(1).upper()
                if aid in want and aid not in out:
                    cells = [c.strip() for c in row.group(2).split("|") if c.strip()]
                    # AC: the criterion is always cell 0 (the Blocking/Priority
                    # columns follow). EC tables VARY — `| Condition | [Severity] |
                    # Expected Behavior |` — and the expected-behavior column is
                    # the vehicle disambiguator, so join ALL cells rather than
                    # assume its position (Codex review: a positional cells[1]
                    # grabs an intervening column on a 4-column EC table).
                    if aid.startswith("EC-"):
                        text = " — ".join(cells)
                    else:
                        text = cells[0] if cells else ""
                    if text:
                        out[aid] = text
        elif in_fr:
            head = _FR_HEADING_RE.match(line)
            if head:
                aid = head.group(1).upper()
                if aid in want and aid not in out:
                    name = head.group(2).strip()
                    body = ""
                    for j in range(i + 1, min(i + 6, len(lines))):
                        if _ANY_HEADING_RE.match(lines[j]):
                            break  # stop at the next heading — don't bleed sections
                        s = lines[j].strip()
                        if s and not s.startswith("|"):
                            body = s
                            break
                    text = f"{name} {body}".strip()
                    if text:
                        out[aid] = text
    return out


def _render_subprocess_template(anchor: str) -> list[str]:
    """A STRUCTURAL fill-in template for a subprocess command contract (not a
    concrete drafted command — that source-grounded draft is Phase 2). Keyed to
    the anchor so the author fills specifics from source; satisfies Phase-0
    (behavioral `then:`, exact `covers`)."""
    return [
        "      add to the Acceptance Criteria section:",
        "      ```yaml",
        "      contracts:",
        "        - id: <unique-id>",
        "          fr: <the parent FR, e.g. FR-1>",
        "          type: command",
        f"          given: <{anchor}'s precondition>",
        "          when: { command: <the CLI/startup command it describes> }",
        "          then: { exit_code: <nonzero if it is a failure case>, "
        "file: <output file>, pattern: \"<a literal string its outcome "
        "produces>\" }",
        f"          covers: [{anchor}]",
        "      ```",
    ]


def render_floor_remediation(
    gaps: list[CoverageGap],
    uncovered_anchors: list[str],
    adapters: "list[EcosystemAdapter] | None",
    suggestions: "dict[str, VehicleSuggestion] | None" = None,
) -> str:
    """Render an ACTIONABLE coverage-floor remediation block for the abort.

    Replaces the bare ``uncovered: AC-1, AC-2, ...`` list at the
    ``COVERAGE_FLOOR_VIOLATION`` abort with, per surviving anchor: the anchor id
    + the test-engineer's gap REASON as context + a fixed, ecosystem-blind menu
    of the spec vehicles the author can declare. Returns ``""`` when there is
    nothing to say (no uncovered anchors, or every one is architect-owned).

    Why no per-anchor auto-routing: the only per-anchor evidence available here
    is the barrier'd TE's free-text reason. Regex-matching that per-run LLM prose
    to pick a vehicle is a curation treadmill that never abstains and mis-routes
    on incidental keywords; the structured signals that COULD route (spec
    subprocess tags, architect ``covers``) are empty at the abort by
    construction — a survivor is precisely an anchor no structured signal
    claimed. So the framework surfaces the evidence + the menu and lets the
    AUTHOR map. An adapter MAY override ``classify_floor_anchor`` to contribute a
    genuine STRUCTURED hint; if it returns one it is rendered as subordinate
    context, never as the authority. (PostgresAdapter inherits the no-op.)

    Barrier note: this consumes the barrier-restricted TE's free-text reason to
    render advice TO A HUMAN OPERATOR. It grants no credit and changes no gate,
    so it crosses no information barrier (unlike the credit path, which must
    refuse the TE's classification).

    The ``architect-entry-point`` bucket is intentionally EXCLUDED — Part C's
    coverage_amend handles those automatically; re-listing them would tell the
    author to act on something the framework already retried.
    """
    uncovered = [a.upper() for a in uncovered_anchors]
    buckets = classify_gaps(gaps, uncovered)
    architect_owned = {g.anchor_id for g in buckets[GAP_CATEGORY_ARCHITECT]}

    # Index the TE's gap reason by anchor (every bucket except architect-entry-
    # point). A gap the TE never logged simply has no reason to show.
    gap_by_anchor: dict[str, CoverageGap] = {}
    for key, bucket in buckets.items():
        if key == GAP_CATEGORY_ARCHITECT:
            continue
        for gap in bucket:
            gap_by_anchor.setdefault(gap.anchor_id, gap)

    actionable = [a for a in uncovered if a not in architect_owned]
    if not actionable:
        return ""

    # Natural-numeric order so AC-10 follows AC-9, not AC-1, in the human-facing
    # abort (lexicographic would read wrong on a 30-anchor spec).
    def _natural_key(anchor: str) -> list:
        return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", anchor)]

    suggestions = suggestions or {}
    # Only surface the per-anchor "no suggestion" note when at least one anchor
    # DID get a real suggestion (so the author can tell which to use the menu
    # for). If every anchor abstained (e.g. `claude` absent), suppress the notes
    # entirely so the abort is identical to the pre-Phase-1 menu (Linus C2).
    any_real_suggestion = any(
        s is not None and not s.abstained for s in suggestions.values()
    )
    lines: list[str] = [
        "These anchors block the coverage floor and CANNOT be covered by a "
        "Tier-2 scenario as the spec is currently written. For EACH, read its "
        "reason and declare ONE of the vehicles below in the spec (the pipeline "
        "will not decide scope for you):",
        "",
        *_FLOOR_VEHICLE_MENU,
        "",
        "Anchors (with the test-engineer's reason as context):",
    ]
    for anchor in sorted(actionable, key=_natural_key):
        gap = gap_by_anchor.get(anchor)
        reason = (gap.reason or "").strip() if gap else ""
        lines.append(f"  - {anchor}: {reason or '(no reason logged)'}")
        # Phase-1 vehicle SUGGESTION (advisory; the author confirms + applies).
        # ABSTAIN / no-suggestion anchors fall back to the menu above.
        sug = suggestions.get(anchor)
        if sug is not None and not sug.abstained:
            _caption = VEHICLE_TIERS.get(sug.vehicle, (sug.tier, ""))[1]
            lines.append(
                f"      SUGGESTED vehicle: `{sug.vehicle}` ({sug.tier} — "
                f"{_caption}). {sug.reason}".rstrip()
            )
            lines.append(
                "      Advisory only — confirm before applying; the pipeline "
                "grants no credit for this."
            )
            if sug.vehicle == "subprocess":
                lines.extend(_render_subprocess_template(anchor))
        elif sug is not None and sug.abstained and any_real_suggestion:
            lines.append(
                "      (no confident vehicle suggestion — choose from the menu "
                "above)"
            )
        # Optional adapter-contributed STRUCTURED hint (no-op for current
        # adapters). Rendered as clearly-subordinate context, never authority.
        if gap is not None:
            for adapter in adapters or []:
                hint = adapter.classify_floor_anchor(gap)
                if hint is not None:
                    lines.append(f"      hint ({hint.kind}): {hint.signal_summary}")
                    break
    return "\n".join(lines)
