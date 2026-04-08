# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Start:** 2026-04-07 23:57 UTC
**End:** 2026-04-08 00:37 UTC (~40 min)
**Outcome:** ESCALATED after 2 iterations (plateau)
**Target:** ~/sites/budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap

## Context

First run after bug fixes from the 2026-04-07 pipeline-resilience rewrite:
- **Bug #1** (4db9c58): depends-on ID-form unresolvable deps warn-and-continue
- **Bug #2** (9263923 + scenario-architect.md): retry-2 coverage regen narrows `@untestable` escape hatch, default-testable stance
- **Bug #3** (4391901): architect.md CWD rule extended to all six `commands.*` fields; forbids recursive flags on monorepos

## Run 1

### Phase timeline

| Phase | Start | End | Duration |
|---|---|---|---|
| spec-validator | 23:58:51 | 00:02:22 | 3:31 |
| architect | 00:02:23 | 00:05:22 | 2:59 |
| scenario-architect (generate) | 00:05:23 | 00:07:30 | 2:07 |
| scenario-architect (coverage_regen) | 00:07:30 | 00:11:23 | 3:53 |
| implementer iter1 | 00:11:58 | 00:17:28 | 5:30 |
| smoke iter1 | 00:17:28 | 00:17:33 | 0:05 |
| gate iter1 | — | 00:18:00 | — |
| reviewer iter1 | 00:18:00 | 00:19:52 | 1:52 |
| oracle iter1 | 00:19:52 | 00:21:09 | 1:17 |
| implementer iter2 | 00:21:09 | 00:29:59 | 8:50 |
| smoke iter2 | 00:29:59 | 00:30:04 | 0:05 |
| gate iter2 | — | 00:30:33 | — |
| reviewer iter2 | 00:30:33 | 00:35:01 | 4:28 |
| oracle iter2 | 00:35:01 | 00:36:10 | 1:09 |
| escalation | 00:36:10 | 00:37:18 | 1:08 |

### Gate results

| Iteration | pass_rate | satisfaction | scenarios | trajectory |
|---|---|---|---|---|
| iter1 | 64% | 64 | 37 (0 crashed) | — |
| iter2 | 64% | 64 | 37 (0 crashed) | PLATEAU |

iter2 test breakdown: 24 passed / 16 failed.

### Outcome

`SPEC_ESCALATED` after iter2 plateau; escalation agent produced report at
`.dark-factory/reviews/2026-03-28-api-bootstrap-escalation.md`.

Iteration cost: ~$42 estimated per iter (likely an overcount per sonnet-4).

## Bug fix verification

### Bug #1 — depends-on ID-form: FIXED, confirmed
Pipeline emitted:
```
WARNING: spec '2026-03-28-api-bootstrap' declares ID-form depends-on entries
that cannot be resolved outside a plan context: F-1a — ignoring for standalone run
```
Warn-and-continue behavior working.

### Bug #2 — coverage regen @untestable tightening: PARTIAL
Pre-check found 10 uncovered anchors: `AC-2, EC-1..EC-9`. Coverage regen
attempt 1 added 7 scenarios, **waived 3 with flimsy reasons**:
- `EC-5`: "Deep log redaction behavior not directly testable via public API"
- `EC-6`: "Non-Error thrown values require implementation knowledge"
- `EC-7`: "Health check exceptions require registering throwing health checks"

All three waivers are the exact category Bug #2's stricter prompt was
meant to reject — vague appeals to "implementation knowledge" and generic
"not testable via public API" language, with no specific runtime limitation.

**Root cause of gap:** Bug #2's tightened prompt is wired into the
**retry-2** coverage regen path in `orchestrator.py`. Coverage regen
attempt 1 gets the original, laxer prompt. The agent accepted these
waivers and coverage PASS fired before retry-2 ever executed.

**Action:** Apply the tightened prompt to attempt 1 as well, or add a
code-side rejection of vague reasons regardless of attempt number.

### Bug #3 — monorepo command scoping: FIXED, confirmed
Architect produced correctly scoped commands in manifest:
```yaml
commands:
  install: "cd apps/api && pnpm install"
  build: "cd apps/api && pnpm build"
  server_start: "cd apps/api && pnpm dev"
  test: "cd apps/api && pnpm test"
  lint: "cd apps/api && pnpm lint"
  typecheck: "cd apps/api && pnpm typecheck"
```
No recursive flags (`pnpm -r`, `pnpm --filter`). All six fields scoped to
the same workspace. This is exactly what the CWD-rule extension instructs.

