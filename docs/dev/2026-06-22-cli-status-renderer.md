# `dark-factory status` — run visualization (v1 design note)

Brainstormed 2026-06-22 while hand-triangulating the live spec-4 run
(`a8e1741f`). A separate process CAN observe a live run today — all state is
persisted to `.dark-factory/` as the run proceeds (proven: every status check
this session was a separate process; reads never interfere with the run —
append-only/atomic writes, read-only observer). The gap is presentation, not
data.

## Command shape (v1)

- `dark-factory status [<spec>]` — snapshot of the latest (or named) run.
- `--watch` — redraw on each new telemetry row (tail).
- `--oneline` — compact strip for scripts/CI.

Pure read; no new pipeline state required for the snapshot (but see the
`current_phase` addition below, which makes it clean instead of a 3-source join).

## Data sources on disk (verified against run a8e1741f)

| File | Gives | Granularity |
|------|-------|-------------|
| `state/<spec>.state` | `status=running`, `run_id`, `completed_step` | coarse (major step) |
| `reports/run-telemetry.jsonl` | completed-phase rows (role, phase, exit_code, iteration_index, ts) → the `✓` tree + per-phase timings | per-phase **completion** |
| `audit/<spec>-<run>.log` (last `CONTAINER`) | current agent/role + its container start time → the `⠋ NOW` line + elapsed-in-phase | live current phase |
| `failures/stream_*` (newest) | live agent stdout / sub-phase (e.g. coverage_regen → repair) | live, sub-phase |

## The one upstream gap (and the cheap fix)

There is **no unified `events.jsonl`** with `phase_begin`/`phase_end` markers for
a run. So "what's running right now" must be RECONSTRUCTED by joining the audit
log's last `CONTAINER` line against the telemetry's last completion — workable
but awkward. **Cheap fix (prerequisite, do opportunistically):** write a
`current_phase` / `current_role` / `phase_started_at` field into `<spec>.state`
on each phase-begin, so `status` has ONE authoritative live source. This is a
small additive write on the existing phase-begin path; broadly useful for any
future observability, not just this renderer.

## v1 mock (the recommended primary view)

```
┌─ dark-factory ▸ run ────────────────────────────────────────────────┐
│ spec  <spec>                                  run  <run_id8>          │
│ L3 autonomous · iter 1/3 · elapsed 1h47m · model claude-sonnet-4-6   │
├──────────────────────────────────────────────────────────────────────┤
│ ✓ Spec Validation         19m54s   PASS 98/100                        │
│   ├ elicitation gate          16s   ✓ clean                           │
│   └ deferral gate ×3        4m16s   ✓ majority → FLAGGED:[]            │
│ ✓ Architect               11m02s   manifest ✓                         │
│ ✓ Substrate Owner          2m18s   prep ✓                             │
│ ✓ Proposal Rehearsal       3m41s   12/12 typecheck ✓                  │
│ ⠋ Test Engineer           28m12s   coverage_regen · cycle 2    ◀ NOW  │
│ ◷ QA Holdout                   —   queued  (parallel: off)            │
│ ○ Coverage Floor / Implementer / Gate / Classify / Review / Oracle    │
├──────────────────────────────────────────────────────────────────────┤
│ barrier ▸ Test Engineer   sees: spec ✓   masks: holdouts·source·.git  │
└──────────────────────────────────────────────────────────────────────┘
   ✓ done   ⠋ running   ◷ queued   ○ pending   ✗ failed   ↺ retry
```

The **barrier line** (what the current agent can/can't see) is the
dark-factory-specific element a generic CI viewer wouldn't have — it makes the
information-barrier model visible. The **iteration loop** (collapse completed
iters, expand the live one) is the other pipeline-specific shape worth getting
right.

## Scope discipline

- v1 = renderer over existing files + the `current_phase` state field.
- DEFER to v2: per-phase cost/token rollups (the data is in run-telemetry.jsonl
  but is cap-based fiction today — see the cost-accounting memory), a curses TUI,
  multi-run history.
