"""Agent stream-summarizer — per-invocation observability for the S0c sidecars.

Aggregates a single arrival-timestamped agent stream sidecar into one structured
record. This is the *instrument* for the TE-speedup investigation: we already
capture the raw per-line stream (``run_streaming`` writes ``{"t","line"}`` JSONL
to ``.dark-factory/failures/stream_*.jsonl``) but aggregate nothing — the
465c1e39 postmortem had to extract the per-turn breakdown by hand. This makes
that breakdown a standing per-invocation metric.

Design (see docs/dev/2026-06-23-stream-summarizer.md — authoritative):

- **Read-only / derivative.** Consumes data we already write; NEVER kills, gates,
  retries, or alters a run. No agent/barrier/container change. This is the
  deliberate opposite of the stall-kill watchdog we rejected for lack of evidence.
- **Fail-safe.** ``summarize_stream`` never raises into a run. A truncated or
  malformed sidecar (the dominant case — timeout invocations cut mid-stream) yields
  best-effort partial metrics with ``truncated: True``. Unknown stream-json event
  types are ignored (defensive against claude-code schema drift).
- **Pure.** No side effects; given a path it reads it and returns a dict.

The input sidecar (``darkfactory/utils/subprocess_runner.py``) writes one JSON
object per line: ``{"t": <float seconds since invocation start>, "line": "<raw
claude-code stream-json event, JSON-encoded string>"}``. ``line`` decodes to a
stream-json event whose ``type`` is one of ``system`` (init) | ``assistant``
(content blocks: ``thinking`` | ``text`` | ``tool_use``) | ``user`` (content:
``tool_result``) | ``result`` (final, carries ``usage``).
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any

# Reuse the Check-9 / stub-compile tsc diagnostic regex rather than re-inventing
# a tsc error parser (per the design doc). It matches both diagnostic shapes:
#   path(line,col): error TS2339: msg   and   path:line:col - error TS2339: msg
from darkfactory.adapters.typescript.stub_compile import _TSC_DIAG_RE

log = logging.getLogger(__name__)

# Defensive caps — a sidecar is normally 33-200 short lines, but a runaway agent
# or a Write with a megabyte body could blow these. Mirror the agents.py caps so
# the summarizer can never become a memory/CPU sink on the (off-critical-path)
# observability lane.
_MAX_LINES = 500_000
_MAX_LINE_BYTES = 4_000_000

# A Bash command that runs the TypeScript typechecker. We detect tsc invocations
# from the stream itself (not the phase name) so the spin-vs-progress signal is
# captured wherever tsc runs; ``tsc`` is None when no such invocation is seen.
# This is the headline metric, so the pattern is tight: it matches tsc ONLY in a
# real command position, never as a bare substring. Excludes `cat typecheck.log`,
# `echo typecheck`, `node scripts/typecheck.js`, `ls /tsc` (all observed-plausible
# false positives — Linus) while matching `tsc --noEmit`, `npx tsc`, `pnpm tsc`,
# `./node_modules/.bin/tsc`, `vue-tsc`, and `npm|pnpm|yarn run typecheck`.
_TSC_CMD_RE = re.compile(
    r"(?:^|[&|;]|&&)\s*\.?/?(?:node_modules/\.bin/)?(?:tsc|vue-tsc)\b"      # command-position binary
    r"|/\.bin/(?:tsc|vue-tsc)\b"                                            # bin-path anywhere
    r"|\b(?:npx|pnpm|yarn|bun)(?:\s+(?:exec|run|dlx))?\s+(?:tsc|vue-tsc)\b"  # runner-invoked binary
    r"|\b(?:npm|pnpm|yarn|bun|npx)\s+(?:run\s+)?type-?check\b",             # package typecheck script
    re.IGNORECASE)

# tsc's own summary line: "Found 14 errors in 3 files." / "Found 1 error."
_TSC_FOUND_RE = re.compile(r"Found\s+(\d+)\s+errors?\b", re.IGNORECASE)

# Filename shape (best-effort metadata recovery for offline mode):
#   stream_<role>_<spec>_<run8>_it<N>_[<phase>_]attempt<M>_p<pid>s<seq>.jsonl
# Roles use hyphens, never underscores, so role is the first '_'-delimited token.
# The trailing _p<pid>s<seq> + _it<N> anchors disambiguate the variable-width
# spec/phase in the middle. Best-effort: a spec name that itself contains an
# `_it<digits>_..._attempt<digits>` lookalike can MIS-split (not just fail to
# match). This only labels the record — every metric is derived from the stream
# contents, never the name — and the inline hook passes authoritative metadata
# that overrides the parse, so live runs are always correct; only offline
# spec/run filtering relies on the parse.
_FILENAME_RE = re.compile(
    r"^stream_(?P<role>[^_]+)_(?P<spec>.+?)_(?P<run>[^_]+)_it(?P<iteration>\d+)_"
    r"(?:(?P<phase>.+)_)?attempt(?P<attempt>\d+)_p(?P<pid>\d+)s(?P<seq>\d+)\.jsonl$"
)


def _percentile(sorted_vals: list[float], p: float) -> float:
    """Linear-interpolated percentile (p in [0,1]) over an ascending-sorted list."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(sorted_vals[int(k)])
    return float(sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo))


