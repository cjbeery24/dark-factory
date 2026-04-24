r"""Mechanism-vs-observable token scanner for Postgres specs.

Track C Proposal 4 enforcement: a pre-LLM regex gate that rejects a
spec when it names a specific Postgres mechanism rather than the
observable behavior the mechanism achieves. The token blocklist lives
in :file:`mechanism-tokens.toml` so spec-writer feedback loops
(`/df-write-spec`) can tune it without editing code.

Rationale
---------

Across 20+ trials of the ``database-foundation`` spec (see
``docs/dev/2026-04-22-pipeline-unblock-experiment.md``), the test
engineer consistently generated scenarios with ``tx`SET LOCAL app.x =
'${val}'`` and peer-connection ``current_setting`` reads. The spec
itself is mechanism-first: 29 occurrences of ``SET LOCAL``, 32 of
``app.tenant_id``, 13 of ``current_setting``. The LLM is
pattern-matching off the spec's own language.

Proposal 4 hypothesises that restricting the spec to observable
behavior removes those lexical hooks and forces the architect /
implementer to choose the mechanism, rather than the spec prescribing
it. This module provides the enforcement mechanism.

Scope
-----

- **Postgres-specific.** The token list is in the Postgres adapter
  tree. If the active-adapter set for a project doesn't include
  ``postgres``, this check is a silent no-op.
- **Pre-LLM, deterministic.** Runs inside :func:`run_precheck` before
  the LLM validator. Avoids paying for an LLM round trip when a
  mechanical catch suffices.
- **Fatal on ``severity = "block"``.** Any ``block`` match sets
  :class:`PreCheckResult` ``fatal=True`` the same way
  ``sql_tag_syntax_findings`` does. ``warn`` matches surface in the
  summary but don't gate.

Fence handling
--------------

Code fences are **not** skipped. A spec that shows a concrete ``SET
LOCAL`` statement inside a ``\`\`\`sql`` block is still mechanism
leakage — the scenario author and implementer read those blocks as
canonical examples. If a block truly documents an observable
requirement (e.g., "the app issues this exact SQL to satisfy
compliance audit X"), the author can either carry it as a comment
annotation or scope it narrowly enough that Proposal 4's cost is
justified. The spec-writer loop is the place to negotiate that.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Literal

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - project pins 3.11+
    import tomli as tomllib  # type: ignore[no-redef]


Severity = Literal["block", "warn"]


@dataclass(frozen=True)
class MechanismToken:
    """One row from ``mechanism-tokens.toml``.

    The loader compiles ``pattern`` to :class:`re.Pattern` at load
    time. ``name`` is the stable identifier surfaced in reports and
    telemetry; renaming it is a breaking change.
    """

    name: str
    pattern: re.Pattern[str]
    severity: Severity
    reason: str
    suggest: str


@dataclass
class MechanismTokenFinding:
    """One match of one token in the spec text.

    ``line_number`` is 1-indexed to match other precheck findings.
    ``matched_text`` is the verbatim slice that triggered; it is
    truncated to 120 chars so pathological matches don't balloon the
    summary output.
    """

    token_name: str
    severity: Severity
    line_number: int
    matched_text: str
    reason: str
    suggest: str

    # Convenience for the summary renderer; kept on the dataclass so
    # callers don't need to look up the suggestion from a separate map.
    @property
    def block(self) -> bool:
        return self.severity == "block"


_TOML_NAME = "mechanism-tokens.toml"
_PKG = "darkfactory.adapters.postgres"


def _parse_rows(rows: list[dict[str, object]]) -> list[MechanismToken]:
    """Validate and compile one ``[[token]]`` array from the TOML.

    Raises :class:`ValueError` on malformed entries so a typo in the
    TOML fails the pipeline at import time rather than producing
    silent misses at match time.
    """
    out: list[MechanismToken] = []
    for idx, raw in enumerate(rows):
        try:
            name = str(raw["name"])
            pattern_str = str(raw["pattern"])
            severity_raw = str(raw.get("severity", "block"))
            reason = str(raw["reason"])
            suggest = str(raw["suggest"])
        except KeyError as exc:
            raise ValueError(
                f"mechanism-tokens.toml [[token]] #{idx}: missing required field {exc}",
            ) from exc
        if severity_raw not in ("block", "warn"):
            raise ValueError(
                f"mechanism-tokens.toml [[token]] #{idx} ({name!r}): "
                f"severity must be 'block' or 'warn', got {severity_raw!r}",
            )
        try:
            compiled = re.compile(pattern_str, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(
                f"mechanism-tokens.toml [[token]] #{idx} ({name!r}): "
                f"invalid regex {pattern_str!r}: {exc}",
            ) from exc
        out.append(MechanismToken(
            name=name, pattern=compiled,
            severity=severity_raw,  # type: ignore[arg-type]  # narrowed above
            reason=reason, suggest=suggest,
        ))
    # Stable order by name so reports are reviewer-stable across runs.
    out.sort(key=lambda t: t.name)
    return out


@lru_cache(maxsize=1)
def load_mechanism_tokens() -> tuple[MechanismToken, ...]:
    """Load and compile the Postgres mechanism-token blocklist once.

    The result is cached for the life of the process: the TOML is
    packaged with the library and never changes at runtime. Tests
    that want to exercise the loader on a custom TOML should call
    :func:`load_mechanism_tokens_from_text` directly.
    """
    try:
        payload = resources.files(_PKG).joinpath(_TOML_NAME).read_text(
            encoding="utf-8",
        )
    except (OSError, FileNotFoundError) as exc:
        raise FileNotFoundError(
            f"mechanism-tokens.toml not found in package {_PKG!r}",
        ) from exc
    return tuple(load_mechanism_tokens_from_text(payload))


def load_mechanism_tokens_from_text(text: str) -> list[MechanismToken]:
    """Parse a TOML string into compiled :class:`MechanismToken` entries.

    Exposed so tests can construct arbitrary token tables without
    shipping them as package data.
    """
    data = tomllib.loads(text)
    rows = data.get("token")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ValueError(
            "mechanism-tokens.toml: [[token]] must be an array of tables",
        )
    return _parse_rows(rows)


def check_mechanism_tokens(
    text: str,
    *,
    tokens: tuple[MechanismToken, ...] | None = None,
) -> list[MechanismTokenFinding]:
    """Scan spec text for mechanism-token matches.

    Returns one :class:`MechanismTokenFinding` per (token, match
    location) pair. A single token can produce multiple findings when
    the spec uses the mechanism repeatedly. Findings are deterministically
    ordered by (token_name, line_number) so reports stay reviewer-stable
    across runs.

    Args:
        text: The spec's raw text (no pre-processing needed).
        tokens: Optional override for tests. When ``None``, the default
                Postgres blocklist is loaded.
    """
    if tokens is None:
        tokens = load_mechanism_tokens()
    if not tokens:
        return []
    findings: list[MechanismTokenFinding] = []
    # Pre-split once so per-token scanning shares the same view of the file.
    lines = text.splitlines()
    for token in tokens:
        for match in token.pattern.finditer(text):
            # Convert the byte offset to a 1-based line number. ``str.count``
            # is O(n) per match, but the alternative (scanning per line for
            # every token) is O(n * tokens), which gets worse faster.
            line_no = text.count("\n", 0, match.start()) + 1
            matched = match.group(0)
            # Truncate pathological matches defensively.
            if len(matched) > 120:
                matched = matched[:117] + "..."
            findings.append(MechanismTokenFinding(
                token_name=token.name,
                severity=token.severity,
                line_number=line_no,
                matched_text=matched,
                reason=token.reason,
                suggest=token.suggest,
            ))
    findings.sort(key=lambda f: (f.token_name, f.line_number))
    return findings


def has_block_finding(findings: list[MechanismTokenFinding]) -> bool:
    """Helper: any finding whose severity is ``block``."""
    return any(f.severity == "block" for f in findings)


# Re-export path for tooling that wants to open the TOML manually
# (diagnostics, docs, spec-writer loops).
def tokens_toml_path() -> Path:
    """Absolute path to the shipped mechanism-tokens.toml.

    The loader prefers :mod:`importlib.resources` for package-data
    access, but some tools (an editor, the spec-writer loop)
    reasonably want a path they can open directly.
    """
    with resources.as_file(resources.files(_PKG).joinpath(_TOML_NAME)) as p:
        return Path(p)
