r"""Deterministic check for malformed ``sql`` tagged-template syntax.

The ``sql`` identifier in Drizzle ORM / ``postgres.js`` is used as a
**tagged template** — always followed by a backtick:

    sql`SELECT current_setting('app.tenant_id', true)`

A spec that writes ``sql'SELECT ...'`` or ``sql"SELECT ..."`` — i.e.,
``sql`` followed immediately by a single or double quote — is never
valid JavaScript regardless of imports, deps, or surrounding context.
An agent faithfully reproducing the snippet produces uncompilable code.

Motivation: the LLM spec validator sometimes catches this and sometimes
doesn't (seen live on 2026-04-21 Phase E: same spec bytes produced 4
PASS and 1 FAIL across 5 runs). The LLM's semantic checks are
inherently variance-prone; a literal syntax error like ``sql'...'`` is
mechanically matchable and belongs in precheck, not in the LLM prompt.

Scope boundary: this check is deliberately narrow. It fires only when
``sql`` (word-boundaried) appears in an expression position (preceded
by ``(``, whitespace, ``,``, ``=``, or line start), followed
immediately by ``'`` or ``"``, followed by a canonical SQL statement
keyword. That combination has no valid JavaScript reading.

- ``sql(...)`` — valid Drizzle function call form, not flagged.
- ``sql\`...\``` — valid tagged template, not flagged.
- ``'sql'`` inside a string literal — not at a word boundary after a
  non-word char that isn't ``(``/``,``/``=``, so not flagged in
  practice.
- ``mySql"..."`` or ``xsql'...'`` — not flagged (``sql`` not at a word
  boundary with a benign preceding char).

If the narrow scope proves to miss real-world typos, widen it here
rather than moving the check to the LLM.

TODO(df-#sql-tag-syntax-a6-followup): this module is a narrow regex
bridge. The real fix is to extend A6 (``stub_compile.py``) to cover
**inline code spans** in markdown (not just §5 Interface Contract
fenced blocks) and widen its fatality scope. tsc / Babel will catch
``sql'…'`` (and every other JS/TS syntax error) as a parse error for
free — once A6 scans inline spans, this module becomes redundant.
Retire it at that point rather than adding more adapter-level regex
linters here. Signal to move: a third defect-class-of-this-kind
shows up — that's the prompt to invest in the parser-based approach
instead of writing a third regex.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from darkfactory.spec.spec_model import FencedBlock, SpecModel

log = logging.getLogger(__name__)


_JS_TS_LANGS: frozenset[str] = frozenset({
    "typescript", "ts", "tsx", "mts", "cts",
    "javascript", "js", "jsx", "mjs", "cjs",
})


# Match a ``sql`` identifier (word-bounded, lowercase by convention)
# followed IMMEDIATELY by ``'`` or ``"``, then optional whitespace, then
# a canonical SQL statement keyword. The preceding-char anchor
# (line-start | whitespace | one of ``(``, ``,``, ``=``, ``:``, ``>``)
# keeps us in expression position, rejecting ``mysql'...'`` and the
# like (which would instead hit ``[a-z]sql['"]`` and be filtered by the
# anchor group).
_MALFORMED_SQL_TAG_RE = re.compile(
    r"""
    (?:^|(?<=[(\s,=:>]))     # expression-position anchor (non-capturing)
    sql                       # the identifier itself, lowercase
    (?P<quote>['"])           # the offending quote — should be backtick
    \s*                       # optional whitespace before the keyword
    (?P<kw>
        SELECT | INSERT | UPDATE | DELETE |
        CREATE | ALTER  | DROP   | TRUNCATE |
        GRANT  | REVOKE |
        SET    | WITH   |
        BEGIN  | COMMIT | ROLLBACK | SAVEPOINT |
        VACUUM | ANALYZE | EXPLAIN | USE
    )\b
    """,
    re.VERBOSE | re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class SqlTagSyntaxFinding:
    """A single malformed ``sql<quote>...`` occurrence in a TS/JS fence."""

    line_number: int           # 1-based, absolute line number in the spec
    section_path: tuple[str, ...]
    quote_char: str            # the offending char — always ``'`` or ``"``
    context: str               # trimmed snippet including the match
    message: str               # human-readable diagnostic


def _is_js_or_ts_fence(block: FencedBlock) -> bool:
    return block.language.lower() in _JS_TS_LANGS


def _section_path_for_line(model: SpecModel, abs_line: int) -> tuple[str, ...]:
    """Best-effort section-path lookup for a line in the raw spec text.

    Walks the model's section tree and returns the deepest section whose
    heading starts at or before ``abs_line``. Returns ``()`` when the
    match sits above the first heading (e.g., in a top-level preamble).
    """
    best: tuple[str, ...] = ()
    best_line = -1
    for block in model.all_fenced_blocks:
        # FencedBlock carries section_path and a heading line above it;
        # we can reuse its section_path when the block contains ``abs_line``.
        if block.line_number <= abs_line <= block.end_line:
            return block.section_path
        if block.line_number <= abs_line and block.line_number > best_line:
            best = block.section_path
            best_line = block.line_number
    return best


def _in_inline_code_span(line_text: str, match_start_col: int) -> bool:
    """True when the column-start falls inside a backtick-delimited span
    on the same line.

    Approximation: count backticks in ``line_text[:match_start_col]`` —
    odd count means we're inside an open span. This is imperfect for
    multi-backtick delimiters (```` `` `` ````) but markdown inline code
    in practice uses single backticks, and the pattern we match is
    narrow enough that a miss here just falls through to the fenced-block
    path.
    """
    prefix = line_text[:match_start_col]
    return (prefix.count("`") % 2) == 1


def check_sql_tag_syntax(model: SpecModel, *, raw_text: str | None = None) -> list[SqlTagSyntaxFinding]:
    """Return findings for every malformed ``sql<quote>...`` occurrence.

    Scans two surfaces:

    1. Fenced JS/TS code blocks — the canonical place to put interface
       contracts and worked examples.
    2. Inline backtick-delimited code spans anywhere in the raw spec
       text (including markdown table cells, where the AC-5 defect on
       budget-brain-v2 2026-03-28-database-foundation lived).

    ``raw_text`` is optional; when omitted, only fenced blocks are
    scanned. ``run_precheck`` always passes it so the inline-span
    surface is covered there.
    """
    findings: list[SqlTagSyntaxFinding] = []
    seen: set[tuple[int, int]] = set()  # dedupe by (abs_line, column)

    # Surface 1 — fenced JS/TS blocks.
    for block in model.all_fenced_blocks:
        if not _is_js_or_ts_fence(block):
            continue
        body = block.content
        if not body:
            continue
        body_start_line = block.line_number + 1
        for match in _MALFORMED_SQL_TAG_RE.finditer(body):
            rel_line = body.count("\n", 0, match.start())
            abs_line = body_start_line + rel_line
            line_start = body.rfind("\n", 0, match.start()) + 1
            col = match.start() - line_start
            key = (abs_line, col)
            if key in seen:
                continue
            seen.add(key)
            line_end = body.find("\n", match.end())
            if line_end == -1:
                line_end = len(body)
            context = body[line_start:line_end].strip()
            quote_char = match.group("quote")
            findings.append(
                SqlTagSyntaxFinding(
                    line_number=abs_line,
                    section_path=block.section_path,
                    quote_char=quote_char,
                    context=context,
                    message=(
                        f"malformed `sql` tagged-template: `sql{quote_char}…{quote_char}` "
                        f"is never valid JavaScript — Drizzle/postgres.js `sql` requires "
                        f"backticks (`sql`…`). Replace the quote with a backtick."
                    ),
                )
            )

    # Surface 2 — inline code spans in the raw markdown (e.g., table
    # cells). Only emit when the match sits inside a backtick-delimited
    # span to keep false-positive risk bounded.
    if raw_text is not None:
        for match in _MALFORMED_SQL_TAG_RE.finditer(raw_text):
            abs_line = raw_text.count("\n", 0, match.start()) + 1
            line_start = raw_text.rfind("\n", 0, match.start()) + 1
            col = match.start() - line_start
            key = (abs_line, col)
            if key in seen:
                continue
            line_end = raw_text.find("\n", match.end())
            if line_end == -1:
                line_end = len(raw_text)
            line_text = raw_text[line_start:line_end]
            if not _in_inline_code_span(line_text, col):
                continue
            seen.add(key)
            quote_char = match.group("quote")
            findings.append(
                SqlTagSyntaxFinding(
                    line_number=abs_line,
                    section_path=_section_path_for_line(model, abs_line),
                    quote_char=quote_char,
                    context=line_text.strip(),
                    message=(
                        f"malformed `sql` tagged-template: `sql{quote_char}…{quote_char}` "
                        f"is never valid JavaScript — Drizzle/postgres.js `sql` requires "
                        f"backticks (`sql`…`). Replace the quote with a backtick."
                    ),
                )
            )

    findings.sort(key=lambda f: f.line_number)
    return findings