def _decode_event(line: str) -> dict | None:
    """Decode the inner stream-json event string to a dict, or None if unparseable."""
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _has_thinking(content: list) -> bool:
    return any(
        isinstance(b, dict) and b.get("type") == "thinking" for b in content
    )


def _tool_result_text(block: dict) -> str:
    """Flatten a tool_result block's content (str or list of text parts) to text."""
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return ""


def _count_tsc_errors(text: str) -> int:
    """Count tsc errors in a tool_result. Prefer tsc's own ``Found N errors``
    summary (authoritative, version-stable); fall back to counting distinct
    diagnostic lines via the reused stub-compile regex."""
    found = _TSC_FOUND_RE.search(text)
    if found:
        return int(found.group(1))
    return sum(1 for ln in text.splitlines() if _TSC_DIAG_RE.match(ln.strip()))


def _parse_metadata(name: str) -> dict:
    """Best-effort role/spec/iteration/phase/attempt from the sidecar filename."""
    meta: dict[str, Any] = {
        "role": None, "spec": None, "run_id": None,
        "iteration": None, "phase": None, "attempt": None,
    }
    m = _FILENAME_RE.match(name)
    if not m:
        return meta
    meta["role"] = m.group("role")
    meta["spec"] = m.group("spec")
    meta["run_id"] = m.group("run")
    meta["phase"] = m.group("phase")  # None when the phase segment is absent
    try:
        meta["iteration"] = int(m.group("iteration"))
    except (TypeError, ValueError):
        pass
    try:
        meta["attempt"] = int(m.group("attempt"))
    except (TypeError, ValueError):
        pass
    return meta


def _empty_summary(name: str) -> dict:
    """Schema-complete record with zeroed metrics (no usable stream)."""
    meta = _parse_metadata(name)
    return {
        **meta,
        "sidecar": name,
        "outcome": "incomplete",
        "truncated": True,
        "wall_seconds": 0.0,
        "actual_turns": 0,
        "thinking_turns": 0,
        "thinking_seconds": 0.0,
        "tool_seconds": 0.0,
        "idle_seconds": 0.0,
        "tool_calls": {},
        "files_written": 0,
        "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0},
        "gaps": {"p50": 0.0, "p90": 0.0, "max": 0.0, "n_over_120s": 0},
        "tsc": None,
        "events_observed": 0,
    }


