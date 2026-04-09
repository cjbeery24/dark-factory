"""Manifest.runtime → environment delta transformation.

The Architect's `runtime:` block in the architecture manifest is the
proposed amendment to the environment contract. This module:

  1. Maps RuntimeDecl fields to environment-contract paths.
  2. Maps RuntimeDecl.services to ``services.<name>.<field>`` ops.
  3. Maps RuntimeDecl.test_base_url to ``server.test_base_url``.
  4. Filters by spec_type (bootstrap may set everything; feature/bugfix
     are forbidden from touching runtime.language and layout.source_dirs).
  5. Builds a delta TOML file at
     ``.dark-factory/architecture/environment.deltas/NNNN-<spec>.toml``
     where NNNN is the next available numeric prefix.
  6. Returns the new path so the caller can refold + materialize.

Allowlist enforcement is done here, not in the agent. The agent prompt
documents the rule, but the orchestrator is the security boundary.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import tomli_w

from darkfactory.architecture.deltas import (
    EnvironmentConflict,
    SCHEMA_VERSION,
    hash_view,
    list_deltas,
    materialize,
)
from darkfactory.architecture.environment import (
    BOOTSTRAP_ONLY_PATHS,
    EnvironmentContract,
    ServiceEntry,
)
from darkfactory.spec.manifest import RuntimeDecl

log = logging.getLogger(__name__)


class AmendmentRejected(RuntimeError):
    """The proposed amendment violates the spec_type allowlist."""


def _next_delta_number(deltas_dir: Path) -> int:
    """Return the next NNNN prefix.

    0000 is *convention*, not enforced: it's the slot the Phase 2.4
    backcompat shim writes to. Projects that never ran the shim simply
    have an unused 0000 slot. Real spec deltas always start at 0001
    (max+1, with max starting at 0 so empty dir → 1).
    """
    if not deltas_dir.exists():
        return 1
    existing = list_deltas(deltas_dir)
    max_n = 0
    for p in existing:
        m = re.match(r"^(\d{4})-", p.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def _slugify(spec_id: str) -> str:
    """Best-effort filesystem-safe slug for the delta filename."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", spec_id).strip("-")
    return s or "spec"


def _filter_by_spec_type(
    ops: list[tuple[str, object]],
    spec_type: str,
) -> list[tuple[str, object]]:
    """Reject writes to bootstrap-only paths for non-bootstrap specs.

    Raises ``AmendmentRejected`` if the agent tried to amend a bootstrap-
    only field. We don't silently drop — that would mask agent mistakes.
    """
    if spec_type == "bootstrap":
        return ops
    forbidden = [path for path, _ in ops if path in BOOTSTRAP_ONLY_PATHS]
    if forbidden:
        raise AmendmentRejected(
            f"spec_type={spec_type!r} may not amend bootstrap-only paths: "
            f"{forbidden}. Only spec_type='bootstrap' may set "
            f"runtime.language or layout.source_dirs."
        )
    return ops


def _build_ops(rt: RuntimeDecl) -> list[tuple[str, object]]:
    """Read amendment ops from RuntimeDecl fields.

    Sources:
    - ``amendment_ops``: populated by ``manifest._parse_runtime`` from the
      manifest YAML (covers commands, server, runtime, layout fields).
    - ``services``: each RuntimeServiceDecl is expanded to
      ``services.<name>.<field>`` ops for non-default fields.
    - ``test_base_url``: mapped to ``server.test_base_url``.

    Sorted for stable delta file output (canonical_dump sorts on read,
    but the delta file itself shouldn't reorder between runs of the
    same input).
    """
    ops: list[tuple[str, object]] = list(rt.amendment_ops.items())

    # Services → service delta ops.
    for svc in rt.services:
        prefix = f"services.{svc.name}"
        ops.append((f"{prefix}.image", svc.image))
        if svc.env:
            ops.append((f"{prefix}.env", dict(svc.env)))
        if svc.health_cmd:
            ops.append((f"{prefix}.health_cmd", svc.health_cmd))
        if svc.health_timeout != ServiceEntry().health_timeout:
            ops.append((f"{prefix}.health_timeout", svc.health_timeout))
        if svc.inject_env:
            ops.append((f"{prefix}.inject_env", dict(svc.inject_env)))
        if svc.test_only:
            ops.append((f"{prefix}.test_only", True))
        if svc.depends_on:
            ops.append((f"{prefix}.depends_on", list(svc.depends_on)))

    # test_base_url → server.test_base_url.
    if rt.test_base_url:
        ops.append(("server.test_base_url", rt.test_base_url))

    return sorted(ops)


