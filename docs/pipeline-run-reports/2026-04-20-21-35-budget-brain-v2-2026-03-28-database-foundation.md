# Pipeline Run Report — `2026-03-28-database-foundation` (1230234d)

**Target project:** `budget-brain-v2`
**Run ID:** `1230234d74264efb`
**Started:** 2026-04-20 21:35 local (05:35Z UTC)
**Ended:** 2026-04-20 22:30 local (05:30Z UTC) — pipeline exited at classifier abort
**Wall time:** ~55 minutes (of 90-minute cap)
**Spec commit baseline:** Phase D spec migration with `## 8a. Non-Negotiable Acceptance Criteria` (AC-6, AC-7, AC-10, AC-12, AC-13, AC-14)
**Validation at entry:** PASS 85/100, divergence LOW, blockers 0

## Outcome

**ABORTED at iter-3 / defect-classifier (not SHIP / NO-SHIP / ESCALATED).**

The orchestrator intentionally aborted iteration 3 after the defect classifier returned `count_mismatch` on two successive attempts. No terminal pipeline event (`SPEC_SHIPPED`, `SPEC_NOSHIP`, `SPEC_ESCALATED`) was emitted. The run's archive has `iter-1/` and `iter-2/` folders but **no `iter-3/` and no `final/meta.json`** — the abort path does not invoke the archive finalization handler.

Verbatim from `/tmp/df-run-2026-03-28-database-foundation.log:112`:

> `ERROR: error: Defect classifier could not produce a valid classification after 2 attempts (final error: 'count_mismatch'). Iteration aborted — continuing would feed the Oracle degraded input.`

## Iterations

| Iter | Gate pass_rate | Gate exit | Trajectory | Notes |
|-----:|---:|---:|---|---|
| 1 | 0% | 2 | — | Setup FAIL (drizzle-kit migrate hung at `applying migrations...`). Iter 1 had a deterministic-preflight failure → focused implementer retry → preflight cleared. Export Contract: 1 violation. |
| 2 | 0% | 2 | PLATEAU | Setup FAIL, identical `applying migrations...` stall. Oracle’s iter-1 feedback ("wire `registerDatabaseHealthCheck` / `tenantContext` plugin") was addressed but the migration itself never completes in gate. |
| 3 | 83% (gate phases) / 60% (scenarios: 30/50 pass) | 1 | MIXED | Setup unblocked. 9/11 gate phases PASS; Substrate Verification FAIL, Import Contract FAIL, Blind Scenarios FAIL (20/50 failures, 14 crashes). defect-classifier `count_mismatch` x2 → abort. |

Oracle iter-1 NO-SHIP; Oracle iter-2 NO-SHIP; Oracle iter-3 **never ran** (aborted before reviewer iter 3).

## Wall time

~55 minutes total. Key spans (absolute seconds from events.jsonl):

| Phase | Duration |
|---|---:|
| spec-validator | 165s |
| architect | 277s |
| qa-holdout | 461s |
| test-engineer generate + coverage_regen | 264s + 193s |
| implementer iter-1 (two attempts) | 370s + 206s |
| implementer iter-2 | 153s |
| implementer iter-3 | 270s |
| defect-classifier iter-3 (two attempts) | 103s + 158s |

## State file (final, at abort)

```
spec=2026-03-28-database-foundation
run_id=1230234d74264efb
spec_hash=03880360e89af86b52c6247b4b0b9d4a78d4996cf8d8b23122a19aaf84641996
last_decision=NO-SHIP
server_error=
smoke_result=passed
phase=review
pass_rate=83
gate_exit_code=1
status=interrupted
iteration=3
completed_step=2
```

Note `last_decision=NO-SHIP` is the iter-2 Oracle decision; it was not updated for iter-3 because Oracle iter-3 never ran.

## Quality gate summary (iter-3, verbatim)

```
# Quality Gate Summary: 2026-03-28-database-foundation
Date: 2026-04-21T05:26:05Z

| Gate | Status |
|------|--------|
| Install | PASS |
| Setup | PASS |
| Build | PASS |
| Lint | PASS |
| Type Check | PASS |
| Substrate Verification | FAIL |
| Import Contract | FAIL |
| Self-Tests | PASS |
| Scenario Lint | PASS |
| Blind Scenarios (executable) | FAIL |
| Mutation Testing | SKIP |
<!-- END GATE TABLE -->

## Result: FAIL
```

Scenario counters (`scenario-counts.env`):
```
SCENARIO_TOTAL=50  SCENARIO_PASSED=30  SCENARIO_FAILED=20
SCENARIO_CRASHED=14  SCENARIO_ASSERT_FAILED=6  SCENARIO_SKIPPED=0
SCENARIO_RATE=60   SATISFACTION_SCORE=60
```

