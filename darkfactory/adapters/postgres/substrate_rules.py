"""Loader for ``substrate-rules.toml``.

The TOML file is the public-surface taxonomy of triggers → required
substrate primitives. This module's job is the load-and-validate seam
between that data file and the rest of the adapter: callers (validator
addendum, architect addendum, ``verify_substrate()``) operate on
``SubstrateRule`` instances and never re-parse the TOML themselves.

Why a separate module: keeping the loader out of ``adapter.py`` lets
the contract harness import and exercise the rules without dragging
in adapter side effects (sql_lint, detection probes), and makes it
trivial to add per-rule unit tests in isolation.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

_RULES_PATH = Path(__file__).with_name("substrate-rules.toml")
_rules_cache: list["SubstrateRule"] | None = None


# Bumped when the rule schema changes in a way callers must be aware
# of. Pinned in the TOML so a load mismatch is loud at startup, not a
# silent KeyError three layers deep.
EXPECTED_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SubstrateRule:
    """One row of the substrate-rules taxonomy.

    Fields mirror the TOML structure so ``substrate-rules.toml`` is the
    source of truth and this class is a typed view over it. Adding a
    field requires bumping ``EXPECTED_SCHEMA_VERSION`` and updating the
    TOML; the loader rejects mismatched files at startup.
    """

    trigger: str
    match_patterns: tuple[re.Pattern[str], ...]
    required_primitives: tuple[str, ...]
    optional_primitives: tuple[str, ...]
    required_scenarios: tuple[str, ...]
    description: str

    def matches(self, text: str) -> bool:
        """True iff any ``match_patterns`` pattern fires against ``text``."""
        return any(p.search(text) for p in self.match_patterns)


def load_substrate_rules() -> list[SubstrateRule]:
    """Read ``substrate-rules.toml`` once, cache, return.

    Fails loudly on missing / malformed file because the rules are part
    of the packaged wheel; a missing file is a packaging bug that
    should surface in CI, not silently degrade substrate verification.
    """
    global _rules_cache
    if _rules_cache is None:
        try:
            text = _RULES_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(
                f"PostgresAdapter substrate-rules.toml missing at "
                f"{_RULES_PATH}"
            ) from exc
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise RuntimeError(
                f"PostgresAdapter substrate-rules.toml is malformed: {exc}"
            ) from exc

        version = data.get("schema_version")
        if version != EXPECTED_SCHEMA_VERSION:
            raise RuntimeError(
                f"PostgresAdapter substrate-rules.toml schema_version "
                f"{version!r} != expected {EXPECTED_SCHEMA_VERSION}. "
                f"Update the loader and bump EXPECTED_SCHEMA_VERSION "
                f"together — the mismatch means a TOML field was added "
                f"or removed without a code-side migration."
            )

        out: list[SubstrateRule] = []
        for entry in data.get("rule", []):
            out.append(_compile_rule(entry))
        _rules_cache = out
    return _rules_cache


def _compile_rule(entry: dict) -> SubstrateRule:
    """Validate one TOML ``[[rule]]`` entry and compile its patterns.

    Each ``match_patterns`` string is compiled to a case-insensitive
    regex once at load time. Compiling at load (not at scan) means a
    malformed pattern fails the load (loud, surfaces in CI) instead of
    failing the first scan against a real spec (silent until that
    spec runs).
    """
    trigger = entry.get("trigger", "")
    if not isinstance(trigger, str) or not trigger:
        raise RuntimeError(
            f"substrate-rules entry missing 'trigger' field: {entry!r}"
        )
    raw_patterns = entry.get("match_patterns", [])
    if not isinstance(raw_patterns, list) or not raw_patterns:
        raise RuntimeError(
            f"substrate-rules entry {trigger!r}: 'match_patterns' must be "
            f"a non-empty list"
        )
    compiled: list[re.Pattern[str]] = []
    for pat in raw_patterns:
        try:
            compiled.append(re.compile(pat, re.IGNORECASE))
        except re.error as exc:
            raise RuntimeError(
                f"substrate-rules entry {trigger!r}: invalid regex "
                f"{pat!r}: {exc}"
            ) from exc
    primitives = entry.get("required_primitives", [])
    if not isinstance(primitives, list):
        raise RuntimeError(
            f"substrate-rules entry {trigger!r}: 'required_primitives' "
            f"must be a list"
        )
    optional = entry.get("optional_primitives", [])
    if not isinstance(optional, list):
        raise RuntimeError(
            f"substrate-rules entry {trigger!r}: 'optional_primitives' "
            f"must be a list"
        )
    scenarios = entry.get("required_scenarios", [])
    if not isinstance(scenarios, list):
        raise RuntimeError(
            f"substrate-rules entry {trigger!r}: 'required_scenarios' "
            f"must be a list"
        )
    return SubstrateRule(
        trigger=trigger,
        match_patterns=tuple(compiled),
        required_primitives=tuple(str(p) for p in primitives),
        optional_primitives=tuple(str(o) for o in optional),
        required_scenarios=tuple(str(s) for s in scenarios),
        description=str(entry.get("description", "")).strip(),
    )


def triggered_rules(spec_text: str) -> list[SubstrateRule]:
    """Return rules whose match_patterns fire against ``spec_text``."""
    return [r for r in load_substrate_rules() if r.matches(spec_text)]
