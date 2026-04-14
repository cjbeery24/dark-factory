"""Post-mortem calibration dataset — one JSONL row per pipeline run.

E2 from ``docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md``.

**This is data capture, not a gate.** Each SHIP or ESCALATED run appends one
row to ``docs/post-mortem-dataset.jsonl``. Over ~10+ runs the dataset
becomes input for:

- Validator-score calibration (deflate scores that consistently precede
  escalation)
- Architecture-consistency-check pair-table growth (each escalation
  surfaces new ``spec-phrase × env-command`` pairs for A1)
- Validator prompt shaping (feed recent post-mortems as context)

Rows are authored manually while writing a ``docs/pipeline-run-reports/*.md``
post-mortem. The CLI entry point is ``dark-factory post-mortem append``.
The data-producing step is a human decision (which root causes apply); the
schema exists to make that decision structured and comparable across runs.

## Schema

Each row is a JSON object with the following fields:

``schema_version`` (int)
    Monotonic version. Increment on breaking field changes.

``spec_id`` (str)
    The spec's stable identifier, e.g. ``"2026-03-28-database-foundation"``.

``run_id`` (str)
    Pipeline run id from events.jsonl. Uniquely identifies this row among
    multiple runs of the same spec.

``date`` (str)
    ISO-8601 date (``YYYY-MM-DD``) — when the run finished.

``target_project`` (str)
    Name of the project the pipeline ran against, e.g. ``"budget-brain-v2"``.

``outcome`` (str)
    One of ``OUTCOMES`` — {"shipped", "escalated", "aborted"}.

``iterations`` (int)
    Number of iterations actually completed (1–3 today; may exceed 3 later).

``validator_verdict`` (str)
    One of ``VALIDATOR_VERDICTS`` — {"PASS", "FAIL"}.

``validator_score`` (int | null)
    Percentage 0–100 from spec-validator phase, or null if unavailable.

``validator_divergence`` (str | null)
    One of ``VALIDATOR_DIVERGENCE`` — {"LOW", "MEDIUM", "HIGH"}, or null.

``validator_blockers`` (list[str])
    Short blocker labels reported by the validator. Strings only; no
    vocabulary restriction — validator phrasing varies and that is the
    signal we want to preserve.

``stuck_set_categories`` (list[str])
    Free-form human-assigned tags describing the stable failing-set at
    escalation. Empty for SHIP outcomes.

``root_cause_codes`` (list[str])
    Codes from ``ROOT_CAUSE_CODES`` vocabulary. Must all be known codes
    — unknown codes raise ValueError. Grow the vocabulary by editing the
    frozenset below, not by passing free-form strings.

``dark_factory_head`` (str | null)
    Git SHA of dark-factory at run start, for repro.

``notes`` (str | null)
    One-line free-form note. No paragraphs — if it wraps, put it in the
    post-mortem document, not here.
"""
from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from darkfactory.utils.json_helpers import append_jsonl


# Schema version. Bump on breaking field changes. Readers check this.
SCHEMA_VERSION = 1

# Allowed values for constrained enum-like fields. Strings, not Enum, so
# the JSONL stays JSON-native without a custom serializer.
OUTCOMES: frozenset[str] = frozenset({"shipped", "escalated", "aborted"})
VALIDATOR_VERDICTS: frozenset[str] = frozenset({"PASS", "FAIL"})
VALIDATOR_DIVERGENCE: frozenset[str] = frozenset({"LOW", "MEDIUM", "HIGH"})

# Root cause code vocabulary. Grow this frozenset as new failure modes
# emerge in post-mortems; do NOT pass free-form strings at runtime — the
# whole point of the dataset is calibration across comparable codes.
#
# The three codes called out in the plan's E2 acceptance are:
#   broken_scenarios, architect_spec_drift, slow_implementer.
# Anything else in this set is drawn from the 2026-04-14 post-mortem's
# enumerated failure modes or from the existing escalation-report template.
ROOT_CAUSE_CODES: frozenset[str] = frozenset({
    # Scenario-side failure modes
    "broken_scenarios",                    # scenarios assert things not in spec
    "scenarios_assert_library_internals",  # e.g., ``_`` drizzle internals
    "scenarios_beyond_spec_scope",
    # Architect-side
    "architect_spec_drift",                # env contract contradicts spec
    # Implementer-side
    "slow_implementer",                    # ignored feedback for multiple iters
    "wrong_architectural_approach",
    # Spec-side
    "spec_ambiguity",
    "spec_incomplete",
    "feature_too_complex_for_single_spec",
    # Infra / pipeline-side
    "classifier_silent_fail",              # classifier errored but didn't gate
    "test_infrastructure_problem",
    "external_dependency_issue",
})