def _render_delta(
    spec_id: str,
    parent_hash: str,
    ops: Iterable[tuple[str, object]],
) -> str:
    """Render a delta file as TOML text via tomli_w.

    Builds the full document as a dict and dumps once. Avoids the
    string-split-on-equals trick that would silently corrupt multi-line
    values like arrays of tables.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "spec_id": spec_id,
        "parent_hash": parent_hash,
        "applied_at": now,
        "ops": [
            {"op": "set", "path": path, "value": value}
            for path, value in ops
        ],
    }
    return tomli_w.dumps(doc)


def amend_environment_from_manifest(
    rt: RuntimeDecl,
    spec_id: str,
    spec_type: str,
    project_dir: Path,
) -> Path | None:
    """Transform a manifest's runtime block into an environment delta.

    Returns the path to the new delta file, or None if the runtime block
    contained no env-contract-mappable fields (the typical empty-runtime
    feature spec). Always materializes the contract afterwards so
    ``environment.toml`` reflects the latest state — including for empty
    deltas, where materialization is a no-op refold.

    Raises:
      AmendmentRejected: spec_type forbids one of the proposed paths.
      EnvironmentConflict: parent_hash mismatch during fold/materialize
        — the orchestrator must halt and escalate.
    """
    arch_dir = project_dir / ".dark-factory" / "architecture"
    deltas_dir = arch_dir / "environment.deltas"
    env_path = arch_dir / "environment.toml"

    ops = _build_ops(rt)
    ops = _filter_by_spec_type(ops, spec_type)

    if not ops:
        # Nothing to amend. Re-materialize anyway so the file reflects
        # the current fold (cheap, idempotent, and protects against
        # half-migration recovery from earlier phases).
        if deltas_dir.exists():
            materialize(deltas_dir, env_path)
        return None

    # parent_hash = hash of the contract BEFORE this delta applies. Fold
    # the existing log to get the current view.
    if deltas_dir.exists() and list_deltas(deltas_dir):
        from darkfactory.architecture.deltas import fold_deltas
        parent_view = fold_deltas(deltas_dir)
    else:
        parent_view = EnvironmentContract()
    parent = hash_view(parent_view)

    # Idempotency check: if every proposed op already matches the current
    # folded view, there's nothing to write. This protects against delta
    # log growth on cached-manifest replays, where the orchestrator
    # re-applies a manifest whose runtime block has already been amended
    # into the contract by a prior run.
    def _current(path: str) -> object:
        parts = path.split(".")
        if len(parts) == 2:
            section_obj = getattr(parent_view, parts[0], None)
            if section_obj is None:
                return object()  # sentinel: never equal
            return getattr(section_obj, parts[1], object())
        if len(parts) == 3 and parts[0] == "services":
            # 3-level service path: services.<name>.<field>
            entry = parent_view.services.get(parts[1])
            if entry is None:
                return object()
            return getattr(entry, parts[2], object())
        return object()  # unknown path shape — force delta write

    if all(_current(path) == value for path, value in ops):
        log.info(
            "environment amendment: all %d op(s) already match current view; "
            "skipping delta write (idempotent replay)",
            len(ops),
        )
        materialize(deltas_dir, env_path)
        return None

    deltas_dir.mkdir(parents=True, exist_ok=True)
    n = _next_delta_number(deltas_dir)
    filename = f"{n:04d}-{_slugify(spec_id)}.toml"
    delta_path = deltas_dir / filename

    # Snapshot environment.toml BEFORE we touch anything so we can
    # restore it byte-for-byte if materialize fails. The unlink-only
    # rollback is insufficient: a non-atomic materialize could leave
    # environment.toml reflecting a fold that the (now-rolled-back) log
    # no longer matches, and the next run hits a phantom conflict.
    env_snapshot: bytes | None
    if env_path.exists():
        env_snapshot = env_path.read_bytes()
    else:
        env_snapshot = None

    delta_path.write_text(
        _render_delta(spec_id, parent, ops), encoding="utf-8",
    )
    log.info(
        "environment amendment: wrote %s with %d op(s) for spec_type=%s",
        delta_path.name, len(ops), spec_type,
    )

    try:
        materialize(deltas_dir, env_path)
    except Exception:
        # Restore the snapshot AND drop the delta we just wrote, in that
        # order. If we drop the delta first and the restore fails, the
        # next run sees a stale environment.toml with no record of how
        # it got there.
        if env_snapshot is not None:
            env_path.write_bytes(env_snapshot)
        elif env_path.exists():
            env_path.unlink()
        delta_path.unlink(missing_ok=True)
        raise

    return delta_path
