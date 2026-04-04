# Pipeline Run Report: budget-brain-v2 / auth-schemas-middleware

- **Date:** 2026-04-02 16:07 UTC
- **Spec:** 2026-03-30-auth-schemas-middleware
- **Target repo:** budget-brain-v2
- **Dark Factory version:** latest (main branch, commit 84ec1cb)
- **Pipeline command:** `dark-factory run 2026-03-30-auth-schemas-middleware`

---

## Pre-Run Setup

### Repository Reset
- Restored tracked files via `git checkout -- .`
- Deleted all untracked files/dirs via `git clean -fd` (removed: `.dark-factory/architecture/`, `.dark-factory/failures/`, review artifacts, `src/`)
- Removed `.dark-factory/state/2026-03-30-auth-schemas-middleware.state`
- Removed `.dark-factory/scenarios/2026-03-30-auth-schemas-middleware/`
- Removed all `.dark-factory/reports/2026-03-30-auth-schemas-middleware-*` files
- No `config.sh` existed (already absent)

### Agent/Template Update
- Copied latest `agents/` and `templates/` from dark-factory to budget-brain-v2/.dark-factory/

### Docker Image Rebuild
- Ran `dark-factory build node`
- All layers cached — no rebuild needed
- Images: dark-factory-proxy, dark-factory-base (node:22-alpine, claude-code@2.1.63, pnpm@9.15.9), dark-factory-node, dark-factory-playwright, dark-factory-playwright-node

---

## Run 1

### Timeline
| Phase | Start (UTC) | End (UTC) | Duration | Exit Code |
|-------|-------------|-----------|----------|-----------|
| Spec Pre-check | ~16:09 | ~16:09 | <1m | 0 (warning) |
| Spec Validation | ~16:09 | ~16:13 | ~4m | 1 (FAIL) |
| Architecture | — | — | — | skipped |
| Scenario Generation | — | — | — | skipped |
| Implementation | — | — | — | skipped |
| Quality Gate | — | — | — | skipped |
| Review | — | — | — | skipped |
| Oracle | — | — | — | skipped |

### Observations

1. **Spec pre-check** passed with 1 ambiguity warning: `"etc."` on line 21 (in the problem statement: "settings, transactions, budgets, etc."). Pipeline correctly proceeded despite the warning.

2. **Spec validation** failed with `verdict=FAIL, score=30, divergence=HIGH, blockers=3`.

3. The validator identified 3 critical blockers:
   - **Dependency chain mismatch**: Spec declares `depends-on: [F-1a]` but requires infrastructure from F-1b (API Bootstrap) and F-1c (Database Foundation) — specifically Fastify server, Drizzle ORM, and PostgreSQL connection.
   - **Missing infrastructure**: No Fastify server, database connection, or ORM setup exists in the codebase. Only a minimal placeholder exists at `apps/api/src/index.ts`.
   - **Missing npm packages**: `drizzle-orm`, `jose`, `fastify`, `postgres`, `zod` not installed.

4. The validator acknowledged the spec's **technical design quality is excellent** — good security architecture (RLS, JWT verification, tenant isolation), comprehensive test contracts, well-defined interface contracts. The low score reflects infrastructure readiness, not spec quality.

5. Pipeline state file shows `status=interrupted, iteration=1, completed_step=0` — clean abort after validation failure.

6. Docker containers were properly cleaned up after the run.

### Issues Found

#### Issue 1: Validator conflates spec quality with implementation readiness (PIPELINE CONCERN)
- **Severity:** Medium
- **Description:** The validator gave a score of 30/100 and divergence=HIGH, but admits the spec itself has "excellent technical design." The low score and FAIL verdict are driven by the codebase not having prerequisite infrastructure — not by spec ambiguity or incompleteness.
- **Expected behavior:** The validator should distinguish between:
  - (a) Spec quality issues (ambiguity, missing sections, untestable criteria) → REVISE
  - (b) Dependency/infrastructure readiness issues → separate signal, perhaps a WARNING or a different verdict category
- **Impact:** A high-quality spec that correctly declares its dependencies should not score 30/100. The validator is overstepping its role by evaluating implementation feasibility rather than spec completeness.
- **Recommendation:** Consider either (a) teaching the validator to focus on spec quality and flag dependency issues as warnings not blockers, or (b) adding a separate "dependency readiness check" step before validation that verifies prerequisite specs have been implemented.

#### Issue 2: Spec metadata `depends-on` is genuinely incomplete (SPEC ISSUE)
- **Severity:** High (for the spec, not the pipeline)
- **Description:** The spec declares `depends-on: [F-1a]` but should be `depends-on: [F-1a, F-1b, F-1c]`. The spec explicitly references Fastify hooks, Drizzle migrations, and PostgreSQL RLS — all from F-1b/F-1c.
- **Impact:** Even if the validator behavior were changed, this spec cannot be implemented until F-1b and F-1c are built. The dependency metadata needs correction.

#### Issue 3: No dependency resolution in pipeline (PIPELINE GAP)
- **Severity:** Low
- **Description:** The pipeline doesn't check whether `depends-on` specs have been successfully shipped before running. It relies entirely on the spec validator to catch this. A pre-flight dependency check could catch this earlier and with a clearer error message.

### Outcome

**FAILED** — Pipeline aborted at spec validation. No implementation attempted.

---

## Summary & Metrics

| Metric | Value |
|--------|-------|
| Total iterations | 0 (never reached implementation) |
| Final outcome | FAIL at spec validation |
| Total wall-clock time | ~4 minutes |
| Blocking issues | 1 (spec depends on unimplemented infrastructure) |
| Pipeline bugs found | 0 (behavior is defensible, but see Issue 1 for improvement opportunity) |
| Pipeline improvement opportunities | 2 (validator role clarity, dependency pre-flight check) |

## Action Items for Pipeline Improvement

1. **Consider separating spec quality scoring from implementation readiness assessment** — The validator should score based on spec completeness/clarity, with a separate readiness check for dependency fulfillment.
2. **Add pre-flight dependency check** — Before running validation, verify that all `depends-on` specs have `status=shipped` in their state files. Fail fast with a clear message.
3. **Fix the spec** — Update `depends-on: [F-1a]` to `depends-on: [F-1a, F-1b, F-1c]` in the auth-schemas-middleware spec. Then implement F-1b and F-1c before re-running.
