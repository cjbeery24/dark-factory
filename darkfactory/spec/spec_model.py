"""Markdown-AST-backed structural model of a Dark Factory spec.

This module replaces line-window/regex reasoning for the shape-claim and
acceptance-criteria precheck rules with a typed model built by walking
markdown-it-py's token stream. See ``docs/plans/precheck-ast-rewrite.md``.

The model is intentionally flat (document-order list of Sections) — every
caller in this slice asks "find sections by name and look at their
contents", not "walk a tree".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from markdown_it import MarkdownIt
from markdown_it.token import Token


class SpecModelParseError(Exception):
    """Raised when ``parse_spec_model`` cannot understand a spec.

    Caught at the top of ``run_precheck`` and surfaced as a single hard-fail
    finding. There is no per-check fallback — partial coverage is the failure
    mode this rewrite exists to eliminate.
    """


@dataclass
class FencedBlock:
    line_number: int          # 1-based, of the opening fence
    end_line: int             # 1-based, of the closing fence (inclusive)
    language: str             # "" if no info-string, else "json", "yaml", etc.
    content: str              # body without the fence delimiters
    section_path: tuple[str, ...]


@dataclass
class TableRow:
    line_number: int
    cells: list[str]
    section_path: tuple[str, ...]


@dataclass
class Table:
    """A grouping of TableRows that markdown-it parsed as one GFM table.

    Critical for ``TABLE_BROKEN_AT_AC``: a table the user wrote contiguously
    but which got split by an interleaved fence/paragraph becomes TWO
    Table objects in the model. That structural fact is the detector.
    """
    line_number: int          # opening row (1-based)
    end_line: int             # closing row (1-based, inclusive)
    rows: list[TableRow]
    section_path: tuple[str, ...]


@dataclass
class Paragraph:
    line_number: int
    text: str                 # plain-text, inline markup stripped
    raw: str = ""             # original markdown text including ** etc.


@dataclass
class Heading:
    line_number: int
    level: int                # 1-6
    text: str                 # plain text, inline markup stripped
    raw: str                  # original line for error reporting


@dataclass
class Section:
    heading: Heading
    end_line: int             # 1-based, exclusive
    fenced_blocks: list[FencedBlock] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    paragraphs: list[Paragraph] = field(default_factory=list)


@dataclass
class SpecModel:
    sections: list[Section]
    all_fenced_blocks: list[FencedBlock]
    all_tables: list[Table]

    def find_section(self, name: str) -> Section | None:
        target = name.strip().lower()
        for s in self.sections:
            if s.heading.text.strip().lower() == target:
                return s
        return None

    def iter_sections(self) -> Iterator[Section]:
        return iter(self.sections)

    def wire_contracts(self) -> dict[str, FencedBlock]:
        """Return ``{contract_name_lower: defining FencedBlock}``.

        A wire contract is any sub-heading at any depth that lives directly
        under a heading whose normalized text equals 'wire contracts'
        (case-insensitive, exact match — substring matches like
        ``Wire Contracts Overview`` are rejected to avoid collisions). The
        defining FencedBlock is the first fenced block under that sub-heading.
        Only contracts that have a fenced block are returned here; use
        :meth:`wire_contract_names` to also see heading-only declarations.
        """
        result: dict[str, FencedBlock] = {}
        for fb in self.all_fenced_blocks:
            path_lower = [p.strip().lower() for p in fb.section_path]
            wc_idx = -1
            for i, p in enumerate(path_lower):
                if p == "wire contracts":
                    wc_idx = i
                    break
            if wc_idx == -1:
                continue
            if wc_idx + 1 >= len(fb.section_path):
                continue
            name = fb.section_path[wc_idx + 1].strip().lower()
            if name and name not in result:
                result[name] = fb
        return result

    def wire_contract_names(self) -> dict[str, FencedBlock | None]:
        """Return ``{contract_name_lower: FencedBlock | None}``.

        Like :meth:`wire_contracts` but also surfaces contracts that have a
        sub-heading but no fenced block yet (value is ``None``). Used by the
        shape-claim check so references can resolve against heading-only
        contracts, while diagnostics can flag that the contract has no
        literal shape yet.
        """
        result: dict[str, FencedBlock | None] = {}
        # Walk sections: any section whose parent heading is exactly
        # "wire contracts" (normalized) is a contract name.
        for section in self.sections:
            # Build this section's ancestor path from its own section_path
            # context. We approximate by looking at fenced_blocks beneath it,
            # but heading-only sections have none. Use the section list order
            # instead: find sections whose immediate ancestor is a WC heading.
            pass
        # Re-derive via heading stack walk over all sections in document order.
        stack: list[tuple[int, str]] = []
        for section in self.sections:
            level = section.heading.level
            while stack and stack[-1][0] >= level:
                stack.pop()
            # Before pushing: if parent (top of stack) is exactly "wire contracts"
            # this section is a contract name.
            if stack and stack[-1][1].strip().lower() == "wire contracts":
                name = section.heading.text.strip().lower()
                if name and name not in result:
                    # Find first fenced block inside this section's line range.
                    fb: FencedBlock | None = None
                    for candidate in self.all_fenced_blocks:
                        if section.heading.line_number <= candidate.line_number < section.end_line:
                            # Ensure it's directly in this sub-tree.
                            fb = candidate
                            break
                    result[name] = fb
            stack.append((level, section.heading.text))
        return result


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _md_parser() -> MarkdownIt:
    return MarkdownIt("commonmark").enable("table")


def _heading_text_from_inline(tok: Token) -> str:
    """Extract plain text from a heading inline token, stripping markup."""
    if tok.children is None:
        return (tok.content or "").strip()
    parts: list[str] = []
    for c in tok.children:
        if c.type in ("text", "code_inline"):
            parts.append(c.content)
        elif c.type == "softbreak" or c.type == "hardbreak":
            parts.append(" ")
    return "".join(parts).strip()


def _inline_plain_text(tok: Token) -> str:
    if tok.children is None:
        return tok.content or ""
    out: list[str] = []
    for c in tok.children:
        if c.type in ("text", "code_inline"):
            out.append(c.content)
        elif c.type in ("softbreak", "hardbreak"):
            out.append(" ")
    return "".join(out)


def parse_spec_model(text: str) -> SpecModel:
    """Walk markdown-it tokens once and build a SpecModel.

    Raises ``SpecModelParseError`` if the underlying parser raises (corrupt
    input, encoding issue, etc.).
    """
    try:
        md = _md_parser()
        tokens = md.parse(text)
    except Exception as exc:  # pragma: no cover - defensive
        raise SpecModelParseError(str(exc)) from exc

    raw_lines = text.split("\n")
    total_lines = len(raw_lines)

    sections: list[Section] = []
    all_fences: list[FencedBlock] = []
    all_tables: list[Table] = []

    # Heading stack: list of (level, text) for the current path.
    heading_stack: list[tuple[int, str]] = []

    def current_path() -> tuple[str, ...]:
        return tuple(t for _, t in heading_stack)

    # Walk tokens. We need to attach blocks to the most recent Section.
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        ttype = tok.type

        if ttype == "heading_open":
            level = int(tok.tag[1:])  # h1 -> 1
            inline = tokens[i + 1] if i + 1 < n else None
            text_val = _heading_text_from_inline(inline) if inline else ""
            line_no = (tok.map[0] + 1) if tok.map else 0
            raw = raw_lines[tok.map[0]] if tok.map and tok.map[0] < total_lines else ""
            # Pop heading stack to this level
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, text_val))

            # Close ALL prior open Sections whose heading level is >= the
            # incoming heading's level. This terminates not just the most
            # recent open section, but every still-open ancestor at or below
            # the new section's level. Without this, deeper nested sections
            # keep their tentative end_line at EOF and get over-included by
            # section-subtree queries.
            if sections:
                for s in reversed(sections):
                    if s.end_line == total_lines + 1 and level <= s.heading.level:
                        s.end_line = line_no

            heading = Heading(line_number=line_no, level=level, text=text_val, raw=raw)
            section = Section(heading=heading, end_line=total_lines + 1)
            sections.append(section)

            # Skip past inline + heading_close
            i += 3
            continue

        if ttype == "fence":
            line_start = (tok.map[0] + 1) if tok.map else 0
            line_end = (tok.map[1]) if tok.map else line_start
            fb = FencedBlock(
                line_number=line_start,
                end_line=line_end,
                language=(tok.info or "").strip().split()[0] if (tok.info or "").strip() else "",
                content=tok.content or "",
                section_path=current_path(),
            )
            all_fences.append(fb)
            if sections:
                sections[-1].fenced_blocks.append(fb)
            i += 1
            continue

        if ttype == "table_open":
            # Walk forward collecting rows until table_close.
            table_start = (tok.map[0] + 1) if tok.map else 0
            table_end = (tok.map[1]) if tok.map else table_start
            rows: list[TableRow] = []
            j = i + 1
            current_cells: list[str] | None = None
            current_row_line = 0
            while j < n and tokens[j].type != "table_close":
                tj = tokens[j]
                if tj.type in ("tr_open",):
                    current_cells = []
                    current_row_line = (tj.map[0] + 1) if tj.map else 0
                elif tj.type in ("th_open", "td_open"):
                    # Next inline token holds the cell text.
                    if j + 1 < n and tokens[j + 1].type == "inline":
                        current_cells = current_cells if current_cells is not None else []
                        current_cells.append(_inline_plain_text(tokens[j + 1]))
                elif tj.type == "tr_close":
                    if current_cells is not None:
                        rows.append(TableRow(
                            line_number=current_row_line,
                            cells=current_cells,
                            section_path=current_path(),
                        ))
                        current_cells = None
                j += 1
            table = Table(
                line_number=table_start,
                end_line=table_end,
                rows=rows,
                section_path=current_path(),
            )
            all_tables.append(table)
            if sections:
                sections[-1].tables.append(table)
            i = j + 1  # past table_close
            continue

        if ttype == "paragraph_open":
            inline = tokens[i + 1] if i + 1 < n else None
            line_no = (tok.map[0] + 1) if tok.map else 0
            text_val = _inline_plain_text(inline) if inline else ""
            # Capture the raw markdown for this paragraph from source lines
            # so callers can detect bold/asterisk markup that the inline
            # text-flatten step strips.
            raw_text = ""
            if tok.map:
                raw_text = "\n".join(raw_lines[tok.map[0]:tok.map[1]])
            para = Paragraph(line_number=line_no, text=text_val, raw=raw_text)
            if sections:
                sections[-1].paragraphs.append(para)
            i += 3  # paragraph_open, inline, paragraph_close
            continue

        i += 1

    # Close any still-open sections at EOF.
    for s in sections:
        if s.end_line == total_lines + 1:
            s.end_line = total_lines + 1

    return SpecModel(
        sections=sections,
        all_fenced_blocks=all_fences,
        all_tables=all_tables,
    )
