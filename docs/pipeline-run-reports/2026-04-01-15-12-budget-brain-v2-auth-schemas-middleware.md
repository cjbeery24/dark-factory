# Pipeline Run Report: budget-brain-v2 / 2026-03-30-auth-schemas-middleware

**Date:** 2026-04-01 15:12 UTC
**Repository:** budget-brain-v2
**Spec:** 2026-03-30-auth-schemas-middleware
**Goal:** Track pipeline improvement/regression metrics

---

## Run 1

**Started:** 2026-04-01 ~15:12 UTC
**Ended:** 2026-04-01 ~15:47 UTC (wall time: ~34 min / 2042s)
**Run ID:** 6fad376fcc464252
**Final Status:** ESCALATED after 3 iterations
**Total API Turns:** 1000

### Pre-run State
- Modified tracked files: apps/api/package.json, apps/api/src/index.ts, apps/api/tsconfig.tsbuildinfo, pnpm-lock.yaml
- Untracked implementation artifacts from previous runs present
- Existing pipeline state file present
- All cleaned before run

### Pipeline Timeline

| Step | Duration (approx) | Result |
|------|-------------------|--------|
| Spec pre-check | instant | PASS (1 ambiguity warning: "etc.") |
| Spec validation | ~3 min | PASS |
| Architecture manifest | ~2 min | Generated |
| Scenario generation | ~3 min | 5 scenario files + 1 tier1 contract test (30 TCs) |
| Smoke test | instant | All 30 scenarios fail cleanly (healthy baseline) |
| **Iteration 1** | ~6 min | NO-SHIP |
| **Iteration 2** | ~5 min | NO-SHIP |
| **Iteration 3** | ~5 min | NO-SHIP (early escalation: trajectory=PLATEAU) |
| Escalation agent | ~1 min | Escalation report generated |

### Iteration Details

#### Iteration 0 (Initial Quality Gate — before iteration 1 implementation feedback)

