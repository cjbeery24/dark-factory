"""Phase 6.5 — plan rollback.

``dark-factory plan rollback <plan> --to <spec>`` drops every delta file
contributed by specs that shipped *after* the target spec, refolds the
remaining delta log to rematerialize ``environment.toml``, and appends a
rollback record to ``.dark-factory/plan/rollbacks.jsonl``.

Design decisions:

- **shipped.jsonl is not mutated.** It's append-only by contract (Phase
  5.1). The record of "what shipped when" is immutable history; what
  changes is whether those ships are still *active*. The rollback log
  is a separate sidecar file that callers can consult to filter out
  rolled-back specs.

- **Archived scenario directories are not deleted.** They're part of
  the audit trail. A rolled-back spec whose archive still exists can be
  inspected; deleting them would make post-mortem analysis harder. The
  smoke probe already refuses to run scenarios for stranded specs
  (their env hash is unreachable after rollback), so keeping the
  archives around has no correctness cost.

- **The target spec is preserved.** `--to alpha` means "roll back TO
  alpha", i.e. alpha and everything before it stays shipped. Everything
  after alpha is rolled back. If the target is not in shipped.jsonl,
  rollback refuses.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from darkfactory.architecture.deltas import materialize
from darkfactory.plan.state import load_shipped

log = logging.getLogger(__name__)

_ROLLBACK_RELPATH = Path(".dark-factory") / "plan" / "rollbacks.jsonl"


@dataclass
class RollbackResult:
    target: str
    rolled_back_specs: list[str] = field(default_factory=list)
    removed_deltas: list[str] = field(default_factory=list)
    new_env_hash: str = ""


class RollbackError(Exception):
    """Raised when a rollback cannot be completed (target not found,
    delta file missing, etc.)."""


def rollback_log_path(project_dir: Path) -> Path:
    return project_dir / _ROLLBACK_RELPATH


def load_rolled_back(project_dir: Path) -> set[str]:
    """Read the rollback log and return specs covered by a *committed* rollback.

    Pending rollbacks (written before delete/materialize, not yet marked
    committed) are ALSO treated as rolled-back from the reader's
    perspective: the operator intended to roll them back, and if the
    process crashed mid-rollback we must not show them as still-shipped.
    A human recovery step can clear stale pending records if needed.

    Missing file → empty set. Malformed lines raise.
    """
    path = rollback_log_path(project_dir)
    if not path.exists():
        return set()
    result: set[str] = set()
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RollbackError(
                f"rollbacks.jsonl line {lineno}: invalid JSON: {exc}"
            ) from exc
        # Both pending and committed records mask the specs; only an
        # explicit "aborted" record would un-mask (future feature).
        if raw.get("status") == "aborted":
            continue
        for spec in raw.get("rolled_back_specs", []):
            result.add(spec)
    return result


def _append_rollback_record(
    project_dir: Path, result: RollbackResult, *, status: str
) -> None:
    """Append a rollback record with the given status (pending/committed/failed)."""
    path = rollback_log_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "target": result.target,
            "status": status,
            "rolled_back_specs": result.rolled_back_specs,
            "removed_deltas": result.removed_deltas,
            "new_env_hash": result.new_env_hash,
            "rolled_back_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        },
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())


def rollback_plan_to(
    project_dir: Path, target_spec: str, *, dry_run: bool = False
) -> RollbackResult:
    """Roll the plan back to ``target_spec`` inclusive.

    Preconditions:
    - ``target_spec`` must appear in shipped.jsonl.
    - Every rolled-back spec whose ``delta_filename`` is set must have
      that delta file still present on disk, otherwise the operation
      refuses rather than produce a partial rollback.

    With ``dry_run=True``, the result is computed but no files are
    deleted and no rollback record is written.
    """
    rows = load_shipped(project_dir, strict=True)
    spec_ids = [r.spec_id for r in rows]
    if target_spec not in spec_ids:
        raise RollbackError(
            f"rollback target {target_spec!r} is not in shipped.jsonl"
        )

    # Already-rolled-back specs must not be double-counted.
    previously_rolled = load_rolled_back(project_dir)

    target_idx = spec_ids.index(target_spec)
    later_rows = [
        r for r in rows[target_idx + 1:] if r.spec_id not in previously_rolled
    ]

    result = RollbackResult(target=target_spec)
    result.rolled_back_specs = [r.spec_id for r in later_rows]

    deltas_dir = project_dir / ".dark-factory" / "architecture" / "environment.deltas"
    delta_files_to_remove: list[Path] = []
    for r in later_rows:
        if not r.delta_filename:
            continue
        delta_path = deltas_dir / r.delta_filename
        if not delta_path.exists():
            raise RollbackError(
                f"delta file {r.delta_filename!r} for rolled-back spec "
                f"{r.spec_id!r} not found — refusing to partial-rollback"
            )
        delta_files_to_remove.append(delta_path)
    result.removed_deltas = [p.name for p in delta_files_to_remove]

    if dry_run:
        return result

    # Two-phase commit: write a 'pending' record BEFORE we unlink any
    # delta files. load_rolled_back() treats pending rows as rolled-back
    # so load_shipped() returns the post-rollback view immediately. If
    # the process crashes between unlink and materialize, the pending
    # record is still on disk and operators have an audit trail of
    # "rollback was attempted, finish it by hand".
    _append_rollback_record(project_dir, result, status="pending")

    for p in delta_files_to_remove:
        p.unlink()

    env_path = project_dir / ".dark-factory" / "architecture" / "environment.toml"
    try:
        contract = materialize(deltas_dir, env_path)
        from darkfactory.architecture.deltas import hash_view
        result.new_env_hash = hash_view(contract)
    except Exception:
        log.exception("rollback: rematerialize failed after delta removal")
        _append_rollback_record(project_dir, result, status="failed")
        raise

    # Mark the rollback committed. Both 'pending' and 'committed' mask
    # the specs for readers; the distinction is for operators auditing
    # whether a crashed rollback needs manual recovery.
    _append_rollback_record(project_dir, result, status="committed")
    log.info(
        "rollback: to=%s rolled_back=%d removed_deltas=%d new_hash=%s",
        target_spec, len(result.rolled_back_specs),
        len(result.removed_deltas), result.new_env_hash[:12],
    )
    return result
