# Sprint 1 Part B (T1.5–T1.7) — Benchmark runner + scorecard design

> Design for Neo review BEFORE implementation. Implements 00-RESET-PLAN
> Sprint 1 Part B: the from-clean-state, multi-stack, full-pipeline batch
> runner that emits a minimal JSONL scorecard. Neo flagged this as the
> heaviest/most-underestimated item and warned against the project's
> build-elaborate-mechanism instinct — this design is deliberately small.

## What this is / is NOT

**IS:** a tool that, given a battery config, runs `dark-factory run <spec>`
from a clean state N times per (target, spec, stack) and records one scorecard
row per run: `{spec, stack, target, run, outcome, exit_code, cost_usd,
iterations, run_id, wall_s, ts}`. `SHIP_WITH_GAPS`/`STABLE_FAILURE` counts as a
non-ship (the whole point).

**IS NOT (explicit anti-over-build boundaries):**
- No determinism computation, no cost-mean aggregation, no spec-vs-impl
  attribution **in the runner** — those are post-hoc queries over the JSONL
  (Neo's correction #3/#4 from the plan review). The runner emits rows; it does
  not analyze them.
- **Does NOT execute a real benchmark in this sprint.** There is no Docker in
  the dev session and the target projects (budget-brain-v2, a fastapi project)
  live outside this repo. Part B delivers the *tested tool*; producing a
  baseline number is Sprint 2 (T2.3), run by the user on a real machine.
- No new pipeline phase, no changes to `run_pipeline` / orchestrator.

## Why a clean events file removes the hard part

`events.jsonl` is per-project and append-only (`json_helpers.py:12`); run_id is
NOT on stdout (`pipeline.py:128` generates it; only in events/state/archive).
The map surfaced the simplification: **the runner deletes
`<target>/.dark-factory/events.jsonl` immediately before each run**, so after
the run the entire file is that one run's events. No run_id correlation, no
file-offset tracking, no archive-dir dependency. Metrics come from parsing the
whole (single-run) events.jsonl.

## Components (new package `darkfactory/benchmark/`)

### `outcomes.py` (pure, fully unit-testable)
- `Outcome` enum: `CLEAN_SHIP`(0), `FAILED`(1), `ESCALATED`(2), `ABORTED`(3),
  `HOLD`(4), `STABLE_FAILURE`(5), `ERROR`(other/crash).
- `outcome_from_exit_code(code: int) -> Outcome` — exact map to the confirmed
  orchestrator exit codes; unknown codes → `ERROR`.

### `scorecard.py` (pure, fully unit-testable)
- `RunMetrics` dataclass: `cost_usd: float|None`, `iterations: int|None`,
  `run_id: str|None`.
- `parse_run_metrics(events_path: Path) -> RunMetrics` — reads a single-run
  events.jsonl (tolerant of malformed lines, mirroring
  `remediation._read_records_tolerant`). Extracts: `run_id` from any event;
  `cost_usd` from the last `COST_SUMMARY` event's `data.estimated_usd`;
  `iterations` from the terminal `SPEC_*` event's `data.iterations` (SHIPPED /
  ESCALATED / STABLE_FAILURE carry it; FAILED/ABORTED may not → None). Missing
  file or no events → all-None (never raises).
- `ScorecardRow` dataclass + `row_to_json(row) -> str` and
  `append_scorecard(path, row)` (append one JSON line).

### `battery.py` (pure parse, unit-testable)
- Parse a battery TOML into a typed structure:
  ```toml
  runs = 3                     # N runs per (spec, stack); default 1
  [[target]]
  path = "/abs/path/to/budget-brain-v2"
  stack = "typescript-postgres"
  reset_cmd = "git reset --hard <pin> && git clean -fd -e .dark-factory/specs"
  setup_cmd = ""               # optional, run ONCE per target before its runs (e.g. image rebuild)
  specs = ["2026-03-28-database-foundation", "shared-parse-amount"]
  ```