Import Contract diagnostic (verbatim):
```
| Import Contract | FAIL | 3 entry point(s) — one or more failed to resolve |
cmd: pnpm exec vitest run --config .df-vitest-resolution-check.config.ts
     ../../.dark-factory/scratch/resolution-check/2026-03-28-database-foundation/contract-00.test.ts
     ../../.dark-factory/scratch/resolution-check/2026-03-28-database-foundation/contract-01.test.ts
     ../../.dark-factory/scratch/resolution-check/2026-03-28-database-foundation/contract-02.test.ts
cwd: apps/api
```

## Substrate verification

Iter-3 `substrate-findings.json` (verbatim):

```json
{"status": "fail", "findings": [
  {"severity": "error", "code": "substrate-guc-no-check",
   "message": "GUC entry 'app.tenant_id' has no registration_check; manifest is malformed",
   "path": "/manifest.toml", "line": 0},
  {"severity": "error", "code": "substrate-rls-no-check",
   "message": "rls_table entry 'baseline_pattern' missing rowsecurity_check or policy_count_check; manifest is malformed",
   "path": "/manifest.toml", "line": 0}
]}
```

Phase B’s psycopg pin is now in effect (no more `substrate-psycopg-missing`), so the verifier reached the manifest-consumer step. The findings are real: the architect emitted a substrate manifest with missing `registration_check` / `rowsecurity_check` / `policy_count_check` fields. This is a manifest-template gap, not a verifier bug.

## Setup phase hang (iter 1 & 2) — verbatim

Both iter-1 and iter-2 gate summaries ended identically:

```
### Setup Failure Details
No config path provided, using default 'drizzle.config.ts'
Reading config file '/project/apps/api/drizzle.config.ts'
Using 'postgres' driver for database querying
[⣷] applying migrations...
<!-- SETUP_FAILURE=true -->
## Result: FAIL (setup)
```

No error, no traceback — `drizzle-kit migrate` hangs on `applying migrations...` and is eventually killed by the gate timeout. Iter-3 passed Setup, so the issue is implementation-state-dependent: the iter-1/2 implementations produced a `drizzle.config.ts` / `DATABASE_URL` / schema combination that caused drizzle-kit to block rather than error out. Iter-3 did not reproduce it.

## Defect-classifier abort (iter-3)

The classifier wrote this envelope both attempts:
```json
{"iteration": 3, "schema_version": 1, "error": "count_mismatch", "defects": []}
```
Expected: one defect per scenario-failure (20) OR per registered scenario (50). Observed: 0. The agent returned an empty defects array while the orchestrator expected a non-zero count that matched one of the two counter totals. Two attempts × `count_mismatch` → orchestrator abort with the preserved error line in the log.

Suspected cause: classifier prompt regression, scenario/gate counter drift, or a new classifier output-schema expectation that wasn't met. This is a **pipeline regression worth investigating before the next run** — prior shipped runs (d8268a4a, dc53cf0d) did not hit this path because their iter-3 was ship-eligible before the classifier ran, or the counts aligned.

## Notable events

- `SMOKE_COMPLETED` all three iterations clean (`server_error` empty).
- `COVERAGE_GAP` fired once after test-engineer's `generate` phase → `COVERAGE_REGEN` → `COVERAGE_PASS`. Scenario count grew from 9 → 11 visible scenarios (expanded further at execution into 50 individual test cases).
- `TRAJECTORY_CLASSIFIED iter=2 trajectory=PLATEAU` (iter 1→2 no improvement).
- `TRAJECTORY_CLASSIFIED iter=3 trajectory=MIXED` (iter 2→3 major recovery).
- `CLASSIFIER_IO_HASH ... error=count_mismatch` (attempts 0 and 2 — attempt numbering is non-contiguous in the emitted events).
- No `BARRIER_PATH_ECHO_DETECTED` events.

## Server / service errors

Smoke test all three iterations: `server_error=""`, `smoke_result=passed`. No startup failure logs to attach.

## Audit log spot-check — barrier masks

Verbatim excerpts from `.dark-factory/audit/2026-03-28-database-foundation-1230234d74264efb.log`:

**qa-holdout** — source dir masked, visible-test dir masked, `.git` masked:
```
docker run --rm ... -v .../.dark-factory/holdouts:...:rw
  --tmpfs /project/apps/api/src:ro,size=0
  --tmpfs /project/.dark-factory/scenarios:ro,size=0
  --tmpfs /project/.git:ro,size=0
  --label df-role=qa-holdout dark-factory-node ...
INFORMATION BARRIER: You MUST NOT access source code directories or .dark-factory/scenarios/
```

