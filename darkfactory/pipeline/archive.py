"""Per-run archive of pipeline artifacts.

The pipeline writes iteration outputs (reviews, reports, audit logs,
architecture manifests, status files, state) into well-known
``.dark-factory/`` directories. Successive iterations reuse those
paths — iter 2 overwrites iter 1's quality-gate report, iter 3
overwrites iter 2's feedback, etc. That is fine for the live pipeline
(which only needs the current iteration's view), but it makes
post-mortem inspection frustrating: after a 3-iteration run all that
remains of iter 1 is what the pipeline happened to preserve in
``docs/pipeline-run-reports/``.

This module copies the interesting artifacts into a run-scoped archive
folder *outside* the live ``.dark-factory/`` tree, keyed by datetime +
spec name + short run_id. The archive is observational — no pipeline
code reads from it. Callers invoke three entry points:

* ``init_archive_root`` — compute the archive path and create it.
  Called once per pipeline run, before the iteration loop.
* ``snapshot_iteration`` — copy the current iteration's artifacts
  into ``<root>/iter-<N>/``. Called at iteration boundaries so iter 1
  is captured before iter 2 begins overwriting its files.
* ``snapshot_final`` — copy run-level artifacts into ``<root>/final/``
  along with a ``meta.json`` recording the terminal status. Called
  once from ship/escalate/abort handlers.

Design constraints
------------------
* Best-effort: archive failures must not break the pipeline. The
  archive is a diagnostic aid, not a correctness contract.
* Side-effect free w.r.t. the pipeline: write only to
  ``.dark-factory/archive/``; do not touch anything the pipeline
  reads from.
* Observational: do not redact or alter artifact content — the whole
  point is to preserve what the pipeline actually produced on a
  per-iteration basis.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path


log = logging.getLogger(__name__)


# Directories under .dark-factory/ whose files we mirror into the
# archive on each iteration snapshot. Each entry is a (source_subdir,
# glob) pair — the glob is matched against ``<project_dir>/.dark-factory/
# <source_subdir>/``. A ``<spec>*`` filter keeps the archive scoped to
# the current spec.
_ITERATION_SOURCES: tuple[tuple[str, str], ...] = (
    # Agent reviews (feedback files, validation reports, reviewer
    # outputs) are the highest-signal artifacts for post-mortem.
    ("reviews", "*"),
    # Reports: quality-gate, scenario-generation, architecture,
    # substrate-findings, scenario-counts, etc.
    ("reports", "*"),
    # Audit logs record which files the agents read/wrote.
    ("audit", "*"),
    # Architecture outputs: manifest, precision-hint, environment
    # contract deltas, substrate manifests.
    ("architecture", "*"),
    # State file captures pipeline phase/status at snapshot time.
    ("state", "*"),
    # Precheck advisories, if any.
    ("precheck", "*"),
    # Test-engineer scenarios (visible + _contracts). Live under
    # ``.dark-factory/scenarios/<spec>/`` — archived so the
    # ``--anchor-scenarios`` V-dimension flag can point at a stable
    # historical path. Previously only the legacy ``archived-runs/``
    # flat dump captured these; adding them here makes
    # ``.dark-factory/archive/`` the single archival surface (Task 3
    # of 2026-04-22-iteration-steering-invariants).
    ("scenarios", "*"),
)

# Run-level artifacts snapshotted into ``<root>/final/``. These are
# not per-iteration — they're aggregate across the run.
_FINAL_SOURCES: tuple[tuple[str, str], ...] = (
    ("events.jsonl", ""),  # single file
    ("failures", "*"),
    ("metrics.jsonl", ""),
)


# Subdirs whose spec-scoped content lives in a nested
# ``<subdir>/<spec>/...`` layout. These need ``_copy_tree_filtered``
# so the path-component-prefix match picks up the nested leaves;
# flat subdirs use ``_copy_filtered`` on the filename-prefix match.
_TREE_FILTERED_SUBDIRS = frozenset({"architecture", "scenarios"})


def _archive_root(project_dir: Path) -> Path:
    return project_dir / ".dark-factory" / "archive"


def init_archive_root(
    project_dir: Path, spec_name: str, run_id: str,
) -> Path:
    """Create the archive root for this pipeline run and return its path.

    Directory name encodes datetime + spec + short run_id so multiple
    runs for the same spec on the same day sort chronologically and
    never collide. The run_id prefix is also unique on its own, but
    leading with the timestamp makes the directory listing scan-
    friendly.

    Returns the archive root path. Creates the directory. Best-effort:
    falls back silently to a process-temp path if the project dir is
    read-only (shouldn't happen in normal flows, but the archive must
    never break the pipeline).
    """
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    short_run = (run_id or "")[:8] or "norunid"
    root = _archive_root(project_dir) / f"{ts}-{spec_name}-{short_run}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        # Write a tiny manifest so a human browsing the archive tree
        # can see which run this was without reading every iter dir.
        (root / "run.json").write_text(
            json.dumps(
                {
                    "spec": spec_name,
                    "run_id": run_id,
                    "started_at": ts,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        log.info("archive: initialized run archive at %s", root)
        return root
    except OSError as e:
        log.warning("archive: could not create archive root %s (%s)", root, e)
        return root


def _copy_filtered(src_dir: Path, dst_dir: Path, spec_name: str) -> int:
    """Copy files from ``src_dir`` matching ``spec_name*`` into ``dst_dir``.

    Returns the number of files copied. Unreadable source files are
    skipped — we deliberately do not raise.
    """
    if not src_dir.exists() or not src_dir.is_dir():
        return 0
    count = 0
    for entry in src_dir.iterdir():
        if not entry.is_file():
            continue
        # Filter: only files whose name starts with the spec name, or
        # which are shared audit/state files. The substrate-manifests
        # subdir is handled separately below.
        if not entry.name.startswith(spec_name):
            continue
        try:
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, dst_dir / entry.name)
            count += 1
        except OSError as e:
            log.debug("archive: skipped %s (%s)", entry, e)
    return count


def _copy_tree_filtered(src_dir: Path, dst_dir: Path, spec_name: str) -> int:
    """Walk ``src_dir`` recursively and mirror spec-scoped files."""
    if not src_dir.exists() or not src_dir.is_dir():
        return 0
    count = 0
    for entry in src_dir.rglob("*"):
        if not entry.is_file():
            continue
        rel = entry.relative_to(src_dir)
        # Keep the file if either the leaf filename or any path
        # component starts with the spec name — substrate manifests
        # live at ``architecture/substrate-manifests/<spec>.toml``.
        if not (
            entry.name.startswith(spec_name)
            or any(p.startswith(spec_name) for p in rel.parts)
        ):
            continue
        dst = dst_dir / rel
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, dst)
            count += 1
        except OSError as e:
            log.debug("archive: skipped %s (%s)", entry, e)
    return count


def snapshot_iteration(
    archive_root: Path | None,
    project_dir: Path,
    spec_name: str,
    completed_iteration: int,
) -> None:
    """Copy iteration artifacts into ``archive_root/iter-<N>/``.

    The ``completed_iteration`` argument is the 1-indexed number of the
    iteration whose artifacts are being archived — NOT the counter of the
    next iteration about to start. Callers MUST pass
    ``len(spec_run.iterations)`` (or ``spec_run.iteration - 1`` at the top
    of an iteration before its IterationState has been appended).

    Historical bug (run 3b1d2bc7 before this change): the ship/escalate/
    abort handlers passed ``spec_run.iteration`` directly, which had
    already been incremented past the last real iteration by the bottom-
    of-loop `+= 1`. The archive thus grew an empty ``iter-<max+1>/`` dir
    and skipped the real final iteration. The parameter was renamed to
    make the intended semantics explicit.

    Best-effort: catches and logs all errors. ``completed_iteration < 1``
    is treated as a no-op — nothing completed yet, nothing to snapshot.
    """
    if archive_root is None:
        return
    if completed_iteration < 1:
        log.debug(
            "archive: snapshot_iteration called with completed_iteration=%d; no-op",
            completed_iteration,
        )
        return
    try:
        dst_base = archive_root / f"iter-{completed_iteration}"
        dst_base.mkdir(parents=True, exist_ok=True)
        total = 0
        for subdir, _glob in _ITERATION_SOURCES:
            src = project_dir / ".dark-factory" / subdir
            dst = dst_base / subdir
            if subdir in _TREE_FILTERED_SUBDIRS:
                total += _copy_tree_filtered(src, dst, spec_name)
            else:
                total += _copy_filtered(src, dst, spec_name)
        log.info(
            "archive: iter-%d snapshot captured %d file(s) into %s",
            completed_iteration, total, dst_base,
        )
    except Exception as e:  # noqa: BLE001 — archive must never break the pipeline
        log.warning(
            "archive: iter-%d snapshot failed (%s)", completed_iteration, e,
        )


def snapshot_final(
    archive_root: Path | None,
    project_dir: Path,
    spec_name: str,
    run_id: str,
    status: str,
    iteration: int,
) -> None:
    """Copy run-level artifacts and write a terminal ``meta.json``.

    Called once from ship/escalate/abort handlers. Status is one of
    ``shipped`` / ``escalated`` / ``aborted`` / ``failed`` — the
    literal string recorded in ``meta.json``.
    """
    if archive_root is None:
        return
    try:
        dst = archive_root / "final"
        dst.mkdir(parents=True, exist_ok=True)
        total = 0
        for name, _glob in _FINAL_SOURCES:
            src = project_dir / ".dark-factory" / name
            if src.is_file():
                try:
                    shutil.copy2(src, dst / src.name)
                    total += 1
                except OSError as e:
                    log.debug("archive: skipped %s (%s)", src, e)
            elif src.is_dir():
                total += _copy_filtered(src, dst / src.name, spec_name)

        meta = {
            "spec": spec_name,
            "run_id": run_id,
            "status": status,
            "final_iteration": iteration,
            "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        (dst / "meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8",
        )
        log.info(
            "archive: final snapshot captured %d file(s) (status=%s) into %s",
            total, status, dst,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("archive: final snapshot failed (%s)", e)
