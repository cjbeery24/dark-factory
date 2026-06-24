# Design: Agent Stream-Summarizer (per-invocation observability)

**Status:** DESIGN — not implemented. Neo (design) + Linus (code) gate before build.
**Date:** 2026-06-23
**Motivation:** TE-speedup investigation. We already capture raw per-line
timestamped streams (S0c sidecars) but **aggregate nothing** — the 465c1e39
postmortem extracted the per-turn breakdown *by hand* for one run. This makes
that breakdown a **standing per-invocation metric**, so every run is
auto-analyzable and the optimization levers can be chosen from data, not hand-analysis.

## Problem (what's missing today)

- `events.jsonl` `turns` field is **useless** — it reports `max_turns` (always 200),
  not actual turns. The entire turn-count thesis is uncheckable from events.
- Where the wall goes per invocation (thinking vs tool vs idle), the tool-call
  mix, token/cache usage, and the gap distribution all live **only** in the raw
  S0c sidecars and must be reconstructed by hand.
- The `validate`-phase **spin-vs-progress** question (turn-bound vs
  convergence-bound — the diagnosis that decides whether parallelism is even the
  right lever) requires reading the tsc error counts across turns by hand.

## Input: the S0c sidecar format (already produced)

`.dark-factory/failures/stream_<role>_<spec>_<runid>_it<N>_<phase>_attempt<M>_p<pid>s<seq>.jsonl`

Each line: `{"t": <float seconds since invocation start>, "line": "<raw claude-code stream-json event, JSON-encoded string>"}`.

`line` decodes to a claude-code stream-json event: `system`(init) | `assistant`(content
blocks: `thinking` | `text` | `tool_use`) | `user`(content: `tool_result`) |
`result`(final, carries `usage`). Confirmed format (run4 validator sidecar):
`{"t":1.004,"line":"{\"type\":\"system\",\"subtype\":\"init\",...}"}`.

## Output: one structured record per invocation

Emit an `AGENT_STREAM_SUMMARY` event to `events.jsonl` (and a flat
`.dark-factory/metrics/stream-summary.jsonl` for easy analysis):

```json
{
  "event": "AGENT_STREAM_SUMMARY", "ts": "...", "spec": "...", "run_id": "...",
  "data": {
    "role": "test-engineer", "phase": "validate", "iteration": 1, "attempt": 1,
    "outcome": "completed|timeout|failed", "truncated": false,
    "wall_seconds": 1444,
    "actual_turns": 87,            // count of assistant messages — FIXES the turns=200 bug
    "thinking_turns": 81,
    "thinking_seconds": 1330,      // Σ gap-before each thinking-bearing assistant turn
    "tool_seconds": 95,            // Σ (tool_use -> matching tool_result) elapsed
    "idle_seconds": 19,            // unattributed residual
    "tool_calls": {"Read": 22, "Bash": 14, "Write": 9, "Edit": 31, "Glob": 4},
    "files_written": 9,            // distinct paths from Write/Edit
    "tokens": {"input": 0, "output": 3400, "cache_read": 4200000, "cache_creation": 18000},
    "gaps": {"p50": 0.5, "p90": 18.0, "max": 722.0, "n_over_120s": 6},
    "tsc": {"invocations": 5, "error_counts": [14, 14, 12, 14, 13]}  // typecheck phases only; null otherwise
  }
}
```

### Each metric → which lever it informs
| Metric | Decides / informs |
|--------|-------------------|
| `actual_turns` | The turn-count thesis (currently uncheckable). Per-run, per-phase. |
| `thinking_seconds` / `tool_seconds` / `idle_seconds` | Where the wall goes — confirms thinking-bound, no hand-analysis. |
| `tool_calls`, `files_written` | Exploration-vs-authoring split; whether the TE burns turns reading vs writing. |
| `tokens.cache_read` / `cache_creation` | **Gates prefix-folding (lever #2):** if the cold-start prefix is mostly cache-hits, folding phases saves little → measure before building. |
| `gaps` (max, n_over_120s) | Thinking-latency profile; post-hoc stall analysis WITHOUT a kill mechanism (we dropped stall-kill — no evidence of recurring hangs). |
| `tsc.error_counts` | **The decisive validate diagnosis:** `[14,14,12,14,13]` = SPIN (convergence-bound → parallelism useless, fix type-resolution); `[14,9,4,1,0]` = PROGRESS (turn-bound → parallelism/folding help). |

## Mechanics

- **New module** `darkfactory/observability/stream_summary.py`:
  `summarize_stream(sidecar_path) -> dict`. Pure function, no side effects.
- **Turn/gap attribution:** walk lines in `t` order. Classify each decoded event;
  the gap *before* an `assistant`/`thinking` event accrues to `thinking_seconds`;
  a `tool_use`→matching `tool_result` span accrues to `tool_seconds`. Residual → `idle`.
- **Tokens:** read from the final `result` event's `usage`; if absent (timeout — no
  final result), sum per-`assistant`-message `usage` if present, else mark tokens
  partial and set `truncated: true`.
