"""Deterministic error-code → message consistency lint (Sprint 3b).

A spec that maps the same error code to two *different* literal message
strings across sections is a real contradiction: implementations and tests
can't both be right, and downstream blind scenarios that assert on the exact
string become ambiguous. The spec-validator's LLM Consistency Check detects
this class only ~half the time (measured 2/4 on a real spec) because its Pass-1
extraction (``agents/spec-validator.md:167-209``) is FR-centric and structurally
never walks the §5 error-message table or the §10 examples where the strings
live.

This module is the deterministic backstop: a tolerant ``code -> set(message)``
extractor over the spec text, flagging any code that resolves to ≥2 distinct
*fully-resolved* literals. It runs as an orchestrator code-gate (like the M1
command-scope check ``darkfactory.spec.manifest.validate_command_scoping``),
INDEPENDENT of the LLM ledger — it fires even when ``consistency=PASS`` / the
ledger is empty. That independence is the whole point: it catches the class N/N,
not 2/4.

Scope (Neo, Sprint 3b): error-code → message ONLY. The mechanically-detectable
class taxonomy and a structured error-contract spec-template SSOT are explicitly
deferred. Fail-OPEN on unparseable input, fail-CLOSED only on a confident hit
(≥2 distinct fully-resolved literals), so false positives stay near zero.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# An error code is an UPPER token, ≥2 chars: starts with a letter, ends with a
# letter/digit, may contain underscores (NOT_A_NUMBER) or none (EMPTY, TIMEOUT).
# Single-word codes are in scope (FIX-IT): the prior `(?:_…)+` required an
# underscore and silently skipped EMPTY/TIMEOUT, a false-negative hole. Matching
# ordinary ALLCAPS prose (MUST/JSON/API) is avoided not by the pattern but by
# POSITION — codes are only read from a `code: "X"` literal or a header-qualified
# message-table code cell, never from free text.
_CODE = r"[A-Z][A-Z0-9_]*[A-Z0-9]"

# Object-literal form: code: "CODE" ... message: "msg" (wire-contract rows,
# §10 example comments). code and message captured independently per line.
_OBJ_CODE = re.compile(rf'\bcode:\s*"({_CODE})"')
_OBJ_MSG = re.compile(r'\bmessage:\s*"((?:[^"\\]|\\.)*)"')

# A double-quoted literal (used to pull the message out of a table cell).
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
_BARE_CODE = re.compile(rf"^`?({_CODE})`?$")
# A markdown table separator row, e.g. |---|:--:|
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


def _is_placeholder(msg: str) -> bool:
    """A message that is not a fully-resolved literal (so it must NOT count as a
    distinct message). Covers the §10 ``message: ...`` ellipsis placeholder and
    empty/whitespace captures."""
    stripped = msg.strip()
    if not stripped:
        return True
    # only dots / ellipsis (e.g. "..." or ".")
    if set(stripped) <= {"."}:
        return True
    return False


@dataclass
class _Mapping:
    """All distinct message literals seen for one code, with line anchors."""

    messages: set[str] = field(default_factory=set)
    lines: dict[str, list[int]] = field(default_factory=dict)

    def add(self, message: str, lineno: int) -> None:
        self.messages.add(message)
        self.lines.setdefault(message, []).append(lineno)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _extract(spec_text: str) -> dict[str, _Mapping]:
    """Tolerant ``code -> _Mapping`` extraction over the whole spec.

    Two precise sources only (the loose "any backticked code + any quoted string
    on a table row" heuristic false-positived on FR *intent* tables that pair a
    code with a quoted INPUT, so it is intentionally NOT used):

    1. Object-literal ``code: "X", message: "Y"`` — wire-contract rows + §10
       examples. The explicit ``message:`` key makes this unambiguous.
    2. A markdown table whose HEADER has both a "code" column and a "message"
       column (the §5 normative message table). Intent tables (header
       ``| # | Intent | Layer | AC |``) and input→output tables (``| Input |
       Output |``) lack a "message" header and are excluded.
    """
    out: dict[str, _Mapping] = {}
    lines = spec_text.splitlines()
    # message-table column indices, set when a qualifying header is seen
    code_col: int | None = None
    msg_col: int | None = None
    in_msg_table = False

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()

        # --- table state machine -------------------------------------------
        if not stripped:
            in_msg_table = False  # blank line ends the table
        elif (
            stripped.startswith("|")
            and i < len(lines)
            and _TABLE_SEP.match(lines[i])  # next line is the separator row
            and not _TABLE_SEP.match(line)
        ):
            # This is a header row. Qualify it: needs a code col AND a msg col.
            header = [c.lower() for c in _cells(line)]
            cc = next((j for j, c in enumerate(header) if "code" in c), None)
            mc = next((j for j, c in enumerate(header) if "message" in c), None)
            if cc is not None and mc is not None and cc != mc:
                code_col, msg_col, in_msg_table = cc, mc, True
            else:
                in_msg_table = False
        elif (
            in_msg_table
            and stripped.startswith("|")
            and not _TABLE_SEP.match(line)
            and code_col is not None
            and msg_col is not None
        ):
            cells = _cells(line)
            if max(code_col, msg_col) < len(cells):
                cm = _BARE_CODE.match(cells[code_col].strip().strip("`").strip())
                qm = _QUOTED.search(cells[msg_col])
                if cm and qm and not _is_placeholder(qm.group(1)):
                    out.setdefault(cm.group(1), _Mapping()).add(qm.group(1), i)

        # --- object-literal form (independent of table state) --------------
        obj_codes = _OBJ_CODE.findall(line)
        obj_msgs = _OBJ_MSG.findall(line)
        if len(obj_codes) == 1 and len(obj_msgs) == 1:
            if not _is_placeholder(obj_msgs[0]):
                out.setdefault(obj_codes[0], _Mapping()).add(obj_msgs[0], i)
    return out


def validate_error_message_consistency(spec_text: str) -> list[dict[str, object]]:
    """Flag any error code mapped to ≥2 distinct fully-resolved message literals.

    Returns a list of violation dicts (empty = consistent), each with::

        {"code": str, "messages": [str, ...], "lines": {msg: [lineno, ...]}, "reason": str}

    Deterministic and LLM-free. Fail-open: codes with 0 or 1 resolved message
    produce no violation.
    """
    mappings = _extract(spec_text)
    violations: list[dict[str, object]] = []
    for code, m in sorted(mappings.items()):
        if len(m.messages) < 2:
            continue
        ordered = sorted(m.messages)
        anchors = ", ".join(
            f'"{msg}" (line{"s" if len(m.lines[msg]) > 1 else ""} '
            f'{", ".join(str(n) for n in m.lines[msg])})'
            for msg in ordered
        )
        violations.append(
            {
                "code": code,
                "messages": ordered,
                "lines": {msg: m.lines[msg] for msg in ordered},
                "reason": (
                    f"Error code {code} is mapped to {len(ordered)} different "
                    f"message strings: {anchors}. A code must map to ONE exact "
                    f"message (downstream tests assert the literal string). "
                    f"Reconcile to a single normative message."
                ),
            }
        )
    return violations


def format_error_message_failure(violations: list[dict[str, object]]) -> str:
    """Byte-addressable failure message for the orchestrator gate."""
    lines = [
        "spec validation failed: error-message consistency lint found "
        f"{len(violations)} code(s) mapped to conflicting messages:",
    ]
    for v in violations:
        lines.append(f"  - {v['reason']}")
    return "\n".join(lines)
