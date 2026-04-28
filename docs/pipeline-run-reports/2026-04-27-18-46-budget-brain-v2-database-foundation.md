# Pipeline Run Report — `2026-03-28-database-foundation` against `budget-brain-v2`

**Run started:** 2026-04-27 17:31:40 (UTC-7)  
**Run id:** `0784fe2ba5d6452f`  
**Outcome:** `SPEC_ESCALATED` after 2 implementer iterations  
**Wall clock:** ~73 minutes (4400s)  
**Total estimated cost:** ~$84 USD (2 × $42, 200 turns each)  
**Spec validation pre-flight:** PASS (95/100, LOW divergence, 0 blockers)

This is the **first end-to-end pipeline run** post-Phase-4 of the architectural restructure (B5 / B4 / B6.5 / B6 / Phase-4 ergonomics) and the first since the
`pre-architectural-restructure-2026-04-25` tag. Spec was densified in commit `5eb486b` to add `canonical_violation` / `canonical_clean` pins per §4a entry plus a `test_server` block.

---

## Outcome at a glance

| Metric | Iter 1 | Iter 2 |
|---|---:|---:|
| Pass rate | **50 %** (29 / 59) | **66 %** (35 / 64) |
| Trajectory class | — | **PLATEAU** |
| Smoke gate | PASS (advisory) | PASS (fatal) |
| Quality gate | FAIL (exit 1) | FAIL (exit 1) |
| Implementer wall | 645 s | 361 s |
| Iter cost (telemetry) | $42 | $42 |
| Files changed | architect+impl baseline | + `server.ts`, `logger.ts`, `config.ts`, `scripts/` |
| Oracle decision | NO-SHIP | NO-SHIP |

**Terminal exit message** (from `/tmp/df-run-…log`):
```
Escalation recommended: 6 SPEC_AMBIGUOUS + 0 BRITTLE out of 10 failures
Running escalation agent...
ESCALATED: 2026-03-28-database-foundation (after 2 iterations)
```

The pipeline escalated rather than running iter 3-implementer because the
stable-failure rule (`SPEC_AMBIGUOUS ≥ 50 % of remaining failures`) fired —
6 of 10 outstanding failures had been classified `SPEC_AMBIGUOUS` across the
two iterations.

---

## State file (final)

```
spec=2026-03-28-database-foundation
run_id=0784fe2ba5d6452f
spec_hash=32d0cd09257f72bc5252ad2e19fcf380929a352af926c20d30610e6746cb8a88
completed_step=2
iteration=2
server_error=
smoke_result=passed
pass_rate=66
gate_exit_code=1
last_decision=NO-SHIP
phase=oracle_complete
status=escalated
```

---

## Quality-gate cards

### Iteration 1 (preserved in `archive/20260427-173140-…/iter-1/`)

| Gate | Status | Details |
|------|--------|---------|
| Install | PASS | `cd apps/api && pnpm install` |
| **Setup** | **PASS** | `cd apps/api && npx drizzle-kit migrate` |
| Build / Lint / Type Check | PASS | exit 0 |
| Substrate Verification | SKIP | no substrate manifest required (RLS substrate primitive declared `verifiable=false`) |
| Import Contract | PASS | 4 entry points resolve at runtime |
| Self-Tests | PASS | exit 0 |
| **Scenario Lint** | **FAIL** | `[pipeline] ? shape issue(s); details in audit dir (Implementer-masked)` |
| **Blind Scenarios** | **FAIL** | exit 1 — 30 failed / 29 passed / 39 skipped (98 vitest cases) |

### Iteration 2 (current `reports/`)

| Gate | Status | Details |
|------|--------|---------|
| Install / Setup / Build / Lint / Type Check | PASS | — |
| Import Contract / Self-Tests | PASS | — |
| **Scenario Lint** | **FAIL** | (count redacted, same shape as iter 1) |
| **Blind Scenarios** | **FAIL** | 29 failed / 35 passed / 34 skipped (98 vitest cases) |

> **Important wire-truth:** `Setup` PASSed on **both** iterations — `drizzle-kit migrate` exited 0 against a real Postgres reachable from the gate. The Postgres substrate is therefore available; the failures below are all on the implementation side, not on container infrastructure. (My initial diagnosis during the run that this was a substrate gap was wrong; the 29 / 59 split itself disproves it — half the scenarios talk to the DB successfully.)

---

## Visible Blind-Scenario failure clusters

Two failure shapes dominated both iterations:

### Cluster A: migration journal never created (AC-24 / AC-4 / AC-5)

```
FAIL  schema-helpers.test.ts > [AC-24] baseline migration applies without error and is recorded
PostgresError: relation "__drizzle_migrations" does not exist
 ❯ sql postgres:106:11
```

Mechanism: `drizzle-kit migrate` runs in Setup but **applies zero migration
files** (the implementer didn’t author any `.sql` migrations, only the
`drizzle.config.ts` + `apps/api/scripts/`). With no migrations applied,
drizzle never creates its journal table, and AC-24’s assertion against
`__drizzle_migrations` fails on first SELECT.

