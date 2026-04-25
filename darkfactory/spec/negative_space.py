"""Parse the §4a Negative Space, Invariants, and Lifecycle block from
a spec, plus the §5b ``test_server`` wire contract.

These are spec-side declarations of (a) commands / test patterns that
MUST NOT appear in downstream artifacts, (b) positive-form invariants
the architect's manifest MUST satisfy, (c) ordered procedural
sequences the implementer MUST honor, and (d) the test-server lifecycle
contract that collapses three independent port-resolution mechanisms
(Tier 1 compiler default, architect manifest port, qa-holdout LLM
hardcode) into a single role-of-authority — the spec.

This module is intentionally narrow: it parses, it does not enforce.
Cross-checks against architect manifest output, scenario files, and
gate-side enforcement live in their respective layers (``architecture/``,
``scenarios/validator.py``, ``pipeline/quality_gate.py``).

Origin: Phase 5 Stage 2 / 2026-04-25, motivated by Stage 1 echo-endpoint
trial 2 surfacing three role-output reconciliation gaps where decisions
made in one role's locus could not be reconciled or overridden by
another role with the relevant write authority. The negative-space
schema is the unifying fix: declarations live with the spec (the
single document every role reads), and downstream consumers honor them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class ForbiddenCommand:
    """A command pattern that MUST NOT appear in architect-emitted
    runtime commands.

    ``pattern`` is a Python regex. ``applies_to`` is the list of
    architect manifest fields the validator scans (e.g., ``commands.setup``,
    ``commands.install``). ``reason`` is human-readable; surfaced in the
    block message when the validator rejects a command.
    """
    pattern: str
    reason: str
    applies_to: list[str] = field(default_factory=list)


@dataclass
class ForbiddenTestShape:
    """A test-source pattern that MUST NOT appear in scenarios or
    holdouts.

    Enforced by the gate's scenario-lint phase pre-execution. Same
    shape as ``ForbiddenCommand`` but applies to test-source files
    rather than architect-emitted commands.
    """
    pattern: str
    reason: str
    applies_to: list[str] = field(default_factory=list)


@dataclass
class EcosystemInvariant:
    """A positive-form contract the architect's manifest output (or a
    downstream artifact) MUST satisfy.

    ``enforcement`` names the enforcement layer responsible for
    verifying it (e.g., ``architect.amend_check``, ``scenarios.lint``,
    ``substrate-agent``). Distinct from forbidden_*: enforces what
    MUST be true, not what MUST NOT be.
    """
    name: str
    invariant: str
    enforcement: str = ""


@dataclass
class RequiredLifecycleSequence:
    """An ordered procedural constraint the implementer MUST honor.

    ``sequence`` is a list of step names ordered earliest-to-latest.
    The substrate agent / gate-Setup phase verifies each prerequisite
    has run before its dependent step.
    """
    name: str
    sequence: list[str]
    rationale: str = ""
    enforcement: str = ""


@dataclass
class TestServerContract:
    """Spec-declared test-server lifecycle and base URL.

    Single role-of-authority: spec declares ``command`` + ``base_url``
    once; architect emits matching ``environment.toml`` entries; gate
    exports ``TEST_BASE_URL`` env to the test runner; Tier 1 compiler
    reads from the manifest (no hardcoded fallback); qa-holdout prompt
    instructs LLM to read from env.

    All three fields are optional so partial declarations don't fail
    parsing — the validator surfaces missing-field warnings separately.
    """
    command: str = ""
    base_url: str = ""
    startup_timeout_seconds: int = 10


@dataclass
class NegativeSpaceFindings:
    """Aggregated parse result for §4a + §5b.test_server.

    Empty defaults across the board: a spec that omits §4a entirely
    produces an empty findings object, NOT a parse error. The validator
    decides whether emptiness is acceptable on a per-spec basis (e.g.,
    a refactor spec may legitimately have no forbidden commands; a
    database spec usually shouldn't).
    """
    forbidden_commands: list[ForbiddenCommand] = field(default_factory=list)
    forbidden_test_shapes: list[ForbiddenTestShape] = field(default_factory=list)
    ecosystem_invariants: list[EcosystemInvariant] = field(default_factory=list)
    required_lifecycle_sequences: list[RequiredLifecycleSequence] = field(default_factory=list)
    test_server: TestServerContract = field(default_factory=TestServerContract)
    parse_errors: list[str] = field(default_factory=list)
    """Non-fatal parse issues (malformed YAML, unknown keys). Surfaced
    as warnings; empty list means clean parse."""


# ---------------------------------------------------------------------------
# YAML block extraction
# ---------------------------------------------------------------------------

# Match ```yaml fenced blocks. The `(.*?)` is non-greedy so we don't
# eat through to the next ``` in a different block.
_YAML_FENCE = re.compile(
    r"```ya?ml\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def _yaml_blocks_in(text: str) -> list[str]:
    """Return the contents of every ```yaml / ```yml fenced block.

    Order is source-order (top to bottom of the input text).
    """
    return [m.group(1) for m in _YAML_FENCE.finditer(text)]


# Section heading detection. Matches ``## 4a. ...`` / ``## 5b. ...`` /
# ``## Test Server`` etc. — case-insensitive on the keyword portion.
def _section_body(text: str, heading_pattern: str) -> str:
    """Return the body text under the FIRST heading matching
    ``heading_pattern`` (case-insensitive), up to the next ``## `` heading
    or end-of-text. Empty string when no match.

    ``heading_pattern`` is a regex applied to the line *after* the
    ``## `` prefix (e.g., ``r"4a\\.\\s+Negative Space"``).
    """
    lines = text.splitlines(keepends=False)
    in_section = False
    body: list[str] = []
    pat = re.compile(r"^##\s+" + heading_pattern, re.IGNORECASE)
    for line in lines:
        if in_section:
            if line.startswith("## "):
                break
            body.append(line)
            continue
        if pat.match(line):
            in_section = True
    return "\n".join(body)


# ---------------------------------------------------------------------------
# Per-block parsers
# ---------------------------------------------------------------------------

def _parse_forbidden_commands(
    raw: Any, errors: list[str],
) -> list[ForbiddenCommand]:
    if not isinstance(raw, list):
        errors.append(
            f"forbidden_commands: expected list, got {type(raw).__name__}"
        )
        return []
    out: list[ForbiddenCommand] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(
                f"forbidden_commands[{i}]: expected mapping, got "
                f"{type(item).__name__}"
            )
            continue
        pattern = item.get("pattern", "")
        if not isinstance(pattern, str) or not pattern:
            errors.append(
                f"forbidden_commands[{i}]: missing or non-string `pattern`"
            )
            continue
        reason = item.get("reason", "")
        applies = item.get("applies_to", []) or []
        if not isinstance(applies, list):
            errors.append(
                f"forbidden_commands[{i}]: `applies_to` must be a list"
            )
            applies = []
        out.append(ForbiddenCommand(
            pattern=pattern,
            reason=str(reason) if reason else "",
            applies_to=[str(a) for a in applies],
        ))
    return out


def _parse_forbidden_test_shapes(
    raw: Any, errors: list[str],
) -> list[ForbiddenTestShape]:
    if not isinstance(raw, list):
        errors.append(
            f"forbidden_test_shapes: expected list, got {type(raw).__name__}"
        )
        return []
    out: list[ForbiddenTestShape] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(
                f"forbidden_test_shapes[{i}]: expected mapping, got "
                f"{type(item).__name__}"
            )
            continue
        pattern = item.get("pattern", "")
        if not isinstance(pattern, str) or not pattern:
            errors.append(
                f"forbidden_test_shapes[{i}]: missing or non-string "
                f"`pattern`"
            )
            continue
        reason = item.get("reason", "")
        applies = item.get("applies_to", []) or []
        if not isinstance(applies, list):
            errors.append(
                f"forbidden_test_shapes[{i}]: `applies_to` must be a list"
            )
            applies = []
        out.append(ForbiddenTestShape(
            pattern=pattern,
            reason=str(reason) if reason else "",
            applies_to=[str(a) for a in applies],
        ))
    return out


def _parse_ecosystem_invariants(
    raw: Any, errors: list[str],
) -> list[EcosystemInvariant]:
    if not isinstance(raw, list):
        errors.append(
            f"ecosystem_invariants: expected list, got {type(raw).__name__}"
        )
        return []
    out: list[EcosystemInvariant] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(
                f"ecosystem_invariants[{i}]: expected mapping, got "
                f"{type(item).__name__}"
            )
            continue
        name = item.get("name", "")
        if not isinstance(name, str) or not name:
            errors.append(
                f"ecosystem_invariants[{i}]: missing or non-string `name`"
            )
            continue
        invariant = item.get("invariant", "")
        enforcement = item.get("enforcement", "")
        out.append(EcosystemInvariant(
            name=name,
            invariant=str(invariant) if invariant else "",
            enforcement=str(enforcement) if enforcement else "",
        ))
    return out


def _parse_required_lifecycle_sequences(
    raw: Any, errors: list[str],
) -> list[RequiredLifecycleSequence]:
    if not isinstance(raw, list):
        errors.append(
            f"required_lifecycle_sequences: expected list, got "
            f"{type(raw).__name__}"
        )
        return []
    out: list[RequiredLifecycleSequence] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(
                f"required_lifecycle_sequences[{i}]: expected mapping, "
                f"got {type(item).__name__}"
            )
            continue
        name = item.get("name", "")
        if not isinstance(name, str) or not name:
            errors.append(
                f"required_lifecycle_sequences[{i}]: missing or "
                f"non-string `name`"
            )
            continue
        seq = item.get("sequence", []) or []
        if not isinstance(seq, list) or not all(isinstance(s, str) for s in seq):
            errors.append(
                f"required_lifecycle_sequences[{i}]: `sequence` must be "
                f"a list of strings"
            )
            seq = []
        rationale = item.get("rationale", "")
        enforcement = item.get("enforcement", "")
        out.append(RequiredLifecycleSequence(
            name=name,
            sequence=[str(s) for s in seq],
            rationale=str(rationale) if rationale else "",
            enforcement=str(enforcement) if enforcement else "",
        ))
    return out


def _parse_test_server(
    raw: Any, errors: list[str],
) -> TestServerContract:
    if raw is None:
        return TestServerContract()
    if not isinstance(raw, dict):
        errors.append(
            f"test_server: expected mapping, got {type(raw).__name__}"
        )
        return TestServerContract()
    command = raw.get("command", "") or ""
    base_url = raw.get("base_url", "") or ""
    timeout = raw.get("startup_timeout_seconds", 10)
    if not isinstance(command, str):
        errors.append("test_server.command: must be a string")
        command = ""
    if not isinstance(base_url, str):
        errors.append("test_server.base_url: must be a string")
        base_url = ""
    try:
        timeout_int = int(timeout)
    except (TypeError, ValueError):
        errors.append(
            "test_server.startup_timeout_seconds: must be an integer"
        )
        timeout_int = 10
    return TestServerContract(
        command=command,
        base_url=base_url,
        startup_timeout_seconds=timeout_int,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_negative_space(text: str) -> NegativeSpaceFindings:
    """Parse a spec's §4a Negative Space block and §5b.test_server
    contract into a structured ``NegativeSpaceFindings``.

    Strategy: scan for ```yaml fenced blocks anywhere in §4a and §5b,
    union their top-level keys, dispatch to per-block parsers. Unknown
    keys at the top level are ignored (forward-compatibility — a future
    schema addition shouldn't break older tooling).

    A spec with no §4a section returns a fully-empty findings object
    with no parse errors — consumers decide whether emptiness is
    acceptable.
    """
    findings = NegativeSpaceFindings()

    # Scan §4a body for the four list-typed blocks.
    section_4a = _section_body(text, r"4a\.\s+")
    section_5b = _section_body(text, r"5b\.\s+")

    parsed_keys: dict[str, Any] = {}
    for body in (section_4a, section_5b):
        if not body:
            continue
        for raw in _yaml_blocks_in(body):
            try:
                doc = yaml.safe_load(raw)
            except yaml.YAMLError as e:
                findings.parse_errors.append(
                    f"YAML parse error in §4a/§5b block: {e}"
                )
                continue
            if not isinstance(doc, dict):
                # Bare list / scalar at top level — ignore. Specs use
                # mapping-of-named-blocks shape.
                continue
            for k, v in doc.items():
                # Last-write-wins on collisions; document this once
                # we have evidence specs intentionally split a key
                # across blocks (currently unsupported).
                parsed_keys[k] = v

    findings.forbidden_commands = _parse_forbidden_commands(
        parsed_keys.get("forbidden_commands", []), findings.parse_errors,
    )
    findings.forbidden_test_shapes = _parse_forbidden_test_shapes(
        parsed_keys.get("forbidden_test_shapes", []), findings.parse_errors,
    )
    findings.ecosystem_invariants = _parse_ecosystem_invariants(
        parsed_keys.get("ecosystem_invariants", []), findings.parse_errors,
    )
    findings.required_lifecycle_sequences = _parse_required_lifecycle_sequences(
        parsed_keys.get("required_lifecycle_sequences", []),
        findings.parse_errors,
    )
    findings.test_server = _parse_test_server(
        parsed_keys.get("test_server"), findings.parse_errors,
    )
    return findings


__all__ = [
    "ForbiddenCommand",
    "ForbiddenTestShape",
    "EcosystemInvariant",
    "RequiredLifecycleSequence",
    "TestServerContract",
    "NegativeSpaceFindings",
    "extract_negative_space",
]
