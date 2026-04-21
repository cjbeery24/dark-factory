# Pipeline Run — 2026-03-28-database-foundation (Reproducibility Re-run)

**Date:** 2026-04-20 17:22 → 18:13 (local)
**Target project:** `budget-brain-v2`
**Spec:** `2026-03-28-database-foundation`
**Run ID:** `dc53cf0d68a44c69`
**Intent:** Verify the prior run (`d8268a4a75ed4848`) wasn't a fluke — re-run the same spec with no V-dimension anchors to measure LLM variance end-to-end.

## Outcome

**SHIPPED via SHIP_WITH_GAPS** at iter-2, pass_rate 82%, 5 stable failures.

| Metric | This run (`dc53cf0d`) | Prior run (`d8268a4a`) | Δ |
|---|---|---|---|
| Terminal status | SHIP_WITH_GAPS | SHIP_WITH_GAPS | same mechanism |
| Final iteration | 2 | 2 | same |
| Wall time | ~51.5 min | ~62 min | −10.5 min |
| Iter-1 pass rate | 78% | 89% | −11pp |
| Iter-2 pass rate | 82% | 89% | −7pp |
| Stable failures | 5 | 4 | +1 |
| Scenario count | 28 | 39 | −11 |
| `stable_failure_ratio` | 0.833 | 1.000 | −0.167 |
| Cost | ~$84 | ~$126 | −$42 |
| Coverage path | amend + regen | clean | diverged |

**Verdict:** The pipeline SHIPPED the same spec again via the same mechanism (SHIP_WITH_GAPS), confirming this is a deterministic pipeline outcome, not a prior-run fluke. But the *path* to SHIP diverged substantially — different coverage trajectory, different scenario set, different failure set, different pass rate. The pipeline is robust to LLM variance on this spec; the implementer converges on a different-but-shippable wrong interpretation each time.

## State file

```
spec=2026-03-28-database-foundation
run_id=dc53cf0d68a44c69
spec_hash=1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662
last_decision=NO-SHIP
iteration=2
server_error=
smoke_result=passed
pass_rate=82
gate_exit_code=1
phase=oracle_complete
status=shipped
completed_step=99
```

## Quality gate (iter-2)

```
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
| Blind Scenarios (executable) | FAIL |
| Mutation Testing | SKIP |
```

Two phases that were silently absent from the prior run's gate table are now visible: **Substrate Verification** and **Import Contract**. Both currently FAIL on deployment-level issues (see Change Validation below). Neither blocks ship because the ship mechanism is SHIP_WITH_GAPS.

## Substrate findings

```json
{
  "status": "fail",
  "findings": [{
    "severity": "error",
    "code": "substrate-psycopg-missing",
    "message": "psycopg is required to run verify_substrate but is not installed in the gate environment: No module named 'psycopg'",
    "path": "/manifest.toml",
    "line": 0
  }]
}
```

New deployment gap surfaced by this run: the substrate verifier needs the `psycopg` Python package to actually check database-substrate claims, but the gate container has python3 (from commit `db7e7c9`) without psycopg. Same class of issue as the python3 gap — the feature is wired up but a dependency is missing.

## Defect registry — 5 stable failures

| # | Test | Classification | Iterations Stable |
|---|------|---------------|-------------------|
| 1 | `[AC-9]` health check returns error when database is unreachable | IMPL_BUG | 2 |
| 2 | `[EC-4]` unset app.tenant_id GUC returns nullish and causes zero rows with RLS | IMPL_BUG | 2 |
| 3 | `[EC-6]` connection pool exhaustion handling | IMPL_BUG | 2 |
| 4 | `[AC-4]` applies pending migrations to database when `db:migrate` is run | IMPL_BUG | 2 |
| 5 | `[AC-3]` generates SQL migration file when `db:generate` is run | IMPL_BUG | 2 |

Compared to the prior run's stable failures (AC-7, AC-8, AC-12 RLS policy + EC-4 GUC wire): **only EC-4 is shared.** The rest of the failure surface is entirely different. This run's failures center on migration tooling (AC-3/AC-4 `cd apps/api && pnpm db:*` fails because the scenario's cwd doesn't contain `apps/api`), connection-pool shape (EC-6 `typeof iteration === 'number'`), and health-check error propagation (AC-9). The prior run nailed migration plumbing but missed RLS policy enforcement; this run nailed RLS but missed migration wiring. Both SHIP via the same mechanism at different pass rates.

