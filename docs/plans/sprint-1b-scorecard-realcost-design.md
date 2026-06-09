# Part B follow-up — scorecard real-cost from run-telemetry (item #2)

> Design for Neo review BEFORE implementation. Fixes the benchmark scorecard's
> cost gap surfaced by the first real run: `cost_usd=null` on aborts, and (per
> item #1) the only available cost was estimate-at-cap fiction. The honest fix:
> consume the REAL per-agent token usage already captured in
> `.dark-factory/reports/run-telemetry.jsonl`.

## Why

- `run_pipeline` reports `total_turns += config.agent_max_turns` (cap, per
  agent) and `COST_SUMMARY.estimated_usd = estimate_cost(total_turns)` — cap
  fiction, no cache modeling. (Measured: validator est said 400K out tokens;
  real was 7,276.)
- The REAL per-agent usage IS captured at `.dark-factory/reports/
  run-telemetry.jsonl` (schema v1: `input_tokens`, `output_tokens`,
  `cache_read_tokens`, `cache_creation_tokens`, `model_id`, `wall_clock_ms`,
  `role`, `exit_code`) by `agents._emit_telemetry` — but **nothing reads it**
  (persisted-but-not-consumed). It has lines even on aborts (the validator line
  exists despite the abort), so it fixes the abort-cost gap for free.
- `_framework_reset` already wipes `reports/` before each run, so the file is
  single-run (same guarantee as events.jsonl).

## Scope (tight)

- **In:** the benchmark scorecard consumes run-telemetry real tokens + derives a
  cost estimate.
- **Out (tracked follow-up, NOT this change):** fixing the pipeline's own
  `total_turns`/`COST_SUMMARY` to use real tokens. That touches budget
  enforcement + many tests + historical comparability — a separate item. This
  change only makes the *scorecard* honest.

## Components

### `scorecard.py` (pure)
- `RunUsage` dataclass: `input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cache_creation_tokens`, `agents` (count), `wall_ms` (sum). All ints.
- `parse_run_usage(telemetry_path) -> RunUsage` — tolerant sum over agent lines
  (`read_jsonl_tolerant`); missing file → all-zero. Pure.
- `estimate_cost_usd(usage, model_id) -> float | None` — cache-aware pricing
  from a small built-in `_MODEL_RATES` table (per-1M-token input/output rates
  for current models: claude-opus-4-*, claude-sonnet-4-*, claude-haiku-4-*).
  Cache ratios (Anthropic standard): `cache_read = 0.1 × input rate`,
  `cache_creation = 1.25 × input rate`. Unknown model → `None` (tokens still
  reported; $ omitted rather than guessed). model_id taken from the telemetry
  lines (use the modal/most-recent model_id across agents).

### `ScorecardRow` (extend, not replace)
Add real-usage fields: `input_tokens`, `output_tokens`, `cache_read_tokens`,
`cache_creation_tokens`, `agents`. **Redefine `cost_usd`** to be the
real-token-derived estimate (or `None` if model unknown), replacing the old
COST_SUMMARY (cap-based) value. Keep `iterations` from the terminal event.

### `runner.py`
After the run, in addition to `parse_run_metrics(events)`, call
`parse_run_usage(reports/run-telemetry.jsonl)` and `estimate_cost_usd(...)`;
populate the new row fields. Works on every outcome incl. abort/error.

## Tests (no Docker)
- `parse_run_usage`: fixture telemetry with 3 agents → summed tokens + agent
  count; missing file → zeros; malformed line tolerated.
- `estimate_cost_usd`: known model → correct cache-aware $ (hand-computed);
  unknown model → None; zero usage → 0.0.
- `runner`: a fixture that writes a run-telemetry.jsonl during the mocked run →
  row carries real tokens + cost; abort path still gets real tokens (the whole
  point).

## Open decisions for Neo
- **Q1 ($ vs tokens-only):** ship the built-in `_MODEL_RATES` table (proposal),
  or report tokens only and leave $ to post-hoc? Rates drift, but a cost number
  is what makes the scorecard actionable. Proposal: ship the table, `None` on
  unknown model, tokens always present as the honest primary.
- **Q2 (cache ratios):** hardcode 0.1×/1.25× (Anthropic standard) — acceptable
  approximation, or read from config? Proposal: hardcode + comment; revisit if
  a provider differs.
- **Q3 (redefine cost_usd):** OK to change `cost_usd`'s meaning from cap-based
  COST_SUMMARY to real-token-derived? No external consumers yet (scorecard is
  brand new), so no migration. Proposal: yes, redefine.
- **Q4 (scope):** confirm leaving the pipeline's own total_turns/COST_SUMMARY
  cap-fiction untouched (tracked follow-up) is the right boundary for this item.

## Neo review — APPROVE-WITH-CHANGES (applied)

1. **Per-model rates, in `cost.py` not benchmark.** Config has a single rate
   pair and it's OPUS ($15/$75); the real run used Sonnet (~5× cheaper) — a
   single pair is dishonest for mixed/non-Opus runs. Add `MODEL_RATES` (per
   Claude model id → (input,output) per-token) + a `cost_from_tokens(model_id,
   input, output, cache_read, cache_create) -> float | None` helper to
   `intelligence/cost.py` (next to existing rate constants; NOT a parallel table
   in `benchmark/`). Unknown model → None. scorecard imports it.
2. **Filter telemetry by `run_id`** in `parse_run_usage` (the run's run_id from
   the events parse) — don't silently depend on `_framework_reset` having run
   (telemetry opens append-mode; resume could accumulate).
3. **`agents` → `agent_invocations`** (sum of lines = attempts incl. retries;
   correct for cost since you paid for retries; do NOT treat as distinct-role
   count).
4. **Make the split legible:** scorecard `cost_usd` is the real-telemetry value;
   document that events.jsonl COST_SUMMARY stays cap-fiction (~55× high) until
   the separate pipeline follow-up. The real token fields in the row make usage
   legible. Tracked follow-up: fix pipeline total_turns/COST_SUMMARY.
5. **Cache comments:** `cache_read = 0.1× input`, `cache_creation = 1.25× input`
   are the Anthropic 5-minute-tier multipliers; the 1-hour tier (2.0×) is
   intentionally not modeled (pipeline doesn't use it).

Verdicts: Q1 ship $ via cost.py per-model table (not tokens-only, not a
benchmark-layer table); Q2 hardcode 5-min cache multipliers + comment; Q3
redefine cost_usd; Q4 scope boundary correct, label the split.