- **tsc parsing (typecheck phases only):** detect `tool_use` Bash calls whose command
  runs the typechecker; read the matching `tool_result` and count error lines
  (reuse the existing Check-9 tsc error parser if one exists, don't re-invent). Emit
  the per-invocation count; the across-turn array is the spin signal.
- **Two modes:**
  1. **Inline** — call `summarize_stream` right after each agent invocation (where
     `AGENT_COMPLETED`/`AGENT_FAILED` is emitted) and emit `AGENT_STREAM_SUMMARY`.
  2. **Offline CLI** — `dark-factory analyze-streams <spec> [--run <id>] [--all]`
     over existing sidecars, for retro-analysis (works on already-collected data —
     this is what we'll run on run4's TE streams the moment they exist).

## Design principles (and why this is low-risk)

- **Read-only.** Aggregates data we already write. **Never** kills, gates, retries,
  or alters a run. No agent/barrier/container change. This is the opposite of the
  stall-kill watchdog we just rejected — it observes, it does not intervene.
- **Derivative.** No new capture mechanism; the S0c sidecars already exist.
- **Fail-safe.** Malformed/truncated sidecars (timeout invocations cut mid-stream)
  → partial metrics + `truncated: true`, never raise into the run. Unknown
  stream-json event types ignored (defensive against claude-code schema drift).
- **Zero run-time cost.** Parsing a 33–200-line sidecar is trivial; runs after the
  invocation, off the critical path. (Even inline mode is post-hoc per invocation.)

## Risks / edge cases (for Linus)

- **Truncated streams on timeout** — the dominant failure case (validate 1800s
  timeouts). Must produce best-effort metrics from the partial stream + flag it;
  the postmortem already noted streams under-represent disk writes on timeout, so
  cross-check `files_written` against on-disk scenario files where feasible.
- **stream-json schema drift** across claude-code versions — parse by `type` with
  explicit allow-list; ignore unknowns; never assume a field exists.
- **Usage on timeout** — no final `result`; tokens may be partial/absent.
- **Sidecar-collision** — historically a bug (`agents.py:442-449`), now fixed via
  `_STREAM_SEQ`. Summarizer must key on the full sidecar filename (incl. `s<seq>`),
  not role+phase, to avoid merging two invocations.

## Test plan

- **Unit:** synthetic sidecars with a known (turns, thinking gaps, tool calls,
  tsc error sequence) → assert the summary matches exactly, incl. truncated case.
- **Backfill (the immediate payoff):** run the offline CLI over **run4's TE
  sidecars** once available → produce (a) the `validate` spin-vs-progress diagnosis,
  (b) actual TE turn count, (c) cache-hit rate (→ prefix-folding decision). This is
  the experiment that picks the next lever.

## Durable cross-run corpus (the optimization dataset)

The per-invocation summary is not just emitted to the run's `events.jsonl` — it is
**appended to a durable, append-only corpus that survives every run reset, `--fresh`,
and manual clear.** This is the structural fix for the data-loss that motivated this
work (run3's raw sidecars were lost when ephemeral `failures/` was cleared).

- **Trigger:** EVERY invocation, all roles — on both `AGENT_COMPLETED` **and
  `AGENT_FAILED`/timeout**. Timeouts are the highest-value records (a timed-out TE
  generate at 98% thinking / 0 files / one 906s mega-turn is the dataset's most
  important row). One corpus line per invocation.
- **Location (durability is the whole point):** a **user/framework-level** path,
  NOT inside the project's `.dark-factory/` (which gets reset/cleared/wiped).
  Default `~/.dark-factory/corpus/agent-metrics.jsonl`, config override `corpus_path`.
  Cross-project, cross-session, reset-proof, append-only. The summarizer runs
  host-side (it reads the host-side sidecar before any cleanup), so writing here
  has no container/barrier implication.
- **Record schema** = the per-invocation summary (above) **plus provenance and
  spec-shape for correlation**:
  ```json
  { "schema_version": 1, "ts": "...", "project": "budget-brain-v2",
    "spec": "...", "spec_type": "feature", "run_id": "...", "role": "test-engineer",
    "phase": "generate", "iteration": 1, "attempt": 1, "model": "claude-sonnet-4-6",
    "outcome": "timeout",
    "spec_shape": { "fr_count": 3, "ac_count": 8, "ec_count": 7 },
    /* ...all summary metrics: actual_turns, thinking_s, tool_s, tool_calls,
       files_written, tokens{output,cache_read,cache_creation}, gaps{max,p90,n_over_120s},
       tsc{error_counts} ... */ }
  ```
  `spec_shape` lets us correlate spec heaviness with TE behavior (e.g. "do specs
  with ≥7 ECs drive more mega-turns?").
- **Concurrency:** parallel TE + QA complete near-simultaneously and both append —
  use the existing `locked_append_text` (the lock the te-qa parallelization already
  uses for its 3 host writers). No interleaved/corrupt lines.
- **Barrier-safety:** the corpus is **metrics-only** — turns, timings, token *totals*,
  tool-call *histograms*, tsc error *counts*. It contains **no artifact content**
  (no scenario/source code, no test text). It crosses no information barrier; it is
  behavioral metadata, safe to persist host-side outside the project.
- **Optional raw-stream archival:** additionally copy each raw sidecar into
  `~/.dark-factory/corpus/<run_id>/` so future metrics we didn't think to compute can
  be re-derived (guards against the run3 loss for good). Larger storage; make it a
  config flag (`corpus_archive_streams`, default on — sidecars are small).
- **Analysis surface:** `dark-factory analyze-streams --corpus` reads the accumulated
  corpus for cross-run/cross-spec trends (timeout rate by role/spec_type, mega-turn
  frequency, cache-hit distribution, turn-count vs spec_shape) — the queries that
  actually pick optimization levers from a population, not one run.

## Sequencing

1. Build the summarizer (this spec) — the instrument.
2. Backfill it over run4's TE streams → validate diagnosis + turn/cache breakdown.
3. Let the data pick the lever (validate-fix vs prefix-folding vs parallelism).

## Gate

Neo (design) + Linus (code) before implementation, per framework-change policy.
Off the critical path — observability only; the in-flight detached spec-5 run is
untouched (it already writes the sidecars this consumes).
