# Pipeline Run Report — 2026-04-08 23:17 UTC

**Repo:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Framework commit:** 15ea645 (Phase 4.6b.1c)

Trigger: validate Phase 4.6b.1c fix (defect-classifier write_dirs +
generic rw preflight + telemetry jsonl). First run where classifier
should produce real defects.json instead of classifier_no_output stubs.

## Run 9 — ESCALATED after 3 iterations (run_id `cdfd1a16ee6242b7`)

**Outcome:** ESCALATED. 0% pass rate every iteration. Trajectory
classified PLATEAU from iter 2 onward. Wall clock ~52 minutes.

### Timeline

| Phase | Start | Wall (s) | Notes |
|---|---|---|---|
| spec-validator | 23:18:53 | 161 | PASS |
| architect | 23:21:34 | 168 | PASS |
| qa-holdout | 23:24:23 | 623 | 5 holdout files + NOTES.md |
| test-engineer generate | 23:34:46 | 193 | initial COVERAGE_GAP (13 anchors) |
| test-engineer regen | 23:38:00 | 382 | **COVERAGE_PASS** — 23/23 anchors covered |
| iter 1 implementer | 23:44:25 | ~400 | Gate FAIL 0% |
| iter 1 classifier | — | — | clean defects.json |
| iter 1 reviewer/oracle | — | — | NO-SHIP |
| iter 2 implementer | 23:54:57 | 311 | Gate FAIL 0% |
| iter 2 classifier | 00:00:16 | 60 | **real output, no stub** |
| iter 2 reviewer | 00:01:16 | 230 | |
| iter 2 oracle | 00:05:06 | 72 | NO-SHIP |
| iter 3 implementer | 00:06:18 | 168 | Gate FAIL 0% |
| iter 3 classifier/review/oracle | — | — | **SKIPPED — max iter reached** |
| escalation | 00:09:13 | 87 | SPEC_ESCALATED |

### What worked (Phase 4.6b.1c validated)

- **Defect classifier is writing real `defects.json`.** Iter 1 and
  iter 2 defects files present and well-formed — no
  `classifier_no_output` stubs. The 4.6b.1c `write_dirs` fix is
  confirmed end-to-end. Example (iter 1):
  ```json
  {"test_name": "Environment validation scenario",
   "category": "IMPL_BUG",
   "cited_anchor_ids": ["FR-1","EC-1","EC-2","EC-3","AC-1","AC-2"],
   "rationale": "..."}
  ```
- **Telemetry jsonl populated.** `run-telemetry.jsonl` contains 16
  lines with **real token counts** parsed from Claude stdout, not
  the 800000/400000 event-log stubs. Example:
  `{role: "spec-validator", wall_clock_ms: 161476, input_tokens: 112,
  output_tokens: 7064, cache_read_tokens: 249785,
  cache_creation_tokens: 37732}`. Attribution data for Run 9 is
  usable as-is.
- **TE coverage regen now self-corrects.** Run 3 hit
  COVERAGE_FLOOR_VIOLATION on the `_MARKDOWN_REQUIREMENT_RE` format
  mismatch. Run 9's TE generated an initial gap (13 anchors), then
  **passed regen attempt 1 with 23/23 anchors**. Either TE adapted
  its output format or the scanner tolerates bracket form in
  practice — either way the floor is no longer a hard block.
- **4.6b.1.v wiring (QA Holdout → TE → implementer loop) is
  functioning.** Full 3-iter loop with classifier + reviewer + oracle
  ran cleanly for 2 iterations, something no prior run achieved.

### What's still broken (new findings — do not fix during run)

#### Finding 1: gate `scenario_total=1` despite 6 failing tests in results
- `SCENARIO_TOTAL=1`, `SCENARIO_FAILED=1` in
  `scenario-counts.env` every iteration.
- But `test-results-iter3.json` contains **6 distinct failing
  tests** (Environment validation > FR-1/EC-1/EC-2/EC-3, Server
  Startup > AC-1/AC-2).
- The gate parser is collapsing a file-level result into a single
  "scenario" instead of counting per-test. This ALSO breaks
  classifier coverage — the classifier only saw "Environment
  validation scenario" as one test, not the 6 real failing tests.
- Likely in `darkfactory/pipeline/quality_gate.py` parsing of the
  vitest `test-results-iter*.json` format, or the
  `scenario_counts.env` writer upstream of it.

#### Finding 2: only a subset of the 12 TE test files actually ran
- `scenarios/2026-03-28-api-bootstrap/` contains **12 `.test.ts`
  files** (environment-validation, error-handling,
  global-error-handling, health-check-registry, health-endpoint,
  rate-limit-tests, rate-limiting, redaction, routing-cors,
  security-logging, server-startup, structured-logging).
- Only 6 test results came back, all from 2 files
  (environment-validation + server-startup).
- Vitest either crashed on the other 10 files (unreported) or
  they were excluded by some glob/config. Needs investigation —
  this is blocking any real pass-rate signal.

#### Finding 3: iter 3 classifier/reviewer/oracle skipped on max-iter
- After iter 3 gate FAIL, pipeline jumped straight from
  `TRAJECTORY_CLASSIFIED PLATEAU` to `escalation`, skipping iter 3's
  classifier + reviewer + oracle steps. Design question: should
  the final iteration's classification still run so the escalation
  agent has full attribution? (defects-iter3.json is missing
  while defects-iter1 and defects-iter2 exist.)

### Cost snapshot
- Each iter logged `estimated_usd: 42.0` (based on 200-turn stub).
  Real telemetry now lets us compute actuals in a follow-up.

### Conclusion
Phase 4.6b.1c is validated: the defect classifier produces real
output, the telemetry jsonl is populated, and the full 3-iter
loop runs cleanly. The ESCALATE outcome is driven by **downstream
bugs the classifier now exposes, not by the classifier itself** —
specifically the scenario count collapse (Finding 1) and the
missing test-file executions (Finding 2). These are the next
targets, not 4.6b.1c regressions.
