# Pipeline Run Report: budget-brain-v2 / auth-schemas-middleware

**Date:** 2026-03-31 22:58 UTC
**Repo:** budget-brain-v2
**Spec:** 2026-03-30-auth-schemas-middleware
**Operator:** Caleb Beery
**Run ID:** ef752a9c82e3452e

---

## Run 1

**Started:** 2026-03-31 ~22:59 UTC
**Ended:** ~23:28 UTC (~29 minutes)
**Status:** ESCALATED (3 iterations, no ship)

### Pre-run state
- Repo had leftover artifacts from previous pipeline runs (modified files in apps/api, untracked dirs for config/db/features/types, drizzle config/migrations)
- All tracked files restored, untracked source files cleaned, runtime artifacts removed
- Config: node language, pnpm build system, postgres service, enterprise enabled, autonomy level 3
- Docker images rebuilt (all cached, no changes)

### Timeline
| Time (UTC) | Event |
|------------|-------|
| 22:59 | Spec validator started |
| 23:01 | Scenario architect started (spec passed with 1 ambiguity warning: "etc.") |
| 23:04 | Implementer (iter 0) started — scenarios generated: 4 MD files + 1 executable vitest file (00-tier1-contracts.test.ts with 30 tests) |
| ~23:04 | Smoke test ran — **0/30 executable tests passed** (all file existence checks failed) |
| ~23:04 | Quality gate: Build SKIP, scenarios 0/30 |
| 23:04 | Implementer (iter 1) started with feedback |
| ~23:11 | Quality gate iter 1: Build PASS, Lint PASS, TypeCheck PASS, Blind Scenarios PASS (30/30) |
| ~23:14 | Review prepass: 35/60 (test analysis 0/25, code quality 20/20, security 15/15) |
| ~23:14 | Review complete, Oracle: **NO-SHIP** |
| ~23:15 | Implementer (iter 2) started |
| ~23:18 | Quality gate iter 2: Build PASS, Lint PASS, TypeCheck PASS, Blind Scenarios PASS (30/30) |
| ~23:18 | Oracle: **NO-SHIP** |
| ~23:19 | Implementer (iter 3) started |
| ~23:24 | Quality gate iter 3: Build PASS, Lint PASS, TypeCheck PASS, Blind Scenarios PASS (30/30) |
| ~23:26 | Oracle: **NO-SHIP** |
| ~23:28 | Escalation agent ran, pipeline terminated |

### Observations

#### Scenario Generation
- Generated 4 declarative HTTP scenario files (01-contract, 02-error-conditions, 03-security, 04-edge-cases)
- Generated 1 executable vitest file (00-tier1-contracts.test.ts) with 30 static file-reading tests
- All scenarios follow consistent format: `## S-NN:` with Intent, Request, Expect sections
- The declarative HTTP scenarios (01-04) could never execute because server startup was broken
- Only the vitest file (00-tier1) was used for executable testing

#### Smoke Test Failure (Iter 0)
- All 30 vitest tests failed — they check `existsSync()` and `readFileSync()` for implementation files
- **Root cause unclear**: the files existed on the host but the smoke test ran before/during implementation

#### Quality Gate Passed But Pipeline Reported 0% Pass Rate
- **Critical metric mismatch**: Quality gate summary says "Blind Scenarios (executable) | PASS" with 30/30 tests passing
- But the pipeline output consistently reports "Quality gate: 0% pass rate" across all iterations
- This 0% appears to come from the HTTP scenario execution (which requires a running server) rather than the vitest pass rate
- The server never started due to the `server_setup_cmd` bug (see below)

#### Server Startup Bug (Blocking)
- `server_setup_cmd` fails with: `exit 126: /bin/bash: /bin/bash: cannot execute binary file`
- This is a **double-bash invocation** — the quality gate script wraps the command with `/bin/bash -c "..."` but something in the execution chain already prefixes it with `/bin/bash`
- Exit code 126 = command found but not executable (bash binary treated as a bash script)
- This prevented ALL HTTP-based scenarios from executing across all 3 iterations
- **This is a pipeline infrastructure bug, not an implementation issue**

