# Pipeline Run Report — budget-brain-v2 / 2026-03-28-api-bootstrap

**Started:** 2026-04-07 18:26 UTC
**Repo:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap (F-1b)

---

## Run 1 — ESCALATED after 2 iterations

### Setup
- Reset repo, cleared `__pycache__`, `dark-factory init --update`, reinstalled editable, `dark-factory build node`, cleaned orphaned containers/networks. All clean.
- 45-min wall cap was lifted by user mid-run; pipeline allowed to run to natural escalation.

### High-level outcome
- **Final status:** `interrupted` / `SPEC_ESCALATED` (max 2 iterations exhausted)
- **Final state:** `phase=oracle_complete last_decision=NO-SHIP pass_rate=64 gate_exit_code=1 smoke_result=passed server_error=`
- **Run id:** `03fbb68eb5e24ac9`
- **Wall time:** ~49.4 min (18:27:21 → 19:16:44 UTC)
- **Estimated cost:** ~$84 (2 iters × $42 ITER_COST)

### Phase timeline (wall_seconds from events.jsonl)

| Phase | wall_s | Notes |
|---|---:|---|
| spec-validator | 226 | exit 0 |
| scenario-architect generate | 206 | COVERAGE_GAP: 18 anchors uncovered (FR-2, AC-1/2/4/5/6/7/9/10, EC-1..9) |
| scenario-architect coverage_regen #1 | 234 | COVERAGE_GAP: 5 anchors still uncovered (AC-1, AC-2, EC-1..3) |
| scenario-architect coverage_regen #2 | 171 | COVERAGE_PASS, anchor_count=23 |
| **iter1** implementer | 366 | 7 files changed |
| iter1 smoke | ~37 | success, policy=advisory |
| iter1 gate | ~70 | exit 1, **64% pass**, 65 scenarios, 0 crashed |
| iter1 reviewer | 233 | exit 0 |
| iter1 oracle attempt #1 | 111 | exit 0 (NO-SHIP) |
| iter1 oracle attempt #2 | **360** | exit 0 — anomalously long oracle re-run, see issue #1 |
| **iter2** implementer | 386 | 7 files changed |
| iter2 smoke | ~6 | success, policy=**fatal** (smoke policy escalated for iter2) |
| iter2 gate | ~66 | exit 1, **64% pass** — identical to iter1 |
| iter2 trajectory | — | classified **PLATEAU**, "Fundamentally different approach needed" |
| iter2 reviewer | 225 | exit 0 |
| iter2 oracle attempt #1 | 101 | exit 0 |
| iter2 oracle attempt #2 | 44 | exit 0 |
| iter3 escalation | 81 | SPEC_ESCALATED |

### Quality gate breakdown (iter1 & iter2 identical)

```
Build           PASS
Lint            PASS
Type Check      FAIL  (apps/web typecheck — see below)
Self-Tests      FAIL  (vitest: not found in apps/api)
Blind Scenarios FAIL  (cat 11: 0/4 passed; ~64% overall pass rate)
Mutation        SKIP
```

#### Type Check failures (iter1+iter2, identical)
```
apps/web/src/index.tsx(2,16): error TS2307: Cannot find module '@budget-brain/shared'
apps/web/src/index.tsx(6,5): error TS7026: JSX element implicitly has type 'any' because no interface 'JSX.IntrinsicElements' exists.
apps/web/src/index.tsx(6,5): error TS2875: This JSX tag requires the module path 'react/jsx-runtime' to exist
apps/web/src/index.tsx(7,5)/(7,23)/(8,5): TS7026 JSX implicit any
ERR_PNPM_RECURSIVE_RUN_FIRST_FAIL @budget-brain/web@0.0.0 typecheck
```

#### Self-Test failures
```
apps/api test$ vitest run
apps/api test: sh: vitest: not found
ERR_PNPM_RECURSIVE_RUN_FIRST_FAIL @budget-brain/api@0.0.0 test
```

#### Blind Scenario failures (category 11 server-startup-logging, 4 failures)
- `exits with code 1 when NODE_ENV is unsupported value [EC-2]` — got 0, expected 1
- `logs all validation errors when multiple env vars are invalid [EC-3]` — got 0, expected 1
- 2 more in same file expecting non-zero exit on missing/invalid env vars
- All in `.dark-factory/scenarios/2026-03-28-api-bootstrap/11-server-startup-logging.test.ts`
- iter1 and iter2 category-iter env files: `CAT_11_PASSED=0 CAT_11_TOTAL=4 CAT_11_WEIGHT=100` — **identical**

