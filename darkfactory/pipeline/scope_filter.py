"""Phase 4.6c: locked-denominator enforcement for blind scenarios.

When the Scenario Architect generates blind tests, it can:
  (a) generate a test with no FR/AC/EC anchor at all (purely SA-invented
      adversarial),
  (b) tag a test with an anchor ID that is not in the spec's locked
      AnchorManifest (anchor invented or renumbered out of band), or
  (c) tag a test with a real anchor ID but assert behavior on a literal
      input that the spec never specified (e.g. AC-8 mislabeled on a
      `parseAmount("$,123")` test where AC-8's actual predicate is the
      total-function property over arbitrary unicode).

All three cases produce failing scenarios that the Oracle should not weigh
as ship-blockers, because the failure is not against any committed
acceptance criterion. Today, the Oracle weighs them anyway, which is the
defect Phase 4.5a uncovered (see
``docs/pipeline-run-reports/2026-04-08-23-20-budget-brain-v2-shared-parse-amount.md``).

This module exposes two pure functions:

- ``partition_failing_by_scope``: filter a list of failing test names against
  the spec's locked AnchorManifest. Tests whose anchor tag is not in the
  manifest, or which carry no anchor at all, are reclassified out-of-scope.
  This is rule 4.6c.1.

- ``check_literal_input_membership``: for tests that survive 4.6c.1, scan
  the test body for string literals passed to the subject function and
  verify each literal appears somewhere in the spec markdown. Literals not
  in the spec mean the SA invented an input the spec never specified.
  This is rule 4.6c.2.

Both functions are pure (project_dir is read-only). They are called from
``_step_gate`` after the gate has run and before the floor check fires, so
the recomputed in-scope counts can flow into the floor check naturally.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.spec.anchors import AnchorManifest

log = logging.getLogger(__name__)


# Match [AC-8], [FR-3], [EC-11] inside a test name. Same shape as
# scenarios/coverage.py's _ANCHOR_RE but tightened to require the brackets
# (test names use [AC-N], not bare AC-N which would also match prose).
_TAG_RE = re.compile(r"\[(FR|AC|EC)-(\d+)\]", re.IGNORECASE)

# Match a string literal arg passed to a single-arg call of the subject
# function. We don't know the subject function name a priori, but we
# heuristically pick the most-called identifier in the test file (see
# _detect_subject) and then look for `<subject>("...")` or `<subject>('...')`.
# Captures the literal contents (without the surrounding quotes).
_STRING_LITERAL_ARG_RE_TEMPLATE = (
    r"\b{subject}\s*\(\s*"
    r"(?:\"((?:[^\"\\]|\\.)*)\""
    r"|'((?:[^'\\]|\\.)*)'"
    r"|`((?:[^`\\]|\\.)*)`)"
    r"\s*\)"
)


def _normalise(kind: str, num: str) -> str:
    return f"{kind.upper()}-{int(num)}"


@dataclass
class OutOfScopeFailure:
    """A failing test that the scope filter is removing from the denominator."""
    test_name: str
    reason: str   # "no_anchor" | "unknown_anchor:<id>" | "unspecified_input:<literal>"


@dataclass
class ScopePartition:
    """Result of partitioning failing tests by anchor scope."""
    in_scope_failing: list[str] = field(default_factory=list)
    out_of_scope_failing: list[OutOfScopeFailure] = field(default_factory=list)

    def has_changes(self) -> bool:
        return len(self.out_of_scope_failing) > 0


def _extract_tags(test_name: str) -> set[str]:
    """Pull [AC-N]/[FR-N]/[EC-N] tags from a test name. Empty set = no anchor."""
    return {
        _normalise(m.group(1), m.group(2))
        for m in _TAG_RE.finditer(test_name)
    }


def _manifest_anchor_set(manifest: AnchorManifest) -> set[str]:
    """All anchor IDs declared in the spec, as one set for membership checks."""
    out: set[str] = set()
    for ids in (manifest.fr_ids, manifest.ac_ids, manifest.ec_ids):
        for raw in ids:
            # Manifests may store either "AC-1" or "AC-01"; normalise.
            m = re.match(r"(FR|AC|EC)-(\d+)", raw, re.IGNORECASE)
            if m:
                out.add(_normalise(m.group(1), m.group(2)))
    return out


def partition_failing_by_scope(
    failing_test_names: list[str],
    manifest: AnchorManifest,
) -> ScopePartition:
    """Rule 4.6c.1: partition failing tests by AnchorManifest membership.

    A failing test is in-scope iff it carries at least one [FR-N]/[AC-N]/
    [EC-N] tag AND every such tag is present in the manifest. Tests with
    no anchor tag at all are out-of-scope (SA-invented adversarial). Tests
    whose tag is not in the manifest are out-of-scope (anchor invented or
    renumbered out of band).
    """
    partition = ScopePartition()
    declared = _manifest_anchor_set(manifest)

    for name in failing_test_names:
        tags = _extract_tags(name)
        if not tags:
            partition.out_of_scope_failing.append(
                OutOfScopeFailure(test_name=name, reason="no_anchor")
            )
            continue
        unknown = tags - declared
        if unknown:
            partition.out_of_scope_failing.append(
                OutOfScopeFailure(
                    test_name=name,
                    reason=f"unknown_anchor:{','.join(sorted(unknown))}",
                )
            )
            continue
        partition.in_scope_failing.append(name)

    return partition


# ---------------------------------------------------------------------------
# Rule 4.6c.2: literal-input membership check
# ---------------------------------------------------------------------------


def _detect_subject(scenario_files: list[Path]) -> str | None:
    """Pick the most-called identifier across the scenario test files.

    The Scenario Architect tests a single subject function per spec. We
    detect it by finding the identifier most frequently appearing as a
    function call. Heuristic but robust for pure-function specs.
    """
    counts: dict[str, int] = {}
    call_re = re.compile(r"\b([a-zA-Z_$][a-zA-Z0-9_$]*)\s*\(")
    skip = {
        "describe", "it", "test", "expect", "beforeAll", "beforeEach",
        "afterAll", "afterEach", "vi", "fn", "Array", "String", "Number",
        "Object", "JSON", "Math", "Date", "console", "require", "import",
        "if", "for", "while", "return", "function", "constructor",
        "toBe", "toEqual", "toThrow", "not", "Boolean", "Set", "Map",
    }
    for path in scenario_files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in call_re.finditer(text):
            ident = m.group(1)
            if ident in skip or ident[0].isupper():
                continue
            counts[ident] = counts.get(ident, 0) + 1

    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _extract_test_blocks(text: str) -> list[tuple[str, str]]:
    """Yield (test_name, block_body) tuples from a test file.

    A "block" is the body of an `it("name", () => { ... })` call. We use
    a brace-balanced scanner because regex can't reliably match nested
    braces. Test names with closing-paren or escaped quotes are handled
    by skipping into the literal then scanning braces.
    """
    blocks: list[tuple[str, str]] = []
    i = 0
    n = len(text)
    it_call = re.compile(r"\bit\s*\(\s*(['\"`])")

    while i < n:
        m = it_call.search(text, i)
        if not m:
            break
        quote = m.group(1)
        # Find closing quote of test name
        j = m.end()
        while j < n:
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == quote:
                break
            j += 1
        if j >= n:
            break
        test_name = text[m.end():j]
        # Find opening brace of the callback
        k = j + 1
        while k < n and text[k] != "{":
            k += 1
        if k >= n:
            i = j + 1
            continue
        # Brace-balanced scan
        depth = 1
        k += 1
        body_start = k
        while k < n and depth > 0:
            ch = text[k]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch in "'\"`":
                # Skip string literal
                qc = ch
                k += 1
                while k < n:
                    if text[k] == "\\":
                        k += 2
                        continue
                    if text[k] == qc:
                        break
                    k += 1
            k += 1
        if depth == 0:
            blocks.append((test_name, text[body_start:k - 1]))
        i = k

    return blocks


def _extract_literal_args(block_text: str, subject: str) -> list[str]:
    """Find all string literals passed as the sole arg to `subject(...)`."""
    pattern = re.compile(_STRING_LITERAL_ARG_RE_TEMPLATE.format(subject=re.escape(subject)))
    out: list[str] = []
    for m in pattern.finditer(block_text):
        literal = m.group(1) or m.group(2) or m.group(3) or ""
        out.append(literal)
    return out


def _spec_literal_set(spec_text: str) -> set[str]:
    """Extract every string literal that appears in the spec markdown.

    The spec uses backtick-quoted code spans for literal inputs (e.g.
    `` `"$1,234.56"` ``, `` `"1.234"` ``). We capture the contents of
    backtick spans whose content looks like a quoted string literal,
    then strip the surrounding quotes.
    """
    out: set[str] = set()
    code_span_re = re.compile(r"`([^`]+)`")
    # Inside a code span, extract all `"..."` and `'...'` literals. The
    # spec uses both `"foo"` (single literal in span) and `raw === "foo"`
    # (literal embedded in expression), so we always scan the span body
    # for embedded quoted strings rather than requiring the whole span
    # to be a single literal.
    embedded_quoted_re = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'")
    for m in code_span_re.finditer(spec_text):
        content = m.group(1).strip()
        embedded = list(embedded_quoted_re.finditer(content))
        if embedded:
            for em in embedded:
                literal = em.group(1) if em.group(1) is not None else em.group(2)
                # Decode common escapes that the spec writes literally:
                # \u0000 → null byte, \n → newline, \\ → \. The test
                # subject sees the runtime string, not the escaped form.
                try:
                    decoded = literal.encode("utf-8").decode("unicode_escape")
                except UnicodeDecodeError:
                    decoded = literal
                out.add(decoded)
                # Also keep the un-decoded form so tests using the literal
                # source representation still match.
                out.add(literal)
            continue
        # Bare-content form: `1,234.56` (no quotes inside backticks).
        # Skip parens / pipes / equals / brackets which are markdown noise.
        if content and not any(c in content for c in "(|=[<>"):
            out.add(content)
    return out


def check_literal_input_membership(
    in_scope_test_names: list[str],
    spec_text: str,
    scenarios_dir: Path,
) -> ScopePartition:
    """Rule 4.6c.2: reclassify tests asserting on unspecified literal inputs.

    For each in-scope test, find every string literal passed to the subject
    function in its body and verify each literal appears in the spec
    markdown's literal set. If any literal is missing AND the test is not
    a property-style test (>3 distinct literals = property exempt), the
    test is reclassified out-of-scope.
    """
    partition = ScopePartition()

    if not scenarios_dir.exists():
        partition.in_scope_failing = list(in_scope_test_names)
        return partition

    scenario_files = sorted(scenarios_dir.glob("*.test.ts")) + sorted(
        scenarios_dir.glob("*.test.js")
    )
    if not scenario_files:
        partition.in_scope_failing = list(in_scope_test_names)
        return partition

    subject = _detect_subject(scenario_files)
    if not subject:
        partition.in_scope_failing = list(in_scope_test_names)
        return partition

    spec_literals = _spec_literal_set(spec_text)

    # Build a name → block_body index across all scenario files.
    name_to_body: dict[str, str] = {}
    for path in scenario_files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for name, body in _extract_test_blocks(text):
            name_to_body[name] = body

    in_scope_set = set(in_scope_test_names)

    for name in in_scope_test_names:
        body = name_to_body.get(name)
        if body is None:
            partition.in_scope_failing.append(name)
            continue
        literals = _extract_literal_args(body, subject)
        if not literals:
            partition.in_scope_failing.append(name)
            continue
        # Property-style exemption: >3 distinct literals = exploring an
        # input space, not asserting on a specific case.
        if len(set(literals)) > 3:
            partition.in_scope_failing.append(name)
            continue
        missing = [lit for lit in literals if lit not in spec_literals]
        if missing:
            partition.out_of_scope_failing.append(
                OutOfScopeFailure(
                    test_name=name,
                    reason=f"unspecified_input:{missing[0]!r}",
                )
            )
            continue
        partition.in_scope_failing.append(name)

    return partition


def merge_partitions(*partitions: ScopePartition) -> ScopePartition:
    """Compose multiple scope partitions sequentially.

    Used to apply 4.6c.1 then 4.6c.2 in order. The in_scope_failing of the
    last partition is the final in-scope set; out_of_scope_failing
    accumulates across all partitions.
    """
    if not partitions:
        return ScopePartition()
    final = ScopePartition()
    final.in_scope_failing = list(partitions[-1].in_scope_failing)
    for p in partitions:
        final.out_of_scope_failing.extend(p.out_of_scope_failing)
    return final