- **Pre-gate:** FAIL (attempt 1), then PASS (attempt 2)
- **Build:** FAIL — Missing `.js` extensions on imports (node16/nodenext moduleResolution), NeonQueryFunction type mismatch
- **Lint:** FAIL — `@typescript-eslint/no-explicit-any`, unused vars, `no-undef` in drizzle.config.ts
- **Type Check:** FAIL — Same issues as build
- **Smoke test:** 0/1 passed (tier1 contracts: 0/30 — files didn't exist at expected paths in initial run)
- **Blind scenarios:** 0/30 passed

#### Iteration 1

- **Pre-gate:** FAIL (attempt 1), then PASS (attempt 2)
- **Build/Lint/TypeCheck:** PASS
- **Blind Scenarios:** 30/30 PASS (100%)
- **Quality Gate:** PASS
- **Smoke test:** 0/1 (server startup failure: `drizzle-kit push` unknown command — should be `push:pg`)
- **Review score:** 82/100
- **Review prepass:** 55/60 (test=20/25, code quality=20/20, security=15/15)
- **Oracle decision:** NO-SHIP
- **NO-SHIP reason:** Server startup failure prevents HTTP scenario validation; critical tenant isolation gaps (JWT org_id not validated against user tenantId; RLS context never set)

#### Iteration 2

- **Pre-gate:** PASS (attempt 1)
- **Build/Lint/TypeCheck:** PASS
- **Blind Scenarios:** 30/30 PASS (100%)
- **Quality Gate:** PASS
- **Oracle decision:** NO-SHIP
- **NO-SHIP reason:** Same server startup failure persists; security vulnerabilities from iter 1 feedback unaddressed

#### Iteration 3

- **Pre-gate:** FAIL (attempt 1), PASS (attempt 2)
- **Build/Lint/TypeCheck:** PASS
- **Blind Scenarios:** 30/30 PASS (100%)
- **Quality Gate:** PASS
- **Trajectory:** PLATEAU (early escalation triggered)
- **Oracle feedback validation:** Failed 1/3 attempts due to leakage pattern (`\b\d+%\s*(?:pass|fail)`)

### Key Observations

#### Pipeline Behavior (Positive)
1. **Spec pre-check** correctly flagged ambiguous "etc." term
2. **Smoke test** correctly identified a healthy baseline (all fail cleanly before implementation)
3. **Quality gate** correctly detected build/lint/typecheck failures in iter 0 and passed them in iter 1+
4. **Blind scenarios** worked correctly — 30/30 pass from iteration 1 onward
5. **Early escalation** triggered correctly on PLATEAU trajectory at iter 3
6. **Barrier enforcement** — oracle feedback leakage detection caught `100% pass` pattern

#### Pipeline Issues / Improvement Areas
1. **Implementer cannot fix config.toml:** The server startup failure (`drizzle-kit push` vs `push:pg`) is in `.dark-factory/config.toml`, which the Implementer cannot modify (it's masked or read-only). This means the Implementer is structurally blocked from fixing this issue. The Oracle correctly identifies it but the feedback loop can't resolve it — **this is a systemic pipeline gap**.
2. **Security feedback not actioned:** The Implementer received detailed security feedback (tenant validation missing, RLS context not set) in iter 1 but identical issues persisted through iter 2 and 3. This suggests either:
   - The feedback format/structure isn't being effectively consumed by the Implementer agent
   - The Implementer prioritizes functional correctness over security fixes
   - The feedback is too long/detailed for effective agent consumption
3. **Reports not versioned per iteration:** Quality gate summary, smoke test, and review prepass files are overwritten each iteration rather than having per-iteration copies. Only `test-results-iterN.json` and `category-iterN.env` are versioned. This makes post-mortem analysis harder.
4. **Test results JSON format issue:** `test-results-iter{0,1,2,3}.json` could not be parsed by standard JSON tools (custom format with per-line JSON objects without proper array wrapping)
5. **Pre-gate flakiness:** Pre-gate failed on first attempt in 2/3 iterations, then passed on second attempt. This suggests either non-deterministic behavior or a timing issue.
6. **Oracle leakage detection:** Caught `100% pass` patterns in feedback — good, but the retry mechanism (3 attempts) adds latency.

#### Implementation Quality
- Iteration 0: Build fails entirely (wrong import extensions, type mismatches)
- Iterations 1-3: Build/lint/typecheck all pass, 100% blind scenario pass rate
- Reviewer score: 82/100 (strong code quality, blocked by security and deployment issues)
- Security: Two critical HIGH-severity tenant isolation bugs persisted across all 3 iterations

### Metrics

| Metric | Value |
|--------|-------|
| Wall time | 2042s (~34 min) |
| Total API turns | 1000 |
| Iterations completed | 3 |
| Final blind scenario pass rate | 100% (30/30) |
| Pre-gate pass rate | 100% (after retries) |
| Quality gate pass rate | 100% (iter 1-3) |
| Server startup success | 0% (all iterations) |
| Review score | 82/100 |
| Security issues resolved | 0/2 |
| Outcome | ESCALATED |

### Root Cause of Escalation

**Primary:** `server_setup_cmd` in config.toml uses `drizzle-kit push` but the installed drizzle-kit version requires `drizzle-kit push:pg`. This is a config issue outside the Implementer's control (config.toml is not in the Implementer's writable scope). Even if the server could start, two critical security vulnerabilities (tenant validation bypass and non-functional RLS) were never fixed despite explicit feedback.

**Secondary:** Implementer agent does not effectively incorporate security-focused review feedback across iterations. The same HIGH-severity issues (tenant validation missing, RLS context not set) were flagged in every review but never addressed.

### Actionable Insights for Pipeline Improvement

1. **Config validation step:** Add a pre-implementation step that validates `server_setup_cmd` actually works in a test container before entering the iteration loop. This would have caught the `drizzle-kit push` issue before wasting 3 iterations.
2. **Feedback prioritization:** Consider structuring Oracle feedback with explicit priority levels and BLOCKING tags that the Implementer agent must address before re-submission.
3. **Per-iteration report versioning:** Version all report files per iteration (not just test-results and category), enabling better post-mortem analysis.
4. **Server startup pre-check in iteration loop:** Before running quality gate, verify the server actually starts. If it can't start, fail fast and provide targeted feedback rather than running a full gate cycle.
5. **Security feedback tracking:** Add a mechanism to track whether specific security findings from previous iterations were addressed in the current iteration, and escalate earlier if critical findings are repeatedly ignored.
