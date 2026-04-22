"""Post-classifier hint application.

Runs after the defect-classifier agent has produced a validated
``<spec>-defects-iter<N>.json``, before the Oracle consumes it. For
each defect whose scenario describe-chain matches an adapter-contributed
``ClassificationHint`` pattern (against the describe's *exclusive* body
only — not nested sub-describes), the defect's ``category`` is
overridden and an ``internal_reason_code`` is stamped onto the record.

**Barrier discipline (Task 40):** the ``internal_reason_code`` field is
written ONLY to ``defects.json`` (path: ``.dark-factory/reports/``,
which the Implementer container masks as empty tmpfs). The iteration
feedback renderer in ``pipeline.iteration._format_defects_inline`` pulls
``category`` / ``cited_anchor_ids`` / ``test_name`` / ``rationale`` by
name — ``internal_reason_code`` is deliberately NOT in that list, so
adapter-owned pattern-match vocabulary cannot leak to the Implementer.

**Defect → describe-chain linkage (post-Linus pass 2 rescope):**
defects.json records carry ``test_name`` but no file path. We parse each
scenario file once with a unified lexer+tokenizer that tracks string
literals, line comments, and block comments, so ``describe('fake', ...)``
inside a comment or string is NOT harvested. The tokenizer builds a tree
of nested describes with **full chain paths** (``outer > inner``) and,
for each node, returns its *exclusive* body — the parent's text MINUS
the spans of its nested children. Hint regexes run against each
exclusive body independently. A matching body registers its full chain
path as a prefix; a defect whose ``test_name.startswith(path + " > ")``
is then reclassified. The ``+ " > "`` guard prevents ``outer``'s match
from bleeding across sibling inner describes.

**Known limitation:** the tokenizer does NOT disambiguate regex
literals from division operators. A ``/a}b/`` inside a describe body
has an *unbalanced* close-brace that confuses the brace-depth walker.
The failure mode is cross-describe corruption: the file-level stack
ends non-empty at EOF because some describe's ``}`` was miscounted.
When that happens we drop the ENTIRE file's describe map (return ``[]``
from the tokenizer) rather than publish partially-attributed nodes.
This is a silent miss (no hints fire for that file, even ones that
would otherwise match cleanly), not a silent leak — false negatives
are the acceptable direction; false positives would misclassify
real defects. Regex literals with balanced braces (``/[0-9]{36}/``)
happen to work by accident and are pinned as such. See
``tests/unit/test_classification_hints.py`` for the three pinned
shapes: ``test_regex_literal_with_balanced_braces_works_accidentally``,
``test_regex_literal_with_unbalanced_brace_is_known_limitation``, and
``test_unbalanced_regex_drops_entire_file_not_just_its_describe``.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path

from darkfactory.adapters.base import ClassificationHint, EcosystemAdapter

log = logging.getLogger(__name__)


_TS_TEST_SUFFIXES: tuple[str, ...] = (
    ".test.ts",
    ".test.tsx",
    ".test.js",
    ".test.mjs",
    ".test.cjs",
    ".spec.ts",
    ".spec.tsx",
    ".spec.js",
)

# Length of the literal "describe" keyword — cached to avoid recomputing
# inside the hot path of the tokenizer.
_DESCRIBE_KW = "describe"
_DESCRIBE_LEN = len(_DESCRIBE_KW)


def apply_classification_hints(
    defects_path: Path,
    scenarios_dir: Path,
    adapters: Iterable[EcosystemAdapter],
) -> int:
    """Mutate ``defects_path`` in place; return the number of defects updated.

    No-op conditions (return 0 without touching the file):
    - ``defects_path`` missing / unreadable / unparseable JSON.
    - JSON doc carries a non-empty ``error`` field (classifier pipeline
      error; Oracle will NO-SHIP on the error anyway).
    - Empty ``defects`` array.
    - No adapter contributes any hints.
    - ``scenarios_dir`` does not exist (logged at WARNING since the
      expected directory being gone is an operational anomaly, not a
      design no-op like "no adapters contributed hints").

    First-match-wins across the concatenation of all adapters' hints,
    preserving adapter iteration order then per-adapter list order. A
    single defect is reclassified at most once per call, and defects
    that already carry an ``internal_reason_code`` (from a prior call
    or a future adapter that writes them at classification time) are
    skipped — idempotent across repeated invocations.
    """
    try:
        doc = json.loads(defects_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(doc, dict):
        return 0
    if doc.get("error"):
        return 0
    defects = doc.get("defects")
    if not isinstance(defects, list) or not defects:
        return 0

    all_hints: list[ClassificationHint] = []
    for adapter in adapters:
        all_hints.extend(adapter.classification_hints())
    if not all_hints:
        return 0

    scenarios_dir = scenarios_dir.resolve()
    if not scenarios_dir.is_dir():
        log.warning(
            "classification-hint apply: scenarios_dir %s does not exist; "
            "hints not applied this iteration",
            scenarios_dir,
        )
        return 0

    compiled_hints: list[tuple[re.Pattern[str], ClassificationHint, set[str]]] = [
        (re.compile(h.pattern), h, set()) for h in all_hints
    ]

    for scenario_file in _iter_scenario_files(scenarios_dir):
        try:
            text = scenario_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for chain_path, excl_body in _extract_describe_bodies(text):
            for pattern, _hint, paths in compiled_hints:
                if pattern.search(excl_body):
                    paths.add(chain_path)

    updated = 0
    for defect in defects:
        if not isinstance(defect, dict):
            continue
        # Idempotency: skip defects that already carry a reason code.
        # Makes repeated apply() calls safe across retries, resumes, and
        # future adapters that stamp codes at classification time.
        if defect.get("internal_reason_code"):
            continue
        test_name = defect.get("test_name") or ""
        if not isinstance(test_name, str) or not test_name:
            continue
        for _pattern, hint, paths in compiled_hints:
            # Defects always come from it() blocks, whose vitest-
            # concatenated test_name has form ``describe > ... > it``.
            # The ``+ " > "`` guard is essential — without the separator,
            # path ``outer`` would bleed into sibling ``outer2``.
            if any(test_name.startswith(p + " > ") for p in paths):
                prior_category = defect.get("category")
                defect["category"] = hint.category_override
                defect["internal_reason_code"] = hint.internal_reason_code
                updated += 1
                log.info(
                    "classification-hint applied: %s %s -> %s (%s)",
                    test_name[:80],
                    prior_category,
                    hint.category_override,
                    hint.internal_reason_code,
                )
                break

    if updated:
        defects_path.write_text(
            json.dumps(doc, indent=2), encoding="utf-8",
        )
    return updated


def _iter_scenario_files(scenarios_dir: Path) -> list[Path]:
    """Yield TS/JS test files under ``scenarios_dir`` with containment check.

    Excludes ``_contracts/`` (adapter-staged helpers, not scenarios) and
    any path that escapes ``scenarios_dir`` via symlink — the resolve +
    ``relative_to`` guard mirrors the pattern used in
    ``adapters/typescript/type_only_exports.py`` (Task 42 post-Linus
    hardening) so adapter-controlled inputs can't read arbitrary host
    filesystem paths.
    """
    results: list[Path] = []
    for p in scenarios_dir.rglob("*"):
        if not p.is_file():
            continue
        if not p.name.endswith(_TS_TEST_SUFFIXES):
            continue
        if "_contracts" in p.parts:
            continue
        try:
            p.resolve().relative_to(scenarios_dir)
        except ValueError:
            continue
        results.append(p)
    return results


def _extract_describe_bodies(text: str) -> list[tuple[str, str]]:
    """Return ``[(chain_path, exclusive_body), ...]`` via single-pass lexer.

    The lexer walks the text once, tracking:

    * String literals (``' " ` ``) with backslash escapes. Template-
      literal ``${...}`` interpolations are NOT recursed into — template
      literals are opaque to this pass. A ``describe(...)`` inside a
      template-literal *body* is therefore ignored (safe direction).
    * Line comments (``//``) and block comments (``/* */``).
    * Brace depth. When depth returns to the level it held when a
      describe was opened, that describe's body span closes.

    The ``describe`` keyword is recognized only in "code state" (not in
    string / comment state) AND only at word boundaries — an identifier
    ``fooDescribe`` or ``describeX`` is not a match. The callback body
    starts at the first ``{`` following the name string.

    **Chain paths** concatenate ancestor names with ``" > "`` — matching
    vitest's test-name concatenation convention. A defect whose
    ``test_name`` is ``"outer > inner > it_name"`` has chain prefix
    ``"outer > inner"``; registering the outer-only path ``"outer"``
    would incorrectly match siblings of ``inner``, so the caller uses
    ``test_name.startswith(path + " > ")`` semantics.

    **Exclusive bodies:** each returned body is the describe's own span
    with the spans of its immediate nested describes removed. A hint
    pattern that matches inside a nested describe therefore only
    attributes to the nested describe's chain, never to the outer's.

    **Regex-literal limitation:** a ``}`` inside a regex literal can
    prematurely close a describe body. Documented + pinned via
    ``test_regex_literal_is_known_limitation``. Silent miss, not leak.
    """
    nodes = _tokenize_describe_tree(text)
    # Compute exclusive body for each node: text[start:end] minus the
    # spans of immediate children. Immediate = child node whose chain
    # path has exactly one more `>` segment than this node's.
    results: list[tuple[str, str]] = []
    for path, start, end in nodes:
        depth = path.count(" > ")
        children: list[tuple[int, int]] = []
        for cpath, cstart, cend in nodes:
            if cstart <= start or cend >= end:
                continue
            if not cpath.startswith(path + " > "):
                continue
            if cpath.count(" > ") != depth + 1:
                continue
            children.append((cstart, cend))
        children.sort()
        parts: list[str] = []
        cursor = start
        for cstart, cend in children:
            parts.append(text[cursor:cstart])
            cursor = cend
        parts.append(text[cursor:end])
        results.append((path, "".join(parts)))
    return results


def _tokenize_describe_tree(text: str) -> list[tuple[str, int, int]]:
    """Single-pass lexer. Returns ``[(chain_path, body_start, body_end)]``.

    ``body_start`` is the character index AFTER the opening ``{`` of the
    describe's callback body; ``body_end`` is the character index OF the
    closing ``}`` (exclusive upper bound — ``text[body_start:body_end]``
    is the body text). Describes whose callback body can't be matched
    (unbalanced braces through EOF) are silently dropped.
    """
    n = len(text)
    results: list[tuple[str, int, int]] = []
    # Each stack entry: (chain_path, body_start, brace_depth_at_entry).
    stack: list[tuple[str, int, int]] = []
    i = 0
    in_str: str | None = None
    in_line_comment = False
    in_block_comment = False
    brace_depth = 0

    while i < n:
        c = text[i]

        # Comment states short-circuit everything else.
        if in_line_comment:
            if c == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if c == "*" and i + 1 < n and text[i + 1] == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        # String state: only look for the terminating quote (or escape).
        if in_str is not None:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue

        # --- Code state below ---

        # Comment openers.
        if c == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                in_line_comment = True
                i += 2
                continue
            if nxt == "*":
                in_block_comment = True
                i += 2
                continue

        # String openers.
        if c == "'" or c == '"' or c == "`":
            in_str = c
            i += 1
            continue

        # describe(...) recognition — word-boundaried, code-state-only.
        # Identifier-continuation guard rejects both prefix matches
        # (``$describe``, ``_describe``) and suffix matches
        # (``describeX``, ``describe$``, ``describe_``). JS identifiers
        # allow ``$`` as a start/continue char, so ``$`` must appear in
        # both the pre- and post-guards.
        if (
            c == "d"
            and text.startswith(_DESCRIBE_KW, i)
            and (
                i == 0
                or not (
                    text[i - 1].isalnum() or text[i - 1] in ("_", "$")
                )
            )
        ):
            j = i + _DESCRIBE_LEN
            # Guard: next char must NOT continue the identifier.
            if j < n and (text[j].isalnum() or text[j] in ("_", "$")):
                i += 1
                continue
            # Skip whitespace before the open paren.
            while j < n and text[j].isspace():
                j += 1
            if j >= n or text[j] != "(":
                i += 1
                continue
            j += 1
            # Skip whitespace before the name-string.
            while j < n and text[j].isspace():
                j += 1
            if j >= n or text[j] not in ("'", '"', "`"):
                i += 1
                continue
            quote = text[j]
            name_start = j + 1
            # Walk the string body to find the matching quote, honoring
            # backslash escapes. Template-literal interpolation is NOT
            # recursed into — we treat `${...}` as opaque text, so a
            # describe name like \`foo\${bar}\` is captured as ``foo${bar}``
            # which will never match any real test_name.
            k = name_start
            while k < n:
                if text[k] == "\\" and k + 1 < n:
                    k += 2
                    continue
                if text[k] == quote:
                    break
                k += 1
            if k >= n:
                # Unterminated string — bail on this describe-candidate.
                i += 1
                continue
            name = text[name_start:k]
            # Seek to the callback body `{`. Between the closing quote
            # and the `{` there is: the comma, optional whitespace, the
            # arrow-header ``() => `` or ``function () ``. We don't
            # care about intervening tokens — just find the first
            # ``{`` that's in code state.
            m = k + 1
            found_brace = False
            m_in_str: str | None = None
            m_in_line = False
            m_in_block = False
            while m < n:
                mc = text[m]
                if m_in_line:
                    if mc == "\n":
                        m_in_line = False
                    m += 1
                    continue
                if m_in_block:
                    if mc == "*" and m + 1 < n and text[m + 1] == "/":
                        m_in_block = False
                        m += 2
                        continue
                    m += 1
                    continue
                if m_in_str is not None:
                    if mc == "\\" and m + 1 < n:
                        m += 2
                        continue
                    if mc == m_in_str:
                        m_in_str = None
                    m += 1
                    continue
                if mc == "/" and m + 1 < n:
                    if text[m + 1] == "/":
                        m_in_line = True
                        m += 2
                        continue
                    if text[m + 1] == "*":
                        m_in_block = True
                        m += 2
                        continue
                if mc in ("'", '"', "`"):
                    m_in_str = mc
                    m += 1
                    continue
                if mc == "{":
                    found_brace = True
                    break
                m += 1
            if not found_brace:
                i += 1
                continue
            # Push the describe onto the stack. Body starts AFTER the
            # opening brace; body_end will be filled in when we hit the
            # matching close brace.
            parent_path = stack[-1][0] if stack else ""
            chain_path = (
                f"{parent_path} > {name}" if parent_path else name
            )
            stack.append((chain_path, m + 1, brace_depth))
            brace_depth += 1
            i = m + 1
            continue

        # Brace tracking.
        if c == "{":
            brace_depth += 1
            i += 1
            continue
        if c == "}":
            brace_depth -= 1
            # Close any describes whose body just ended (LIFO: only the
            # topmost one can match, since brace depth is monotonic).
            while stack and stack[-1][2] == brace_depth:
                chain_path, body_start, _ = stack.pop()
                results.append((chain_path, body_start, i))
            i += 1
            continue

        i += 1

    # Brace-balance check. Two failure modes from regex-as-division
    # misparse:
    #   1. ``stack`` non-empty at EOF → an unbalanced ``{`` inside a
    #      regex literal (``/a{b/``) or an actual syntax error left a
    #      describe's close-brace never matched.
    #   2. ``brace_depth != 0`` at EOF → an unbalanced ``}`` inside a
    #      regex literal (``/a}b/``) caused a premature describe
    #      closure; the already-popped results have ``body_end``
    #      indices that do NOT correspond to the describe's real
    #      textual end. A sibling's content may be attributed to an
    #      earlier describe.
    # In either case we drop the file's describe map entirely. Silent
    # miss (no hints fire for this file) beats silent leak (wrong
    # attribution → false positive reclassification of a real defect).
    if stack or brace_depth != 0:
        return []
    return results