### Trajectory classification (iter2)
```json
{"trajectory":"PLATEAU","strategy":"Fundamentally different approach needed — same strategy is not working."}
```
Pipeline correctly detected the plateau but still ran through review/oracle/escalation. Behaviour matches design.

### Escalation report (excerpt)
> "ESCALATION CLASSIFICATION: IMPLEMENTATION CRISIS … SCENARIO-DOMINATED PLATEAU — same critical failures invariant across iterations."
> P0s: TypeScript compilation, self-test suite, scenario execution. All UNRESOLVED across iter1→iter2.

### Barrier sanity check (audit log)
- **Spec-validator:** `:ro` mount, no source masking (correct — read-only project, no source isolation needed).
- **Scenario-architect:** masks `/project/apps/api/src`, `/project/apps/web/src`, `/project/packages/shared/src`, `/project/.git` ✅ matches monorepo `source_dirs` from backcompat synthesizer.
- **Implementer:** `:rw` mount, masks `.dark-factory/{scenarios,reports,audit,state,failures}` and `.git` ✅
- **Reviewer:** `:ro` mount, masks `.dark-factory/{scenarios,reports}` and `.git` ✅
- **Oracle:** `:ro` mount, no scenario/report masking (oracle reads reviews dir, sees pre-computed metrics in prompt).
- No `BARRIER_PATH_ECHO_DETECTED` events emitted.

---

## Issues / regressions / observations

### Issue 1 — Oracle attempt #2 in iter1 ran 360s (vs 111s for attempt #1)
Iter1 oracle ran twice back-to-back (both labeled `attempt:1`):
- attempt 1: 111s
- attempt 2: 360s (3.2× longer, near max-turns ceiling)

Iter2 oracle also ran twice (101s + 44s) but with normal durations. Why does the oracle re-run with `attempt:1` rather than `attempt:2`? Either an event-emission bug or a deliberate re-prompt path that isn't bumping the attempt counter. Worth investigating in `pipeline.py`.

### Issue 2 — Smoke policy upgraded from `advisory` to `fatal` between iter1 and iter2
`SMOKE_COMPLETED` policy field: iter1=`advisory`, iter2=`fatal`. Looks intentional (probably "first iter is forgiving") but undocumented in events. Should be made explicit in the report.

### Issue 3 — Two consecutive identical NO-SHIPs (PLATEAU) — pipeline could short-circuit
Iter1 and iter2 produced byte-identical category breakdowns (`CAT_11_PASSED=0 CAT_11_TOTAL=4`). The trajectory classifier flagged PLATEAU in iter2 but the pipeline still spent ~$42 on iter2 reviewer/oracle/escalation. Consider: when PLATEAU is detected and category breakdown is bit-identical, fast-path to escalation and skip reviewer+oracle to save ~6 min and ~$10/iter.

### Issue 4 — Implementer left two real bugs unaddressed for both iters
Both are basic and should be fixable with more aggressive feedback:
- `apps/web` is missing React/JSX types (`@types/react`, `react/jsx-runtime`) and `@budget-brain/shared` workspace link — pure dependency hygiene problem.
- `apps/api` is missing `vitest` as a devDependency — `vitest: not found`.
The implementer is changing 7 files per iter but apparently not touching `package.json`s. Worth examining `oracle_iteration_feedback.md` to see whether the spec-section-anchored feedback is too vague to direct the implementer at the actual missing deps.

### Issue 5 — Category 11 server-startup-logging — implementation never matched scenario expectations
4 scenarios expect `process.exit(1)` on invalid `NODE_ENV` / `PORT` / multi-error. Implementer always returned exit 0. Either the scenarios over-constrain a behaviour the spec doesn't require (false positive), or implementer is silently swallowing validation errors. Either way: 100% stable failure pattern across iters → needs spec/scenario reconciliation.

### Observation — Coverage regen did its job
Initial scenario gen left 18 anchors uncovered; coverage_regen #1 dropped to 5; coverage_regen #2 reached COVERAGE_PASS at 23 anchors. Two regen rounds is acceptable but adds ~7 min wall and ~$24 (estimated). Worth tracking whether the initial gen quality improves with prompt tuning over time.

### Observation — Cost / latency
- 2 iters × $42 + ~$30 (validator + 3× scenario-arch + escalation) ≈ **$114 total**
- 49 min wall for 2 iters; implementer dominates (~6 min each).

---

## Next-run watchlist
- [ ] Does oracle still run twice per iter1 with the second attempt running near max-turns?
- [ ] Does the implementer ever touch `apps/web/package.json` or `apps/api/package.json`?
- [ ] Does PLATEAU short-circuit if added?
- [ ] Coverage regen counts (target: 0 or 1, not 2).
