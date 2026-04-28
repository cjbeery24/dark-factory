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

from darkfactory.git.ops import capture_iteration_diff


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


def diff_sha_pair_for_completed(
    spec_run: object, completed_iteration: int,
) -> tuple[str, str]:
    """Resolve ``(start_sha, end_sha)`` for a completed iteration.

    Mirrors the fallback chain documented on ``_format_prior_iter_oracle_block``
    (iteration.py): the start SHA is the prior iteration's captured
    end SHA when one exists, else the run-start preflight baseline.
    The end SHA is the completed iteration's own captured end SHA.

    Returns ``("", "")`` whenever a usable diff cannot be produced —
    out-of-range iteration index, missing start SHA (no preflight
    baseline on iter 1, prior-iter capture failed on iter 2+), or
    missing end SHA. This is a single-source-of-truth contract: callers
    can short-circuit on ``not start_sha`` alone without re-validating
    each side. The footgun this guards against: ``git diff ..end_sha``
    (with empty start) means "diff working-tree against end_sha", not
    "no-op" — which would silently capture every uncommitted file as
    if the implementer had touched it.

    Duck-typed on the spec_run shape (``.iterations[i].iteration_end_sha``
    and ``.preflight_base_ref``) to keep archive.py dependency-free of
    SpecRun. The two attributes named here are the entire surface;
    Protocol-ifying buys nothing for two fields.
    """
    if completed_iteration < 1:
        return "", ""
    iters = getattr(spec_run, "iterations", None) or []
    if completed_iteration > len(iters):
        return "", ""
    end_sha = getattr(iters[completed_iteration - 1], "iteration_end_sha", "") or ""
    if completed_iteration >= 2:
        start_sha = (
            getattr(iters[completed_iteration - 2], "iteration_end_sha", "") or ""
        )
    else:
        start_sha = getattr(spec_run, "preflight_base_ref", "") or ""
    # Single-source-of-truth: if EITHER side is missing the diff is
    # not safely producible. Returning ("","") here means callers only
    # need one guard (``if not start_sha``) rather than a two-sided
    # check at every call site.
    if not start_sha or not end_sha:
        return "", ""
    return start_sha, end_sha


def snapshot_iteration_diff(
    archive_root: Path | None,
    project_dir: Path,
    completed_iteration: int,
    start_sha: str,
    end_sha: str,
    source_dirs: list[str],
    test_dir: str,
) -> None:
    """Capture the per-iteration implementer diff into the archive.

    Companion to ``snapshot_iteration``. Where snapshot_iteration
    mirrors the *artifacts* the agents produced (reviews, reports,
    audit), this captures the *code changes* bracketed by the two SHAs
    the orchestrator pinned at iteration boundaries. Two consumers,
    two artifacts: file mirror is what Reviewer/Oracle saw; the diff
    is what the Implementer actually did.

    Without the diff, post-mortem can only count files touched (via
    ``count_files_changed``) and not which files, with what intent —
    that's the gap this addresses.

    Best-effort: archive failures must not break the pipeline. Empty
    SHAs (capture failed upstream) and ``completed_iteration < 1`` are
    silent no-ops, matching ``snapshot_iteration``.
    """
    if archive_root is None:
        return
    if completed_iteration < 1:
        return
    if not start_sha or not end_sha:
        log.debug(
            "archive: iter-%d diff capture skipped (empty sha pair)",
            completed_iteration,
        )
        return
    out_path = (
        archive_root / f"iter-{completed_iteration}" / "implementer-changes.patch"
    )
    try:
        ok = capture_iteration_diff(
            project_dir, start_sha, end_sha,
            source_dirs, test_dir, out_path,
        )
        if ok:
            log.info(
                "archive: iter-%d diff captured to %s",
                completed_iteration, out_path,
            )
        else:
            # Promoted from debug to info: snapshot_iteration_diff is
            # the only signal at the call-site that capture failed.
            # Operators scanning at default log level should see that
            # the post-mortem patch is missing rather than have to
            # reach for --log-level=debug to find out.
            log.info(
                "archive: iter-%d diff capture failed; "
                "no implementer-changes.patch will be written",
                completed_iteration,
            )
    except Exception as e:  # noqa: BLE001 — archive must never break the pipeline
        log.warning(
            "archive: iter-%d diff capture failed (%s)", completed_iteration, e,
        )


def snapshot_iteration_diff_for_completed(
    spec_run: object,
    project_dir: Path,
    completed_iteration: int,
    source_dirs: list[str],
    test_dir: str,
) -> None:
    """Convenience wrapper: resolve SHAs and call ``snapshot_iteration_diff``.

    Folds the four-line "compute SHAs, then call diff" sequence used at
    every call site (iter-loop top + ship/escalate/abort handlers) into
    one. ``spec_run`` is duck-typed (``.archive_root``, ``.iterations``,
    ``.preflight_base_ref``) for the same boundary-cleanliness reason
    as ``diff_sha_pair_for_completed``.

    No-op when ``spec_run.archive_root`` is None (archive init failed
    upstream) — matches the lower-level ``snapshot_iteration_diff``
    contract. Source dirs and test dir are passed in (not pulled from
    a config object) because archive.py has no ``PipelineConfig``
    dependency and we don't want to add one.
    """
    archive_root = getattr(spec_run, "archive_root", None)
    if archive_root is None:
        return
    start_sha, end_sha = diff_sha_pair_for_completed(spec_run, completed_iteration)
    snapshot_iteration_diff(
        archive_root, project_dir, completed_iteration,
        start_sha, end_sha, source_dirs, test_dir,
    )


def snapshot_final(
    archive_root: Path | None,
    project_dir: Path,
    spec_name: str,
    run_id: str,
    status: str,
    iteration: int,
    abort_reason: str = "",
) -> None:
    """Copy run-level artifacts and write a terminal ``meta.json``.

    Called once from ship/escalate/abort handlers. Status is one of
    ``shipped`` / ``escalated`` / ``aborted`` / ``failed`` — the
    literal string recorded in ``meta.json``.

    ``abort_reason`` is the ``spec_run.abort_reason`` string set on
    abort paths; only populated when ``status == "aborted"``. When
    truthy and aborting, it is recorded as ``meta["abort_reason"]``
    so a post-hoc consumer (analyze_trial.py classifier, archive
    grep) can distinguish abort taxonomies (e.g.,
    ``scenario_author_retry_exhausted`` vs other aborts) without
    re-parsing the state file. ROADMAP.md Phase 2 B1-cls-audit
    surface (d).
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
        if status == "aborted" and abort_reason:
            meta["abort_reason"] = abort_reason
        (dst / "meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8",
        )
        log.info(
            "archive: final snapshot captured %d file(s) (status=%s) into %s",
            total, status, dst,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("archive: final snapshot failed (%s)", e)
