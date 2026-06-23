"""Durable cross-run agent-metrics corpus (the optimization dataset).

Every agent invocation's stream summary (from ``stream_summary.summarize_stream``)
is appended here to an **append-only, user-level** JSONL that survives every run
reset, ``--fresh``, and manual clear of the project's ``.dark-factory/`` — the
structural fix for the data-loss that motivated this work (run3's raw sidecars
were wiped when the ephemeral ``failures/`` dir was cleared).

Design (docs/dev/2026-06-23-stream-summarizer.md — "Durable cross-run corpus"):

- **Location:** user/framework-level, NOT inside the project's ``.dark-factory/``.
  Default ``~/.dark-factory/corpus/agent-metrics.jsonl``; ``corpus_path`` config
  overrides. Cross-project, cross-session, reset-proof.
- **Trigger:** EVERY invocation, all roles, on both completion AND failure/timeout.
  Timeouts are the highest-value rows (a timed-out TE generate at 98% thinking /
  0 files / one 906s mega-turn is the dataset's most important record).
- **Concurrency:** uses ``locked_append_text`` — the same in-process lock the
  te-qa parallelization uses for its host writers — so parallel TE+QA appends
  (threads of ONE ``dark-factory run`` process) never interleave a torn line.
  NOTE: that ``threading.Lock`` does NOT serialize two *separate* ``dark-factory
  run`` processes writing the same user-level corpus. Cross-process torn-line
  safety there rests on ``O_APPEND`` write atomicity for sub-PIPE_BUF lines on a
  local FS (not guaranteed on NFS); ``read_corpus`` additionally tolerates torn
  lines, so a rare interleave degrades to a dropped row, never a crash.
- **Barrier-safety:** the metrics JSONL is metrics-ONLY (turn counts, timings,
  token *totals*, tool-call *histograms*, tsc error *counts*). NO artifact content
  (no scenario/source text, no file paths). It is behavioral metadata; it crosses
  no information barrier and is safe to persist host-side outside the project.
  ``summarize_stream`` output is metrics-only by construction; this module never
  adds artifact content. The optional raw-sidecar archival (``archive_streams``)
  DOES copy full stream content, but only host-side into ``~/.dark-factory/corpus/``
  — which no agent container bind-mounts (the barrier is the container FS boundary,
  per CLAUDE.md). That safety is contingent on no user-home subtree ever being
  mounted into an agent container; today none is (darkfactory/docker/mounts.py).
- **Fail-safe:** ``append_to_corpus`` never raises into a run.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from darkfactory.utils.json_helpers import locked_append_text

log = logging.getLogger(__name__)

CORPUS_SCHEMA_VERSION = 1

# Dedicated env override for the corpus base dir. Lets the test suite redirect
# the corpus to a tmp dir (the corpus default is the user's real home, which a
# test run must never pollute) and lets operators relocate it without a config
# file. ``corpus_path`` config still takes precedence over this for the file.
_CORPUS_DIR_ENV = "DARK_FACTORY_CORPUS_DIR"

# Distinct FR-/AC-/EC-N identifiers as an advisory spec-shape signal — lets us
# correlate spec heaviness with TE behavior ("do specs with >=7 ECs drive more
# mega-turns?"). Distinct-id counts, not section parsing — robust + decoupled
# from the heavy precheck module.
_FR_RE = re.compile(r"\bFR-(\d+)\b")
_AC_RE = re.compile(r"\bAC-(\d+)\b")
_EC_RE = re.compile(r"\bEC-(\d+)\b")


def default_corpus_dir() -> Path:
    env = os.environ.get(_CORPUS_DIR_ENV)
    if env:
        return Path(env).expanduser()
    return Path.home() / ".dark-factory" / "corpus"


def resolve_corpus_path(corpus_path: str | None) -> Path:
    """The corpus JSONL path: the ``corpus_path`` override, else the default."""
    if corpus_path:
        return Path(corpus_path).expanduser()
    return default_corpus_dir() / "agent-metrics.jsonl"


def spec_shape(spec_text: str | None) -> dict:
    """Advisory {fr_count, ac_count, ec_count} from a spec's text (distinct ids)."""
    if not spec_text:
        return {"fr_count": None, "ac_count": None, "ec_count": None}
    return {
        "fr_count": len(set(_FR_RE.findall(spec_text))),
        "ac_count": len(set(_AC_RE.findall(spec_text))),
        "ec_count": len(set(_EC_RE.findall(spec_text))),
    }


