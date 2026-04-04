# Pipeline Run Report: budget-brain-v2 / auth-schemas-middleware

**Date:** 2026-04-02 06:22 UTC
**Repo:** budget-brain-v2
**Spec:** 2026-03-30-auth-schemas-middleware
**Dark Factory version:** latest (main branch)

---

## Run 1

**Started:** 2026-04-02 ~06:22 UTC
**Ended:** 2026-04-02 ~06:50 UTC (~28 min)
**Status:** ESCALATED after 1 iteration

### Pre-run state
- Previous pipeline run was interrupted at iteration 2, last decision NO-SHIP
- Repo had modified tracked files and untracked implementation artifacts from prior run
- All artifacts cleaned, tracked files restored before run

### Setup
- [x] Synced agents/templates to budget-brain-v2
- [x] Rebuilt docker images (all cached, built successfully)
- [x] Reset repo state (restored tracked files, cleaned untracked, removed state/config artifacts)

### Pipeline execution
- **Iterations completed:** 1 (escalated, did not iterate further)
- **Final decision:** ESCALATED (ENV_ISSUE)
- **Pass rate progression:** iter0: 0/30 (baseline) → iter1: 29/30 static (96%), 0% behavioral
- **Total duration:** ~28 minutes

### Pipeline stages completed
1. **Spec validation:** PASS (1 ambiguity warning for "etc." — non-blocking)
2. **Architecture:** Generated architecture manifest
3. **Scenario generation:** 3 scenario files (01-auth-me-contract, 02-auth-errors, 03-auth-security)
4. **Smoke test:** All scenarios fail cleanly (healthy baseline)
5. **Implementation (iter1):** Build PASS, Lint PASS, Type Check PASS
6. **Pre-gate:** Failed attempt 1, passed attempt 2
7. **Quality gate:** 96% static pass rate, but server startup FAIL
8. **Review:** Code quality 20/20, Security 15/15, but test analysis 5/25 due to server failure
9. **Oracle:** NO-SHIP → detected ENV_ISSUE pattern → escalated

### Observations
- **Implementer produced high-quality code on first attempt:** Schemas, RLS policies, middleware structure, error handling all architecturally sound
- **Code quality and security scores were perfect** (20/20 and 15/15)
- **29/30 static scenario checks passed** — only TC-31 (env var reference check) failed
- **Server startup failure due to missing `pg` package** — drizzle-orm requires `pg` at runtime but it wasn't installed
- **Oracle correctly identified ENV_ISSUE pattern** — 3/3 failures were environment-related, not code-quality issues
- **Escalation was appropriate** — the missing dependency can't be fixed by the Implementer iterating on code logic
- **Oracle feedback validation caught stack trace leakage** — feedback contained `at packageResolve (node:internal/modules/esm/resolve:767:81)` which was correctly flagged as a barrier violation pattern

### Issues
1. **Missing `pg` dependency:** The Architect manifest listed `postgres` (>=3.4.0) but the drizzle-orm node-postgres driver requires the `pg` package. The Implementer installed `postgres` but not `pg`. This is an **Architect manifest accuracy issue**.
2. **ENV_ISSUE escalation prevents iteration:** When server startup fails, the Oracle escalates immediately rather than letting the Implementer try to fix the dependency. This is arguably correct behavior (the Implementer can't modify the lockfile inside Docker), but could be improved by having the pipeline detect missing dependencies earlier.
3. **Pre-gate needed 2 attempts:** First attempt failed — worth investigating why.
4. **Feedback barrier validation:** The oracle feedback contained a Node.js stack trace which was correctly caught as a leakage pattern. The pipeline retried and presumably sanitized it.

---

## Run 2

**Started:**
**Status:** pending

### Setup
### Pipeline execution
### Observations
### Issues

---

## Run 3

**Started:**
**Status:** pending

### Setup
### Pipeline execution
### Observations
### Issues

---

## Run 4

**Started:**
**Status:** pending

### Setup
### Pipeline execution
### Observations
### Issues

---

## Summary

| Run | Iterations | Final Decision | Pass Rate | Notes |
|-----|-----------|---------------|-----------|-------|
| 1   | 1         | ESCALATED     | 96% static, 0% behavioral | Missing `pg` dep → server startup fail → ENV_ISSUE escalation |
| 2   |           |               |           |       |
| 3   |           |               |           |       |
| 4   |           |               |           |       |
