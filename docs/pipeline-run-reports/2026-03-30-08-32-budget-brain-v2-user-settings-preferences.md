# Pipeline Run Report: budget-brain-v2 / user-settings-preferences

**Date:** 2026-03-30
**Spec:** `2026-03-28-user-settings-preferences.md`
**Target repo:** budget-brain-v2
**Pipeline version:** Post phases 1, 2, 3, 4 from path-to-first-ship.md
**Config:** multi_run_n=3, multi_run_k=2, autonomy=3, enterprise=true

---

## Context

This is the second spec to be run through the pipeline on budget-brain-v2. The first spec (monorepo-tooling) shipped after significant pipeline improvements across phases 1-4. This run tests whether those improvements generalize to a more complex spec (API + DB + frontend) vs. the build-tooling-only monorepo-tooling spec.

Key differences from monorepo-tooling:
- 3 FRs (schema/migration, API endpoints, frontend UI) vs. monorepo-tooling's build tooling focus
- Requires database (Drizzle ORM, RLS policies)
- Requires frontend (React 19, Tailwind v4)
- Has cross-feature dependencies (F-2 auth/multi-tenancy)
- 20 scenario intents declared in spec

---

## Run 1

**Start time:** 2026-03-30 08:32
**End time:** 2026-03-30 08:46
**Outcome:** BLOCKED — SA validation failure (systematic)
**Pipeline stage reached:** Step 2 (Scenario Generation) — SA generated scenarios 3 times, all failed validation

### Pre-run state
- budget-brain-v2 repo: clean (no modified/untracked files)
- No existing .dark-factory artifacts for this spec
- Docker images: rebuilt fresh (all cached layers)

### Timeline
1. **Spec pre-check:** PASS (2 ambiguity warnings for "etc." — non-blocking)
2. **Step 1 — Spec Validation:** PASS (completed in ~5 min)
3. **Step 2 — Scenario Generation:** SA generated 4 scenario files across 3 attempts. All 3 attempts failed the `typescript_compilation` validation check. Pipeline interrupted.

### Scenarios Generated (before validation failure)
- `01-database-schema.test.ts` — FR-1 schema/migration verification (file-based, regex matching)
- `02-api-endpoints.test.ts` — FR-2 API endpoint verification
- `03-settings-ui.test.ts` — FR-3 settings page UI verification
- `04-cross-cutting.test.ts` — cross-cutting concerns (logging, error envelope)

Scenario quality looked reasonable: proper vitest imports, file-based assertions using `readFileSync`, regex matching for schema structure. All other validation checks passed.

### Blocking Issue: tsc check fails in pnpm monorepo

**Symptom:** `typescript_compilation` check hard-fails with 3 errors:
```
error TS2688: Cannot find type definition file for 'node'.
error TS2688: Cannot find type definition file for 'vitest/globals'.
tsconfig.check.json(1,74): error TS5095: Option 'bundler' can only be used when 'module' is set to 'preserve' or to 'es2015' or later.
```

**Root cause:** `darkfactory/scenarios/validator.py::_check_typescript_compilation()` creates a `tsconfig.check.json` with:
```json
{
  "compilerOptions": {
    "noEmit": true,
    "strict": true,
    "moduleResolution": "bundler",
    "types": ["vitest/globals", "node"],
    "skipLibCheck": true
  }
}
```

Three problems:
1. **Missing `module` option:** `moduleResolution: "bundler"` requires `module` to be `"preserve"` or `"es2015"`+. Without it, tsc errors.
2. **Type definitions not available:** In pnpm monorepos, `@types/node` and `vitest` types are installed per-workspace (only in `apps/api/node_modules/`), not hoisted to root `node_modules/`. The tsc running from `.dark-factory/scenarios/` traverses up to root but finds nothing.
3. **State dependency:** This is a **regression** from monorepo-tooling shipping successfully. Before monorepo-tooling, `tsc` wasn't at root `node_modules/.bin/` so the check **gracefully skipped** (`"tsc not available (skipped)"`). After monorepo-tooling set up the pnpm workspace, `tsc` is now available via `npx` from root, but the type resolution environment isn't set up for the scenarios directory.

**Evidence:**
- monorepo-tooling scenario validation: `typescript_compilation | PASS | tsc not available (skipped)`
- user-settings scenario validation: `typescript_compilation | FAIL | tsc --noEmit failed`
- `@types/node` exists at `apps/api/node_modules/@types/node` but NOT at root `node_modules/@types/node`
- `tsc` binary exists at `node_modules/.bin/tsc` (available to npx)

**Why this is systematic:** The SA cannot fix this. The SA generates valid TypeScript scenarios. The validation infrastructure creates a broken tsconfig. Every retry produces the same validation failure.

### Other Validation Results (all PASS)
| Check | Status |
|-------|--------|
| file_count | PASS (4 files, min 3) |
| assertions | PASS |
| trivial | PASS |
| empty_bodies | PASS |
| assertion_upper_bound | PASS |
| no_source_reads | PASS |
| no_indexof_ordering | PASS |
| intent_alignment | PASS (skipped — no intents section parsed) |
| no_hardcoded_paths | PASS |

### Warnings
- `env_dependent`: All 4 files flagged (expected for file-based scenarios)
- `regex_multiline_flag`: All 4 files flagged (use toMatch without /m)

---

## Runs 2-4: Not Attempted

The blocking issue is systematic — the tsc validation check will fail identically on every retry because the root cause is in the validator's tsconfig generation, not in the SA's output. Retrying would waste ~15 min per attempt with guaranteed identical failure.

---

## Summary & Metrics

| Metric | Run 1 | Run 2 | Run 3 | Run 4 |
|--------|-------|-------|-------|-------|
| Outcome | BLOCKED | N/A | N/A | N/A |
| Iterations | 0 | — | — | — |
| Scenario count | 4 | — | — | — |
| Pass rate (best) | N/A (never reached gate) | — | — | — |
| Duration | ~14 min | — | — | — |
| Blocking issues | tsc validation | — | — | — |

### Pipeline improvement observations

1. **Critical: tsc check is broken for pnpm monorepos post-first-spec.** The `_check_typescript_compilation` function in `validator.py` has two bugs:
   - Missing `module: "esnext"` (or similar) to pair with `moduleResolution: "bundler"`
   - Type resolution assumes flat `node_modules` — fails with pnpm's per-workspace isolation

2. **This is a "success creates new failure" pattern.** The first shipped spec (monorepo-tooling) set up the pnpm workspace which makes `tsc` discoverable, which activates a previously-dormant code path that has bugs. The tsc check was never actually tested on a real monorepo because it was always skipped.

3. **Potential fixes (for future pipeline improvement):**
   - Add `"module": "esnext"` to the generated tsconfig
   - Run tsc with `--typeRoots` pointing to the nearest `node_modules/@types` that has the needed packages
   - Or: detect pnpm monorepo and set `--paths` / `typeRoots` to include workspace `node_modules`
   - Or: make tsc check a warning instead of hard-fail (like the monorepo-tooling run effectively was)
   - Or: install `@types/node` and `vitest` in a temp location before running tsc

4. **Spec quality was good.** The spec had 3 FRs, well-defined scenario intents (20 total), clear edge cases, and free variables. The SA generated reasonable scenarios that passed all other checks.

5. **SA retry mechanism works correctly** — it retried 3 times after validation failure, but correctly gave up since the root cause is outside its control.
