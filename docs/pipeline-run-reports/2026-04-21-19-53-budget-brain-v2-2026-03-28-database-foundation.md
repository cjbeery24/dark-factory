# Pipeline Run — 2026-03-28-database-foundation on budget-brain-v2

**Run ID:** `e614a17612dd4993`
**Date:** 2026-04-21 19:02 → 19:53 local (02:02:35Z → 02:53:20Z)
**Wall time:** ~50m 45s
**Outcome:** **SPEC_FAILED (aborted at iter-2 defect-classifier, `count_mismatch`)**
**dark-factory HEAD at run:** `d90a3f2` (includes P1 `a50cd1c` + P2 `d90a3f2` fixes from this session)

## Summary

This run validated two pipeline fixes landed earlier in the session:

1. **P1 — scenario-infra fast-fail bypass (`a50cd1c`):** added `import_contract_status` / `substrate_verification_status` to `GateResult`, extracted `should_scenario_infra_fastfail()` helper, and bypassed the guard when either upstream gate FAILs. Locked in by 7 new unit tests in `test_pipeline_terminal_invariants.py::TestScenarioInfraFastfailPredicate`.
2. **P2 — broadened terminal-abort catch (`6d4d0ec`, follow-up fabricated-citation fix `d90a3f2`):** `run_single_spec` catches `(DarkFactoryError, BudgetExceeded)` so abort-path raises reach `_handle_abort` and emit `final/meta.json`.

Both fixes behaved correctly. The run nevertheless aborted — at a **different, pre-existing failure site** (defect-classifier `count_mismatch`, documented in `memory/project_pipeline_unresolved_bugs.md`).

Scenario pass rate improved **0% (iter-1) → 75% (iter-2)** — the largest green delta observed on this spec to date. The substrate manifest, however, came back with **the exact same two errors** as the prior run (`e5634bc6`), strongly indicating the architect prompt cannot produce a well-formed manifest for this spec without intervention.

## Iteration Timeline

| Iter | Pre-gate | Smoke | Quality gate | Pass rate | Defect classifier | Oracle |
|------|----------|-------|--------------|-----------|-------------------|--------|
| 1 | PASS | PASS (advisory) | FAIL | 0% | — | NO-SHIP |
| 2 | PASS | PASS (fatal) | FAIL | 75% | `count_mismatch` x2 → **ABORT** | — (never reached) |

Iteration-1 gate was 0% because all 51 scenarios crashed on build-time contract mismatches (declared exports missing). Iteration-2 implementer corrected most of that — 36/51 passing, 12 assert-failed + 3 crashed.

## State File

```
spec=2026-03-28-database-foundation
run_id=e614a17612dd4993
spec_hash=03880360e89af86b52c6247b4b0b9d4a78d4996cf8d8b23122a19aaf84641996
completed_step=2
last_decision=NO-SHIP
iteration=2
server_error=
smoke_result=passed
phase=review
pass_rate=75
gate_exit_code=1
status=failed
abort_reason=Defect classifier could not produce a valid classification after 2 attempts (final error: 'count_mismatch'). Iteration aborted — continuing would feed the Oracle degraded input.
```

## Quality Gate Summary (iter-2, verbatim)

```markdown
# Quality Gate Summary: 2026-03-28-database-foundation
Date: 2026-04-22T02:50:24Z

| Gate                        | Status |
|-----------------------------|--------|
| Install                     | PASS   |
| Setup                       | PASS   |
| Build                       | PASS   |
| Lint                        | PASS   |
| Type Check                  | PASS   |
| Substrate Verification      | FAIL   |
| Import Contract             | FAIL   |
| Self-Tests                  | PASS   |
| Scenario Lint               | PASS   |
| Blind Scenarios (executable)| FAIL   |
| Mutation Testing            | SKIP   |

## Result: FAIL
```

Scenario counters (`reports/…-scenario-counts.env`):

```
SCENARIO_TOTAL=51
SCENARIO_PASSED=36
SCENARIO_FAILED=15
SCENARIO_ASSERT_FAILED=12
SCENARIO_CRASHED=3
SCENARIO_SKIPPED=0
SCENARIO_RATE=70   # SCENARIO_RATE counts tests passed / total registered; pass_rate=75 excludes crashed
SATISFACTION_SCORE=70
```

## Substrate Verification (iter-2, verbatim)

`reports/2026-03-28-database-foundation-substrate-findings.json`:

```json
{
  "status": "fail",
  "findings": [
    {
      "severity": "error",
      "code": "substrate-guc-no-check",
      "message": "GUC entry 'app.tenant_id' has no registration_check; manifest is malformed",
      "path": "/manifest.toml",
      "line": 0
    },
    {
      "severity": "error",
      "code": "substrate-rls-no-check",
      "message": "rls_table entry 'baseline_pattern' missing rowsecurity_check or policy_count_check; manifest is malformed",
      "path": "/manifest.toml",
      "line": 0
    }
  ]
}
```