### Cluster B: health-probe polarity bug (AC-31, AC-32)

```
FAIL  health-check.test.ts > [AC-31] returns ok when database is reachable
expected { status: 'error' } to match object { status: 'ok' }

FAIL  health-check.test.ts > [AC-32] returns error when database is unreachable
expected { status: 'ok' }    to match object { status: 'error' }
```

Both polarities fail in iter 2, which means the implementation’s
`healthCheck()` returns a **fixed-shape value** that ignores the live state
of the connection. (Iter 1 was AC-31-only; iter 2 introduced enough
plumbing to flip AC-31 to passing and break AC-32.)

### Test-runner counts (iter 2 quality-gate.md)

```
Test Files  12 failed | 1 passed (13)
     Tests  29 failed | 35 passed | 34 skipped (98)
  Duration  10.97 s
```

(Vitest cases include implementer self-tests + skipped-by-tag, so the
relevant figure is the Oracle’s scenario_total = 64 in iter 2 vs 59 in iter 1.
The growth comes from the `coverage_regen` phase adding 13 fingerprints
after the initial validate phase.)

---

## Oracle classifications

| Section | Iter 1 | Iter 2 | Iter 2 prior-blocker resolution |
|---|---|---|---|
| FR-2 (tenant binding plugin) | IMPL_BUG | — | **Partially-Fixed** — `server.ts` modified but cascaded to SCENARIO_CRASH on imports |
| FR-3 (schema helpers, baseline migration) | IMPL_BUG | IMPL_BUG | **Still-Broken** — no schema files in diff |
| AC-4 (migration generation infra) | IMPL_BUG | IMPL_BUG | **Still-Broken** — no migration files in diff |
| AC-5 (migration application) | IMPL_BUG | IMPL_BUG | **Still-Broken** |
| AC-7 (DB_POOL_MAX) | IMPL_BUG | — | **Partially-Fixed** — config touched but scenarios crash on import |
| AC-8 (DATABASE_URL redaction) | IMPL_BUG | IMPL_BUG | **Still-Broken** — `logger.ts` changed but insufficient |
| AC-24 (baseline migration recorded) | IMPL_BUG | IMPL_BUG | (cascade of AC-4/AC-5) |
| AC-31 / AC-32 (health probe polarity) | SCENARIO_CRASH | (cascade of FR-2/AC-4) | — |
| AC-38 (drizzle-orm vs postgres-driver imports) | **SPEC_AMBIGUOUS** | **SPEC_AMBIGUOUS** | — |

Oracle iter 2 pattern analysis (verbatim):

> *“Import resolution failures introduced in iteration 2 suggest changes to module structure without proper TypeScript configuration updates. Core functionality remains unimplemented rather than fixed — migration infrastructure and tenant foundations need systematic implementation rather than configuration tweaks.”*

The iter 2 implementer went after the easy wiring fixes (`server.ts`,
`logger.ts`, `config.ts`) and **skipped the hard cluster** (migration
infrastructure + schema helpers). That, plus the import-resolution
regression those wiring edits caused, is the textbook PLATEAU shape.

`TRAJECTORY_CLASSIFIED` event payload:

```json
{
  "trajectory": "PLATEAU",
  "strategy": "Fundamentally different approach needed — same strategy is not working."
}
```

---

## Substrate verification

```json
{ "status": "skip", "findings": [] }
```

Skipped because the spec’s `rls_policy` substrate primitive is declared
with `verifiable: false` (commit `ca4842d` — recent §4a remediation).

---

## Notable events

- `BARRIER_VIOLATION role=test-engineer path=apps/api/src` (iter 1, validate phase). **False positive from the path-string detector** — the test-engineer’s scenarios legitimately import the implementation paths (`../../../apps/api/src/db/health-check.js`), and the substring matched the violation regex. The container’s `--tmpfs /project/apps/api/src:ro,size=0` mask was correctly applied (verified in audit-log spot-check below); the agent could not actually read source. Documented limitation per `CLAUDE.md`: *“the audit log detects naive/accidental barrier violations via path-string matching; it does not detect natural-language or semantic leakage.”* The flip-side is also true here — it can produce false positives on legitimate import strings.

- `COVERAGE_GAP attempt=1` → `coverage_regen` → `COVERAGE_PASS anchor_count=55`. Test-engineer’s validate phase reported missing AC coverage (FR-1, FR-2, FR-3, FR-4, AC-4, AC-5, AC-6); coverage_regen added 13 scenario fingerprints to close the gap.

- `SMOKE_COMPLETED` both iters: success, no `server_error`. Iter 1 advisory, iter 2 fatal — both passed.

- `ORACLE_METRIC_DELTA` iter 1 and iter 2: `delta=0`, `reprompted=false` — Oracle’s claimed pass-rate matched the gate’s actual pass-rate exactly both times. No reprompt was triggered.

---

## Audit-log barrier spot-check

Verified the Docker masks in the per-role argv strings (the primary
container barrier — `--disallowedTools` is defense-in-depth):

