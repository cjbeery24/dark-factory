"""Deterministic SQL syntax lint on fenced ``sql`` blocks in a spec.

When a spec embeds SQL (DDL, DML, policy definitions) inside fenced
``sql`` blocks, the spec validator LLM sometimes flags *valid* PG16 SQL
as "critical syntax error" — e.g., ``ALTER DATABASE CURRENT`` (a real
form) being marked invalid. Running a deterministic parser against the
same blocks produces a ground-truth verdict that downstream consumers
can use to deflate hallucinated validator findings.

A2's contract:

- Extract every ```` ```sql ```` fenced block via the existing spec model.
- Parse each block with libpg_query via ``pglast``.
- Return one ``SqlLintFinding`` per block that fails to parse.
- If ``pglast`` is not installed, log a single warning and return [].
  A2 is a graceful no-op for deployments that do not want the dep.
- Non-SQL specs (no ``sql`` fences) skip the check entirely.

Motivated by 2026-04-14 database-foundation escalation. See
``docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md``
(Sprint 1, A2).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from darkfactory.spec.spec_model import FencedBlock, SpecModel

log = logging.getLogger(__name__)

_PGLAST_AVAILABLE: bool | None = None  # lazy-resolved on first call


@dataclass(frozen=True)
class SqlLintFinding:
    """A single SQL fenced block that failed to parse."""

    line_number: int        # 1-based, opening fence line
    end_line: int           # 1-based, closing fence line (inclusive)
    section_path: tuple[str, ...]
    error: str              # parser message (syntax error at ...)
    snippet: str            # first ~120 chars of block body for context


def _probe_pglast() -> bool:
    """Cache the availability of pglast across calls in one process."""
    global _PGLAST_AVAILABLE
    if _PGLAST_AVAILABLE is not None:
        return _PGLAST_AVAILABLE
    try:
        import pglast  # noqa: F401
        _PGLAST_AVAILABLE = True
    except ImportError:
        _PGLAST_AVAILABLE = False
    return _PGLAST_AVAILABLE


def _iter_sql_fences(model: SpecModel) -> list[FencedBlock]:
    # Walk the flat canonical list. Fences that appear before the first
    # heading live in ``all_fenced_blocks`` but not under any ``Section``,
    # so per-section iteration would silently miss them. For an A2-grade
    # "ground truth" check, a missed fence is the one failure mode we
    # cannot have.
    return [
        b for b in model.all_fenced_blocks if b.language.lower() == "sql"
    ]


def lint_sql_fences(model: SpecModel) -> list[SqlLintFinding]:
    """Return findings for SQL fenced blocks that fail to parse.

    Empty list means either (a) no SQL fences present, (b) all fences
    parsed clean, or (c) pglast is not installed. The distinction
    matters only to callers who want to light up the "validator
    hallucination suppression" codepath — they should treat "empty
    findings with SQL blocks present" as "deterministic verdict: SQL
    is valid".
    """
    fences = _iter_sql_fences(model)
    if not fences:
        return []

    if not _probe_pglast():
        log.warning(
            "A2: pglast not installed; SQL fence lint skipped "
            "(install with `pip install pglast` to enable)"
        )
        return []

    # Import after probe so the cost is paid only once per process and
    # only when there's actual work to do.
    from pglast import parse_sql
    from pglast.parser import ParseError

    findings: list[SqlLintFinding] = []
    for block in fences:
        body = block.content.strip()
        if not body:
            continue
        error: str | None = None
        try:
            parse_sql(body)
        except ParseError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 — parser may raise subclasses
            # Narrow subclasses of ParseError are handled above; the C
            # parser can also surface memory/encoding/unicode errors that
            # are not ParseError. Keep precheck alive; surface the type
            # name so a surprise parser bug is diagnosable from logs.
            error = f"{type(exc).__name__}: {exc}"
        if error is None:
            continue
        snippet = body[:120].replace("\n", " ")
        findings.append(
            SqlLintFinding(
                line_number=block.line_number,
                end_line=block.end_line,
                section_path=block.section_path,
                error=error,
                snippet=snippet + ("..." if len(body) > 120 else ""),
            )
        )
    return findings


def sql_fences_present(model: SpecModel) -> bool:
    """Whether the spec has any ```` ```sql ```` blocks at all.

    Useful for downstream callers deciding whether A2 has enough signal
    to deflate an LLM "invalid SQL" finding (present=True + findings=[]
    means the validator is hallucinating).
    """
    return bool(_iter_sql_fences(model))