def summarize_stream(
    sidecar_path: "str | Path",
    *,
    wall_seconds: float | None = None,
    **meta_overrides: Any,
) -> dict:
    """Aggregate one S0c stream sidecar into a structured summary dict.

    Pure and fail-safe: NEVER raises. A truncated/malformed sidecar yields
    best-effort partial metrics with ``truncated: True``.

    Args:
        sidecar_path: path to a ``stream_*.jsonl`` sidecar.
        wall_seconds: authoritative wall-clock for this invocation (inline mode).
            When omitted (offline mode) wall is derived from the last event's
            timestamp — which omits any post-last-event SIGKILL idle tail.
        **meta_overrides: authoritative metadata the caller knows directly
            (``role``, ``phase``, ``iteration``, ``attempt``, ``outcome``,
            ``spec``, ``run_id``). Override the filename-parsed best-effort values.

    Returns: a dict matching the AGENT_STREAM_SUMMARY ``data`` schema.
    """
    try:
        path = Path(sidecar_path)
        name = path.name
        try:
            # Stream line-by-line rather than read_text().splitlines() so the
            # _MAX_LINES / _MAX_LINE_BYTES caps actually bound PEAK memory — a
            # pathological multi-GB sidecar is never fully resident (Linus).
            with path.open(encoding="utf-8", errors="replace") as fh:
                summary = _summarize_lines(fh, name, wall_seconds)
        except OSError as exc:
            log.debug("stream summary: cannot read %s (%s)", sidecar_path, exc)
            out = _empty_summary(name)
            out.update({k: v for k, v in meta_overrides.items() if v is not None})
            if wall_seconds is not None:
                out["wall_seconds"] = round(float(wall_seconds), 2)
            return out

        # Authoritative metadata from the caller wins over filename parsing.
        summary.update({k: v for k, v in meta_overrides.items() if v is not None})
        return summary
    except Exception as exc:  # noqa: BLE001 — observability must never raise into a run
        log.debug("stream summary failed (non-fatal): %s", exc)
        out = _empty_summary(Path(str(sidecar_path)).name)
        try:
            out.update({k: v for k, v in meta_overrides.items() if v is not None})
        except Exception:  # noqa: BLE001
            pass
        return out