def _archive_sidecar(corpus_dir: Path, run_id: str | None, sidecar_path: Path) -> None:
    """Copy a raw sidecar into ``<corpus_dir>/<run_id>/`` for future re-derivation."""
    try:
        if not sidecar_path or not Path(sidecar_path).is_file():
            return
        sub = corpus_dir / (run_id or "norunid")
        sub.mkdir(parents=True, exist_ok=True)
        dest = sub / Path(sidecar_path).name
        if not dest.exists():  # idempotent — don't re-copy on a retry/resume
            shutil.copy2(sidecar_path, dest)
    except Exception as exc:  # noqa: BLE001 — archival is best-effort
        log.debug("corpus sidecar archive failed (non-fatal): %s", exc)


def append_to_corpus(
    summary: dict,
    *,
    provenance: dict,
    spec_shape: dict | None = None,
    corpus_path: str | None = None,
    ts: str | None = None,
    archive_streams: bool = True,
    sidecar_path: "str | Path | None" = None,
) -> None:
    """Append one corpus record (summary metrics + provenance + spec_shape).

    Best-effort: NEVER raises into a run. ``provenance`` supplies the
    authoritative {project, spec, spec_type, run_id, role, phase, iteration,
    attempt, model, outcome} and overrides the filename-parsed metadata the
    summary carries.
    """
    try:
        path = resolve_corpus_path(corpus_path)
        # All summary metrics + summary's own (best-effort) metadata, then the
        # authoritative provenance on top. summarize_stream output is metrics-only
        # by construction — no artifact content crosses into the corpus.
        record = dict(summary)
        record.update(provenance)
        record["spec_shape"] = spec_shape or {}
        record["schema_version"] = CORPUS_SCHEMA_VERSION
        record["ts"] = ts or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        locked_append_text(path, json.dumps(record, separators=(",", ":")) + "\n")
        if archive_streams and sidecar_path is not None:
            _archive_sidecar(path.parent, provenance.get("run_id"), Path(sidecar_path))
    except Exception as exc:  # noqa: BLE001 — corpus write must never fail a run
        log.debug("corpus append failed (non-fatal): %s", exc)


def read_corpus(corpus_path: str | None = None) -> list[dict]:
    """Read every corpus record. Returns [] if the corpus doesn't exist.
    Tolerates torn lines (skips them) — never raises."""
    path = resolve_corpus_path(corpus_path)
    rows: list[dict] = []
    try:
        if not path.is_file():
            return rows
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    except OSError as exc:
        log.debug("corpus read failed (non-fatal): %s", exc)
    return rows


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return float(s[mid]) if n % 2 else float((s[mid - 1] + s[mid]) / 2)


def summarize_corpus(records: list[dict]) -> list[dict]:
    """Aggregate corpus rows by (role, phase) into cross-run trend rows.

    Surfaces the population queries that pick optimization levers: timeout rate,
    mega-turn frequency (gaps.n_over_120s), median turns/wall, cache-hit
    distribution — across runs, not one.
    """
    groups: dict[tuple, list[dict]] = {}
    for r in records:
        key = (r.get("role") or "?", r.get("phase") or "-")
        groups.setdefault(key, []).append(r)

    out: list[dict] = []
    for (role, phase), rows in sorted(groups.items()):
        n = len(rows)
        timeouts = sum(1 for r in rows if r.get("outcome") == "timeout")
        truncated = sum(1 for r in rows if r.get("truncated"))
        turns = [r.get("actual_turns", 0) or 0 for r in rows]
        walls = [r.get("wall_seconds", 0) or 0 for r in rows]
        mega = sum((r.get("gaps") or {}).get("n_over_120s", 0) or 0 for r in rows)
        cache_hits = []
        for r in rows:
            toks = r.get("tokens") or {}
            cr = toks.get("cache_read", 0) or 0
            # Denominator = ALL non-output prompt tokens (cache_read + fresh input
            # + cache_creation). A cold-prefix invocation (big cache_creation,
            # ~0 cache_read) therefore reports a LOW hit rate by design — which is
            # the honest signal for the prefix-folding lever: a population full of
            # cold starts genuinely isn't cache-served. Don't read a deflated row
            # as "folding helps" without checking cache_creation share too.
            tot = cr + (toks.get("input", 0) or 0) + (toks.get("cache_creation", 0) or 0)
            if tot:
                cache_hits.append(100.0 * cr / tot)
        out.append({
            "role": role,
            "phase": phase,
            "n": n,
            "timeouts": timeouts,
            "timeout_rate": round(timeouts / n, 3) if n else 0.0,
            "truncated": truncated,
            "median_turns": round(_median(turns), 1),
            "max_turns": max(turns) if turns else 0,
            "median_wall_s": round(_median(walls), 1),
            "mega_turns_total": mega,
            "avg_cache_hit_pct": round(sum(cache_hits) / len(cache_hits), 1) if cache_hits else 0.0,
        })
    return out
