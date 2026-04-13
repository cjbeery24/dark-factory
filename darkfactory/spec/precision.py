"""Precision gap detection — API surface contract ambiguity checks.

Detects spec language that describes cross-boundary API surfaces (factory
functions, error propagation, plugin registration, config sources, lifecycle
hooks, concurrency) without pinning the contract details that two independent
agents would need to agree on.

Unlike shape claims (which catch serialized data format ambiguity), precision
gaps catch API *behavioral* ambiguity: return types, sync/async semantics,
registration order, precedence rules, lifecycle sequencing, and concurrency
mechanisms.

All findings are advisory — they do not block pipeline entry. The LLM-based
Precision Analysis in ``df-validate-spec`` remains the hard gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from darkfactory.spec.spec_model import Section, SpecModel


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PrecisionFinding:
    """A precision gap that could cause implementer/scenario divergence."""
    pattern_id: str          # e.g. "FACTORY_NO_RETURN_TYPE"
    section: str             # heading text
    line_number: int
    matched_text: str        # the triggering text (truncated)
    message: str             # actionable fix suggestion


# ---------------------------------------------------------------------------
# Section filtering
# ---------------------------------------------------------------------------

# Sections where API surface precision matters. Prose-heavy sections like
# Problem Statement or Dependencies are excluded to reduce false positives.
_PRECISION_BEARING_SECTIONS: frozenset[str] = frozenset({
    "functional requirements",
    "interface contract",
    "acceptance criteria",
    "edge cases",
    "error handling",
    "non-functional requirements",
})

# Alternate heading names (subset of precheck._SECTION_ALIASES).
_SECTION_ALIASES: dict[str, str] = {
    "constraints": "constraints",
    "dependencies": "dependencies & out of scope",
}


def _is_precision_bearing(section: Section) -> bool:
    name = section.heading.text.strip().lower()
    return name in _PRECISION_BEARING_SECTIONS


# ---------------------------------------------------------------------------
# Pattern 1: Factory/constructor without return type constraint
# ---------------------------------------------------------------------------

_FACTORY_NAME_RE = re.compile(
    r"\b(?:create|build|init(?:ialize)?|make|new|setup|bootstrap|configure)"
    r"[A-Z]\w*\s*\(",
    re.IGNORECASE,
)

# "returns" followed by something that looks like a type constraint.
# A constrained return looks like: returns `FastifyInstance`, returns a Config,
# returns ServerHandle. Unconstrained: returns the server, returns an object.
# Matches "returns `TypeName`" in raw markdown (backticks preserved)
# or "returns TypeName" where TypeName is PascalCase (plain text).
_RETURNS_TYPE_RAW_RE = re.compile(
    r"\breturns?\s+(?:a\s+)?`[^`]+`",
    re.IGNORECASE,
)
_RETURNS_TYPE_PLAIN_RE = re.compile(
    r"\breturns?\s+(?:a\s+)?[A-Z][a-z]+[A-Z]\w*",  # PascalCase type name
)


def _check_factory_return_type(model: SpecModel) -> list[PrecisionFinding]:
    findings: list[PrecisionFinding] = []
    for section in model.sections:
        if not _is_precision_bearing(section):
            continue

        # Check if any fenced block in this section shows a return type.
        has_type_fence = any(
            fb.language.lower() in ("typescript", "ts", "python", "py", "go", "rust")
            for fb in section.fenced_blocks
        )

        for para in section.paragraphs:
            match = _FACTORY_NAME_RE.search(para.text)
            if not match:
                continue
            func_name = match.group(0).rstrip("(").strip()

            # Check if the same paragraph or section constrains the return type.
            # Check raw text (preserves backticks) and plain text (PascalCase).
            raw = para.raw or para.text
            if _RETURNS_TYPE_RAW_RE.search(raw):
                continue
            if _RETURNS_TYPE_PLAIN_RE.search(para.text):
                continue
            if has_type_fence:
                continue

            findings.append(PrecisionFinding(
                pattern_id="FACTORY_NO_RETURN_TYPE",
                section=section.heading.text.strip(),
                line_number=para.line_number,
                matched_text=func_name + "()",
                message=(
                    f"Factory function `{func_name}()` is described without "
                    f"constraining its return type. Add the exact return type "
                    f"(e.g., 'returns `FastifyInstance`') or a Wire Contract "
                    f"showing the return shape. Without this, the implementer "
                    f"and scenario architect may assume different contracts."
                ),
            ))

        for table in section.tables:
            for row in table.rows:
                joined = " ".join(row.cells)
                match = _FACTORY_NAME_RE.search(joined)
                if not match:
                    continue
                func_name = match.group(0).rstrip("(").strip()
                if _RETURNS_TYPE_RAW_RE.search(joined) or _RETURNS_TYPE_PLAIN_RE.search(joined):
                    continue
                if has_type_fence:
                    continue
                findings.append(PrecisionFinding(
                    pattern_id="FACTORY_NO_RETURN_TYPE",
                    section=section.heading.text.strip(),
                    line_number=row.line_number,
                    matched_text=func_name + "()",
                    message=(
                        f"Factory function `{func_name}()` mentioned without "
                        f"return type constraint."
                    ),
                ))

    return findings


# ---------------------------------------------------------------------------
# Pattern 2: Error propagation without sync/async semantics
# ---------------------------------------------------------------------------

_THROWS_RE = re.compile(
    r"\b(?:throws?|raises?)\s+`?\w+(?:Error|Exception)`?",
    re.IGNORECASE,
)

_ASYNC_QUALIFIER_RE = re.compile(
    r"\b(?:async|await|[Pp]romise|reject(?:s|ed)?|resolve[sd]?|"
    r"coroutine|blocking|synchronous|sync(?:hronously)?)\b",
)


def _check_throws_sync_async(model: SpecModel) -> list[PrecisionFinding]:
    findings: list[PrecisionFinding] = []
    for section in model.sections:
        if not _is_precision_bearing(section):
            continue
        for para in section.paragraphs:
            if not _THROWS_RE.search(para.text):
                continue
            # Check if the same section (not just paragraph) clarifies sync/async.
            section_text = " ".join(p.text for p in section.paragraphs)
            if _ASYNC_QUALIFIER_RE.search(section_text):
                continue
            match = _THROWS_RE.search(para.text)
            findings.append(PrecisionFinding(
                pattern_id="THROWS_NO_SYNC_ASYNC",
                section=section.heading.text.strip(),
                line_number=para.line_number,
                matched_text=match.group(0) if match else "throws/raises",
                message=(
                    f"'{match.group(0) if match else 'throws'}' without "
                    f"sync/async clarification. A synchronous throw uses "
                    f"`expect(() => fn()).toThrow()` while an async rejection "
                    f"uses `await expect(fn()).rejects.toThrow()`. Specify "
                    f"whether the function is synchronous or async."
                ),
            ))

    return findings


# ---------------------------------------------------------------------------
# Pattern 3: Plugin/middleware registration without import semantics
# ---------------------------------------------------------------------------

_REGISTRATION_RE = re.compile(
    r"\b(?:register|plugin|middleware)\b",
    re.IGNORECASE,
)

_IMPORT_SEMANTICS_RE = re.compile(
    r"\b(?:static\s+import|dynamic\s+import|import\s*\(|require\s*\(|"
    r"at\s+startup|before\s+listen|on\s+boot|registration\s+order|"
    r"register(?:ed|s)?\s+(?:before|after|first|last))\b",
    re.IGNORECASE,
)


def _check_plugin_registration(model: SpecModel) -> list[PrecisionFinding]:
    findings: list[PrecisionFinding] = []
    for section in model.sections:
        if not _is_precision_bearing(section):
            continue
        section_text = " ".join(p.text for p in section.paragraphs)
        if not _REGISTRATION_RE.search(section_text):
            continue
        if _IMPORT_SEMANTICS_RE.search(section_text):
            continue
        # Check fenced blocks too — a code block showing registration is sufficient.
        has_code_example = any(
            _REGISTRATION_RE.search(fb.content)
            for fb in section.fenced_blocks
        )
        if has_code_example:
            continue

        # Find the triggering paragraph.
        for para in section.paragraphs:
            match = _REGISTRATION_RE.search(para.text)
            if match:
                findings.append(PrecisionFinding(
                    pattern_id="PLUGIN_NO_IMPORT_SEMANTICS",
                    section=section.heading.text.strip(),
                    line_number=para.line_number,
                    matched_text=match.group(0),
                    message=(
                        f"Plugin/middleware registration mentioned without "
                        f"specifying import style (static vs dynamic import) "
                        f"or registration order. Add a code example or specify "
                        f"'registered at startup via static import' to prevent "
                        f"divergence."
                    ),
                ))
                break  # one finding per section for this pattern

    return findings


# ---------------------------------------------------------------------------
# Pattern 4: Config sources without precedence
# ---------------------------------------------------------------------------

_CONFIG_SOURCE_RE = re.compile(
    r"\b(?:environment\s+variables?|env\s+vars?|config\s+file|\.env|"
    r"defaults?(?:\s+values?)?|command[- ]line|CLI\s+args?|toml|yaml\s+config)\b",
    re.IGNORECASE,
)

_PRECEDENCE_RE = re.compile(
    r"\b(?:preceden|overrid|fallback|fall[sd]?\s+back|priority|"
    r"first|last|trump|wins|if\s+not\s+set|"
    r"take[sd]?\s+priority|higher\s+priority)\b",
    re.IGNORECASE,
)


def _check_config_precedence(model: SpecModel) -> list[PrecisionFinding]:
    findings: list[PrecisionFinding] = []
    for section in model.sections:
        if not _is_precision_bearing(section):
            continue
        section_text = " ".join(p.text for p in section.paragraphs)
        sources = set(_CONFIG_SOURCE_RE.findall(section_text.lower()))
        if len(sources) < 2:
            continue
        if _PRECEDENCE_RE.search(section_text):
            continue
        # Find the first paragraph mentioning a config source.
        for para in section.paragraphs:
            match = _CONFIG_SOURCE_RE.search(para.text)
            if match:
                findings.append(PrecisionFinding(
                    pattern_id="CONFIG_NO_PRECEDENCE",
                    section=section.heading.text.strip(),
                    line_number=para.line_number,
                    matched_text=f"{len(sources)} config sources",
                    message=(
                        f"Multiple configuration sources mentioned without "
                        f"precedence order. Specify which source overrides "
                        f"which (e.g., 'env vars override config file, which "
                        f"overrides defaults')."
                    ),
                ))
                break

    return findings


# ---------------------------------------------------------------------------
# Pattern 5: Lifecycle hooks without ordering
# ---------------------------------------------------------------------------

_LIFECYCLE_RE = re.compile(
    r"\b(?:graceful\s+shutdown|on\s+shutdown|on\s+startup|cleanup|"
    r"teardown|dispose|close\s+connections|drain\s+requests|"
    r"shutdown\s+sequence|startup\s+sequence)\b",
    re.IGNORECASE,
)

_ORDERED_LIST_RE = re.compile(r"^\s*\d+\.\s", re.MULTILINE)


def _check_lifecycle_ordering(model: SpecModel) -> list[PrecisionFinding]:
    findings: list[PrecisionFinding] = []
    for section in model.sections:
        if not _is_precision_bearing(section):
            continue
        section_text = " ".join(p.text for p in section.paragraphs)
        if not _LIFECYCLE_RE.search(section_text):
            continue
        # Check if there's an ordered list in the section's raw paragraphs.
        # Each list item is a separate paragraph; check raw text for "1." prefix.
        has_ordered_list = any(
            _ORDERED_LIST_RE.match(p.raw or p.text) for p in section.paragraphs
        )
        if has_ordered_list:
            continue
        for para in section.paragraphs:
            match = _LIFECYCLE_RE.search(para.text)
            if match:
                findings.append(PrecisionFinding(
                    pattern_id="LIFECYCLE_NO_ORDER",
                    section=section.heading.text.strip(),
                    line_number=para.line_number,
                    matched_text=match.group(0),
                    message=(
                        f"Lifecycle behavior '{match.group(0)}' described "
                        f"without numbered steps. Specify the exact order "
                        f"(e.g., '1. stop accepting connections, 2. drain "
                        f"in-flight requests, 3. close DB pool, 4. exit')."
                    ),
                ))
                break

    return findings


# ---------------------------------------------------------------------------
# Pattern 6: Concurrency without mechanism
# ---------------------------------------------------------------------------

_CONCURRENCY_RE = re.compile(
    r"\b(?:concurrent(?:ly)?|thread[- ]safe|race\s+condition|"
    r"parallel|atomic)\b",
    re.IGNORECASE,
)

_CONCURRENCY_MECHANISM_RE = re.compile(
    r"\b(?:mutex|semaphore|channel|event\s+loop|worker\s+pool|"
    r"connection\s+pool|retry|reject|backpressure|queue|"
    r"single[- ]threaded|serializ|sequentializ)\b",
    re.IGNORECASE,
)


def _check_concurrency_mechanism(model: SpecModel) -> list[PrecisionFinding]:
    findings: list[PrecisionFinding] = []
    for section in model.sections:
        if not _is_precision_bearing(section):
            continue
        section_text = " ".join(p.text for p in section.paragraphs)
        if not _CONCURRENCY_RE.search(section_text):
            continue
        if _CONCURRENCY_MECHANISM_RE.search(section_text):
            continue
        for para in section.paragraphs:
            match = _CONCURRENCY_RE.search(para.text)
            if match:
                findings.append(PrecisionFinding(
                    pattern_id="CONCURRENCY_NO_MECHANISM",
                    section=section.heading.text.strip(),
                    line_number=para.line_number,
                    matched_text=match.group(0),
                    message=(
                        f"Concurrency requirement '{match.group(0)}' without "
                        f"specifying the mechanism or contention behavior. "
                        f"Specify whether requests are queued, rejected, or "
                        f"retried under contention."
                    ),
                ))
                break

    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_precision_gaps(model: SpecModel) -> list[PrecisionFinding]:
    """Run all precision gap checks and return combined findings.

    All findings are advisory — they never block pipeline entry.
    """
    findings: list[PrecisionFinding] = []
    findings.extend(_check_factory_return_type(model))
    findings.extend(_check_throws_sync_async(model))
    findings.extend(_check_plugin_registration(model))
    findings.extend(_check_config_precedence(model))
    findings.extend(_check_lifecycle_ordering(model))
    findings.extend(_check_concurrency_mechanism(model))
    return findings