def _summarize_lines(lines, name: str, wall_seconds: float | None) -> dict:
    """Core parse over an iterable of raw outer JSONL lines (a file handle or a
    list). Separated so summarize_stream's guard wraps everything."""
    summary = _empty_summary(name)

    # 1) Read the outer {"t","line"} records in arrival order.
    records: list[tuple[float, str]] = []
    torn = False
    for i, raw in enumerate(lines):
        if i >= _MAX_LINES:
            torn = True
            break
        raw = raw.strip()
        if not raw:
            continue
        if len(raw) > _MAX_LINE_BYTES:
            torn = True
            continue
        try:
            rec = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            # A torn final line is the canonical SIGKILL-tail signature.
            torn = True
            continue
        if not isinstance(rec, dict) or "t" not in rec or "line" not in rec:
            continue
        try:
            t = float(rec["t"])
        except (TypeError, ValueError):
            continue
        records.append((t, rec["line"]))

    if not records:
        # Nothing usable. Keep the truncated empty shell but honor an override wall.
        if wall_seconds is not None:
            summary["wall_seconds"] = round(float(wall_seconds), 2)
        return summary

    # 2) Decode each event once; keep (t, type, has_thinking, tool_uses, raw_event).
    # An un-decodable INNER event is schema-drift / a malformed line, NOT a torn
    # tail — a stream that still ends with a result envelope is complete. So track
    # inner-decode misses separately and only fold them into ``truncated`` when
    # there's no terminal result (handled below), rather than poisoning the flag
    # on otherwise-healthy streams (Linus).
    parsed: list[dict] = []
    inner_decode_errors = 0
    for t, line in records:
        ev = _decode_event(line)
        if ev is None:
            inner_decode_errors += 1
            parsed.append({"t": t, "type": None, "thinking": False, "tool_uses": [], "ev": None})
            continue
        typ = ev.get("type")
        tool_uses: list[dict] = []
        has_think = False
        if typ == "assistant":
            content = (ev.get("message") or {}).get("content") or []
            if isinstance(content, list):
                has_think = _has_thinking(content)
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        tool_uses.append(b)
        parsed.append({"t": t, "type": typ, "thinking": has_think,
                       "tool_uses": tool_uses, "ev": ev})

    summary["events_observed"] = len(parsed)

    # 3) Result envelope → tokens (billed total) + completed outcome.
    result_ev = next((p["ev"] for p in reversed(parsed)
                      if p["type"] == "result" and p["ev"]), None)
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    if result_ev is not None:
        u = result_ev.get("usage") or {}
        if isinstance(u, dict):
            tokens = {
                "input": int(u.get("input_tokens", 0) or 0),
                "output": int(u.get("output_tokens", 0) or 0),
                "cache_read": int(u.get("cache_read_input_tokens", 0) or 0),
                "cache_creation": int(u.get("cache_creation_input_tokens", 0) or 0),
            }
    else:
        # No terminal result (timeout/SIGKILL) → tokens are a lower bound summed
        # from per-assistant usage, and the stream is truncated by definition.
        torn = True
        for p in parsed:
            if p["type"] != "assistant" or not p["ev"]:
                continue
            u = (p["ev"].get("message") or {}).get("usage") or {}
            if isinstance(u, dict):
                tokens["input"] += int(u.get("input_tokens", 0) or 0)
                tokens["output"] += int(u.get("output_tokens", 0) or 0)
                tokens["cache_read"] += int(u.get("cache_read_input_tokens", 0) or 0)
                tokens["cache_creation"] += int(u.get("cache_creation_input_tokens", 0) or 0)
    summary["tokens"] = tokens

    # 4) Turn / tool / file counts + tsc error sequence.
    actual_turns = 0
    thinking_turns = 0
    tool_calls: dict[str, int] = {}
    files_written: set[str] = set()
    # tool_use_id -> command, so a Bash tsc tool_result can be matched back.
    tsc_tool_ids: dict[str, bool] = {}
    tsc_error_counts: list[int] = []

    for p in parsed:
        if p["type"] == "assistant":
            actual_turns += 1
            if p["thinking"]:
                thinking_turns += 1
            for tu in p["tool_uses"]:
                tname = tu.get("name", "?")
                tool_calls[tname] = tool_calls.get(tname, 0) + 1
                inp = tu.get("input") or {}
                if tname in ("Write", "Edit", "MultiEdit"):
                    fp = inp.get("file_path")
                    if isinstance(fp, str) and fp:
                        files_written.add(fp)
                if tname == "Bash":
                    cmd = inp.get("command", "")
                    if isinstance(cmd, str) and _TSC_CMD_RE.search(cmd):
                        tid = tu.get("id")
                        if isinstance(tid, str):
                            tsc_tool_ids[tid] = True
        elif p["type"] == "user" and p["ev"]:
            content = (p["ev"].get("message") or {}).get("content") or []
            if isinstance(content, list):
                for b in content:
                    if (isinstance(b, dict) and b.get("type") == "tool_result"
                            and b.get("tool_use_id") in tsc_tool_ids):
                        tsc_error_counts.append(_count_tsc_errors(_tool_result_text(b)))

    summary["actual_turns"] = actual_turns
    summary["thinking_turns"] = thinking_turns
    summary["tool_calls"] = tool_calls
    summary["files_written"] = len(files_written)
    if tsc_tool_ids:
        summary["tsc"] = {"invocations": len(tsc_tool_ids),
                          "error_counts": tsc_error_counts}
    # else: stays None (no tsc invocation observed) — per the schema.

    # 5) Wall-time attribution by inter-event gap.
    #    gap-before a thinking-bearing assistant turn -> thinking_seconds
    #    assistant(tool_use) -> next user(tool_result) span -> tool_seconds
    #    idle = wall - thinking - tool (unattributed residual).
    # tool_seconds matches POSITIONALLY (assistant-with-tools -> the immediately
    # following user turn), not by tool_use_id: claude-code packs all of one
    # turn's parallel tool_results into a single user message, so the positional
    # gap captures the whole tool span. The rare case of results split across
    # separate user messages under-attributes the tail to idle — acceptable for
    # an observability instrument (the headline split is thinking-vs-rest).
    thinking_s = 0.0
    tool_s = 0.0
    gaps: list[float] = []
    for prev, cur in zip(parsed, parsed[1:]):
        gap = cur["t"] - prev["t"]
        if gap < 0:
            gap = 0.0
        gaps.append(gap)
        if cur["type"] == "assistant" and cur["thinking"]:
            thinking_s += gap
        elif (cur["type"] == "user" and prev["type"] == "assistant"
              and prev["tool_uses"]):
            tool_s += gap

    last_t = parsed[-1]["t"]
    wall = float(wall_seconds) if wall_seconds is not None else last_t
    if wall < last_t:
        wall = last_t  # an override shorter than observed events is nonsensical
    idle_s = max(0.0, wall - thinking_s - tool_s)

    summary["wall_seconds"] = round(wall, 2)
    summary["thinking_seconds"] = round(thinking_s, 2)
    summary["tool_seconds"] = round(tool_s, 2)
    summary["idle_seconds"] = round(idle_s, 2)

    if gaps:
        sg = sorted(gaps)
        summary["gaps"] = {
            "p50": round(_percentile(sg, 0.50), 2),
            "p90": round(_percentile(sg, 0.90), 2),
            "max": round(sg[-1], 2),
            "n_over_120s": sum(1 for g in gaps if g > 120.0),
        }

    # 6) Outcome / truncation. ``torn`` already covers torn outer records / the
    # no-result tail; an inner-decode miss only signals truncation when paired
    # with a missing envelope (a healthy stream + one drifted line stays clean).
    summary["truncated"] = bool(torn or (inner_decode_errors and result_ev is None))
    # Offline can only distinguish completed vs incomplete from the stream alone;
    # the richer timeout/failed value is supplied by the inline hook's override.
    summary["outcome"] = "completed" if result_ev is not None else "incomplete"
    return summary


