# Pipeline Run Report — budget-brain-v2 / api-bootstrap

- **Date (UTC):** 2026-04-07 21:11
- **Spec:** 2026-03-28-api-bootstrap
- **Repo:** budget-brain-v2
- **Outcome:** ABORTED — stop condition #3 (two consecutive identical NO-SHIP outcomes)

## Run 1

### Setup
- Reset repo state, cleared `.dark-factory/{scenarios,reports,audit,state,failures,events.jsonl}`
- Removed `.dark-factory/architecture/environment.toml` + bootstrap delta
- Cleared `__pycache__`
- `dark-factory init --update`, `pip install -e dark-factory`, `dark-factory build node`
- Cleaned df-* docker containers/networks

### Initial blocker — depends-on resolution
First `dark-factory run` failed instantly:
```
ERROR: spec '2026-03-28-api-bootstrap' declares ID-form depends-on entries that
cannot be resolved without a plan context: F-1a. Run inside a plan, or pass
--force to bypass this check.
```
Re-ran with `--force`. **Bug observation:** Phase 6.4 (depends-on soft check for standalone runs, commit 4245d41) is supposed to soften this for standalone runs, but the check still hard-fails on ID-form `F-1a`. Only `--force` bypasses it. The spec lives in `.dark-factory/specs/` without a plan context, which is the standard standalone case the soft check should handle.

### Pipeline progression (with --force)

| Phase | Started | Completed | Wall (s) | Notes |
|---|---|---|---|---|
| spec-validator | 21:13:58 | 21:16:54 | 175 | OK |
| scenario-architect (generate) | 21:16:54 | 21:19:32 | 157 | 19 anchors uncovered → regen |
| scenario-architect (regen #1) | 21:19:32 | 21:25:18 | 346 | still 11 uncovered |
| scenario-architect (regen #2) | 21:25:19 | 21:30:30 | 312 | 10 anchors waived as untestable, COVERAGE_PASS at 23 anchors |
| implementer iter 1 | 21:30:32 | 21:36:25 | 353 | 7 files changed, smoke passed (advisory) |
| gate iter 1 | 21:36:33 | — | — | exit_code=1, **pass_rate=69**, 59 scenarios, type-check FAIL, self-tests FAIL |
| reviewer iter 1 | 21:36:33 | 21:41:06 | 273 | NO-SHIP |
| oracle iter 1 | 21:41:06 | 21:42:19 | 73 | NO-SHIP |
| implementer iter 2 | 21:42:19 | 21:49:20 | 421 | 7 files changed, smoke passed (now `policy=fatal`) |
| gate iter 2 | 21:49:28 | — | — | exit_code=1, **pass_rate=69 (identical)**, classifier marked PLATEAU |
| reviewer iter 2 | 21:49:28 | (interrupted ~21:51) | — | — |

**Wall clock:** ~38 min from SPEC_START to abort.

### Stop trigger
`TRAJECTORY_CLASSIFIED` event after iter 2 gate:
```json
{"trajectory":"PLATEAU","strategy":"Fundamentally different approach needed — same strategy is not working."}
```
Identical 69% pass rate across iter 1 and iter 2 → matches stop condition #3. Aborted manually.

### Quality gate findings (iter 2)

```
Build: PASS
Lint: PASS
Type Check: FAIL
Self-Tests: FAIL
```

**Type Check failure (apps/web):**
```
src/index.tsx(2,16): error TS2307: Cannot find module '@budget-brain/shared'
src/index.tsx(6,5): error TS7026: JSX element implicitly has type 'any'
src/index.tsx(6,5): error TS2875: This JSX tag requires the module path 'react/jsx-runtime'
```
The implementer never resolved the workspace package + React types in `apps/web`. The spec is `api-bootstrap` — the implementer is being graded against `apps/web` typecheck failures that aren't in scope.

**Self-Tests failure:**
```
sh: vitest: not found
ERR_PNPM_RECURSIVE_RUN_FIRST_FAIL  @budget-brain/api@0.0.0 test: vitest run
```
`vitest` is declared as the test runner but is not installed in `apps/api/node_modules`. Implementer didn't add it as a devDependency, or `pnpm install` did not pick it up.

### State snapshot
```
phase=review
smoke_result=passed
pass_rate=69
gate_exit_code=1
last_decision=NO-SHIP
iteration=2
status=interrupted
```

### Coverage warnings worth noting
Round 1 had 19 uncovered anchors; round 2 had 11; round 3 waived 10 anchors as "untestable" — including AC-1 (server startup logs), AC-2 (invalid PORT exit code 1), EC-1/EC-2 (env var validation). These are testable in principle (process spawn + exit code check), so the scenario architect bailing out as "untestable" looks like a regression in the coverage-regen prompt or the architect's notion of testability.

### Bug observations (no fixes attempted)

1. **Soft depends-on check not bypassing standalone runs.** Spec referenced `F-1a` and the runner refused to launch without `--force`. Phase 6.4 commit message claims this should be soft for standalone runs.
2. **Scenario architect waives obviously testable acceptance criteria as "untestable".** Process exit code + log line scenarios are within reach of HTTP smoke or process-spawn scenarios. Waiving them gives the implementer a free pass on AC-1, AC-2, EC-1, EC-2, EC-3, EC-7, AC-4, AC-7, AC-10, FR-2.
3. **Quality gate type-check runs across all workspaces, not just the spec's target.** A spec scoped to `apps/api` is being failed by `apps/web` type errors. This makes the api-bootstrap spec unshippable for reasons unrelated to its FR/AC.
4. **Implementer plateau on identical 69%.** Two consecutive iterations changed 7 files each but produced byte-identical pass rates. Either the iteration feedback isn't reaching the implementer, or the implementer is making cosmetic changes.
5. **`COVERAGE_REGEN` event records `attempt:1` even on the second regen.** The architect re-triggered with `attempt:1` after `COVERAGE_REGEN attempt:2` was logged — likely a bookkeeping bug in the regen loop.
6. **`vitest not found` in iter 2 self-tests** despite implementer having had iter 1 feedback. Suggests the implementer is editing `package.json` without running `pnpm install`, or quality gate's install step is partial.

### Conclusion
Run aborted at iter 2 reviewer phase due to identical NO-SHIP plateau. Pipeline infrastructure functioned end-to-end (no crashes, barriers held, smoke succeeded). Failures are agent-behavior + scope issues, not pipeline infra issues — except for the depends-on soft-check bug which blocked launch.