**These two findings are byte-identical to run `e5634bc6`.** The architect regenerated the manifest and produced the same malformed shape. The substrate contract (`registration_check`, `rowsecurity_check`, `policy_count_check`) is either absent from or insufficiently load-bearing in the architect prompt.

## Import Contract (iter-2, verbatim excerpt)

```
cmd: pnpm exec vitest run --config .df-vitest-resolution-check.config.ts
     ../../.dark-factory/scratch/resolution-check/…/contract-00.test.ts … contract-05.test.ts
cwd: apps/api
exit: 1

Test Files  6 failed (6)
      Tests  no tests

Error: Failed to load url ../../../apps/api/src/db/index.js (resolved id: …). Does the file exist?
Error: Failed to load url ../../../apps/api/src/db/tenant-context.js …
Error: Failed to load url ../../../apps/api/src/db/schema/helpers.js …
Error: Failed to load url ../../../apps/api/src/db/health-check.js …
Error: Failed to load url ../../../apps/api/src/db/schema/index.js …
Error: Failed to load url ../../../apps/api/src/db/tenant-context-testing.js …
```

All 6 declared entry points unresolvable because the architect wrote `.js` suffixes but the files exist as `.ts`. Same defect class as `e5634bc6`. This failed Import Contract is also why the Blind Scenarios stage had 3 crashed tests — they share the same module resolution.

Notable from gate log (verbatim): `WARNING: export contract violation: apps/api/src/db/tenant-context.ts — declared=['AppError', 'DecoratedRequest', 'INVALID_TENANT_ID', 'TENANT_REQUIRED'] actual=['tenantContext'] missing=[…]`. The implementer-side iter-2 delta fixed most but not all of the manifest-declared exports.

## Defect Classifier Abort

`reports/2026-03-28-database-foundation-defects-iter2.json`:

```json
{"iteration": 2, "schema_version": 1, "error": "count_mismatch", "defects": []}
```

Pipeline retry logic exhausted (2 attempts, verbatim from `/tmp/df-run-*.log`):

```
WARNING: classifier self-reported error: count_mismatch
WARNING: defect classifier attempt 1 failed with 'count_mismatch'; retrying
WARNING: classifier self-reported error: count_mismatch
```

`count_mismatch` is emitted by the classifier itself when `len(defects) != expected_failure_count`; here it returned `[]` (zero defects) against 15 failing scenarios. **This is a pre-existing bug documented in `memory/project_pipeline_unresolved_bugs.md`** — the same failure surfaced on run `1230234d`. No new work in this session touched the classifier.

## Notable Events (from `final/events.jsonl`)

```
SPEC_START                         02:02:35Z
COVERAGE_SPEC_VEHICLE_MISMATCH     02:17:31Z  (EC-1, EC-9 unreachable via test vehicle)
SMOKE_COMPLETED iter=1             02:41:40Z  success=true policy=advisory
ITER_COST iter=1                   turns=400  estimated_usd=84.0
ITERATION_TEMPORAL_SIGNATURE iter=1  (no prior run data — first iteration baseline)
SMOKE_COMPLETED iter=2             02:50:21Z  success=true policy=fatal
ITER_COST iter=2                   turns=200  estimated_usd=42.0
SPEC_FAILED                        02:53:20Z  reason="count_mismatch after 2 attempts"
```

`final/meta.json` (written by the broadened-catch path — **this is the P2 validation**):

```json
{
  "spec": "2026-03-28-database-foundation",
  "run_id": "e614a17612dd4993",
  "status": "aborted",
  "final_iteration": 2,
  "completed_at": "2026-04-21 19:53:20"
}
```

Per-iter archives complete: `archive/…-e614a176/{iter-1,iter-2,final,run.json}`. Compare the malformed archive from `1230234d` (which had `iter-1/iter-2/run.json` but **no `final/`**) — the P2 fix now produces a well-formed archive on this abort path.

## Validation of Session's Pipeline Changes

### P1: scenario-infra fast-fail bypass (commit `a50cd1c`)

The P1 guard never fired in this run, for two legitimate reasons:
- iter-1 had `exit_code=1` but `scenario_total=51` (non-zero) → original `scenario_total == 0` short-circuit excluded it before P1's predicate is relevant.
- iter-2 also had `scenario_total=51` → same short-circuit.

**The guard behaved correctly** (no spurious abort), and the unit-test harness now asserts the bypass would have fired if Substrate Verification or Import Contract FAIL had coincided with `scenario_total == 0`. P1's substantive value for this particular run was nil; its value is structural (telemetry path preserved for the e5634bc6-shape failure).