## New blocker identified: Bug #6 is NOT downstream of Bug #3

**Hypothesis (pre-run):** Bug #6 (vitest-not-found, @types/node-not-found,
eslint plugins missing) would resolve once architect scoped commands into
`apps/api` CWD.

**Observation:** Commands ARE correctly scoped. Gates still fail with
identical errors:

```
# Build (iter2 quality-gate.md)
error TS2688: Cannot find type definition file for 'node'.

# Lint
Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@typescript-eslint/eslint-plugin'
imported from /project/apps/api/eslint.config.js

# Typecheck
error TS2688: Cannot find type definition file for 'node'.

# Self-Tests
sh: vitest: not found
```

**Verification:** `apps/api/node_modules/` and root `node_modules/` are
both empty after the run. The architect-declared `commands.install` was
**never invoked by the pipeline**.

**Root cause (pipeline bug):** `scripts/quality-gate.sh` runs the architect's
`build_cmd`, `lint_cmd`, `typecheck_cmd`, `test_cmd` directly, but never
runs `commands.install` first. The only place install executes is inside
smoke-test mode (lines 705-715), and even then it runs
`pnpm install --frozen-lockfile` from the gate's CWD (repo root), not via
the manifest's `commands.install` (`cd apps/api && pnpm install`).

Searched audit logs across the whole run: **zero** `pnpm install`
invocations.

**Fix (not in this run — documentation only per run rules):**
Wire `commands.install` into the quality-gate.sh entrypoint as a Gate 0
that runs before Gate 1 (Build). This is the load-bearing step for all
node projects that use lockfile-based dep management. A pnpm workspace
with `pnpm-lock.yaml` but no `node_modules` is exactly the state every
gate will hit on a fresh run.

### Scenario pass-rate despite gate failures

Counter-intuitively, 64% of HTTP scenarios passed even though build/test
failed. Inference: the implementer ran `pnpm install` itself inside its
container at least once (its env is `:rw`), the built artifacts from
`apps/api/dist/` persisted to the host mount, and the smoke-test mode
install line 707 populated enough deps for the HTTP server to boot under
`pnpm dev` for the scenario runner. But the gate container then re-ran
build/lint/test without re-installing (since smoke-mode install was in a
separate container invocation), and all deps were gone because the host
`node_modules` was never populated — only the smoke container's had been.

This explains the plateau: the implementer cannot make the gate pass
without the pipeline running install for the gate's container. No amount
of code fixes from the implementer will fix "vitest: not found" when the
gate never installs vitest.

## Escalation trigger analysis

Trajectory classifier fired `PLATEAU` on iter2:
```
trajectory=PLATEAU, strategy="Fundamentally different approach needed —
same strategy is not working."
```
This is correct — the implementer cannot progress because the blocker is
outside its sphere of control (pipeline-side install orchestration).

## Recommendations

1. **Bug #6 must be fixed in the pipeline, not the agents.** Add an
   install step to `scripts/quality-gate.sh` that executes the manifest's
   `commands.install` before Gate 1. Load the command from the
   environment contract at gate start.
2. **Bug #2 prompt tightening should apply on attempt 1**, not only
   retry-2. The 3 waivers we saw are exactly what the tightened prompt
   would have rejected, but it never ran.
3. **Re-run after Bug #6 fix.** Bugs #1 and #3 are confirmed fixed;
   this run provides clean evidence they work as designed. The remaining
   plateau is entirely attributable to Bug #6.

## Raw artifacts

- State: `.dark-factory/state/2026-03-28-api-bootstrap.state`
- Events: `.dark-factory/events.jsonl`
- Escalation: `.dark-factory/reviews/2026-03-28-api-bootstrap-escalation.md`
- Gate report: `.dark-factory/reports/2026-03-28-api-bootstrap-quality-gate.md`
- Manifest: `.dark-factory/architecture/2026-03-28-api-bootstrap-manifest.yaml`
- Iter test results: `.dark-factory/reports/2026-03-28-api-bootstrap-test-results-iter{0,1,2}.json`