- Validation: target path exists, stack non-empty, specs non-empty,
  reset_cmd non-empty (reset is mandatory — a benchmark that doesn't reset is
  measuring drift). Raises `ConfigError` on invalid battery.

### `runner.py` (subprocess orchestration; integration-tested with mocks)
- `run_battery(battery, *, out_path, dry_run=False, progress=print) -> int`:
  for each target: run `setup_cmd` once (if set); for each spec × `runs`:
  1. run `reset_cmd` in the target dir (project-specific source/artifact reset);
  2. delete `<target>/.dark-factory/events.jsonl` (guarantees single-run file);
  3. `subprocess.run(["dark-factory", "run", spec], cwd=target)`, capture
     exit code + wall time; never raise on non-zero (that's the data);
  4. `parse_run_metrics(events.jsonl)`; build `ScorecardRow`; `append_scorecard`.
  - `progress()` a one-line summary per run. `log()` what was skipped (no silent
    caps). Returns 0 if every planned run executed (regardless of run outcomes),
    non-zero only on runner-level failure (e.g., reset_cmd failed).
  - All timestamps via `time.time()` (normal Python — the Date.now restriction
    is workflow-script-only).

### CLI: `dark-factory benchmark <battery.toml> [--out scorecard.jsonl] [--dry-run]`
- Register subparser in `cli.py:main()`, define `_cmd_benchmark(args, project)`,
  add to the `_COMMANDS` dict (per the registration pattern). `--dry-run` prints
  the plan (targets × specs × runs) without executing.

### Example config: `docs/benchmark/example-battery.toml`
- A documented example pointing at placeholder paths (real targets live outside
  this repo). Includes the calibration anchors per T1.7: `shared-parse-amount`
  (known-good → expect CLEAN_SHIP), `2026-03-28-database-foundation` (known-bad,
  self-contradicting → expect STABLE_FAILURE/ESCALATED), and a placeholder
  `python_fastapi` spec so the battery is cross-stack from birth.

## Tests (no Docker required)
- `outcomes`: every exit code 0–5 + unknown → correct Outcome.
- `scorecard.parse_run_metrics`: fixture events.jsonl for each terminal type
  (SHIPPED/STABLE_FAILURE/ESCALATED/FAILED) → correct cost/iters/run_id; missing
  file → all-None; malformed line tolerated.
- `battery`: valid parse; each validation failure raises ConfigError.
- `runner` (mocked `subprocess.run` + temp target dir): asserts reset_cmd runs
  before each run, events.jsonl is deleted before each run, one scorecard row
  per planned run with the right outcome, exit-5 row reads STABLE_FAILURE,
  setup_cmd runs once per target, dry-run executes nothing.

## Reuse (don't reinvent)
- `darkfactory/utils/json_helpers.py:read_jsonl` for reading.
- Tolerant-read pattern from `remediation.py` (skip malformed lines).
- Reset recipe from `docs/plans/2026-04-16-benchmark-run-recipe.md` and
  `scripts/experiment/experiment-trial.sh:49-57` informs the example
  reset_cmd (but reset is the battery's responsibility, not hardcoded).

## Open decisions for Neo
- **Q1 (CLI vs script):** ship as a first-class `dark-factory benchmark`
  subcommand (discoverable, packaged) or a `scripts/benchmark.py`? The reset
  plan calls the scorecard "the linchpin," which argues first-class. **Proposal:
  CLI subcommand** (the testable logic lives in the module either way).
- **Q2 (reset ownership):** reset_cmd is battery-provided and project-specific;
  the runner only additionally wipes events.jsonl. Correct, or should the runner
  own a standard reset (the recipe's `rm -rf .dark-factory/{...}`)? **Proposal:
  battery-provided** — keeps the runner generic across projects/stacks; a wrong
  hardcoded reset is worse than an explicit one.
- **Q3 (subprocess vs in-process):** invoke `dark-factory run` as a subprocess
  (faithful from-clean-state, real exit code, separate atexit) vs calling
  `run_pipeline` in-process. **Proposal: subprocess** — fidelity matters for a
  measurement tool and avoids shared-state/atexit contamination.
- **Q4 (iterations source):** read `data.iterations` from the terminal SPEC_*
  event. FAILED/ABORTED may lack it → None. Acceptable, or parse iter-count
  another way? **Proposal: terminal-event field, None when absent.**
- **Q5 (scope check):** is anything here still over-built relative to "{spec,
  stack, run, outcome, cost, iters} + can run N times from clean state"? Cut it
  if so.

## Neo review — APPROVE-WITH-CHANGES (applied)

Disciplined on the big risk; two BLOCKING correctness holes (both: "measure
what you claim") + 4 scope/clarity fixes. Folded in:

1. **(BLOCKING) Distinguish CLEAN_SHIP from SHIP_WITH_GAPS.** Post-Part-A, exit
   0 is always a genuine Oracle SHIP, but a run that was *also* gap-eligible
   still writes `<spec>-defect-registry.md` (`orchestrator.py:4814`,
   `if spec_run.ship_with_gaps`). A pristine ship and a ship-with-known-gaps are
   both exit 0 — distinguish them by the presence of that file. So the outcome
   classifier takes `(exit_code, defect_registry_present: bool)`: exit 0 + no
   registry → `CLEAN_SHIP`; exit 0 + registry → `SHIP_WITH_GAPS`. (The legacy
   `emit_ship_with_gaps` is intentionally dead after Part A — note it, don't
   refile.)
2. **(BLOCKING) Runner owns framework reset.** Battery `reset_cmd` does only
   project *source* reset. The runner, per run, additionally: wipes
   `.dark-factory/state/` AND `events.jsonl`, passes **`--fresh`** to
   `dark-factory run` (else a prior interrupted run auto-resumes —
   `orchestrator.py:339-340` — and the benchmark silently measures a resumed
   run), and best-effort cleans orphan `df-run-id` Docker containers/networks
   (matching `scripts/experiment/experiment-trial.sh:49-57`). These are
   framework invariants, not project-specific.
3. **Merge `outcomes.py` into `scorecard.py`** → 3 modules (scorecard / battery
   / runner), split only at the pure/impure seam.
4. **Use a tolerant reader, not `read_jsonl`** (which raises on a malformed
   line). Add a public `read_jsonl_tolerant` to `utils/json_helpers.py` (skips
   bad lines, `[]` on missing) and use it; add a "SPEC_FAILED with no `data`
   key" fixture for the iterations-None path. Read `data.iterations` (envelope
   also has top-level `iter`; tolerate absent `data`).
5. **Document the enterprise-off assumption** in the classifier docstring (under
   enterprise, exit 1/2 are ambiguous — regression/canary/rollback overload
   exits, all `enterprise_enabled`-gated) and **warn in battery validation** if
   a target enables enterprise. Add a "never run two batteries against one
   target concurrently" precondition note (torn-line race,
   `remediation.py:317`).
6. **python_fastapi is UNVALIDATED, not cross-stack coverage.** The adapter is
   stub-tier (`adapters/python_fastapi/adapter.py:232,242` return `""`) and has
   almost certainly never run end-to-end. The example battery labels its python
   entry `# UNVALIDATED — Sprint 2 T2.0 must confirm this stack runs at all`,
   and this doc states Part B only makes the runner stack-agnostic — it does NOT
   establish python_fastapi viability. First real battery must include ≥1
   non-anchor spec per stack (don't validate the scorecard solely on the two
   anchors it was designed around).

Verdicts: Q1 CLI subcommand (measurement tool is core, not pipeline mechanism);
Q2 battery owns source reset / runner owns framework reset (refined); Q3
subprocess (fidelity); Q4 terminal-event `data.iterations`, None when absent;
Q5 merge outcomes.py.