#### Oracle Decision Pattern
- Oracle received the review which showed 0/25 test results (from iter 0 data) despite quality gate passing
- The review flagged: SQL injection vulnerability, missing RLS on tenants table, missing indexes, missing db:migrate script
- Oracle consistently said NO-SHIP, primarily because of the "0% pass rate" metric and the SQL injection finding
- The implementer was given feedback about server startup but couldn't fix it (it's a pipeline issue, not a code issue)

#### Review Prepass Bug
- Review prepass consistently scored test analysis as 0/25 with "0% pass rate"
- This appears to read from the smoke test (iter 0) results rather than the current iteration's results
- This inflated the severity of the NO-SHIP decision — the actual executable tests were passing 100%

### Result
**ESCALATED** after 3 iterations. Never shipped.

**Root causes:**
1. `server_setup_cmd` double-bash wrapping bug prevents HTTP scenario execution (pipeline bug)
2. Pipeline reports "0% pass rate" despite quality gate passing 30/30 executable tests (metric mismatch)
3. Review prepass reads stale iter0 test data instead of current iteration data (bug)
4. Oracle uses the incorrect 0% metric to justify NO-SHIP despite quality gate PASS

---

## Runs 2-4

Not attempted. Stopped after Run 1 per operator decision — the blocking issues are pipeline infrastructure bugs that won't be resolved by re-running.

---

## Summary

### Metrics
| Metric | Run 1 |
|--------|-------|
| Pipeline completed? | Yes (escalated) |
| Shipped? | No |
| Iterations used | 3/3 |
| Executable scenario pass rate | 100% (30/30) from iter 1 onward |
| HTTP scenario pass rate | 0% (server never started) |
| Pipeline-reported pass rate | 0% (incorrect) |
| Total duration | ~29 minutes |
| Failure point | Oracle NO-SHIP (based on flawed metrics) |

### Key Findings

#### Pipeline Bugs Found

1. **`server_setup_cmd` double-bash wrapping (CRITICAL)**: The quality gate script wraps the setup command in a way that produces `/bin/bash: /bin/bash: cannot execute binary file`. This completely blocks all HTTP-based scenario testing. Any spec that defines a `server_setup_cmd` will hit this.

2. **Pipeline reports 0% despite quality gate PASS (HIGH)**: The pipeline output says "Quality gate: 0% pass rate" but the quality gate summary says PASS with 30/30 tests passing. The pipeline is reading the HTTP scenario rate (0% because server won't start) while the gate properly reports executable test success. This metric mismatch causes the Oracle to receive contradictory signals.

3. **Review prepass reads stale test data (MEDIUM)**: The review prepass scores test analysis as 0/25 using the smoke test (iter 0) results rather than the current iteration's passing results. This compounds the incorrect 0% signal sent to the Oracle.

4. **Smoke test uses file existence checks before implementation exists (LOW)**: The iter 0 smoke test runs the vitest scenarios which check for implementation files that haven't been created yet. This is expected behavior (the smoke test is supposed to fail on iter 0) but the stale results persist into later iterations via the review prepass.

5. **SOURCE_DIRS masking incomplete for scenario architect**: The scenario architect container masks `--tmpfs /project/src:ro,size=0` but the implementation lives in `apps/api/src/`. The `src/` mask doesn't cover the actual source location. However, since scenarios are generated before implementation, this is a theoretical concern only for this run.

#### Implementation Quality
- The implementation itself appeared solid: clean architecture, proper TypeScript, correct schema design
- Security review flagged a real SQL injection in `SET LOCAL app.tenant_id = '${tenantId}'`
- Code quality scored 20/20, security 15/15 in the review

### Regressions vs Improvements
- **Regression**: The `server_setup_cmd` feature (recently added) has a double-bash wrapping bug that makes it non-functional
- **Regression**: Pipeline-reported pass rate disagrees with quality gate PASS status
- **Stable**: Scenario generation produced well-structured, consistent scenarios
- **Stable**: Docker container isolation and barrier enforcement worked correctly
- **Stable**: Build/lint/typecheck gates all functioned properly