# ---------------------------------------------------------------------------
# Sidecar discovery + flat metrics sink (used by both inline + offline modes)
# ---------------------------------------------------------------------------

# stream sidecars live under failures/ on a live run and are copied verbatim
# into archive/<ts>-<spec>-<run>/failures/ on archival (archive.py).
_SIDECAR_GLOB = "stream_*.jsonl"


def append_metrics_line(project_dir: "str | Path", summary: dict) -> None:
    """Append one summary as a flat JSONL line to
    ``.dark-factory/metrics/stream-summary.jsonl``. Best-effort; never raises."""
    try:
        out = Path(project_dir) / ".dark-factory" / "metrics" / "stream-summary.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(summary) + "\n")
    except Exception as exc:  # noqa: BLE001 — observability must never raise into a run
        log.debug("append_metrics_line failed (non-fatal): %s", exc)


def find_sidecars(
    project_dir: "str | Path",
    *,
    spec: str | None = None,
    run_id: str | None = None,
    include_archive: bool = True,
) -> list[Path]:
    """Discover stream sidecars for offline analysis.

    Searches ``.dark-factory/failures/`` and (when ``include_archive``) every
    ``.dark-factory/archive/*/failures/`` dir. Filters by ``spec`` and/or
    ``run_id`` against the filename-encoded metadata when given. Returns a
    name-sorted list (stable, chronological-ish via the s<seq> suffix).
    """
    root = Path(project_dir) / ".dark-factory"
    search_dirs: list[Path] = [root / "failures"]
    if include_archive:
        archive = root / "archive"
        if archive.is_dir():
            search_dirs.extend(sorted(p / "failures" for p in archive.iterdir()
                                      if p.is_dir()))
    seen: set[str] = set()
    out: list[Path] = []
    for d in search_dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob(_SIDECAR_GLOB)):
            meta = _parse_metadata(p.name)
            if spec is not None and meta.get("spec") != spec:
                continue
            if run_id is not None and (meta.get("run_id") or "") != run_id[:8]:
                continue
            # De-dup is keyed on filename only (the live failures/ copy, searched
            # first, wins over its archive copy). The _p<pid>s<seq> suffix makes a
            # cross-run name collision very unlikely but not impossible (pid reuse
            # + seq reset in a fresh process); on a true collision the second is
            # dropped — acceptable for retro-analysis.
            if p.name in seen:
                continue
            seen.add(p.name)
            out.append(p)
    return out
