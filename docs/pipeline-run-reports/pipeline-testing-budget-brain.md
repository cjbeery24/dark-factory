# Pipeline Testing: budget-brain (2026-03-27)

**Status:** COMPLETE
**Spec tested:** `2026-03-24-project-scaffolding` (pnpm monorepo with TypeScript, Fastify, React, CI/CD)
**Attempts:** 4 (as requested)

## Bugs Found and Fixed

| # | Bug | Severity | Commit |
|---|-----|----------|--------|
| 1 | tmpfs overlays crash read-only containers — Docker can't create `node_modules` mountpoint when `/project` is `:ro` and the dir doesn't exist on the host | Critical | `9faa392` — Skip platform isolation overlays for read-only project mounts |
| 2 | Intermittent vitest detection failure — `pnpm exec` PATH instability in tmpfs containers causes `sh: vitest: not found` | Medium | `4765797` — Prefer direct vitest binary over pnpm exec in scenario runner detection |

### Bug 1: tmpfs on read-only mounts

`build_platform_isolation_overlays()` unconditionally added `--tmpfs /project/node_modules:...` for all node containers. When the project was mounted `:ro` (Spec Validator, Scenario Architect, Reviewer, Oracle) and `node_modules/` didn't exist on the host, Docker failed: `create mountpoint: mkdirat: read-only file system`.

**Fix:** Added `project_rw: bool = True` kwarg. Returns `[]` when False. Passed from `build_agent_args()` which already had `project_rw` at line 91.

**Files:** `darkfactory/docker/mounts.py`, `darkfactory/docker/args.py`, `darkfactory/pipeline/quality_gate.py`, `tests/unit/test_mounts.py` (6 new tests)

### Bug 2: vitest detection priority inversion

`_df_resolve_node_runner()` tried `pnpm exec vitest --version` first. This sometimes succeeded at detection time but failed at run time (`pnpm exec vitest run <dir>` → `sh: vitest: not found`) because pnpm's internal PATH construction via `/bin/sh` is non-deterministic in containers with tmpfs overlays.

**Fix:** Inverted detection priority — find vitest binary on disk first (`find . -maxdepth 6 -name vitest -path "*/node_modules/.bin/vitest"`), validate with `-x` and `--version`, then fall back to `pnpm exec`. The `workspace_vitest` runner invokes the binary directly, bypassing pnpm exec entirely.

**Files:** `scripts/lib/scenarios.sh`

## Per-Attempt Results

| Attempt | Outcome | Pass Rate (iter 1→2→3) | Notes |
|---------|---------|------------------------|-------|
| 1 | Aborted | N/A | Config mismatch: `npm` commands for a `pnpm` monorepo spec. User config error. |
| 2 | Escalated | 77% → 80% → 80% | Bug 1 found and fixed. 20 stuck scenario failures. |
| 3 | Escalated | 81% → 88% → 88% | Clean run. No new bugs. |
| 4 | Escalated | 0% → ? → 88% | Bug 2 found. vitest not detected in iter 1 quality gate. |

## Pipeline Health Assessment

- **Core flow works:** validation → scenarios → implementation → quality gate → review → oracle → iteration
- **Iteration loop works:** pass rates improve across iterations (77→80, 81→88)
- **Escalation works correctly:** pipeline escalates after 3 iterations when convergence plateaus
- **Convergence plateau at ~85-88%:** remaining failures are scenario quality issues, not implementation bugs

## Scenario Quality Observations

The 15-25 stuck failures across runs were caused by:

- **Scenario bugs:** Use of non-existent Chai methods (e.g., `toEndWith`)
- **Overly prescriptive assertions:** Scenarios asserting exact build script format (`packages/shared.*build.*&&.*apps/api`) when the implementation used `pnpm --filter` syntax
- **Undefined field access:** `railway.json` `startCommand` field undefined when scenario expected it to contain specific text
- **Security over-matching:** Security scenarios flagging standard pnpm scripts and TypeScript configs as "suspicious"

These are Scenario Architect quality issues, not pipeline bugs. The failure classification system (Plan 1) would categorize these as BRITTLE and SCENARIO_CRASH.

## Review Chain

Both fixes were reviewed by:
- **Plan review:** Liotta + Neo
- **Implementation review:** Linus + Neo (Bug 1), Liotta + Neo (Bug 2)

All reviews approved. Neo caught a `-maxdepth` flag ordering issue in Bug 2 (non-POSIX placement after test predicates).
