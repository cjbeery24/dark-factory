# Pipeline Run Report — budget-brain-v2 / 2026-03-28-api-bootstrap

**Started:** 2026-04-08 01:09 UTC
**Plan context:** Phase 3 of `docs/plans/2026-04-08-pipeline-install-gate-and-untestable-tightening.md`
**Hypothesis:** Bug #6 (gate container never installs deps) is fixed in Phase 1. Bug #2 (flimsy @untestable waivers on attempt 1) is still present and may continue to leak. Expectation: ship or substantially higher pass rate than the 04-07 23:57 64%/64% plateau.

---

## Run 1 — Bug #6 fix only

### Setup
- Reset budget-brain-v2 (git restore + clean untracked + remove .dark-factory runtime artifacts + remove env.toml/0000-bootstrap delta)
- Cleared dark-factory __pycache__
- `dark-factory init --update` against budget-brain-v2
- `pip install -e ~/sites/dark-factory`
- `dark-factory build node`
- Cleaned orphaned df-* containers/networks

### Run
TBD — to be filled in as the run progresses.

### Findings

#### Process bug: reset prompt leaves orphaned per-spec deltas

The standard reset procedure deletes:
- `.dark-factory/architecture/environment.toml`
- `.dark-factory/architecture/environment.deltas/0000-bootstrap-from-config-toml.toml`

…but leaves per-spec deltas like `environment.deltas/0001-2026-03-28-api-bootstrap.toml` in place. When the next run regenerates `0000-bootstrap-from-config-toml.toml`, its hash differs from what `0001` references, and `fold_deltas()` raises:

```
EnvironmentConflict: 0001-2026-03-28-api-bootstrap.toml: parent_hash mismatch (delta says 3e24a6b23075, view is 4ea2e7c99651)
RuntimeError: environment.toml bootstrap failed: ... Refusing to run on a half-migrated project
```

Pipeline refusal is correct — this is a reset-procedure incompleteness, not a pipeline bug. Per-spec deltas are pipeline-regenerated runtime artifacts and should be wiped alongside `environment.toml` and the bootstrap delta.

**Suggested reset-prompt update** (replace the corresponding bullets in step 1):

> - ALSO remove these so the backcompat synthesizer re-runs with current logic:
>   - `.dark-factory/architecture/environment.toml`
>   - `.dark-factory/architecture/environment.deltas/` (entire directory — both the bootstrap delta and any per-spec deltas; all are regenerated on next run)

Equivalent shell:
```bash
rm -f .dark-factory/architecture/environment.toml
rm -rf .dark-factory/architecture/environment.deltas
```

After this fix, the second `dark-factory run` invocation succeeded past the bootstrap step.

#### Pipeline run — completed (escalated)

**Outcome:** ESCALATED after 2 iterations at **77% / 77% plateau**.
Prior run (04-07 23:57): 64% / 64%. **Phase 1 install fix accounted for +13 percentage points.**

**Gate summary (iter 2):**
```
| Install       | PASS | fallback: pnpm install --frozen-lockfile
| Build         | FAIL | apps/web: Cannot find module '@budget-brain/shared'
| Lint          | PASS |
| Type Check    | FAIL | apps/web: Cannot find module '@budget-brain/shared'
| Self-Tests    | SKIP |
| Blind Scenarios (executable) | FAIL | 77%
```

**Bug #6 — confirmed fixed.** Install ran via fallback ladder (`pnpm install --frozen-lockfile`), exited 0, and the three original symptoms (`vitest: not found`, `Cannot find type definition file for 'node'`, `Cannot find package '@typescript-eslint/eslint-plugin'`) are gone from all gate logs. Lint now PASSES — previously blocked by missing eslint plugin.

**New structural issue surfaced (Bug #7 candidate — workspace build ordering):**
`apps/web` build and typecheck both fail with:
```
apps/web: src/index.tsx(2,16): error TS2307: Cannot find module '@budget-brain/shared' or its corresponding type declarations.
```
Root cause is the architect-emitted build command using `pnpm -r --parallel run build`. Parallel mode does not respect workspace dependency topology, so `apps/web` (which depends on `packages/shared`) can start before `shared` finishes building its `dist/`. Intermittent-ish, but in this run, web lost the race. Fix belongs on the architect side: emit `pnpm -r run build` (topological by default) or `pnpm --filter ...{apps/web}... build`. **Not a pipeline bug — an architect prompt bug.**

**Scenario bugs (tautological — SA repair didn't converge):**
Smoke test classified **14 tautological scenarios** on entry and triggered Scenario Architect repair. Post-repair plateau at 77% suggests some scenarios are still broken. Sample failure (EC-5):
```
AssertionError: expected 'node:internal/modules/cjs/loader:1386…'
  to contain 'should-NOT-be-redacted@example.com'
+ Error: Cannot find module 'pino'
+ Require stack:
+   /tmp/bootstrap-edge-GjnKiC/redact-test.js
```
The scenario writes a test script to `/tmp/bootstrap-edge-GjnKiC/` and shells out to run it, but that temp dir has no `node_modules`. Scenario is trying to test the redaction subsystem in isolation but detaches itself from the project's module resolution — scenario-architect pattern bug, not implementation bug. Oracle correctly classified 8/8 failures as `ENV_ISSUE` ("environment/config issue outside Implementer scope") and triggered escalation.

**Bug #2 — confirmed still present and leaking:**
Oracle feedback validator caught **2 leakage patterns** on the first try:
```
pattern '\b\d+%\s*(?:pass|fail)':          ['77% pass', '77% pass']
pattern '\b(?:all|every|none|no)\s+(?:\d+\s+)?scenarios?\b': ['all scenarios']
```
This is independent of the `@untestable` tightening (Phase 4) but confirms the oracle prompt still generates barrier-leaking language. The framework barrier validator blocked it — defense in depth worked — but it's more evidence that Phase 4's unified prompt work is needed.

**Iteration progression:**
- iter 1: pre-gate FAIL (build + type check), gate FAIL @ 77%, oracle NO-SHIP
- iter 2: same. Identical pass_rate on consecutive iterations → correct early escalation trigger.
- Escalation reason: `8 ENV_ISSUE out of 8 failures — environment/config issue outside Implementer scope`.

**Wall time:** approximate iteration durations from events.jsonl: 90s, 318s, 199s, 237s, 244s, 257s, 138s, 178s, 261s, 141s, 68s, 64s. Total run ~33 minutes. Well under 90-min cap.

**No pipeline crashes, no hangs, no barrier violations slipped through.** Pipeline behavior correct throughout.

### Phase 3 verdict

✅ **Bug #6 fix validated in a full pipeline run.** +13pp pass-rate lift attributable directly to install gate.
Plateau at 77% is caused by two unrelated issues:
1. **Architect-emitted parallel build command** ignores workspace topology — needs prompt fix (new Bug #7 candidate, backlog).
2. **Scenario-architect writing tests to detached /tmp dirs** — needs SA prompt fix (variant of existing SA pattern issues).

Neither is the @untestable Bug #2. Phase 4 should still proceed, but expect the plateau to persist until Bugs #7 + SA /tmp pattern are also fixed. Recommend filing both as new backlog items before starting Phase 4.
