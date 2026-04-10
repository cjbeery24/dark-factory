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

---

## Run 2 (Phase 8 — commands.setup)

**Run ID:** `70b1778f350d4e84`
**Date:** 2026-04-10 03:55 UTC
**Dark Factory version:** Phase 8 (commit 8ee85d8) — commands.setup, skip-rate hard gate, manifest validation
**Status:** CRASHED after gate (setup failure → infra fast-fail exception)

### Timeline

| Step | Wall time | Result |
|------|-----------|--------|
| spec-validator | 128s | PASS |
| architect | 297s | Completed — **commands.setup filled!** |
| qa-holdout | 356s | PASS |
| test-engineer (gen) | 262s | PASS |
| test-engineer (regen) | 235s | Coverage gap → regen → PASS (26 anchors) |
| test-engineer (repair) | 239s | PASS |
| implementer (iter 1) | 448s | PASS (12 files) |
| smoke test | 4s | PASS |
| quality gate | 4s | **FAIL (exit 2) — Setup FAIL** |
| (pipeline crashed) | — | DarkFactoryError: infra fast-fail |

### Key Finding 1: `commands.setup` Works

The architect declared `commands.setup: "cd apps/api && pnpm db:migrate"` — **first time across
4 runs** that the architect included a migration command. The dedicated `commands.setup` field
succeeded where prompt guidance failed in 3 consecutive runs.

### Key Finding 2: Migration Itself Failed

Install PASS, then Setup ran `cd apps/api && pnpm db:migrate` (which invokes `drizzle-kit migrate`).
Drizzle-kit failed with exit 1:

```
No config path provided, using default 'drizzle.config.ts'
Reading config file '/project/apps/api/drizzle.config.ts'
Using 'postgres' driver for database querying
[⣷] applying migrations... ELIFECYCLE  Command failed with exit code 1.
```

Likely cause: the implementer wrote Drizzle schema files but didn't generate migration SQL files
(`drizzle-kit generate`). The `db:migrate` script tries to apply migrations from a `migrations/`
directory that is empty. The architect should have used `setup: "cd apps/api && npx drizzle-kit push"`
(schema push) instead of `pnpm db:migrate` (migration apply).

### Key Finding 3: Pipeline Crash — Infra Fast-Fail False Positive (BUG-3)

After the gate exited 2 (setup failure), the pipeline crashed at `iteration.py:973-990`:

```python
if (
    gate_result.scenario_total == 0
    and not gate_result.sentinel_present
    and config.validate_scenarios
):
    raise DarkFactoryError("Scenario infrastructure failure: scenario files exist but 0 parsed")
```

This check was designed for scenario runner failures (format mismatch). But when `commands.setup`
fails with exit 2, the gate aborts before running any tests — `scenario_total=0` is *expected*.
The fast-fail doesn't distinguish "setup failed, never got to scenarios" from "scenarios ran but
produced 0 results."

**Fix:** Added `gate_result.exit_code != 2` guard to the fast-fail condition. When the gate
aborted due to build/setup failure, zero scenarios is expected — don't crash.

### BUGs / Action Items

| ID | Description | Severity | Resolution |
|----|-------------|----------|------------|
| BUG-3 | Infra fast-fail crashes pipeline on setup failure (scenario_total=0 is expected when gate exits 2) | P1 | **FIXED** — added exit_code != 2 guard |
| BUG-4 | Architect uses `pnpm db:migrate` (migration apply) instead of `npx drizzle-kit push` (schema push) | P2 | Prompt guidance or manifest validation could check for this |
| BUG-5 | `hard_gate_build` defaults to False — setup/install failures proceed to review/oracle unnecessarily | P3 | Consider defaulting to True or auto-enabling on exit 2 |