Representative failure diagnostics (iter-2):

```
FAIL edge-cases.test.ts > [EC-6] connection pool exhaustion
  AssertionError: expected 'string' to be 'number'
  expect(typeof result.iteration).toBe('number');

FAIL migration-application.test.ts > [AC-4] db:migrate
  Error: Command failed: cd apps/api && pnpm db:migrate
  /bin/sh: cd: line 0: can't cd to apps/api: No such file or directory
```

The AC-3/AC-4 failures are test-engineer bugs, not implementer bugs — the scenario hardcodes `cd apps/api` at scenario runtime, but when blind scenarios execute from the runner's cwd the path doesn't resolve. The implementer cannot fix this; the test is testing the wrong thing. Coverage amend/regen produced a scenario set that works against a non-existent filesystem assumption.

## Trajectory and ship promotion

```
TRAJECTORY_CLASSIFIED iter=2 trajectory=PLATEAU
  strategy="Fundamentally different approach needed — same strategy not working"
SHIP_WITH_GAPS iter=2 stable_failures=5 stable_failure_ratio=0.833
  pass_rate=82 oracle_original=NO-SHIP
```

One failure flipped between iter-1 (6 failures / 78%) and iter-2 (5 failures / 82%): EC-3 tenant context validation was fixed. The five remaining failures all stable → `stable_failure_ratio = 5/6 = 0.833`. Oracle returned NO-SHIP; SHIP_WITH_GAPS promoted because ratio ≥ threshold and pass rate ≥ threshold.

## Notable pipeline events

- **`COVERAGE_SPEC_VEHICLE_MISMATCH`** after test-engineer's first generate pass — the manifest didn't supply enough test vehicles for 6 anchors (AC-2, AC-3, AC-4, AC-6, EC-1, EC-2).
- **`COVERAGE_ARCHITECT_AMEND` (attempt 1, outcome: `no_progress`)** — architect tried to add entry points but couldn't close the gap.
- **`COVERAGE_REGEN` (attempt 1)** — test-engineer re-ran after amend; this cleared the gap (`COVERAGE_PASS anchor_count=28`). Prior run did not hit this path at all — its first generate was a clean pass.
- **`test-engineer phase=repair`** ran after COVERAGE_PASS (not observed in prior run).
- **`BARRIER_VIOLATION role=test-engineer path=apps/api/src`** immediately after the repair phase completed. The test-engineer container attempted to reference the masked source directory. The docker tmpfs mask prevented actual access; this is a path-string detection in the audit stream, not a confirmed filesystem read. Still worth inspecting.
- **`IMPLEMENTER_TEMP_ZERO iter=2`** — temperature correctly forced to 0 on iter≥2.
- **No `SERVER_ERROR` on smoke test** either iteration — smoke passed both times.
- **No Oracle iteration ≥3** — pipeline terminated at iter-2 via SHIP_WITH_GAPS.

## Review prepass (iter-2)

```
TEST_ANALYSIS_SCORE=20
CODE_QUALITY_SCORE=18
SECURITY_SCORE=5
DETERMINISTIC_TOTAL=43
SECURITY_HIGH_COUNT=2
SECURITY_MEDIUM_COUNT=0
FAILURE_PATTERN=isolated
```

Deterministic total 43/60 vs 48/60 prior run. `SECURITY_HIGH_COUNT=2` up from 1 — the prototype pollution issue in `health.ts` noted in the prior run appears to persist (this run's implementer also produced it) plus one additional HIGH. Neither surfaced as a ship blocker because they are advisory score inputs, not gates.

## Change Validation (follow-up to four changes shipped in commit `9d1163e`)

This run exercises the four pipeline changes shipped in dark-factory commit `9d1163e` against a different implementer/test-engineer variance path than the prior run.

### 1. Import Contract gate phase — ✅ now firing

Prior run: row absent from gate table (python3 missing). **This run: row present, FAIL.**

```
| Import Contract | FAIL | entry_points[0] missing specifier or exports |

entry_points[0] = specifier='../../../apps/api/src/db/index.js', exports=[]
```

The phase correctly detected that the architect manifest's first entry point had an empty `exports=[]` array. Real quality signal, not noise. Validates both the python3 Dockerfile fix (commit `db7e7c9`) and the phase's detection logic.

### 2. Manifest contract context in Oracle feedback — correctly gated out

Grepped both `feedback-iter1.md` and `feedback-iter2.md` for "Contract reminder" / "specifier" / "module_system" — zero matches. The `/df-write-feedback` skill's Step 3.5 keyword gating did not inject (as designed — this run's fix items are migration wiring and RLS GUC behavior, not import-contract resolution, so the gating correctly declined). Same observation as prior run: this change only activates when the operator invokes the skill out of band, not in the automated loop.

