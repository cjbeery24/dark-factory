# Pipeline Run Report: budget-brain-v2 / 2026-03-28-database-foundation

**Date:** 2026-04-10 03:03 UTC
**Repo:** budget-brain-v2 (pnpm monorepo, Node/TypeScript)
**Spec:** 2026-03-28-database-foundation
**Dark Factory version:** Phase 7 (commit ce5a656) — skip observability, infra-failure detection, migration prompt
**Previous best:** 37% pass rate (Phase 6, run_id 73d7977162de4d6c, escalated after 2 iterations — 18/26 tests silently skipped)

## Purpose

Verify Phase 7 fixes address the 37% plateau caused by silent test skips:
1. Architect prompt now instructs chaining migration into `commands.install`
2. Skip count parsing surfaces SCENARIO_SKIPPED and SCENARIO_REGISTERED
3. Skip threshold >50% flags infrastructure failure with clear messaging
4. Infra-failure classification routes to ENV_ISSUE escalation

**Environment reset:** Full clean (procedure below).

## Run 1

**Run ID:** `67e3035dcb1f42ac`
**Status:** INTERRUPTED after architect (step 1 of pipeline)
**Iterations completed:** 0 (never reached implementation/gate)

### Timeline

| Step | Wall time | Result |
|------|-----------|--------|
| spec-validator | 72s | PASS |
| architect | 165s | Completed — manifest produced |
| (pipeline died) | — | Process interrupted before scenario/implement/gate |

### Architect Manifest Analysis

The architect produced `2026-03-28-database-foundation-manifest.yaml` declaring:
- `runtime.services.postgres` (type: postgresql, version: 16, test_only: true)
- Drizzle ORM dependencies (`drizzle-orm`, `drizzle-kit`, `postgres`)
- Database scripts (`db:generate`, `db:migrate`, `db:studio`)

**Missing:** No `commands.install` amendment chaining migration into install. Despite Phase 7's
architect prompt addition ("DATABASE SCHEMA SETUP (mandatory)" section with explicit Drizzle example),
the architect did not include `npx drizzle-kit push` or equivalent in `commands.install`.

This confirms the finding from the previous run: **prompt guidance is insufficient** to ensure
the architect produces the correct `commands.install` value. The architect declares the database
service correctly but does not connect the dots to chain migration setup.

### Key Finding: Prompt Guidance Insufficient (confirmed)

Two consecutive runs with explicit architect prompt guidance about database schema setup both
failed to produce `commands.install` with migration commands. The architect:
- Correctly declares postgres service
- Correctly adds Drizzle dependencies and scripts
- Does NOT chain migration into `commands.install`

This validates the need for a **structural/mechanical solution** rather than prompt tweaks.

### Phase 7 Observability — Not Tested

Skip parsing (Fix 1), skip threshold (Fix 2), and infra-failure classification (Fix 4) were
deployed but never exercised — the pipeline died before reaching the quality gate. These fixes
remain unvalidated in production.

### BUGs / Action Items

| ID | Description | Severity | Resolution |
|----|-------------|----------|------------|
| BUG-1 | Pipeline interrupted after architect — no error record in failures/ | P2 | Investigate: process crash, OOM, or context exhaustion? |
| BUG-2 | Architect ignores migration prompt guidance (2nd consecutive run) | P1 | Structural fix needed — prompt approach abandoned |

### Recommended Next Steps

1. **Implement structural migration fix** — one of:
   - Skip-rate hard gate (cheap, immediate signal)
   - `commands.setup` dedicated env contract field (real fix)
   - Manifest validation gate (defense-in-depth)
2. **Re-run pipeline** after structural fix to validate Phase 7 observability + new fix
3. **Investigate pipeline interruption** — may be resource/process issue unrelated to code changes
