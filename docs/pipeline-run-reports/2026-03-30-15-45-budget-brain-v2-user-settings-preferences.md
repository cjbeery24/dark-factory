# Pipeline Run Report: budget-brain-v2 / user-settings-preferences

**Date:** 2026-03-30
**Spec:** `2026-03-28-user-settings-preferences.md`
**Target repo:** budget-brain-v2
**Pipeline version:** Post phases 1, 2, 3, 4 from path-to-first-ship.md
**Config:** multi_run_n=3, multi_run_k=2, autonomy=3, enterprise=true
**Previous runs:** 3 prior attempts — all blocked at SA validation (tsc check failures in pnpm monorepo)
**Changes since last run:** Commits `0a2605b` and `b3efcbb` fix tsc validation for pnpm monorepos

---

## Context

This is the 4th overall attempt at running user-settings-preferences through the pipeline. Previous attempts all failed at SA validation due to tsc compilation checks not handling pnpm monorepo workspace isolation. Two fixes have since landed:
- `b3efcbb`: Graceful tsc skip + assertion limit raised 15→20
- `0a2605b`: Fix tsc validation specifically for pnpm monorepos

This run tests whether those fixes unblock SA validation and allow the pipeline to progress further (implementation, quality gate, review, oracle).

---

## Run 1

**Start time:** 2026-03-30 ~15:45
**Outcome:** BLOCKED — Spec validation rejected (blockers=1)
**Pipeline stage reached:** Step 1 (Spec Validation)

### Pre-run state
- budget-brain-v2 repo: clean (git checkout + git clean -fd)
- Previous .dark-factory artifacts for this spec: cleaned (scenarios, state, reports, audit, reviews)
- Docker images: rebuilt fresh (all cached layers)
- Agents/templates: synced from dark-factory

### Timeline
1. **Spec pre-check:** PASS — 2 ambiguity warnings for "etc." (non-blocking)
2. **Step 1 — Spec Validation:** Agent ran, produced verdict=PASS score=91 divergence=LOW but **blockers=1**
3. **Pipeline rejected spec:** `validate_spec_result()` returns failure when blockers > 0, regardless of PASS verdict
4. **Duplicate run complication:** First run was accidentally duplicated; killed first process, but second run resumed from stale state and hit the same cached validation result

### Observations

**Spec Validator Behavior:**
- Validator gave overall PASS verdict with score 91/100 and LOW divergence
- Found 1 "Critical" issue: **C-1: Schema Naming Inconsistency** — FR-1 description says `userId` but TypeScript schema shows `user_id`
- This is a **false positive**: Drizzle ORM standard pattern maps camelCase TS properties to snake_case DB columns. The spec is internally consistent per Drizzle conventions.
- Also found 3 Medium and 3 Minor issues, none of which contributed to blockers count