**qa-holdout (iter 1):**
- `--tmpfs /project/apps/api/src:ro,size=0` ✅ (source masked)
- `--tmpfs /project/.dark-factory/scenarios:ro,size=0` ✅ (visible tests masked)
- `--tmpfs /project/.git:ro,size=0` ✅
- `.dark-factory/holdouts:rw` mounted (write target)

**test-engineer (iter 1):**
- `--tmpfs /project/apps/api/src:ro,size=0` ✅ (source masked — same as qa-holdout)
- `--tmpfs /project/.dark-factory/holdouts:ro,size=0` ✅ (the *opposite* visible-test dir is masked)
- `--tmpfs /project/.git:ro,size=0` ✅
- `.dark-factory/scenarios:rw` mounted (write target)
- Read-only adapter contract overlay: `…/scenarios/2026-03-28-…/_contracts:ro` ✅ (B9d staging)

The cross-mask (qa-holdout sees holdouts, test-engineer sees scenarios) is what
makes the audit log’s `BARRIER_VIOLATION` for test-engineer reading
`apps/api/src` a false positive: the path was masked at the kernel level,
the detector tripped on a string substring inside an `import` statement.

---

## Escalation-agent output quality

The `escalation` role at iter 3 produced
`.dark-factory/reviews/2026-03-28-database-foundation-escalation.md` —
**but the report contains numerous fabrications:**

| Escalation report claim | Actual value |
|---|---|
| “validation scores 87/100” | 95/100 |
| “Iteration 1 … 72% pass rate” | 50 % |
| “Iteration 2 … 82% (23/28 scenarios)” | 66 % (35 / 64) |
| “Iteration 1 (Completed: 2026-04-21)” | 2026-04-27 |
| Cited acceptance criteria “AC-14, AC-16, AC-18” | These IDs do not exist in the spec |

The escalation agent appears to have hallucinated a parallel narrative.
It correctly identified the SHAPE of the failure (migration infra +
tenant isolation incomplete) but invented every quantitative claim. This
is a real gap — the escalation agent is the artifact a human picks up,
and the numbers it cites are evidence-free.

**Action item:** the escalation agent prompt or its input bundle needs
hardening — it should be required to anchor every percentage, AC-id, and
date against an actual artifact (`events.jsonl`, `reports/…json`, the
spec). This run is the cleanest reproducer to date.

---

## Server / service errors

None. `SMOKE_COMPLETED` reported empty `server_error` on both iterations.

---

## Standout findings (why this run mattered)

1. **B6.5 round-trip + §4a tightening did its job** — Scenario Lint FAILed
   on every iteration (count redacted to the Implementer, but the
   gate-table line confirms the lint phase is biting). The H2-loop closure
   work is live and operational.

2. **Substrate-prep / verify hand-off works** — `Setup` PASSed both times
   with `drizzle-kit migrate`, confirming the gate has Postgres reachable
   and the substrate-owner emit produced what the gate runner needed
   even though `verifiable=false` made verification skip.

3. **The path-string `BARRIER_VIOLATION` is noisy** — for any spec where
   scenarios legitimately import implementation paths (i.e. all of them),
   the detector will trip on the import string. Worth either narrowing
   the detector (require an actual `read()` syscall, not just a path
   substring), or adding a known-noise allowlist for in-import-statement
   path tokens.

4. **The escalation agent hallucinates numbers**, see above. This is
   the most actionable framework finding from the run.

5. **PLATEAU detection + stable-failure escalation worked correctly** —
   the pipeline correctly identified that iter 3 wouldn’t fix it and
   escalated rather than burning $42 + 6 minutes on another implementer
   pass against the same pattern. The triggering rule
   (`SPEC_AMBIGUOUS ≥ 50 % of failures`) fired on 6/10.

6. **`archive/` gitignore gap caught and fixed pre-run** — bb-v2’s
   `.dark-factory/.gitignore` was missing `archive/`, which was
   accumulating untracked dirs. Fixed in bb-v2 commit `7a76475` and the
   framework template in dark-factory commit `72e53ba` (this commit’s
   parent). 24 prior archive snapshots were orphaned in `git status`
   before the fix.

---

## Artifacts

- Spec (densified, commits `fc45778` → `5eb486b`):  
  `bb-v2:.dark-factory/specs/2026-03-28-database-foundation.md`
- Architect manifest:  
  `bb-v2:.dark-factory/architecture/2026-03-28-database-foundation-manifest.yaml`
- Per-iter snapshots:  
  `bb-v2:.dark-factory/archive/20260427-173140-2026-03-28-database-foundation-0784fe2b/{iter-1,iter-2,final}/`
- Oracle feedback:  
  `bb-v2:.dark-factory/reviews/2026-03-28-database-foundation-feedback-iter{1,2}.md`
- Escalation (with caveats above):  
  `bb-v2:.dark-factory/reviews/2026-03-28-database-foundation-escalation.md`
- Live log:  
  `/tmp/df-run-2026-03-28-database-foundation.log`
- Events:  
  `bb-v2:.dark-factory/events.jsonl` (115 events)
