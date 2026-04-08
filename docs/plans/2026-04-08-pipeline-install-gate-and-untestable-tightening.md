# Pipeline Install Gate + @untestable Tightening

**Created:** 2026-04-08
**Context:** Follow-up to `2026-04-06-pipeline-resilience-rewrite.md`. Budget-brain-v2 api-bootstrap run on 2026-04-07 23:57 UTC escalated after 2 iterations at 64%/64% plateau. Post-mortem identified one new structural bug (#6) and confirmed Bug #2's fix was only partially applied. See run report `docs/pipeline-run-reports/2026-04-07-23-57-budget-brain-v2-2026-03-28-api-bootstrap.md`.

**Cross-project constraint:** every fix must be viable for node / python / go / rust, monorepo or single-package. Not just budget-brain-v2 (pnpm monorepo).

---

## Root causes

### Bug #6 — gate container never installs dependencies
- `scripts/quality-gate.sh` runs `build_cmd`, `lint_cmd`, `typecheck_cmd`, `test_cmd` directly.
- Never invokes `manifest.commands.install`. The only install in the script is a hard-coded `pnpm install --frozen-lockfile || npm ci` inside smoke-test mode (lines 705-715), run from repo root, not the manifest's scoped command.
- Result: architect emits `cd apps/api && pnpm install`, nothing consumes it, `node_modules/` stays empty, all four gates fail with "vitest: not found" / "@types/node not found" / "@typescript-eslint/eslint-plugin not found".
- Deeper smell: `commands.install` is a contract without a consumer. Multiple containers (gate, smoke, implementer) each reinvent their own install logic inconsistently.

### Bug #2 — @untestable tightening only on retry-2
- Tightened coverage-regen prompt that narrows `@untestable` escape hatch lives on the **retry-2** branch in `orchestrator.py`.
- Coverage regen attempt 1 uses the original lax prompt.
- In the 04-07 run, attempt 1 waived EC-5/EC-6/EC-7 with flimsy reasons ("not testable via public API", "requires implementation knowledge") and coverage PASS fired before retry-2 ever executed.

---

## Plan

### Phase 1 — Bug #6: defensive install step in quality-gate.sh

Ship alone. Validates cleanly on its own without confounding with Bug #2.

- [x] **1.1** Read current `scripts/quality-gate.sh` gate setup (manifest parse, env contract load) to locate the right insertion point — after manifest is loaded, before Gate 1 (build).
- [x] **1.2** Design the install fallback ladder:
  - If `manifest.commands.install` is present and non-empty → run it (single source of truth, CWD encoded by architect).
  - Else, language-detect from lockfile / manifest presence (ordered):
    - **Node:** `package.json` + `pnpm-lock.yaml` → `pnpm install --frozen-lockfile`; + `yarn.lock` → `yarn install --frozen-lockfile`; + `package-lock.json` → `npm ci`.
    - **Python:** `requirements.txt` → `pip install -r requirements.txt`; else `pyproject.toml` → `pip install -e .`; else `setup.py` → `pip install -e .`. Note: `poetry.lock` / `uv.lock` projects MUST set `commands.install` explicitly — fallback does not cover them.
    - **Go:** `go.mod` → `go mod download`. Note: best-effort only — requires network, may fail under egress filtering. Go projects are expected to set `commands.install` or rely on `go build`/`go test` fetching at build time.
    - **Rust:** `Cargo.toml` → `cargo fetch`. Same network caveat as Go.
  - Else → no-op with logged warning. Proceed to Gate 1. (Go/Rust projects where `go build`/`cargo build` handles fetch transparently will still pass.)
- [x] **1.3** Implement the step. Fail hard (infrastructure failure, not scenario failure) if the explicit `commands.install` is present but exits non-zero. Surface this in `.dark-factory/reports/<spec>-quality-gate-summary.md` via an `install_failure: true` field in the JSON summary and an `Install` row in the gate table, so Oracle and the trajectory classifier don't misread it as scenario/code failure. Fallback-path failures (language-detected) log a warning and proceed — they're best-effort.
- [x] **1.4** Remove the hard-coded smoke-mode install at lines 705-715 — it's redundant once the defensive step runs before all gates. Smoke mode should go through the same install path.
- [x] **1.5** Verify: language fallback ordering is deterministic; no double-install; monorepo CWD is architect-controlled (not pipeline-guessed).
- [x] **1.6** Linus review of the quality-gate.sh diff.
- [x] **1.7** Commit.

### Phase 2 — Validate Bug #6 fix cheaply ✅ PASSED 2026-04-08 01:08 UTC

Result: gate ran against existing iter2 checkout. Install row showed `PASS | fallback: pnpm install --frozen-lockfile`. Build PASS, Type Check PASS (both require node_modules — proof install worked). Lint now fails on real rule violations (no-undef on `crypto`, `setTimeout`, etc.), not "command not found". All three original symptoms (`vitest: not found`, `Cannot find type definition file for 'node'`, `Cannot find package '@typescript-eslint/eslint-plugin'`) absent. Phase 2.2 host-side `ls vitest` check is moot — install runs in container, torn down on exit; Build PASS is the equivalent signal.

- [ ] **2.0** Verify the 04-07 failed-run checkout is still on disk and untouched: `budget-brain-v2/apps/api/src/` matches iter2 implementer output, `pnpm-lock.yaml` present, `node_modules/` absent. If reset has already happened, **skip Phase 2 entirely** and go directly to Phase 3 — there's no cheap intermediate left.
- [ ] **2.1** `dark-factory gate 2026-03-28-api-bootstrap` against the existing checkout. ~2 min.
- [ ] **2.2** Success criterion is **specifically**: the install step executes and exits 0, AND the strings `vitest: not found`, `Cannot find type definition file for 'node'`, and `Cannot find package '@typescript-eslint/eslint-plugin'` disappear from gate logs. Pass-rate improvements are a bonus signal, not the signal — iter2's implementer code may still have semantic bugs. For an unambiguous check: `docker exec` into the gate container post-install and `ls apps/api/node_modules/.bin/vitest` — file should exist.
- [ ] **2.3** If the unambiguous check passes, proceed to Phase 3. If not, diagnose (defer further phases until fixed).

### Phase 3 — Full rerun api-bootstrap with Bug #6 fix only ✅ COMPLETED 2026-04-08 01:48 UTC

Result: ESCALATED after 2 iterations at 77%/77% plateau (up from 64%/64% pre-fix — **+13pp**). Install gate ran via fallback `pnpm install --frozen-lockfile`, Lint now PASSES (previously blocked by missing eslint plugin). New structural issues surfaced: (a) architect-emitted `pnpm -r --parallel run build` ignores workspace topology → `apps/web` races ahead of `packages/shared`; (b) scenario-architect writes test scripts to `/tmp/` detached from project module resolution. Neither is Bug #2. Report: `docs/pipeline-run-reports/2026-04-08-01-09-budget-brain-v2-2026-03-28-api-bootstrap.md`.

- [ ] **3.1** Reset budget-brain-v2 to clean state (prior run's reset procedure).
- [ ] **3.2** Create new pipeline run report file: `docs/pipeline-run-reports/{YYYY-MM-DD-HH-MM}-budget-brain-v2-2026-03-28-api-bootstrap.md`.
- [ ] **3.3** Run `dark-factory run 2026-03-28-api-bootstrap`. Monitor. 90-min hard cap.
- [ ] **3.4** Document outcome. Expected: ship or much higher pass rate. If still plateau, Bug #6 fallback is wrong or there's a Bug #7.
- [ ] **3.5** Note: Bug #2 will still allow flimsy waivers on attempt 1 — expected in this run, fixed in Phase 4.

### Phase 3.6 — Bug #7: orchestrator cached-manifest path bypasses env amend ✅ 2026-04-08

Root cause of the 77% plateau was NOT an architect prompt bug. `_step_architecture_lock` had an early-return at the spec_hash resume gate that skipped `amend_environment_from_manifest` entirely. After a reset procedure that wiped `environment.deltas/` but left the manifest YAML in place, env.toml silently reverted to the legacy bootstrap and the architect's correctly-scoped `cd apps/api && pnpm build` commands were discarded.

- [x] Extract `_apply_manifest_to_environment` helper in `darkfactory/pipeline/orchestrator.py` (parse + validate + scope + env-validate + amend + legacy bridge). Call it from both the cached-manifest resume path and the fresh-architect path.
- [x] Wrap the cached-path helper call in try/except DarkFactoryError: on failure, log and regenerate (handles the case where the project tree changed between runs and a previously-accepted manifest no longer validates).
- [x] Add idempotency check in `darkfactory/architecture/amend.py::amend_environment_from_manifest`: after folding parent view, if all proposed ops already match the view, skip the delta write and just re-materialize. Prevents delta log growth on cached replays.
- [x] Linus review — ACCEPT.
- [x] Commit.
- [x] Validate: full rerun of api-bootstrap (2026-04-08 03:07 UTC). ✅ env.toml now materializes architect's scoped `cd apps/api && ...` commands; delta `0001-2026-03-28-api-bootstrap.toml` written. All structural gates PASS (Install/Build/Lint/Type Check/Self-Tests). Escalated at 56% with Oracle classifying 3/5 failures as non-implementation (2 BRITTLE + 1 ENV_ISSUE) — plateau cause has shifted from structural to scenario-quality. Report: `docs/pipeline-run-reports/2026-04-08-03-07-budget-brain-v2-2026-03-28-api-bootstrap.md`.

### Phase 4 — Bug #2: apply tightened prompt on attempt 1 + code-side regex rejection

- [ ] **4.1** Read `darkfactory/pipeline/orchestrator.py` coverage-regen section to locate the attempt-1 vs retry-2 prompt branch.
- [ ] **4.2** Unify: tightened prompt (default-testable stance, six concrete testable categories, narrow runtime-limitation escape hatch) becomes the only prompt — delete the lax variant.
- [ ] **4.3** Add a deterministic regex blacklist over the `@untestable` reason field. Match **phrases**, not bare words, to avoid false positives on legitimate reasons that happen to mention "complex" or "unclear". Reject: `not testable via public API`, `not directly testable`, `requires implementation knowledge`, `internal detail`, `too complex to test`, `hard to test`, `reason unclear`, `implementation detail`, `cannot be tested`. Rejection triggers a coverage-regen retry with the specific reason appended to the prompt. Negative test cases MUST include legitimate uses like "complexity analysis shows O(n^2) behavior" and "timing unclear in microsecond range" to verify the matcher isn't over-broad.
- [ ] **4.4** Add a spec-validator rule: if a spec's FRs touch installable dependencies (imports, requires, uses: blocks), `commands.install` MUST be populated in the manifest regardless of spec_type. Fail-closed if missing.
- [ ] **4.5** Update `agents/scenario-architect.md` if the default-testable language there needs to reinforce attempt-1 behavior.
- [ ] **4.6** Unit tests for the regex blacklist (deterministic; cheap; each reason phrase gets a positive and a legitimate-runtime-limitation negative).
- [ ] **4.7** Linus review.
- [ ] **4.8** Commit.

### Phase 5 — Validate Bug #2 fix

- [ ] **5.1** Full rerun of api-bootstrap (same spec). Create new pipeline run report.
- [ ] **5.2** Expected: coverage regen attempt 1 either covers EC-5/EC-6/EC-7 or rejects the waiver and retries. No flimsy waivers pass.
- [ ] **5.3** Expected: plateau resolved (Bug #6 fixed in Phase 1, Bug #2 fixed in Phase 4, no known structural blocker remaining). Ship or very high pass rate.

### Cross-phase tracking

- [ ] **X.1** After Phase 3 completes, update this plan file with a link to the new pipeline run report.
- [ ] **X.2** After Phase 5 completes, update this plan file with a link to the new pipeline run report and a short outcome summary.

### Phase 6 — Backlog (do not execute this run; file as stubs)

- [ ] **6.1** Stub spec: `manifest-consumer-grep-lint`. Static grep for architect-emitted manifest field names (`commands.*`, `server.*`, `layout.*`, `services`, `test_base_url`) in `darkfactory/` source. Warn on zero hits per field. Would have caught Bug #6 on the first run where architect set `commands.install` and nothing read it. Narrow grep version only — no per-spec-type consumer registry (meta-rule trap).
- [ ] **6.2** Stub spec: `manifest-command-dispatcher`. Unify gate / smoke / implementer setup on a single `run_manifest_command(name, manifest)` helper that honors architect-declared CWD. Today three call sites reinvent it inconsistently (e.g. smoke-mode install uses `pnpm install --frozen-lockfile` from repo root instead of the architect's scoped install). Dispatcher + consumer-lint together prevent the next "contract without a consumer" class of bug.

---

## Open decisions at phase boundaries

- **After Phase 2:** if gate-only validation fails, stop and diagnose before running Phase 3. Don't burn 40 min on a full rerun when the cheap check said no.
- **After Phase 3:** if plateau persists with only Bug #6 fixed, investigate whether Bug #2's flimsy waivers are themselves the blocker (some waived anchor corresponds to a scenario category that plateaued) before starting Phase 4. May need to reorder.
- **After Phase 5:** if still not shipping, examine which of the 16 failing scenarios are persistent vs newly-resolved. Write a new post-mortem before more fixes.

## Out of scope for this plan

- Manifest-command dispatcher refactor (backlog).
- Consumer-lint implementation (backlog).
- Implementer-side install behavior (implementer's `:rw` container already handles its own install; Bug #6 is about the gate container specifically).
- Full language-matrix unit test suite for quality-gate.sh (`dark-factory gate` on existing checkouts is the honest smoke test; synthetic fixtures would rot).