# Codes must match this shape to avoid typoed free-form strings sneaking
# past the frozenset check if someone bypasses validation.
_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class PostMortemRow:
    """A single calibration-dataset row. All fields validated on __post_init__."""

    spec_id: str
    run_id: str
    date: str                                # YYYY-MM-DD
    target_project: str
    outcome: str                             # in OUTCOMES
    iterations: int
    validator_verdict: str                   # in VALIDATOR_VERDICTS
    validator_score: int | None = None       # 0–100 or None
    validator_divergence: str | None = None  # in VALIDATOR_DIVERGENCE or None
    validator_blockers: tuple[str, ...] = ()
    stuck_set_categories: tuple[str, ...] = ()
    root_cause_codes: tuple[str, ...] = ()
    dark_factory_head: str | None = None
    notes: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_nonempty(self.spec_id, "spec_id")
        _require_nonempty(self.run_id, "run_id")
        _require_nonempty(self.target_project, "target_project")
        if not _DATE_PATTERN.match(self.date):
            raise ValueError(
                f"date must be YYYY-MM-DD, got {self.date!r}"
            )
        if self.outcome not in OUTCOMES:
            raise ValueError(
                f"outcome must be one of {sorted(OUTCOMES)}, got {self.outcome!r}"
            )
        if self.validator_verdict not in VALIDATOR_VERDICTS:
            raise ValueError(
                f"validator_verdict must be one of {sorted(VALIDATOR_VERDICTS)}, "
                f"got {self.validator_verdict!r}"
            )
        if (
            self.validator_divergence is not None
            and self.validator_divergence not in VALIDATOR_DIVERGENCE
        ):
            raise ValueError(
                f"validator_divergence must be one of "
                f"{sorted(VALIDATOR_DIVERGENCE)} or None, "
                f"got {self.validator_divergence!r}"
            )
        if self.iterations < 0:
            raise ValueError(f"iterations must be >= 0, got {self.iterations}")
        if self.validator_score is not None and not (
            0 <= self.validator_score <= 100
        ):
            raise ValueError(
                f"validator_score must be 0..100 or None, "
                f"got {self.validator_score}"
            )
        # Root cause codes — every code must be in the vocab.
        unknown = [c for c in self.root_cause_codes if c not in ROOT_CAUSE_CODES]
        if unknown:
            raise ValueError(
                f"unknown root_cause_codes: {unknown}. "
                f"Add new codes by editing ROOT_CAUSE_CODES in "
                f"darkfactory/calibration/post_mortem.py, not by passing "
                f"free-form strings."
            )
        # Defense in depth: shape-check every code.
        for c in self.root_cause_codes:
            if not _CODE_PATTERN.match(c):
                raise ValueError(
                    f"malformed root cause code {c!r} "
                    f"(expected snake_case matching {_CODE_PATTERN.pattern})"
                )
        # Notes must be a one-liner.
        if self.notes is not None and "\n" in self.notes:
            raise ValueError(
                "notes must be a one-liner; put paragraphs in the "
                "post-mortem report, not in the dataset row"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-ready dict. Tuples become lists.

        Field order: ``schema_version`` first so a reader can short-circuit
        on a version mismatch without parsing the rest of the line.
        """
        d = dataclasses.asdict(self)
        # asdict() leaves tuples as tuples; the JSON encoder would coerce
        # them to lists implicitly, but we want the dict itself to be
        # JSON-shaped so equality checks against parsed JSON match.
        for k, v in list(d.items()):
            if isinstance(v, tuple):
                d[k] = list(v)
        ordered = {"schema_version": d.pop("schema_version")}
        ordered.update(d)
        return ordered


def _require_nonempty(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def append_row(row: PostMortemRow, *, path: Path) -> None:
    """Append one row to the calibration JSONL.

    Uses ``O_APPEND`` semantics (via ``open("a")``) so concurrent appends
    from two processes interleave at line boundaries rather than
    interleaving mid-line. Parent directory is created if missing.
    """
    append_jsonl(path, row.to_dict())


def default_dataset_path(project_root: Path) -> Path:
    """Return the canonical path ``docs/post-mortem-dataset.jsonl``."""
    return project_root / "docs" / "post-mortem-dataset.jsonl"


def read_rows(path: Path) -> list[PostMortemRow]:
    """Read and re-validate every row in the dataset.

    Any row that fails validation raises — the whole point of the
    dataset is integrity, so a corrupt row is a bug we want to see.
    """
    if not path.exists():
        return []
    rows: list[PostMortemRow] = []
    with path.open() as f:
        for i, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"{path}:{i}: invalid JSON — {e}"
                ) from e
            rows.append(_row_from_dict(raw, source=f"{path}:{i}"))
    return rows


def _row_from_dict(raw: dict[str, Any], *, source: str) -> PostMortemRow:
    """Build a PostMortemRow from a raw dict, preserving validation."""
    # Accept both list and tuple in the wire format; dataclass needs tuple.
    def _as_tuple(v: Any) -> tuple[str, ...]:
        if v is None:
            return ()
        if isinstance(v, (list, tuple)):
            return tuple(v)
        raise ValueError(f"{source}: expected list, got {type(v).__name__}")

    # Check schema_version BEFORE required-field validation so a
    # forward-compat row (with new required fields we don't know about)
    # fails with a clear version-mismatch message instead of a
    # "missing field" message about a field that doesn't exist in our
    # schema yet.
    schema_version = raw.get("schema_version", 1)
    if schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"{source}: unsupported schema_version {schema_version} "
            f"(this build reads version {SCHEMA_VERSION})"
        )

    # Wrap missing required keys with the source location — otherwise
    # KeyError propagates with no file:line context, which defeats the
    # point of the source-tracking we already pay for in read_rows.
    for required in (
        "spec_id", "run_id", "date", "target_project",
        "outcome", "iterations", "validator_verdict",
    ):
        if required not in raw:
            raise ValueError(
                f"{source}: missing required field {required!r}"
            )
    return PostMortemRow(
        spec_id=raw["spec_id"],
        run_id=raw["run_id"],
        date=raw["date"],
        target_project=raw["target_project"],
        outcome=raw["outcome"],
        iterations=int(raw["iterations"]),
        validator_verdict=raw["validator_verdict"],
        validator_score=raw.get("validator_score"),
        validator_divergence=raw.get("validator_divergence"),
        validator_blockers=_as_tuple(raw.get("validator_blockers")),
        stuck_set_categories=_as_tuple(raw.get("stuck_set_categories")),
        root_cause_codes=_as_tuple(raw.get("root_cause_codes")),
        dark_factory_head=raw.get("dark_factory_head"),
        notes=raw.get("notes"),
    )
