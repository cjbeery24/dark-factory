"""Precheck advisory persistence — shared by orchestrator and CLI validate.

Phase 0 of the resilience rewrite. The same `precheck_advisory.json` payload
must be written from both the pipeline runner and the standalone validate
command, byte-identical for the same spec content. The advisory is mounted
into the Architect container as input context (it is not used as a gate).

Location: `.dark-factory/precheck/<spec>.json`. This is a NEW top-level
directory, deliberately outside `.dark-factory/state/` because state is
masked from the Architect container (see darkfactory/docker/barriers.py).
The precheck directory is not in any agent's mask list, so every agent that
has the project mounted can read it.

The JSON payload is deterministic: sorted keys, ASCII-only, fixed indent.
Two writes against the same `PreCheckResult` MUST produce the same bytes.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from darkfactory.spec.precheck import PreCheckResult


__all__ = [
    "ensure_precheck_dir",
    "precheck_advisory_path",
    "build_advisory_payload",
    "write_precheck_advisory",
]


def ensure_precheck_dir(project_dir: Path) -> Path:
    """Idempotently create `.dark-factory/precheck/` and return its path."""
    target = project_dir / ".dark-factory" / "precheck"
    target.mkdir(parents=True, exist_ok=True)
    return target


def precheck_advisory_path(project_dir: Path, spec_name: str) -> Path:
    """Return the canonical path for a spec's precheck advisory JSON.

    Pure: does not touch the filesystem. Callers that need the directory to
    exist must call `ensure_precheck_dir` explicitly (or use
    `write_precheck_advisory`, which does it for them).
    """
    return project_dir / ".dark-factory" / "precheck" / f"{spec_name}.json"


def _finding_to_dict(obj: Any) -> Any:
    """Convert dataclass findings into JSON-friendly dicts.

    AnchorManifest is intentionally excluded from the advisory payload — it
    is persisted separately by the orchestrator (see orchestrator.py:295).
    """
    if obj is None:
        return None
    if is_dataclass(obj):
        return asdict(obj)
    return obj


def build_advisory_payload(result: PreCheckResult) -> dict[str, Any]:
    """Build the deterministic dict that gets serialized to JSON.

    Excludes the anchor manifest (persisted separately). Includes the rendered
    `summary` string so the Architect sees the same human-readable context the
    operator sees on a fatal abort. `summary` is a deterministic function of
    `result`, so byte-identity holds.
    """
    return {
        "fatal": result.fatal,
        "summary": result.summary,
        "structure": {
            "score": result.structure.score,
            "passed": result.structure.passed,
            "sections": [
                {
                    "name": s.name,
                    "status": s.status.value,
                    "line_number": s.line_number,
                }
                for s in result.structure.sections
            ],
        },
        "placeholders": [_finding_to_dict(p) for p in result.placeholders],
        "ambiguities": [_finding_to_dict(a) for a in result.ambiguities],
        "criteria_issues": [_finding_to_dict(c) for c in result.criteria_issues],
        "shape_claims": [
            {
                "section": sc.section,
                "line_number": sc.line_number,
                "matched_token": sc.matched_token,
                "context": sc.context,
                "section_path": list(sc.section_path),
                "available_wire_contracts": sc.available_wire_contracts,
                "would_break_table": sc.would_break_table,
            }
            for sc in result.shape_claims
        ],
        "anchor_intent_issues": list(result.anchor_intent_issues),
        "service_advisories": [
            {
                "service_keyword": sa.service_keyword,
                "line_number": sa.line_number,
                "context": sa.context,
            }
            for sa in result.service_advisories
        ],
        "precision": [
            {
                "pattern_id": pf.pattern_id,
                "section": pf.section,
                "line_number": pf.line_number,
                "matched_text": pf.matched_text,
                "message": pf.message,
            }
            for pf in result.precision
        ],
        "fr_breadth": [
            {
                "fr_id": fb.fr_id,
                "intent_count": fb.intent_count,
                "threshold": fb.threshold,
            }
            for fb in result.fr_breadth
        ],
        "fr_count": result.fr_count,
        "scenario_intents": (
            {
                "present": result.scenario_intents.present,
                "fr_intent_counts": dict(
                    sorted(result.scenario_intents.fr_intent_counts.items())
                ),
                "issues": list(result.scenario_intents.issues),
            }
            if result.scenario_intents is not None
            else None
        ),
        "contracts": (
            {
                "present": result.contracts.present,
                "count": result.contracts.count,
                "invariant_count": result.contracts.invariant_count,
                "errors": list(result.contracts.errors),
            }
            if result.contracts is not None
            else None
        ),
        "blocking_acs": dict(result.blocking_acs),
        "blocking_ac_issues": [
            {
                "kind": issue.kind,
                "line_number": issue.line_number,
                "message": issue.message,
                "id": issue.id,
            }
            for issue in result.blocking_ac_issues
        ],
    }


def write_precheck_advisory(
    project_dir: Path, spec_name: str, result: PreCheckResult
) -> Path:
    """Serialize the precheck result to its canonical advisory path.

    Output bytes are stable: sorted keys, ASCII-escaped non-ASCII, two-space
    indent, trailing newline. Two calls with the same `PreCheckResult` data
    produce byte-identical files. Phase 0 exit criteria depend on this.
    """
    ensure_precheck_dir(project_dir)
    path = precheck_advisory_path(project_dir, spec_name)
    payload = build_advisory_payload(result)
    text = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True)
    path.write_text(text + "\n", encoding="utf-8")
    return path