**test-engineer** — source dir masked, opposite visible-test dir masked, `.git` masked:
```
docker run --rm ... -v .../.dark-factory/scenarios:...:rw
  --tmpfs /project/apps/api/src:ro,size=0
  --tmpfs /project/.dark-factory/holdouts:ro,size=0
  --tmpfs /project/.git:ro,size=0
  --label df-role=test-engineer dark-factory-node ...
INFORMATION BARRIER: You MUST NOT access source code directories or .dark-factory/holdouts/
```

Both roles mask `.git` and the opposite visible-test dir. `--disallowedTools` only covers one source_dir at the prompt layer (defense-in-depth limitation documented in CLAUDE.md); Docker tmpfs is the primary barrier and here masks `apps/api/src` only. `apps/web/src` and `packages/shared/src` from the project config were NOT enumerated as tmpfs masks in the audit argv — **defense-in-depth gap to confirm against the orchestrator’s SOURCE_DIRS enumeration** (if they aren’t listed in the adapter config for test-engineer/qa-holdout, this is expected; otherwise a gap). Flag, don't block.

---

## Change Validation

### 1. Import Contract gate phase — **FIRED (FAIL on real defect)**

- **Fired:** yes. Gate summary line (verbatim):
  > `| Import Contract | FAIL | 3 entry point(s) — one or more failed to resolve |`
- **Behaved correctly:** yes — the phase ran vitest against 3 generated contract tests and reported a genuine failure, not the false-FAIL that motivated the pyyaml rewrite. The phase fired between Substrate Verification and Self-Tests (not between Substrate and Self-Tests as the skill docs state — current position is between Self-Tests and Scenario Lint). Python3 is available in the gate container (the phase produced output); no deployment blocker.
- **Did NOT fire iter 1/2:** because Setup FAIL short-circuited the gate before the Import Contract phase was reached. This is correct gate-ordering behavior.

### 2. Manifest contract context in Oracle feedback — **NOT EXERCISED**

- **Fired:** no.
- **Reason:** iter-1 and iter-2 feedback files (`reviews/2026-03-28-database-foundation-feedback-iter1.md`, `-iter2.md`) contain no bullets matching `specifier`, `named.export`, or `module.system` (grep `-iE "specifier|named.export|module.system|manifest"` — zero hits). The iter-1 feedback cited FR-4 and FR-2 by section anchors but did not attach manifest entry-point specifiers to the fix items.
- **Possible causes:** (a) `/df-write-feedback` Step 3.5 only injects when a cited section maps to a manifest entry point — FR-4 (health-check registration) / FR-2 (RLS plugin registration) may not have matched entries in the spec’s manifest section. (b) Feedback generation ran in iter-2 but never in iter-3 (aborted), so the diff-for-iter-3 validation case is unavailable.
- **Recommend:** re-run a clean pipeline where at least one fix item anchors to a manifest entry point, then diff feedback-iter1 vs feedback-iter2 for injected bullets.

### 3. MEDIUM precision auto-hint — **CORRECTLY SKIPPED**

- **Fired:** no.
- **Behaved correctly:** yes. Spec validated at divergence=LOW, so `darkfactory/architecture/precision_hint.py` should skip. Verified: `ls .dark-factory/architecture/*precision-hint* → no matches` (expected).
- **Stale-hint clearing:** not testable this run (nothing to clear).

### 4. Per-run artifact archive — **PARTIALLY FIRED**

