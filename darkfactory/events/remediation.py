"""C1 · Structured remediation events.

Every pre-implementer gate (A1 consistency, precheck, spec validator,
scenario validator, smoke test) that can fail before the implementer runs
emits a ``REMEDIATION_TARGET`` event describing *how* to fix the failure.
The *next* run emits a paired ``REMEDIATION_OUTCOME`` classifying whether
the applied fix worked. Together they give C5's policy auto-graduation
logic real success-rate data per rule — without them, "≥10 successful
Level 2-3 remediations" is unmeasurable.

Schema is versioned so future changes don't silently break consumers
(``remediate`` CLI, C3 failure-time UX, C5 calibration). Bump
``SCHEMA_VERSION`` and add a reader branch when you change field shapes.

All fields documented in the plan's §C1 JSON block. The event record
itself uses the standard events.jsonl envelope (``ts``, ``event``,
``spec``, ``run_id``, ``data``); all C1-specific fields live inside
``data`` so existing event-consumers keep parsing.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from darkfactory.events.emitter import emit_event
from darkfactory.models.events import Event, EventType

log = logging.getLogger(__name__)

__all__ = [
    "SCHEMA_VERSION",
    "FailureClass",
    "RemediationType",
    "AppliedBy",
    "Outcome",
    "emit_remediation_target",
    "emit_remediation_outcome",
    "emit_remediation_accepted_novel",
    "is_novel_rule",
    "most_recent_unpaired_target",
    "most_recent_target_for_spec",
    "find_targets_by_run_id",
    "find_latest_target",
    "classify_outcome",
    "aggregate_outcome_stats",
]

SCHEMA_VERSION = 1

# Literal types that double as documentation for consumers. Keep in
# lockstep with plan §C1 (line 510) — adding a new class requires
# bumping SCHEMA_VERSION and updating consumers (C2 remediate CLI,
# C3 failure-time UX, C5 calibration). ``stub_compile_error`` is
# reserved for the precheck stub_compile gate; not wired in Sprint 1
# (findings are advisory) but kept so the gate can adopt it without
# a schema bump.
FailureClass = Literal[
    "consistency_violation",
    "spec_quality",
    "precheck_finding",
    "stub_compile_error",
    "proposal_typecheck_failure",
]

RemediationType = Literal[
    "add_project_hint",
    "edit_spec",
    "edit_env_contract",
    "manual",
]

Outcome = Literal[
    "fixed",
    "same_failure_again",
    "different_failure",
    "shipped",
    "unresolved",
]

AppliedBy = Literal["human", "auto", "ci"]


def emit_remediation_target(
    events_file: Path,
    *,
    spec: str,
    run_id: str,
    failure_class: FailureClass,
    rule_or_check: str,
    remediation_type: RemediationType,
    remediation_target_path: str,
    auto_applicable: bool,
    rationale: str,
    suggested_diff: str = "",
    next_command: str = "",
    iteration: int = 0,
) -> Event:
    """Emit a REMEDIATION_TARGET event for a pre-implementer gate failure.

    ``rule_or_check`` is the stable identifier that ties the target to its
    eventual ``REMEDIATION_OUTCOME`` — use the A1 rule name, the precheck
    finding type, or a short stable slug for validator failures. Novelty
    detection (``novel_rule_name`` field) is computed from this value
    against prior events.jsonl records for the same project.

    ``next_command`` defaults to a generic ``dark-factory remediate
    <run-id>`` template when empty; callers may override with a more
    specific command (``dark-factory hints add <rule>``, etc.).

    ``events_file`` must not be ``None`` — emission is never optional.
    Gates that previously treated events.jsonl plumbing as deferred must
    now thread a concrete path in. For orchestrator contexts that lack a
    run_id (e.g., standalone ``dark-factory validate`` CLI), pass an
    empty string; the rest of the record remains valid.
    """
    novel = is_novel_rule(events_file, rule_or_check)
    data = {
        "schema_version": SCHEMA_VERSION,
        "failure_class": failure_class,
        "rule_or_check": rule_or_check,
        "remediation_type": remediation_type,
        "remediation_target_path": remediation_target_path,
        "auto_applicable": auto_applicable,
        "suggested_diff": suggested_diff,
        "rationale": rationale,
        "next_command": next_command or _default_next_command(run_id),
        "novel_rule_name": novel,
    }
    return emit_event(
        events_file,
        EventType.REMEDIATION_TARGET,
        spec_name=spec,
        run_id=run_id,
        iteration=iteration,
        data=data,
    )


def emit_remediation_outcome(
    events_file: Path,
    *,
    spec: str,
    run_id: str,
    remediation_run_id: str,
    rule_or_check: str,
    remediation_type: RemediationType,
    outcome: Outcome,
    applied_by: AppliedBy = "human",
    applied_at: str = "",
    iteration: int = 0,
) -> Event:
    """Emit a REMEDIATION_OUTCOME paired to an earlier REMEDIATION_TARGET.

    ``remediation_run_id`` is the ``run_id`` of the original failing run
    (the one that emitted the target). This run's ``run_id`` is the
    rerun. One paired outcome per target — emit exactly once when this
    run's fate is decided (ship, another failure of the same rule, a
    different failure, or unresolved at the end).

    ``applied_at`` defaults to "now" (UTC ISO-8601) if empty — matches
    the emission timestamp within a second, which is accurate enough
    for the calibration window.
    """
    data = {
        "schema_version": SCHEMA_VERSION,
        "remediation_run_id": remediation_run_id,
        "rule_or_check": rule_or_check,
        "remediation_type": remediation_type,
        "outcome": outcome,
        "applied_at": applied_at or _utc_iso_now(),
        "applied_by": applied_by,
    }
    return emit_event(
        events_file,
        EventType.REMEDIATION_OUTCOME,
        spec_name=spec,
        run_id=run_id,
        iteration=iteration,
        data=data,
    )


def emit_remediation_accepted_novel(
    events_file: Path,
    *,
    spec: str,
    run_id: str,
    rule_or_check: str,
    remediation_type: RemediationType,
    applied_by: AppliedBy = "human",
    reason: str = "",
) -> Event:
    """Emit a REMEDIATION_ACCEPTED_NOVEL event (C5 audit trail).

    Fires when ``dark-factory remediate --apply --accept-novel`` bypasses
    the first-occurrence guard on a rule_or_check that has never been
    applied to this project before. The plan requires this be logged
    "as a degraded run" so a later reviewer can reconstruct which novel
    rules entered the project without human y/n confirmation.

    ``spec`` / ``run_id`` name the *failing* run whose target is being
    applied (not the current remediate invocation, which doesn't have a
    stable run_id of its own). ``applied_by`` distinguishes human
    operators from CI contexts — CI should be rare here, since
    ``--accept-novel`` is an explicit override that shouldn't be a
    default in CI configs.
    """
    data = {
        "schema_version": SCHEMA_VERSION,
        "rule_or_check": rule_or_check,
        "remediation_type": remediation_type,
        "applied_by": applied_by,
        "reason": reason,
    }
    return emit_event(
        events_file,
        EventType.REMEDIATION_ACCEPTED_NOVEL,
        spec_name=spec,
        run_id=run_id,
        data=data,
    )


def aggregate_outcome_stats(
    events_file: Path,
) -> dict[str, dict[str, int]]:
    """Group REMEDIATION_OUTCOME events by ``rule_or_check`` and tally
    per-outcome counts.

    Returns a dict shaped as ``{rule_name: {outcome: count, ...,
    "total": n}}`` where the inner dict carries every ``Outcome`` literal
    observed for that rule plus a ``"total"`` sum. Rules with zero
    outcome events are not present in the result — absence is the
    absence-of-evidence signal ``hints stats`` surfaces as "no data".

    Missing/empty events files return an empty dict — a brand-new
    project has no outcomes to aggregate. **Raises ``OSError``** when
    the file exists but is unreadable (permission denied, etc.) so the
    CLI can distinguish the benign empty case from a configuration
    issue; the user wants different messages for "no data yet" vs "can't
    read the file." Torn-write tolerant via the same
    ``_read_records_tolerant`` pathway used everywhere else in this
    module.
    """
    records = _read_records_tolerant(events_file, raise_on_os_error=True)
    if not records:
        return {}

    stats: dict[str, dict[str, int]] = {}
    for rec in records:
        if rec.get("event") != EventType.REMEDIATION_OUTCOME.value:
            continue
        data = rec.get("data") or {}
        rule = data.get("rule_or_check")
        outcome = data.get("outcome")
        if not rule or not outcome:
            # Malformed payload — skip rather than crash. C1's schema
            # pins these fields, but a future schema-version bump could
            # rename them; defensive skip keeps older callers working.
            continue
        bucket = stats.setdefault(rule, {"total": 0})
        bucket[outcome] = bucket.get(outcome, 0) + 1
        bucket["total"] += 1
    return stats


def is_novel_rule(events_file: Path, rule_or_check: str) -> bool:
    """Return True when this rule_or_check has never been seen in any
    prior REMEDIATION_TARGET event for this project.

    Project-scoped, not spec-scoped — a rule that fired on spec A
    ceases to be "novel" for spec B even though the two specs are
    independent. This matches the plan's C5 semantics:
    novel-on-first-occurrence triggers human confirmation *once per
    project*, not once per spec.

    Returns True for missing/empty events.jsonl (bootstrapping case:
    the project has no history so *every* rule is novel). Tolerates
    malformed JSONL lines — torn appends from concurrent writers
    should not flip every rule back to "novel" on read. Returns True
    only when the file is entirely unreadable (OSError), which defers
    the calibration signal to human review rather than silently
    marking every repeat rule familiar.
    """
    records = _read_records_tolerant(events_file)
    if records is None:
        return True

    for rec in records:
        if rec.get("event") != EventType.REMEDIATION_TARGET.value:
            continue
        data = rec.get("data") or {}
        if data.get("rule_or_check") == rule_or_check:
            return False
    return True


def _read_records_tolerant(
    events_file: Path, *, raise_on_os_error: bool = False,
) -> list[dict] | None:
    """Read events.jsonl and skip unparseable lines (warn, don't raise).

    Returns ``None`` on OSError (file is entirely unreadable — e.g.
    permission error or the path is a directory). Returns an empty
    list when the file is missing or empty. Individual bad lines are
    logged at WARNING and skipped; the rest of the file is still
    usable.

    With ``raise_on_os_error=True``, propagates the OSError instead of
    returning ``None``. Used by finders called from user-facing CLI
    paths where "file unreadable" is a distinct signal the user needs
    to see (different from "file has no matching records").

    ``append_jsonl`` does not hold a file lock; torn-line races are
    rare but possible. A single JSONDecodeError on one line must not
    shadow hundreds of valid records.
    """
    if not events_file.exists():
        return []

    try:
        with events_file.open() as f:
            raw_lines = f.readlines()
    except OSError as exc:
        if raise_on_os_error:
            raise
        log.warning("remediation: cannot read %s: %s", events_file, exc)
        return None

    records: list[dict] = []
    for lineno, line in enumerate(raw_lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            records.append(json.loads(stripped))
        except ValueError as exc:
            log.warning(
                "remediation: skipping malformed JSONL line %d in %s: %s",
                lineno, events_file, exc,
            )
    return records


def most_recent_unpaired_target(
    events_file: Path,
    spec: str,
    *,
    exclude_run_id: str = "",
) -> dict | None:
    """Return the most-recent REMEDIATION_TARGET for ``spec`` that
    (a) was emitted by a run *other than* ``exclude_run_id`` and
    (b) has no matching REMEDIATION_OUTCOME pointing at its run_id.

    Used at terminal-state emission to find the prior run's unpaired
    target. Crucially, passing ``exclude_run_id=current_run_id`` prevents
    the current run's own targets from shadowing a genuinely-unpaired
    prior target — without this, a chained failure (run_N failed, human
    edited, run_N+1 also fails before terminal) would orphan run_N's
    target forever, breaking the "exactly one outcome per target"
    invariant the plan requires.

    Also enforces idempotency on resume: if an outcome event already
    exists for a given target.run_id, we don't re-pair that target.

    Tolerates malformed JSONL lines — a torn append should not block
    pairing the rest of the log. Returns ``None`` on OSError.
    """
    records = _read_records_tolerant(events_file)
    if records is None:
        return None

    # Collect all remediation_run_ids that already have outcomes. A target
    # is "paired" iff one of these matches its run_id.
    paired_run_ids: set[str] = set()
    for rec in records:
        if rec.get("event") != EventType.REMEDIATION_OUTCOME.value:
            continue
        rid = (rec.get("data") or {}).get("remediation_run_id")
        if isinstance(rid, str) and rid:
            paired_run_ids.add(rid)

    for rec in reversed(records):
        if rec.get("event") != EventType.REMEDIATION_TARGET.value:
            continue
        if rec.get("spec") != spec:
            continue
        target_run_id = rec.get("run_id", "")
        if exclude_run_id and target_run_id == exclude_run_id:
            continue
        if target_run_id in paired_run_ids:
            continue
        return rec
    return None


# Backwards-compatible alias — `most_recent_target_for_spec` is still used
# by tests that don't care about pairing semantics. Kept so the public
# API grows cleanly.
def most_recent_target_for_spec(
    events_file: Path,
    spec: str,
) -> dict | None:
    """Return the most-recent REMEDIATION_TARGET for ``spec`` (paired or
    not). Prefer ``most_recent_unpaired_target`` at pair-emission sites."""
    records = _read_records_tolerant(events_file)
    if records is None:
        return None
    for rec in reversed(records):
        if rec.get("event") != EventType.REMEDIATION_TARGET.value:
            continue
        if rec.get("spec") != spec:
            continue
        return rec
    return None


def find_targets_by_run_id(
    events_file: Path,
    run_id: str,
) -> list[dict]:
    """Return all REMEDIATION_TARGET events emitted by ``run_id``, most-
    recent-first.

    A single pipeline run may emit multiple targets (one per rule
    violation at A1, for example). The C2 ``remediate`` CLI uses this
    to present the full list so the user can pick which contradiction
    to codify first; ``--apply`` without further qualification acts on
    the most-recent (index 0).

    Returns an empty list when the file is missing, empty, or contains
    no matching target. **Raises ``OSError``** when the file exists but
    is unreadable (permission denied, path-is-directory, etc.) — the
    CLI caller distinguishes "unreadable file" from "no targets for
    this run_id" so the user knows whether to fix permissions or check
    their run_id.

    Tolerates malformed JSONL lines — a torn append from a concurrent
    writer must not hide valid targets.
    """
    if not run_id:
        # Empty run_id would match envelopes with missing/empty run_id,
        # which is never what the caller wants (C2's CLI layer rejects
        # empty run_id at arg-parse time). Guard here as belt-and-braces
        # for direct callers that forget the check.
        return []

    records = _read_records_tolerant(events_file, raise_on_os_error=True)
    # With raise_on_os_error=True, None is never returned — but mypy
    # doesn't know that, hence the defensive check.
    if records is None:
        return []

    matches: list[dict] = []
    for rec in records:
        if rec.get("event") != EventType.REMEDIATION_TARGET.value:
            continue
        if rec.get("run_id") != run_id:
            continue
        matches.append(rec)

    # Most-recent-first matches the file's append order. No timestamp-
    # based reordering — a torn write could produce out-of-order records
    # but the append order is what the user's recollection maps to
    # ("the last thing that happened in that run"), so we preserve it.
    matches.reverse()
    return matches


def find_latest_target(events_file: Path) -> dict | None:
    """Return the most-recent REMEDIATION_TARGET across the entire file.

    Used by ``dark-factory remediate --latest`` when the user omits a
    run_id — they want "fix whatever most recently broke". No pairing
    filter (that's a separate concept handled by
    :func:`most_recent_unpaired_target`): if the user already paired a
    prior target, C2 should not silently skip it — it's still the
    most-recent failure-shape signal they have.

    Returns ``None`` when the file is missing, empty, or has no
    REMEDIATION_TARGET events. **Raises ``OSError``** when the file
    exists but is unreadable.
    """
    records = _read_records_tolerant(events_file, raise_on_os_error=True)
    if records is None:
        return None
    for rec in reversed(records):
        if rec.get("event") == EventType.REMEDIATION_TARGET.value:
            return rec
    return None


def classify_outcome(
    events_file: Path,
    *,
    prior_target: dict,
    this_run_id: str,
    terminal_status: Literal["shipped", "escalated", "failed", "stable_failure"],
    gates_executed: bool = True,
) -> Outcome:
    """Classify how *this* run resolved (or failed to resolve) the
    ``prior_target``'s flagged rule.

    Rules, in order:
    - SHIPPED → "shipped" (the user cares that it shipped; rule-equivalence
      is less interesting than that the pipeline made it through).
    - Current run emitted a ``REMEDIATION_TARGET`` with the *same*
      ``rule_or_check`` → "same_failure_again".
    - Current run emitted a ``REMEDIATION_TARGET`` with a *different*
      ``rule_or_check`` → "different_failure".
    - Terminal status failed/escalated with NO new target this run AND
      at least one pre-implementer gate executed → "fixed": the prior
      rule stopped firing, even though the pipeline aborted for a
      later/unrelated reason. C5 should count this as a successful
      remediation for the prior rule.
    - Terminal status failed/escalated with NO new target this run AND
      ``gates_executed=False`` → "unresolved": the pipeline aborted
      before any pre-implementer gate ran (budget exhausted at startup,
      dry-run abort, bootstrap exception). There is no evidence the
      prior rule is fixed — crediting "fixed" here would inflate C5's
      per-rule success rate and wrongly auto-graduate a rule the user
      never actually re-tested.

    ``gates_executed`` is passed by the orchestrator as ``spec_run.
    completed_step > 0`` — step 1 is spec validation, so any non-zero
    value means at least the spec validator gate ran. Default is True
    so that direct unit-test callers (which construct synthetic events
    and don't plumb pipeline state) keep the prior, more generous
    semantic; production callsites always pass the real flag.

    Reads events.jsonl once up-front, tolerating malformed lines.
    """
    prior_rule = (prior_target.get("data") or {}).get("rule_or_check")
    if terminal_status == "shipped":
        return "shipped"

    records = _read_records_tolerant(events_file)
    if records is None:
        return "unresolved"

    this_run_rules: list[str] = []
    for rec in records:
        if rec.get("event") != EventType.REMEDIATION_TARGET.value:
            continue
        if rec.get("run_id") != this_run_id:
            continue
        rule = (rec.get("data") or {}).get("rule_or_check")
        if rule:
            this_run_rules.append(rule)

    if not this_run_rules:
        if not gates_executed:
            # Pipeline aborted before any pre-implementer gate ran.
            # We have no evidence that the prior rule is fixed; crediting
            # "fixed" here would poison C5's calibration counter.
            return "unresolved"
        # No new gate fired this run and at least one gate ran. The prior
        # rule is fixed from the gate's perspective; failure came from
        # elsewhere (implementer, oracle, deploy, ops). Credit the
        # remediation.
        return "fixed"

    if prior_rule in this_run_rules:
        return "same_failure_again"
    return "different_failure"


def _default_next_command(run_id: str) -> str:
    if run_id:
        return f"dark-factory remediate {run_id}"
    return "dark-factory remediate --latest"


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