### P2: broadened abort catch (commits `6d4d0ec`, `d90a3f2`)

Directly validated. The classifier abort raised `HardAbort` → reached `_handle_abort` via the `(DarkFactoryError, BudgetExceeded)` catch → produced `final/meta.json` and `SPEC_FAILED` event cleanly. The prior `1230234d` run on this same failure class left `final/` missing.

## Audit log spot-check: barrier masks correct

From `audit/2026-03-28-database-foundation-e614a17612dd4993.log` (verbatim fragments):

| Agent | `apps/api/src` | `.dark-factory/scenarios` | `.dark-factory/holdouts` | `.dark-factory/reports` | `.git` | Project mount |
|-------|:---:|:---:|:---:|:---:|:---:|:---:|
| spec-validator | `:ro,size=0` | — | — | — | `:ro,size=0` | `:ro` |
| qa-holdout | `:ro,size=0` | `:ro,size=0` | **writable** | **writable** | `:ro,size=0` | `:ro` |
| test-engineer | `:ro,size=0` | **writable** | `:ro,size=0` | **writable** | `:ro,size=0` | `:ro` |
| architect | — | `:ro,size=0` | `:ro,size=0` | **writable** | `:ro,size=0` | `:ro` |
| implementer | **writable** | **writable** (readable only) | `:ro,size=0` | `:ro,size=0` | `:ro,size=0` | `:rw` |
| reviewer | — | `:ro,size=0` | `:ro,size=0` | `:ro,size=0` | `:ro,size=0` | `:ro` |
| defect-classifier | — | — | — | **writable** | `:ro,size=0` | `:ro` |

`grep -c BARRIER_PATH_ECHO_DETECTED`: **0**. No barrier violations detected.

## Server / service errors

No server-side errors; both smoke checks passed (iter-1 advisory, iter-2 fatal policy both `success=true`). `gate-telemetry.jsonl` and `{spec}-{run}.log` under `audit/` — no crashes, no OOM.

## Warnings (not causal but worth noting)

Dozens of `SOURCE_DIRS_COVERAGE` warnings on `.dark-factory/archive/…-e614a176/iter-1/…` — the architect is picking up paths *inside its own run archive* as "sources". This is a pipeline hygiene artifact, not a defect that caused this abort, but it should be filtered out of the architect's source-discovery heuristic.

## Assessment vs prior runs

| Run | Outcome | Aborted at | Pass rate high-water | Substrate | Import |
|-----|---------|-----------|----------------------|-----------|--------|
| `d8268a4a` (20260420-130705) | SHIP (via gaps) | — | n/a | PASS | PASS |
| `dc53cf0d` (20260420-172213) | SHIP (via gaps) | — | n/a | PASS | PASS |
| `1230234d` (20260420-213551) | SPEC_FAILED | iter-3 classifier `count_mismatch` | ~? | FAIL | FAIL |
| `e5634bc6` (20260421-172706) | ABORT (preflight/fast-fail) | iter-1 scenario_total=0 | 0% | FAIL | FAIL |
| **`e614a176` (this run)** | **SPEC_FAILED** | iter-2 classifier `count_mismatch` | **75%** | FAIL | FAIL |

Pattern: once architect produces a malformed substrate manifest + `.js`-suffix import contract, the implementer can still partially recover (iter-2 reached 75% pass rate) — but the **blocker is two-layer**: (1) architect repeatedly emits the same malformed manifest shape; (2) when scenarios do run, the classifier `count_mismatch` bug aborts before Oracle can decide.

## Follow-ups (ordered by blast radius)

1. **Architect template fix (cheapest) —** update `agents/architect.md` / manifest-synthesis prompt to require `registration_check`, `rowsecurity_check`, `policy_count_check` per substrate contract, and to emit Import Contract entry points with language-native extensions (`.ts`, not `.js`). This targets the root cause of three consecutive FAILs on substrate + import.
2. **Defect-classifier `count_mismatch` bug —** pre-existing, separately tracked in `memory/project_pipeline_unresolved_bugs.md`. Unblocks ship path even when pass rate is high. Classifier emitted `defects=[]` against 15 failures; likely a prompt/parsing regression under certain failure shapes.
3. **P4 (architect-output pre-iteration validation) —** now empirically indicated. Second consecutive run where architect emits identical malformed output. The P4 design (architect → validator → scenario-architect with architect-retry on failure) would catch this *before* burning an iteration on a provably-broken manifest.
4. **Archive hygiene —** filter `.dark-factory/archive/` out of the architect source-scan heuristic to eliminate the 20+ `SOURCE_DIRS_COVERAGE` warnings and the risk of an architect seeing prior-run reports.