- **Fired:** yes for iter-1 and iter-2; no for iter-3 and no final/.
- **Archive directory:** `.dark-factory/archive/20260420-213551-2026-03-28-database-foundation-1230234d/`
- **Contents:** `run.json`, `iter-1/`, `iter-2/`. **Missing: `iter-3/` and `final/meta.json`.** The abort path (orchestrator's "defect classifier could not produce a valid classification ... Iteration aborted") does not invoke the archive finalization handler. This is a gap in `archive.py` — it needs an abort-handler branch that writes `final/meta.json` with `terminal_status: aborted`.
- **Spec-scoped filtering — PARTIAL:** files inside `iter-1/reviews/` all start with the spec name (spec-scoped ✓), but the snapshot includes stale files from the prior `dc53cf0d` run that were never cleaned from the live `reviews/` dir. Example: `iter-1/reviews/2026-03-28-database-foundation-feedback-iter2.md` has mtime `Apr 20 18:13:51` — from the 17:22 reproducibility run, four hours before this run started. **The archiver filters by spec name, not by run.** Not strictly a regression (reviews/ was never in the step-1 wipe list) but it makes per-run archives misleading. Recommend either (a) add `reviews/` to the runtime-wipe set, or (b) have the archiver filter by file mtime ≥ run_start_time.
- **`archive:` log warnings:** none (grep `-iE "archive" /tmp/df-run-*.log` returned only unrelated SOURCE_DIRS_COVERAGE warnings).
- **SOURCE_DIRS_COVERAGE warnings — side effect of archive/:** the test-presence analyzer emitted ~10 WARNINGs pointing at `.dark-factory/archive/<run>/iter-1/...` paths, e.g.
  > `WARNING: test_presence: SOURCE_DIRS_COVERAGE: added source '.dark-factory/archive/.../iter-1/architecture/...manifest.yaml' is outside configured source_dirs ['apps/api/src'] — no test presence requirement enforced`

  These are advisory-only (no test-presence requirement triggered) but indicate the analyzer's source-scan picks up archive artifacts. Archive paths should be excluded from source scans.

---

## Phase D Validation

**Verdict: did NOT exercise the blocking-AC veto path (negative test).**

- **Blocking ACs in spec:** AC-6, AC-7, AC-10, AC-12, AC-13, AC-14 — all six under `## 8a. Non-Negotiable Acceptance Criteria`.
- **Did precheck parse them?** **Ambiguous — no visible evidence in the precheck artifact.**
  - `precheck/2026-03-28-database-foundation.json` top-level keys: `['ambiguities', 'anchor_intent_issues', 'contracts', 'criteria_issues', 'fatal', 'fr_count', 'placeholders', 'precision', 'scenario_intents', 'service_advisories', 'shape_claims', 'structure', 'summary']`.
  - **No `blocking_acs`, `blocking_ac_issues`, or `non_negotiable_section` field** in the serialized JSON.
  - `structure.sections[]` enumerates 10 sections: Problem Statement, Functional Requirements, Non-Functional Requirements, Constraints, Interface Contract, Edge Cases, Error Handling, Acceptance Criteria, Examples, Dependencies & Out of Scope. **"Non-Negotiable Acceptance Criteria" is NOT listed** — suggesting the precheck's section-enumerator doesn't include it, even though `blocking_acs.py` defines aliases for it.
  - `summary`: `"Pre-check passed. 0 issues, 0 warnings."` — validator accepted the spec.
  - Events log: grep `-iE 'precheck|blocking|ac_blocking|non.negotiable' events.jsonl` → zero hits.
  - **Possible interpretations:** (a) `parse_blocking_acs` ran at spec-load time and populated `SpecRun.ac_blocking` in memory but the precheck JSON serializer wasn't updated to include it (plumbing gap). (b) The section was silently skipped. The `## 8a.` numeric prefix might or might not be accepted by the section-name normalizer.
  - **Recommend:** add either a `blocking_acs` field to the precheck JSON, or a `PRECHECK_BLOCKING_ACS_PARSED` event, so downstream observers can confirm parsing.

- **Did the veto fire?** **No — the veto path was not reached.** Iter-3 aborted at defect-classifier before reviewer/Oracle could evaluate `should_ship_with_gaps`. Pass rate (83% gate / 60% scenarios) would have made the run a SHIP_WITH_GAPS candidate if the prior two runs are a guide, and EC-4 (fail-closed RLS) — which maps closely to AC-12 — is among the blocking ACs, so the veto *might* have fired if Oracle had run. Cannot confirm.

- **Negative-test status:** this run is a partial negative test (abort ≠ ship), but does not validate the veto logic itself. Phase E acceptance — "SPEC_ESCALATED or SPEC_NOSHIP when EC-4 still fails" — is not demonstrated by this run.

---

## Summary of findings for the next pipeline fix pass

1. **New pipeline regression: defect-classifier `count_mismatch` abort path.** Investigate whether the classifier's expected-count contract drifted from the scenario/gate counters, whether the retry logic can tolerate zero-defects when scenarios did fail, and whether the abort path should write `final/meta.json` before exiting.
2. **Archive abort-handler gap.** `.dark-factory/archive/.../final/meta.json` should be written on abort with terminal_status=aborted (currently only ship/escalate/abort-handler writes it for graceful terminals; the classifier-abort path exits before reaching this code).
3. **Archive stale-file leak.** `reviews/` isn't in the step-1 runtime wipe; archive snapshots include prior-run content with mtimes predating the run. Either wipe `reviews/` or filter archive contents by mtime.
4. **Phase D plumbing visibility.** `blocking_acs` map parsed (likely) but not serialized to the precheck JSON nor emitted as an event. Add an explicit observable.
5. **Substrate-manifest template.** Architect emitted a manifest with missing `registration_check` / `rowsecurity_check` / `policy_count_check` — template needs a stricter example or a validator that fails architect output.
6. **Oracle feedback manifest-context injection.** Never observed in iter-1 or iter-2 feedback. Confirm injection conditions and re-run.
7. **iter-1/2 Setup hang.** The implementation's `drizzle-kit migrate` hung before reaching scenarios; migrations-into-substrate (Phase C as planned) was NOT committed — only the scenario lint subset landed. Revisit whether to complete Phase C so Setup doesn't run migrations at all.