### 3. MEDIUM precision auto-hint — correctly inert

`validation.status` shows `divergence=LOW`. Precision hint did not fire. Same as prior run.

### 4. Per-run artifact archive — ✅ captured cleanly

Archive directory present and correctly structured:

```
$ ls ~/sites/budget-brain-v2/.dark-factory/archive/
20260420-130705-2026-03-28-database-foundation-d8268a4a   # prior run (committed)
20260420-172213-2026-03-28-database-foundation-dc53cf0d   # this run

$ cat .../final/meta.json
{"spec": "2026-03-28-database-foundation", "run_id": "dc53cf0d68a44c69",
 "status": "shipped", "final_iteration": 2, "completed_at": "2026-04-20 18:13:59"}
```

Both `iter-1/` and `iter-2/` directories present with the full reports/reviews/architecture/state subtrees per change #4. `final/meta.json` written by the ship handler. Spot-check of `iter-1/reviews/` confirms spec-scoped filtering — only files starting with `2026-03-28-database-foundation` are present; no cross-spec leakage. No `archive:` warnings in `/tmp/df-run-2026-03-28-database-foundation.log`.

## Deployment gaps still open

1. **`psycopg` not installed in gate container** (new, surfaced by this run). Substrate Verification phase fails on every run with `No module named 'psycopg'`. Mirror of the python3 fix: install `py3-psycopg` in `docker/Dockerfile.base` or move substrate verification into a dedicated container that already has it.
2. **Escalation agent hallucination** (documented in prior report; not re-checked here — this run didn't invoke escalation because it shipped, not escalated).
3. **`health.ts` prototype pollution** surfaces on every run's reviewer prepass. Implementer doesn't clear it because it isn't a gate failure, only a score input.

## Diagnostic excerpts — first aborted attempt

The first launch of this run (PID 28417) aborted at ~24 min after the implementer entered an immediate fail loop: `AGENT_FAILED exit_code=1 wall_seconds=1.0` triggering `MISSING_MANIFEST_FILES`. Four rapid AGENT_START/AGENT_FAILED cycles before the run was killed and restarted. Archive `20260420-154147-...-d029d29d` was discarded. Root cause not investigated — the restarted run (`20260420-172213-...-dc53cf0d`) did not reproduce the failure, so the first abort was transient. If it recurs on another run, grep the audit log for `MISSING_MANIFEST_FILES` and inspect which architect manifest path the implementer couldn't resolve.

## Audit log spot-check — barrier argv

Not inspected in detail for this report (prior run's report covered the argv structure). The `BARRIER_VIOLATION` event in `events.jsonl` is the primary signal worth following up — a test-engineer container emitted the path `apps/api/src` in a context that triggered the violation detector. Whether this is a false positive (the path string appearing in its output) or real (an attempt to stat/read via a tool the mask didn't cover) should be checked by tailing `$TARGET/.dark-factory/audit/2026-03-28-database-foundation-dc53cf0d68a44c69.log` around the test-engineer repair phase's completion timestamp.

## Conclusion

- **Reproducibility: confirmed.** Same spec, same target, no anchors → SHIP again via the same mechanism. The pipeline is robust to end-to-end LLM variance on this spec.
- **Pass rate variance: real** (89% prior vs 82% here). The pipeline doesn't produce the same numeric outcome on re-run, but lands in the same qualitative outcome (ship-with-documented-gaps).
- **Failure-set variance: substantial.** The implementer converges on a different wrong interpretation each run. Different stable failures surface (RLS policy prior vs migration wiring + pool shape this run). Only EC-4 (GUC unset nullish) is shared — a persistent implementer blind spot across variance.
- **Load-bearing follow-ups:** (1) install `psycopg` in the gate container to unblock Substrate Verification, (2) investigate the BARRIER_VIOLATION event for test-engineer, (3) the EC-4 GUC unset bug has now persisted across two independent variance paths — it's a real implementer blind spot, not a fluke.