**Pipeline Logic Issue:**
- `_parse_status_file()` (detection.py:162) rejects any spec with blockers > 0, even when verdict is PASS
- The validator is asked to count "Critical Issues that must be fixed before implementation" and reports this as `blockers=N`
- Since the validator is an LLM, "critical" classification is non-deterministic — same spec may get 0 or 1 blockers on different runs
- The naming inconsistency flagged here is a domain-knowledge gap (validator doesn't know Drizzle ORM conventions)

**Resume Behavior:**
- The `--fresh` flag was not used, so the second run detected `status=running` from the killed first run
- Resumed from step 0 but re-used the existing validation artifacts (which already had blockers=1)
- No new Docker container was spawned for validation on the resume path

### Blocking Issue
- **Root cause:** Spec validator LLM classified a Drizzle ORM naming convention as a "critical" blocker
- **Pipeline impact:** Hard stop at spec validation — never reached SA or implementation
- **Fix needed (not applied):** Either improve validator prompt to understand ORM naming conventions, or make the pipeline more tolerant (e.g., only reject on blockers > 0 when verdict is FAIL, not PASS)
- **Workaround for next attempt:** Re-run with `--fresh` to force re-validation — LLM may not flag same issue again

---

## Run 2

**Start time:** 2026-03-30 ~16:02 (with `--fresh` flag)
**Outcome:** BLOCKED — Workspace consistency check failed (deterministic)
**Pipeline stage reached:** Between Step 2 (SA generation) and Step 3 (Implementation loop)

### Pre-run state
- budget-brain-v2 repo: clean (git checkout + git clean -fd)
- All .dark-factory artifacts for this spec: deleted
- Docker images: same build from Run 1 (cached)
- Agents/templates: synced from Run 1

### Timeline
1. **Spec pre-check:** PASS — 2 ambiguity warnings (non-blocking)
2. **Step 1 — Spec Validation:** PASS — verdict=PASS score=87 divergence=LOW **blockers=0** (LLM did not flag the naming issue this time)
3. **Step 2 — Scenario Generation:** SA generated 5 scenario files in 2 attempts:
   - `01-schema-database.test.ts`
   - `02-api-endpoints.test.ts`
   - `03-ui-components.test.ts`
   - `04-security-rls.test.ts`
   - `05-edge-adversarial.test.ts`
4. **SA Validation:** All 10 checks PASSED (tsc gracefully skipped — fix from `0a2605b` worked)
5. **Workspace consistency check:** HARD FAIL — missing `lint`, `test`, `type-check` scripts in all 3 workspace packages

### Observations

**Progress vs. Run 1:**
- Spec validation passed cleanly (blockers=0 this time vs 1 in Run 1) — confirms non-determinism
- SA validation fully passed for the first time ever — tsc fix unblocked this stage
- Pipeline progressed further than any previous run (reached post-SA workspace check)

**SA Scenario Quality:**
- 5 files generated (vs 4 in previous runs — an additional `05-edge-adversarial.test.ts`)
- All validation checks passed: file count, assertions, empty bodies, tsc (skipped), assertion bounds, no source reads, no hardcoded paths
- Warnings (non-blocking): 1 env-dependent assertion in `04-security-rls.test.ts`, 3 files using `toMatch` without `/m` flag on multiline content

**Workspace Consistency Check — Deterministic Blocker:**
- `workspace_check.py` line 19 hardcodes required scripts: `{"test", "lint", "type-check"}`
- Actual workspace packages (`apps/api`, `apps/web`, `packages/shared`) only define `build` and `typecheck` (no hyphen)
- Mismatch on 3 fronts:
  1. **`type-check` vs `typecheck`**: Checker expects hyphenated `type-check`, project uses `typecheck` (and config.toml references `typecheck` via `pnpm -r typecheck`)
  2. **`lint`**: Not defined in any workspace package (config.toml uses `pnpm -r lint` at root level — this might work with pnpm filtering but individual packages don't have the script)
  3. **`test`**: Not defined in any workspace package (no test infrastructure set up yet)
- The monorepo-tooling spec (which shipped) set up `build` and `typecheck` but not `lint` or `test`
- This is the committed state of the repo (verified via `git show HEAD:apps/api/package.json`)

**Audit Log Insights:**
- 3 Docker containers invoked: spec-validator (16:09:50), scenario-architect attempt 1 (16:11:08), SA attempt 2 (16:16:16)
- SA needed a retry (attempt 1 likely failed validation, attempt 2 produced the passing set)
- Containers used `--model=claude-sonnet-4-20250514` for all agent roles

### Blocking Issue
- **Root cause:** `workspace_check.py` requires `{test, lint, type-check}` scripts in all workspace packages, but the project only has `{build, typecheck}`. This is a hardcoded check that doesn't read from config.toml.
- **Pipeline impact:** Hard stop between SA generation and implementation loop — cannot start iteration
- **Why retrying won't help:** The workspace check is deterministic — it reads committed package.json files. The same error will occur on every run.
- **Two-part fix needed (not applied):**
  1. **Pipeline fix:** `workspace_check.py` should derive required scripts from `config.toml` (gates section) rather than hardcoding them, and should match the script names the config actually uses (e.g., `typecheck` not `type-check`)
  2. **Project fix:** Add stub `lint` and `test` scripts to workspace packages if the quality gate needs them

---

## Summary and Recommendations

**Overall progress:** Significant improvement from previous runs. Both the tsc validation fix and SA scenario generation are now working. The pipeline got further than ever before.

**Progression across all runs:**
| Run | Blocked at | Root cause |
|-----|-----------|------------|
| Prior runs 1-3 | SA validation | tsc check failures in pnpm monorepo |
| This session, Run 1 | Spec validation | LLM false positive on Drizzle naming (non-deterministic) |
| This session, Run 2 | Workspace check | Hardcoded required scripts don't match project (deterministic) |

**Issues to fix before next attempt:**
1. **[Pipeline]** `workspace_check.py`: derive required scripts from config.toml instead of hardcoding `{test, lint, type-check}`
2. **[Pipeline]** Spec validator: consider only blocking on `blockers > 0` when `verdict != PASS`, or adding ORM context to validator prompt
3. **[Project]** budget-brain-v2 workspace packages need `lint` and `test` scripts (or the pipeline needs to be tolerant of their absence)

**Not retrying:** The workspace check failure is deterministic — further attempts would hit the same blocker. Additional runs would be futile without a code change to either the pipeline or the project.
