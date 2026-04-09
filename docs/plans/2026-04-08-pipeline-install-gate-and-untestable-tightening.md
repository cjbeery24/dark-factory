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

- [x] **2.0** Existing checkout verified on disk; Phase 2 ran against it. (Validated in commit `b775b1c`.)
- [x] **2.1** `dark-factory gate` executed against existing iter2 checkout.
- [x] **2.2** Install step executed, exited 0, and all three canonical "command not found" strings disappeared from gate logs. Build PASS and Type Check PASS acted as the equivalent of the `ls vitest` check (both require `node_modules`).
- [x] **2.3** Unambiguous check passed; routed to Phase 3.

### Phase 3 — Full rerun api-bootstrap with Bug #6 fix only ✅ COMPLETED 2026-04-08 01:48 UTC

Result: ESCALATED after 2 iterations at 77%/77% plateau (up from 64%/64% pre-fix — **+13pp**). Install gate ran via fallback `pnpm install --frozen-lockfile`, Lint now PASSES (previously blocked by missing eslint plugin). New structural issues surfaced: (a) architect-emitted `pnpm -r --parallel run build` ignores workspace topology → `apps/web` races ahead of `packages/shared`; (b) scenario-architect writes test scripts to `/tmp/` detached from project module resolution. Neither is Bug #2. Report: `docs/pipeline-run-reports/2026-04-08-01-09-budget-brain-v2-2026-03-28-api-bootstrap.md`.

- [x] **3.1** budget-brain-v2 reset to clean state.
- [x] **3.2** Run report written: `docs/pipeline-run-reports/2026-04-08-01-09-budget-brain-v2-2026-03-28-api-bootstrap.md`.
- [x] **3.3** Full pipeline run executed under monitor.
- [x] **3.4** Outcome documented: +13pp (64%→77% plateau), install path validated, two new structural issues surfaced (monorepo build topology, `/tmp` SA scripts) which became Phase 3.6 / 4.5 targets.
- [x] **3.5** Confirmed: Bug #2 waivers still allowed on attempt 1 in this run (expected); fixed in Phase 4.

### Phase 3.6 — Bug #7: orchestrator cached-manifest path bypasses env amend ✅ 2026-04-08

Root cause of the 77% plateau was NOT an architect prompt bug. `_step_architecture_lock` had an early-return at the spec_hash resume gate that skipped `amend_environment_from_manifest` entirely. After a reset procedure that wiped `environment.deltas/` but left the manifest YAML in place, env.toml silently reverted to the legacy bootstrap and the architect's correctly-scoped `cd apps/api && pnpm build` commands were discarded.

- [x] Extract `_apply_manifest_to_environment` helper in `darkfactory/pipeline/orchestrator.py` (parse + validate + scope + env-validate + amend + legacy bridge). Call it from both the cached-manifest resume path and the fresh-architect path.
- [x] Wrap the cached-path helper call in try/except DarkFactoryError: on failure, log and regenerate (handles the case where the project tree changed between runs and a previously-accepted manifest no longer validates).
- [x] Add idempotency check in `darkfactory/architecture/amend.py::amend_environment_from_manifest`: after folding parent view, if all proposed ops already match the view, skip the delta write and just re-materialize. Prevents delta log growth on cached replays.
- [x] Linus review — ACCEPT.
- [x] Commit.
- [x] Validate: full rerun of api-bootstrap (2026-04-08 03:07 UTC). ✅ env.toml now materializes architect's scoped `cd apps/api && ...` commands; delta `0001-2026-03-28-api-bootstrap.toml` written. All structural gates PASS (Install/Build/Lint/Type Check/Self-Tests). Escalated at 56% with Oracle classifying 3/5 failures as non-implementation (2 BRITTLE + 1 ENV_ISSUE) — plateau cause has shifted from structural to scenario-quality. Report: `docs/pipeline-run-reports/2026-04-08-03-07-budget-brain-v2-2026-03-28-api-bootstrap.md`.

### Phase 4 — Bug #2: apply tightened prompt on attempt 1 + code-side regex rejection ✅ SHIPPED

Landed in commit `842af1a` ("Tighten scenario-architect untestable criteria"). Related Bug #3 CWD rule extension in `4391901`.

- [x] **4.1** Located coverage-regen section in `orchestrator.py`.
- [x] **4.2** Tightened prompt unified — lax variant deleted.
- [x] **4.3** Phrase-level regex blacklist shipped with legitimate-use negative tests.
- [x] **4.4** Spec-validator rule enforcing `commands.install` when FRs touch installable deps — fail-closed.
- [x] **4.5** `agents/scenario-architect.md` updated for default-testable stance.
- [x] **4.6** Unit tests for the regex blacklist.
- [x] **4.7** Linus review completed.
- [x] **4.8** Committed (`842af1a`).

### Phase 4.5+ — Diagnostic localization (measurement-first, gated)

**Motivation.** Three prior runs (23:57 / 01:09 / 03:07) each revealed a new SA failure mode — detached `/tmp` scripts, then `spawnSync` subprocess tests, then over-specific log/timing asserts. Every "fix" so far has moved bugs around at N=1, and we have no statistical signal to distinguish progress from noise. Adversarial review by Liotta + neo converged: **do not change the ship gate before localizing whether the blocker is the SA, the spec, or the Oracle**. Run cheap, decisive diagnostics first; defer any ship-gate changes until evidence demands them.

**Binding meta-rules (apply to every gate below):**
- **Pre-commit thresholds.** Every phase gate writes a `phase-gate.json` to the pipeline run report directory with its threshold, denominator, and baseline values, and is `git commit`-ed BEFORE the pipeline run that measures against it. Post-hoc threshold tuning = plan failure; halt and replan.
- **No AC renumbering.** Once a baseline AC set is captured (4.6a.1), all later coverage numerators are computed against those original AC IDs. Spec amendments may map multiple extraction surfaces onto one AC; they may not create new AC IDs that inflate the denominator.
- **Phase budget tripwire.** Any phase that exceeds 2x its estimated cost triggers a mandatory halt and user check-in before continuing.
- **Meta-tripwire.** If no spec has shipped end-to-end after 3 calendar weeks in Phase 4.x, the bias inverts: the next run ships at pass_rate ≥70% under explicit documented-gaps rules, and we replan from there.

#### Phase 4.5a — Trivial pure-function spec ✅ COMPLETED 2026-04-08 23:20 PDT

**Result: SHIP (process-defect-blocked).** See `docs/pipeline-run-reports/2026-04-08-23-20-budget-brain-v2-shared-parse-amount.md`.

**Hypothesis affirmatively validated.** F-U1 (parseAmount) hit 8/8 SHIP-side preconditions: Tier-1 contracts 3/3, all AC-aligned tests pass, zero CRASH, zero `/tmp`, zero subprocesses, 93% scenario pass rate, all structural gates green every iteration. Pure-function specs DO eliminate the surface-complexity failure modes that crippled api-bootstrap.

**Process defect uncovered:** Oracle was allowed to weigh 4 SA-invented out-of-set scenarios (mislabeled `[AC-8]`) as ship-blockers, despite the locked AC set being committed in `2d6916d`. The phase-gate's locked denominator was not honored at decision time. This is the meta-rule violation that phase-gates exist to prevent.

**Routing:** Skip 4.5b (lifecycle bridge would have shipped on the same hypothesis but blocked by the same defect). Go directly to **Phase 4.6c** (locked-denominator enforcement) below — the enforcement fix unblocks F-U1, then re-validate against api-bootstrap.

**Original spec brief (kept for history):**

- [x] **4.5a.1** F-U1 spec `2026-04-08-shared-parse-amount.md` written and committed to budget-brain-v2.
- [x] **4.5a.2** `phase-gate.json` committed in `2063d46` before the run.
- [x] **4.5a.3** Spec run through pipeline 2026-04-08 23:20 PDT.
- [x] **4.5a.4 — VALIDATION GATE** resolved as SHIP (process-defect-blocked). Routed to 4.6c per findings above.

  Original gate table (kept for history):

  | Outcome | Criteria | Next phase |
  |---|---|---|
  | **SHIP** | Pipeline returns SHIP within ≤3 iterations AND reviewer score ≥60 AND ≥1 scenario exists in any failure-mode class that broke api-bootstrap | → 4.5b (lifecycle bridge) |
  | **SHIP-SUSPECT** | Pipeline returns SHIP but reviewer score <60 OR zero scenarios in api-bootstrap failure classes | → halt, diagnose before proceeding |
  | **PLATEAU** | NO-SHIP, regardless of pass rate | → 4.6b (SA-systemic path) |

  **Measurable artifact:** per-AC coverage table committed to the run report.

#### Phase 4.5b — Lifecycle-bridge spec (transfer check) — SKIPPED

**Skipped** per 4.5a routing: F-U1 validated the pure-function hypothesis but uncovered a process defect (Oracle weighing out-of-manifest scenarios). Running 4.5b would have hit the same defect without producing new signal. Re-enter only if 4.6c.E routes back here.

- [ ] ~~**4.5b.1**~~ SKIPPED
- [ ] ~~**4.5b.2**~~ SKIPPED
- [ ] ~~**4.5b.3**~~ SKIPPED
- [ ] ~~**4.5b.4 — VALIDATION GATE:**~~

  | Outcome | Criteria | Next phase |
  |---|---|---|
  | **SHIP** | SHIP within ≤3 iterations AND the lifecycle AC has passing coverage | → 4.6a (api-bootstrap is genuinely spec-hostile; amend it) |
  | **LIFECYCLE-PLATEAU** | 4.5a SHIPPED but 4.5b does not, AND failures concentrate on the lifecycle AC | → SA can handle pure functions but not lifecycle → api-bootstrap amendment won't help → 4.6b linter path |
  | **GENERAL PLATEAU** | fails broadly | → 4.6b (SA-systemic path) |

#### Phase 4.6a — Spec-hostility path (spec amendment) — SUPERSEDED

**Superseded by Phase 4.6b (reference-architecture realignment).** Entry conditions were *"Run ONLY if 4.5a SHIPS and 4.5b SHIPS"*; 4.5a finished as SHIP (process-defect-blocked), 4.5b was skipped, so the gate was never cleanly met. More importantly, the hypothesis here — "api-bootstrap's blockers are test-hostile anchors, amend the spec" — is a spec-side fix that would confound attribution on the F-1b run under the realigned topology. The adversarial review concluded the dominant blocker is topological (Oracle doing two jobs, SA doing two jobs), not spec-hostility. Amending api-bootstrap before fixing the topology would mean a green F-1b couldn't distinguish "spec amendment helped" from "classifier/SA split helped."

**Re-entry condition:** If Phase 4.6b.3 routes to `CLASSIFIER-IMPROVED-NO-SHIP` or `SA-SPLIT-IMPROVED-NO-SHIP` AND the classifier's output attributes the remaining failures to test-hostile anchors specifically, draft a fresh spec-amendment phase informed by the classifier's actual output. The tasks below are kept only as reference for the kind of work that phase would entail.

**Original brief (kept for history, not executed as drafted):**

- [ ] **4.6a.1** Audit api-bootstrap's failing anchors from the 03:07 run. Capture the **baseline AC set** (locked — no renumbering allowed past this point). Produce a table:

  | AC ID | Classification | Pure-function surface available? | Proposed extraction |
  |---|---|---|---|

- [ ] **4.6a.2** Amend spec 2026-03-28-api-bootstrap with an explicit `TESTABLE SURFACES` section naming the pure functions that cover each problematic AC. Extractions map onto existing AC IDs — no new AC numbers.
- [ ] **4.6a.3** `phase-gate.json` committed with baseline X/Y and target.
- [ ] **4.6a.4** Rerun.
- [ ] **4.6a.5 — VALIDATION GATE:**

  Let `X` = ACs with passing coverage in the 03:07 baseline, `Y` = total ACs in the baseline set (locked).

  | Outcome | Criteria | Next phase |
  |---|---|---|
  | **PROGRESS** | Post-amendment coverage ≥ (X+3)/Y AND the X+3 set includes **≥1 AC that was previously SCENARIO_CRASH classified** AND zero regressions (no AC loses coverage) | → Phase 5 (full api-bootstrap ship attempt) |
  | **SOFT-PROGRESS** | Coverage improves but fails one of the above constraints | → diagnose specific shortfall, do not auto-route to 4.6b |
  | **NO-PROGRESS** | Coverage ≤ X/Y OR any regression | → escalate to 4.6b |

#### Phase 4.6b — Reference-architecture realignment (NEW PRIMARY PATH)

**Reframe.** Prior versions of Phase 4.6 ordered a scenario linter ahead of the SA role split on "reversibility" grounds. Empirically, every downstream-patch intervention we've tried has failed to ship a spec, and the adversarial review of Phase 4.6c identified the underlying pattern: we have been patching around agents that are doing two jobs (one SA doing visible-test and adversarial-holdout work; one Oracle doing classification and decision) instead of splitting the roles the way the reference architecture does.

The reference shipped 7 CRUD features with n=1 of each specialized role. We have shipped 0. The honest conclusion is to **revert closer to the reference topology before building any more filters, linters, or scope enforcers**. Two structural moves, sequenced so each produces clean attribution against F-1b.

**Ordering rationale:** classifier split first, then SA split. The classifier split is lower-risk (additive agent, Oracle prompt rewrite, no change to scenario generation) and directly fixes observed Oracle failures (hallucinated SCENARIO_CRASH counts, inconsistent BRITTLE/ENV_ISSUE tagging, out-of-manifest weighting). The SA split changes scenario *content* and will confound attribution if run concurrently.

##### Phase 4.6b.0 — Defect Classifier agent extraction (strip classification from Oracle)

**Goal.** Add the 11th Yassine agent we currently fold into the Oracle. The Oracle's prompt shrinks to "given these classified counts and this behavioral report, ship or not." Classification becomes independently measurable, and the Oracle stops being both judge and taxonomist.

**What's moving:**
- Classification tags (`IMPL_BUG`, `SPEC_AMBIGUOUS`, `ENV_ISSUE`, `SCENARIO_CRASH`, `BRITTLE`) move out of the Oracle prompt and into a new `defect-classifier` agent.
- `classification_counts`, `escalation_category`, `failure_feedback_iterations` are populated by the classifier, not the Oracle.
- The Oracle reads the classifier's structured output as input, alongside the gate counters it already consumes.

**Design constraints:**
- **Non-mutating.** The classifier annotates; it does not rewrite `scenario_fail_count` / `scenario_total` / `pass_rate`. Those come from the gate and only the gate. Meta-rule 5 still holds.
- **Read-only.** Classifier container mounts project `:ro`, gate reports `:ro`, spec `:ro`, scenarios `:ro`. Same barrier profile as the Reviewer.
- **Single job.** The classifier produces a structured JSON report (`defects.json`): `[{test_name, category, confidence, rationale}]`. No prose, no ship/no-ship language.
- **Oracle is the consumer.** The Oracle prompt is rewritten to: "Given these gate counts, this behavioral report, and this defect classification, decide SHIP / NO-SHIP / ESCALATE." Oracle loses the ability to invent classification tags that disagree with counters (Bug #9 fix).

**Tasks:**
- [x] **4.6b.0.i** Add `AgentRole.DEFECT_CLASSIFIER` to `darkfactory/models/agent.py` and a `BarrierConfig` for it in `darkfactory/docker/barriers.py` (project `:ro`, scenarios and reports `:ro`, audit/state/failures masked, `.git/` masked — same as Reviewer).
- [x] **4.6b.0.ii** Write `agents/defect-classifier.md` — the agent's role definition. Explicit: single job is tagging failing tests into {`IMPL_BUG`, `SPEC_AMBIGUOUS`, `ENV_ISSUE`, `SCENARIO_CRASH`, `BRITTLE`, `OUT_OF_SCOPE`, `UNKNOWN`}. No decision language. Output schema committed in the agent file.
- [x] **4.6b.0.iii** Wire a new `_step_classify` step in `darkfactory/pipeline/iteration.py` that runs between `_step_quality_gate` and `_step_review`. Consumes gate result, produces `defects.json` written to `.dark-factory/reports/<spec>-defects-iter<N>.json`.
- [x] **4.6b.0.iv** Rewrite `agents/oracle.md` to remove all classification language. Add explicit input: "Read `.dark-factory/reports/<spec>-defects-iter<N>.json` for the classification of each failing test. You do not re-classify; you decide."
- [x] **4.6b.0.v** Remove classification-population code paths from the Oracle output parser. `classification_counts`, `escalation_category`, `failure_feedback_iterations` are now populated from the classifier's JSON, not the Oracle's prose.
- [x] **4.6b.0.vi** **GATE (unit-test level):** 7 new `parse_defects_json` tests + 2 `TestDefectsJsonSourceOfTruth` regression tests (valid defects.json overrides wrong Oracle block; errored defects.json does NOT silently fall back to prose). Existing Oracle feedback-parse tests retained for legacy `defects_path=None` path.
- [x] **4.6b.0.vii** Bug #9 sub-check: cross-validation built into `_step_classify` — stamps `crash_split_mismatch` into defects.json when `count(category=="SCENARIO_CRASH") != gate_result.scenario_crashed`; also stamps `count_mismatch` on total mismatch.
- [x] **4.6b.0.viii** Linus review. Progressive — each subtask reviewed and iterated.
- [x] **4.6b.0.ix** Committed across 5 commits (9ab4fc1 … 6574d34).

##### Phase 4.6b.1 — Scenario Architect role split (Test Engineer + QA-Holdout)

**Only after 4.6b.0 commits and the unit-test gate is green.** The two splits are sequenced so attribution is clean: if F-1b behavior changes after 4.6b.0 commit, that delta is attributable to the classifier split alone. Subsequent F-1b behavior after 4.6b.1 is attributable to the SA split.

**Goal.** Match Yassine's topology: one agent writes the visible test suite the Developer can read, constrained to AC predicates. A separate agent writes adversarial blind holdouts the Developer cannot read. Separate agent definitions, separate context windows, parallel invocation.

**Tasks:**
- [x] **4.6b.1.i** Added `AgentRole.TEST_ENGINEER` and `AgentRole.QA_HOLDOUT`.
- [x] **4.6b.1.ii** Wrote `agents/test-engineer.md` — AC-aligned visible tests under `.dark-factory/scenarios/<spec>/`.
- [x] **4.6b.1.iii** Wrote `agents/qa-holdout.md` — adversarial holdouts under `.dark-factory/holdouts/<spec>/` (relocated from `scenarios/<spec>/holdout/` to avoid spec-aware subdirectory masking).
- [x] **4.6b.1.iv** Barrier configs added for both roles in `docker/barriers.py`; `_ANALYSIS_ROLES_WITH_SOURCE_BARRIER` extended to include them in `darkfactory/pipeline/agents.py`.
- [x] **4.6b.1.v** Orchestrator rewire landed. All three SCENARIO_ARCHITECT invocation sites (`_step_scenario_generation`, `_enforce_coverage_floor`, `_step_scenario_repair`) now invoke `AgentRole.TEST_ENGINEER`. New `_invoke_qa_holdout` helper runs adversarially against `.dark-factory/holdouts/<spec>/` with no retry loop (visible AC suite is the gate; holdouts are supplementary). QA Holdout is called from inside `_step_scenario_generation` *before* the TE generation/retry/coverage-floor block so resume-after-TE-success still reaches it; idempotency keyed on the holdouts report file, not a coarse directory-contents check. Linus review also caught a barrier leak in 4.6b.1.iv — `qa_masked` did not include `.dark-factory/scenarios/`, so the QA Holdout container could have read TE's output and biased adversarial exploration; fixed here by adding `_SCENARIOS` to `qa_masked` and `_HOLDOUTS` to `te_masked`, plus updating stale prompt text that still referenced the old `scenarios/<spec>/visible/` and `scenarios/<spec>/holdout/` paths. Pinned step-function registry in `tests/unit/test_step_begin_enforcement.py` extended to `_invoke_qa_holdout`. 88 targeted unit tests green; 4 preexisting failures in unrelated files confirmed pre-existing via `git stash`.
- [x] **4.6b.1.vi** Implementer barrier updated: now masks `.dark-factory/holdouts/` instead of `.dark-factory/scenarios/`; prompt rewritten to explicitly allow reading visible AC tests. Reviewer and Architect barriers extended to mask holdouts as well.
- [x] **4.6b.1.vii** `agents/scenario-architect.md` and `agents/scenario-architect-reference.md` deleted. `darkfactory/setup/validator.py` required-files list swapped from `scenario-architect.md` to `test-engineer.md` + `qa-holdout.md`. `templates/project-claude-addition.md` agent-definitions list updated. The `AgentRole.SCENARIO_ARCHITECT` enum value, its `BarrierConfig`, and associated unit/integration tests remain as dead code — they are no longer reachable from the orchestrator and can be swept in a later cleanup phase. No regressions on the unit suite (62 preexisting failures, identical set pre- and post-change).
- [x] **4.6b.1.viii** **GATE (unit-test level):** existing barrier test `test_implementer_masks_scenarios` rewritten to `test_implementer_masks_holdouts_not_visible_scenarios` encoding the new invariant. 82 tests green.
- [x] **4.6b.1.ix** Linus review. Progressive — barrier swap reviewed in-line (no new objections vs 4.6b.0 pattern). Full review deferred until 4.6b.1.v lands with orchestrator changes.
- [x] **4.6b.1.x** Commit landed as a single infrastructure commit; orchestrator rewrite will land as 4.6b.1.v in a follow-up.

##### Phase 4.6b.1a — Format A retirement (blocker for 4.6b.2)

**Entered after Run 6 (2026-04-08-16-42 report)** surfaced a contract conflict: the new Test Engineer prompt commits to native executable tests (`.test.ts` / `test_*.py` / etc.), but `darkfactory/scenarios/validator.py:288` still hard-fails any spec with `has_api_contracts=true` that does not emit Format A `.scenarios.md` files. TE is wedged between two mutually-exclusive contracts and deterministically aborts at TE validation on any HTTP API spec.

**Decision (Option A — retire Format A entirely).** Evidence supporting retirement:
1. **Not inherited from Yassine.** Web research turned up no primary source (article / repo / code sample) in which Yassine's Test Engineer produces a markdown HTTP DSL. The closest comparable OSS project (`foundatron/octopusgarden`) uses YAML holdouts judged by an LLM, not a bespoke runner. Format A is a Dark-Factory-internal invention.
2. **Not original to Dark Factory either.** Initial commit `bc727dc` shipped a Scenario Architect that wrote **native executable tests** dispatched by `scripts/run-scenarios.sh` (project's configured test command). Format A was added ~4 months later in `bfb15b2` ("Add Format A/B scenario runners (HTTP and browser journey)") as a convenience so SA could emit framework-agnostic HTTP tests without knowing the project's test framework. The 4.6b.1 TE restores the original design.
3. **Format A is not Gherkin.** Its grammar is `# Scenario:` / `## Step N` / `## Expect` with HTTP-verb-shaped steps — a bespoke HTTP assertion DSL that can only express "fire request, check response." Retiring it loses a DSL, not behavioral-testing capability. TE writing `fastify.inject({...})` + `expect(res.statusCode).toBe(200)` via the native runner is strictly more expressive.
4. **Maintenance tax.** Follow-on commits `90d2829` / `ba608a2` / `03fa31d` / `c0e9583` / `1a08727` / `f802ff4` all fix bugs unique to the bespoke runner (format mismatch, dual-writer, SIGPIPE, 0% pass-rate skip bug, service sidecar coordination). pytest+httpx / vitest+supertest do not have this class of bug.
5. **Two independent neo reviews** concur that Option A best matches the goals-doc end-state where TE writes visible executable tests (Yassine's "44 tests") and QA-Holdout writes adversarial executable tests — single dispatch path, no format confusion.

**Future-extension note.** If literal Gherkin `.feature` files are ever wanted, the correct add is a real BDD runner (pytest-bdd / cucumber-js) as an optional TE output format authored by TE with step definitions — not a Format A revival. Tracked only as a note here; not planned work.

**Tasks:**
- [ ] **4.6b.1a.i** Delete `darkfactory/scenarios/validator.py:286-301` (`behavioral_scenarios_required` gate). Delete the downstream `parseable` sub-check at lines 449-481 (or wholesale remove `_validate_markdown_only` if no remaining caller needs it after this sweep). Delete the markdown-only pass-through at line 262.
- [ ] **4.6b.1a.ii** Delete `scripts/lib/http-scenario-runner.py` and its invocation branch in `scripts/quality-gate.sh:1006-1019`. Delete `_DF_FORMAT_PATTERNS_HTTP` in `scripts/lib/scenarios.sh:22-23` and the `detect_scenario_formats` path that discriminates Format A. Leave the executable-tests dispatch path untouched.
- [ ] **4.6b.1a.iii** Update `tests/unit/test_sa_validation.py:319,331,344` — delete the three `behavioral_scenarios_required` assertions (and delete the enclosing tests if they have no remaining meaningful assertions after the check is gone). Delete `tests/unit/test_http_scenario_runner.py` entirely.
- [ ] **4.6b.1a.iv** Grep `Format A` / `.scenarios.md` / `http-scenario-runner` across `docs/CONFIGURATION.md`, `docs/TROUBLESHOOTING.md`, `docs/WORKFLOW.md`, `docs/TOOLS.md` and delete/update the references. Minimal edits — just remove the retired path from anything that currently claims both formats are supported.
- [ ] **4.6b.1a.v** Full unit-test suite green. `has_api_contracts=true` spec must no longer trigger any Format-A-related validator branch.
- [ ] **4.6b.1a.vi** Commit as a single retirement commit. Message must reference Run 6 as the trigger and this plan section for the rationale.

##### Phase 4.6b.1b — Defect-classifier attribute drift (Run 7 blocker)

**Trigger:** Run 7 (2026-04-08, post-Phase 4.6b.1a) reached iteration 1 with a legitimate `pass_rate=26%` — the first end-to-end signal under the TE/QA-Holdout topology — then crashed in `iteration.py:1022` with `AttributeError: 'GateResult' object has no attribute 'scenario_fail_count'`. See the Run 7 section of `docs/pipeline-run-reports/2026-04-08-16-42-budget-brain-v2-2026-03-28-api-bootstrap.md` for the full traceback and context.

**Root cause.** Two dataclasses use different names for the same concept: `GateResult.scenario_failed` (canonical, `quality_gate.py:174`) vs `IterationState.scenario_fail_count` (canonical, `models/pipeline.py:37`). `iteration.py:1338` is the bridge that copies one into the other. Three sites in `iteration.py` mistakenly reach for the `IterationState` name on a `GateResult` object: lines 1022 (defect-classifier gate condition, the crash site), 1587 (`expected_total` computation), and 1597 (classifier prompt template). Wired in Phase 4.6b.0.iii/iv; never exercised end-to-end because prior runs never reached iter-1 quality-gate completion with `pass_rate < 100`. Neo review corroborated: `darkfactory/` has no other stale references.

**Blocks:** Phase 4.6b.2. Run 8 is not possible until the AttributeError is resolved.

**Tasks:**
- [x] **4.6b.1b.i** Rename the three `gate_result.scenario_fail_count` references in `darkfactory/pipeline/iteration.py` (lines 1022, 1587, 1597) to `gate_result.scenario_failed`. One-token change per site. Do not touch the `IterationState.scenario_fail_count` side of the bridge at line 1338 — that remains the canonical name on `IterationState`.
- [x] **4.6b.1b.ii** Add a unit test in `tests/unit/test_iteration.py` (or the equivalent existing test file for `_step_classify` / defect-classifier dispatch) that constructs a `GateResult` with `pass_rate < 100` and drives the branch at `iteration.py:1022` end-to-end far enough to prove the attribute is read correctly. Goal: attribute drift on `GateResult` is caught at CI, not at Run 8. A mock-`_step_classify` that asserts the gate-condition path was taken is sufficient — we are testing the bridge, not the classifier body.
- [x] **4.6b.1b.iii** Investigate the Run 7 soft warning `barrier violations detected in test-engineer output: ['apps/api/src']`. Read `darkfactory/audit/checker.py` to confirm the matcher is the documented naive substring matcher, read the actual TE output from Run 7's artifacts to verify the mention was in scenario-description text (not a real filesystem read — TE runs with `SOURCE_DIRS[]` masked in its container). If confirmed false positive, document in this task and close without code change. If any ambiguity, escalate before Run 8.
- [x] **4.6b.1b.iv** Full unit-test suite green (`python -m unittest discover tests/unit`). The new test from 4.6b.1b.ii must fail on the unfixed `iteration.py` and pass after the rename.
- [x] **4.6b.1b.v** Commit as a single fix commit. Message must reference Run 7 as the trigger and this plan section for the rationale.

**Design follow-up — do not execute this run.** The `GateResult.scenario_failed` / `IterationState.scenario_fail_count` naming asymmetry is the design smell that produced this drift. File as a backlog task: unify the names across both dataclasses (probably standardize on `scenario_failed` since that is what every other quality-gate reader already uses), collapse the bridge at `iteration.py:1338`, and update all downstream reads. Out of scope for 4.6b.1b — that rename has a much larger blast radius than the three-site fix and deserves its own plan section after 4.6b.2 produces F-1b signal. Track under Phase 6 / backlog.

##### Phase 4.6b.1c — Defect-classifier write mount + Oracle pass-rate leakage (Run 8 blockers)

**Trigger:** Run 8 (2026-04-08, post-Phase 4.6b.1b) was the first run to complete the full Phase 4.6b.1 iteration loop end-to-end (3 iters, every agent role, normal stable-failure escalation). Phase 4.6b.1b fix held — zero `AttributeError`s. But two new issues surfaced that together block any real F-1b attribution signal from 4.6b.2:

1. **Defect classifier writes no file, every iteration** → structurally forced NO-SHIP under safety rule 4.6b.0.iv → attribution data never produced.
2. **Oracle feedback echoes "6% pass"** → barrier-leakage regex triggers validation retry loop, and Implementer could in principle see the pass-rate numeral if the retry loop ever drops a violation.

See the Run 8 section of `docs/pipeline-run-reports/2026-04-08-16-42-budget-brain-v2-2026-03-28-api-bootstrap.md` for full context and neo's review for root-cause citations.

**Issue 1 root cause (neo-confirmed, one-line config bug).** `darkfactory/docker/barriers.py:218-222` defines the `DEFECT_CLASSIFIER` BarrierConfig *without* a `write_dirs=` field. Every other role that writes under `.dark-factory/reports/` lists it explicitly (ARCHITECT :170, SCENARIO_ARCHITECT :181, TEST_ENGINEER :210, QA_HOLDOUT :216, EVALUATOR :227). `darkfactory/docker/args.py:115` gives a `:rw` project mount only to IMPLEMENTER; every other role gets `:ro` plus per-path rw overlays via `build_rw_overlays` (`darkfactory/docker/mounts.py:60`). Without `write_dirs`, the classifier container has no rw overlay at `.dark-factory/reports/`, so the agent's Write silently fails (agent exits 0, no file written). `_step_classify` at `iteration.py:1641` then correctly stamps `classifier_no_output` — that's the artifact on disk in Run 8 (`budget-brain-v2/.dark-factory/reports/2026-03-28-api-bootstrap-defects-iter{1,2}.json`). The prompt (`iteration.py:1589-1611`) and agent definition (`agents/defect-classifier.md:24`) are both correct — **pure mount bug**.

**Issue 2 design note.** `audit/checker.py:72` (leakage regex `\b\d+%\s*(?:pass|fail)`) is correct and stays. The robust fix for Oracle's parroting is upstream: stop showing Oracle the pass-rate numeral in its prompt at all, and pass a coarse tier (`low` / `partial` / `high`) as the only quantitative signal. Instruction-follow failures compound ("don't say 6%" is a weak barrier); arithmetic censorship via prompt-template data removal does not. Same pattern already used elsewhere (spec-section anchors instead of scenario names).

**Blocks:** Phase 4.6b.2. Until the classifier produces a real defects.json, Oracle is structurally pinned to NO-SHIP and attribution signal is undefined.

**Tasks** (scope set by liotta+neo consensus — see review discussion in commit `07061fd..` context):

- [ ] **4.6b.1c.i** Add `write_dirs=[".dark-factory/reports"]` to the `DEFECT_CLASSIFIER` BarrierConfig at `darkfactory/docker/barriers.py:218-222`. One-line fix. Match the pattern used by EVALUATOR (line 227). This is the actual Run 8 blocker.
- [ ] **4.6b.1c.ii** Add generic `write_dirs` pre-flight in the agent container launch path (`darkfactory/docker/args.py` or wherever `run_agent_docker` assembles the container). After the container starts with its rw overlays, loop over the role's declared `write_dirs` and `os.access(..., os.W_OK)` each one. Fail fast with the role name if any declared write dir is read-only in the mounted filesystem. Catches this entire bug class — any role whose declared `write_dirs` don't match its actual rw mounts fails loud on iter 1 instead of silently stubbing for 3 iterations. Replaces the deferred per-classifier pre-flight touch.
- [ ] **4.6b.1c.iii** Two tests, complementary (neo flip — do not subsume):
  - **(a)** `ROLE_BARRIERS` snapshot/golden-file test over the full barrier dict shape. Any silent edit (add role, forget `write_dirs`, accidentally drop a masked dir) fails CI with a visible diff. Permanent guardrail.
  - **(b)** Targeted unit test: construct `DEFECT_CLASSIFIER` BarrierConfig and assert `write_dirs` contains `.dark-factory/reports`. Documents *intent* — when (a) fails six months from now, (b) says *why* it mattered. ~15 lines.
- [ ] **4.6b.1c.iv** Schema test for `defect-classification.json` output shape. Now that the classifier can actually write, lock the JSON shape against the contract in `agents/defect-classifier.md` before Oracle / downstream starts consuming new fields. Place alongside `parse_defects_json` tests in `tests/unit/test_iteration.py`.
- [ ] **4.6b.1c.v** Per-agent telemetry jsonl. Emit one structured line per agent invocation to `.dark-factory/reports/run-telemetry.jsonl`. Required fields: `schema_version=1, run_id, spec_name, role, iteration_index, phase, wall_clock_ms, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, timestamp`. Append-only (~8-15 lines/run — negligible). Pin `schema_version` from line one so Run 9 analysis code doesn't break silently when fields are added. Find the agent invocation site (`invoke_agent` in `darkfactory/pipeline/iteration.py` and wherever JSON responses from `claude -p --output-format=json` are parsed) and add the emit. **This is the data that makes deferring tasks v/vi safe** — without it, Run 9 is flying blind on cost, cycle-time, and Oracle anchoring attribution.
- [ ] **4.6b.1c.vi** Trajectory-detector fingerprint inspection (10 minutes, decision output only — no code change unless the answer surprises us). Open `darkfactory/pipeline/trajectory.py` (or wherever stable-failure detection lives) and answer one question: **does a stub raising `classifier_no_output` produce the same stable-failure fingerprint as a real plateau with matching traceback shape?** If fingerprint is file:line-based → safe, no change, note in commit body. If fingerprint includes assertion text / exception type → stubs could collide with real failures, add a filter that treats all-stubs trajectories as `UNKNOWN` rather than `PLATEAU`. Decision output is one paragraph in the commit body.
- [ ] **4.6b.1c.vii** 2-minute audit-by-grep of Phase 4.6b.* roles (TEST_ENGINEER, QA_HOLDOUT, DEFECT_CLASSIFIER) against `write_dirs` / actual step-function write paths. Neo already audited 4 of 5 roles in the Run 8 review — only remaining check is "no new role was ever plumbed to write outside its declared dirs in its step function." Report inline in the commit body.
- [ ] **4.6b.1c.viii** Full unit-test suite green. New tests from iii(a), iii(b), iv must all fail on unfixed `barriers.py` / pass after. Generic preflight from ii must fail on unfixed barriers and pass after.
- [ ] **4.6b.1c.ix** Commit as a single fix commit. Message must reference Run 8 as the trigger, this plan section for the rationale, and record the fingerprint-inspection result from vi inline.

**Deferred with named triggers** (neo+liotta agreed — do not do now):
- Pre-flight touch specifically inside `_step_classify` — subsumed by the generic preflight in 4.6b.1c.ii. Re-trigger: if generic preflight proves insufficient (e.g. per-iteration mount drift observed in Run 9).
- Oracle prompt-template tier substitution (replace `{pass_rate}%` with `low/partial/high`). Re-trigger: Run 9 telemetry shows Oracle anchoring on pass-rate numerals (e.g. shipping at ~72% "close enough" or NO-SHIP at 99% with "not perfect" reasoning). Preemptive sanitization = tuning a calibration knob with zero data — the telemetry from 4.6b.1c.v is what lets us decide with evidence. Keep the `audit/checker.py:72` leakage regex as defense-in-depth in the meantime.
- Trajectory-detector code change — do the 10-minute inspection in 4.6b.1c.vi now; actual code change only if the inspection finds fingerprint-text-collision risk.

**Design follow-up — Phase 6 backlog, not this run.** Add a schema-level assertion on `BarrierConfig` construction: if a role is declared in the set that `_step_*` functions write to `.dark-factory/reports/` for, require `write_dirs` to include it. Enforced at orchestrator startup, not at the first failure in iter-1. Same pattern as the `ROLE_BARRIERS` snapshot test in iii(a) but runtime instead of test-time.

##### Phase 4.6b.1d — Gate scenario count collapse + vitest load-error reporter gap (Run 9 blockers)

**Trigger:** Run 9 (2026-04-08, post-4.6b.1c) was the first run where the defect classifier produced real output and per-agent telemetry populated. Phase 4.6b.1c held. But the new instrumentation exposed two gate-layer bugs that together prevent any real F-1b pass-rate signal:

1. `scenario-counts.env` reports `SCENARIO_TOTAL=1, SCENARIO_FAILED=1` every iteration while `test-results-iterN.json` contains 6 distinct failing test rows. Classifier attribution is degraded to one synthetic "Environment validation scenario" — Oracle and trajectory detection starve on 1 data point.
2. 10 of 12 TE-written `.test.ts` files crashed at module load (`TypeError: Cannot redefine property: spawnSync` — shared-helper mock bug in the target), but the gate reporter only matches per-test `FAIL > describe > test` lines, so file-level load errors are invisible to the classifier and the implementer feedback loop.

**Diagnosis (neo, authoritative):** Finding 1 is a `scripts/quality-gate.sh::_parse_scenario_counts` regex bug — it only matches `Tests  N passed` and falls through to the binary fallback stamp `(total=1, failed=1)` when every test fails. Finding 2 is a reporter completeness gap in `_write_per_test_results` — vitest file-level FAIL banners (distinct from per-test FAIL rows) are not emitted as entries in the per-test JSON.

**Blocks:** Phase 4.6b.2. Until the gate reports real counts and real load errors, the classifier attribution is synthetic and the plateau/escalation verdicts cannot be trusted.

**Tasks:**
- [ ] **4.6b.1d.i [P0]** Shell fix in `scripts/quality-gate.sh::_parse_scenario_counts`. Add a failure-only branch (`Tests\s+[0-9]+ failed` without `passed`) and a combined-line branch (`Tests\s+[0-9]+ failed \| [0-9]+ passed`, order-independent). Locked by a shell fixture test under `tests/shell/` (or Python wrapper in `tests/unit/test_quality_gate.py`) using captured Run 9 vitest output. Assert `SCENARIO_FAILED >= 6, SCENARIO_TOTAL >= 6` for the Run 9 fixture.
- [ ] **4.6b.1d.ii [P0]** Pre-flight coupling check before implementing task i: inspect `darkfactory/pipeline/quality_gate.py::_accumulate_ab_scenario_counts` (and any other accumulator that reads `scenario-counts.env`) for additive combination with JSON-derived counts. If any path adds shell counts to JSON row counts, document the coupling and switch that path to JSON-authoritative reads *before* task i lands. Neo flagged this as the highest-risk silent regression — post-fix double-counting would be invisible. Report inline in the commit body.
- [ ] **4.6b.1d.iii [P0]** Fail-loud gate telemetry invariant. Write one structured line per gate invocation to `.dark-factory/audit/gate-telemetry.jsonl` with `schema_version=1, run_id, spec_name, iteration_index, reported_total, reported_failed, parsed_rows, timestamp`. Enforce two-way invariant: `parsed_rows == reported_total`. Mismatch → non-zero gate exit with visible error message naming the discrepancy. **Do not include a three-way `discovered/executed/reported` check** — the `discovered` count would require a third vitest-output parser, out of scope. Unit test locks the invariant: feed a row-count mismatch and assert the gate exits non-zero with the expected message.
- [ ] **4.6b.1d.iv [P1]** Extend `_write_per_test_results` in `scripts/quality-gate.sh` to emit synthetic `load_error` entries for vitest file-level FAIL banners missing from per-test output: `{"name": "<file>::__module_load__", "status": "failed", "error_type": "load_error", "file": "<file>"}`. Parse the `Test Files  X failed (Y)` summary line; for every file named in a file-level FAIL banner that does not produce a per-test row, emit one synthetic entry. Shell fixture test with a Run 9-shape captured output: 2 file-level banners + 1 normal per-test FAIL → 3 JSON entries, one with `error_type: "load_error"`.
- [ ] **4.6b.1d.v [P0]** Full unit-test suite green. The new tests from i, iii, iv must fail on unfixed code and pass after. Baseline is ~62 preexisting failures as of `15ea645`; commit must preserve that count, not introduce new regressions.
- [ ] **4.6b.1d.vi [P0]** Commit as a single fix commit. Message references Run 9 as trigger, this plan section for rationale, records the task ii coupling-check result inline, and notes that Finding 3 (iter-3 classifier/reviewer/oracle skipped on max-iter) is split into Phase 4.6b.1e.

**Deferred with named triggers** (neo consensus — do not do now):
- Dedicated integration test `test_scenario_counts_consistency.py` (originally proposed task 5). Subsumed by the task i fixture test + the task iii fail-loud invariant. A third fake-vitest integration test is speculative surface area. Re-trigger: if Run 10 surfaces a third parser disagreement not covered by either existing test.
- Three-way `discovered/executed/reported` telemetry (Liotta's original framing). Subsumed by the two-way invariant in task iii. Re-trigger: if Run 10 shows vitest reporting fewer files than `ls scenarios/` without any file-level FAIL banner — only then does a third parser surface earn its complexity.
- Any `BarrierConfig`-style schema assertion on gate output shape. Phase 6 backlog.

##### Phase 4.6b.1e — Iter-N control-flow: classifier/reviewer/oracle skip on max-iter (Run 9 Finding 3)

**Trigger:** Run 9 escalation sequence: after iter 3 gate FAIL and `TRAJECTORY_CLASSIFIED PLATEAU`, the pipeline jumped directly to the escalation agent, skipping iter 3's classifier, reviewer, and oracle steps. `defects-iter1.json` and `defects-iter2.json` exist; `defects-iter3.json` does not. The escalation agent therefore has full attribution for iters 1-2 but none for iter 3 — the final iteration before escalation is the one whose signal matters most.

**Scope:** control-flow fix in `darkfactory/pipeline/iteration.py` where the max-iter path jumps to escalation. Either run the classifier/reviewer/oracle before escalating (so every iter has a matching defects.json), or explicitly document that the escalation agent consumes iter `N-1`'s artifacts and guard against reading a missing `defects-iter<max>.json`. Design decision required before implementation.

**Blocks:** nothing — this is a diagnostics completeness fix, not a run-breaker. Enter after 4.6b.1d commits. Do not enter before 4.6b.2 unless 4.6b.1d exposes additional iter-N control-flow bugs.

- [ ] **4.6b.1e.i** Read `iteration.py` max-iter branch + escalation dispatch. Confirm the skip is intentional (escalation is "new budget, different agent") or incidental (fell out of the loop structure).
- [ ] **4.6b.1e.ii** Decide: (a) run classifier/reviewer/oracle on iter N before escalation and have escalation read `defects-iter<max>.json`, OR (b) have escalation dispatch call the classifier inline on its own behalf. Design output only — one-paragraph decision with rationale.
- [ ] **4.6b.1e.iii** Implement whichever option wins. Test: force max-iter reach in a unit test and assert the chosen artifact is present.
- [ ] **4.6b.1e.iv** Commit.

##### Phase 4.6b.1f — Manifest parser hardening: reject string-shaped response/request_body (Run 10 crash)

**Trigger:** Run 10 (post-4.6b.1d) crashed in `_step_scenario_generation` after qa-holdout with `AttributeError: 'str' object has no attribute 'get'` at `darkfactory/spec/manifest.py:835`. Root cause: the architect wrote `response` as a YAML-quoted string containing inline JSON-like text (`"{ status: 200, body: { ... } }"`), which slipped past `parse_manifest` because `ApiContract.response: dict` was a type hint only — no runtime check. `format_manifest_for_sa` then blindly called `.get`, crashing the whole pipeline before any scenario ran.

**Neo review consensus:** three-layer fix collapses to two layers once the parser guard lands (formatter guard becomes dead code by dataclass invariant). Architect prompt correction promoted to P0 (same commit) because without it every future run burns an iteration on deterministic parse failure. Add `request_body` guard alongside `response` — same failure mode waiting to happen.

**Tasks:**
- [x] **4.6b.1f.i [P0]** Parser guard in `_parse_api_contract` at `darkfactory/spec/manifest.py:~339`: `isinstance(..., dict)` for both `response` AND `request_body` after reading from raw YAML. Fail with error message naming the contract identity (`{METHOD} {path}`) and the expected shape.
- [x] **4.6b.1f.ii [P0]** Architect prompt correction in `agents/architect.md` around line 163: replace the inline flow-style `response: { status: 200, ... }` example with explicit block-style nested dict. Add a comment warning that quoted strings are rejected by the parser.
- [x] **4.6b.1f.iii [P0]** Regression tests in `tests/unit/test_manifest.py::TestParseManifest`:
  - `test_api_contract_response_must_be_dict_run10_regression` — feeds the exact Run 10 string shape, asserts parse error names `GET /health` and `response`.
  - `test_api_contract_request_body_must_be_dict` — same guard for request_body.
  - `test_api_contract_dict_response_still_accepted` — positive control, asserts block-style dict responses parse cleanly.
- [x] **4.6b.1f.iv [P0]** Full unit suite green. 69 preexisting failures baseline unchanged, +3 new passing tests.
- [x] **4.6b.1f.v [P0]** Single commit referencing Run 10 as trigger.

**Deleted from original plan (neo critique):**
- Formatter guard in `format_manifest_for_sa`. Dead code once parser guard lands — `ApiContract.response` is typed `dict` with exactly one `.get` consumer at line 835, so the dataclass invariant is restored at parse time. Violates 4.6b.1c narrow-fix precedent.

##### Phase 4.6b.3 — Optimization backlog (non-blocking)

Tracked optimizations surfaced during 4.6b.1/4.6b.2 work. None block 4.6b.2 or any downstream phase — they are listed here so they do not get lost. Enter only after 4.6b.2 produces F-1b attribution signal.

- [ ] **4.6b.3.i** Parallelize `_invoke_qa_holdout` and the Test Engineer generation block in `_step_scenario_generation`. They are structurally independent (disjoint inputs: both read the validated spec + architecture manifest only; disjoint outputs: TE → `scenarios/<spec>/`, QA → `holdouts/<spec>/`; no data dependency in either direction). Today they run sequentially purely for implementation simplicity. Parallelization is also *safer* for the information barrier: running in concurrent containers makes it structurally impossible for either to observe the other's in-progress writes, closing a theoretical window where a barrier-mask bug could let the second agent read the first's partial output. Expected wall-time saving in Run 3 profile: ~175s (shorter of QA:292s / TE:175s), ~20% of pre-implementer wall time. Cost delta: $0. Engineering cost is non-trivial: concurrent subprocess management, interleave-safe events.jsonl writes, coordinated idempotency/resume (what if QA completes and TE crashes mid-flight?), failure semantics (does one's failure abort the other's in-flight container?), and the `step_begin` pinning tests in `tests/unit/test_step_begin_enforcement.py` need to permit two concurrent step-function frames. Deferred because (a) coverage-scanner fix is what actually blocks 4.6b.2, and (b) if the 200-turn per-agent ceiling is fixed later, the absolute wall-time payoff shrinks proportionally.

##### Phase 4.6b.2 — F-1b cold run under realigned topology

**Entry conditions:** 4.6b.0, 4.6b.1, 4.6b.1a, 4.6b.1b, 4.6b.1c, and 4.6b.1d all committed. 4.6c.A–E is **not entered before 4.6b.2.** The scope filter stays default-off. Neither the SA prompt nudge (former 4.6c.B) nor the attribution rubric (former 4.6c.C) is required up front — the classifier + SA split subsumes them. The rubric is still required before the run, but its categories are the classifier's output schema, not a hand-drafted list.

**Tasks:**
- [ ] **4.6b.2.i** Commit `docs/pipeline-run-reports/phase-gates/2026-04-08-phase-4.6b-f1b-attribution.md`: the rubric is now literally the classifier's output categories, with one line per category describing what it means for F-1b specifically. Pre-registered before the run.
- [ ] **4.6b.2.ii** Reset budget-brain-v2 to clean state.
- [ ] **4.6b.2.iii** Run `dark-factory run 2026-03-28-api-bootstrap` under the realigned topology with `enable_scope_filter=False`. Monitor cost against the 03:07 baseline tripwire.
- [ ] **4.6b.2.iv** Classify the outcome against the committed rubric. No post-hoc edits.
- [ ] **4.6b.2.v** Commit the run report.

##### Phase 4.6b.3 — Decision gate based on F-1b outcome

| Outcome | Criteria | Next phase |
|---|---|---|
| **SHIP** | F-1b ships end-to-end under its iteration budget | → Phase 5. Mark 4.6c and 4.6d as retired. Delete `scope_filter.py` and its wiring in a follow-up commit — it was the wrong abstraction; the right fix was topological. |
| **CLASSIFIER-IMPROVED-NO-SHIP** | F-1b escalates but classifier output shows markedly cleaner signal vs. the 03:07 baseline (no hallucinated CRASH counts, consistent BRITTLE/ENV_ISSUE tagging, stable across iterations) AND the escalation cause is a genuine impl issue the classifier tagged `IMPL_BUG` ≥2 iterations running | → the pipeline is now producing honest signal; the blocker is implementer capability on this spec shape. Escalate to user for a spec-scoping conversation. |
| **SA-SPLIT-IMPROVED-NO-SHIP** | F-1b escalates but SA split produced tests that Test Engineer → AC-anchored, QA-Holdout → adversarial-only, with zero cross-contamination AND at least one previously-failing out-of-manifest pattern no longer appears | → genuine progress; iterate on spec scoping with the user. |
| **4.5a-PATHOLOGY (n=2)** | F-1b escalates AND classifier still tags ≥1 failing test as `OUT_OF_SCOPE` (meaning: even a dedicated classifier agent sees the scenario as out-of-manifest) | → this is the n=2 evidence the meta-rule 5 requires. Proceed to Phase 4.6c.A (flag scope filter on) as a targeted enforcement on the classifier's `OUT_OF_SCOPE` bucket. |
| **SURFACE-COMPLEXITY** | F-1b escalates with CRASH / `/tmp` / subprocess / timing-assert failures | → route to a new Phase 4.6b.4 scenario linter targeting only the observed patterns (not speculative rules). |
| **REGRESSION** | F-1b does worse than the 03:07 baseline, OR agent orchestration breaks, OR classifier/SA split produces incoherent output | → rollback 4.6b.0 + 4.6b.1, escalate to user for replan. |

**Cost ceiling:** 4.6b.0 + 4.6b.1 are both code-only changes bounded by unit tests. 4.6b.2 is one F-1b run. Tripwire = 2× the 03:07 F-1b cost. Additional runs (PATHOLOGY → 4.6c.A, SURFACE-COMPLEXITY → 4.6b.4) each require separate user go-ahead.

##### Phase 4.6b.4 — Scenario linter (CONTINGENCY, formerly primary 4.6b.1)

**Only entered if 4.6b.3 routes here.** The rules I originally drafted (`R1` spawnSync, `R2` detached-tmp, `R3` HTTP grammar) are kept verbatim as a contingency, but they are *not* the primary path any more. The primary path is the topology fix, not the symptom linter.

- [ ] **4.6b.4.i** (deferred, see original Phase 4.6b.1 draft in git history for the rule set if entered)

#### Phase 4.6c — Locked-denominator scope filter (BUILT, DEFAULT-OFF, DEMOTED — contingency after 4.6b.3)

**Demotion note.** The realigned Phase 4.6b (classifier extraction + SA split) is now the primary path and runs before 4.6c. 4.6c is entered only if 4.6b.3 routes to the `4.5a-PATHOLOGY (n=2)` branch — meaning even a dedicated Defect Classifier agent tags failing tests as `OUT_OF_SCOPE` and the pipeline still fails to ship. At that point the scope filter becomes a targeted enforcement on the classifier's explicit `OUT_OF_SCOPE` bucket rather than a freestanding filter, which resolves the original "mutating Oracle inputs" objection: the classifier already owns the classification, and the filter just consumes one of its buckets.



**Origin.** Phase 4.5a ran F-U1 to 8/8 SHIP-side preconditions but Oracle NO-SHIPed because 4 SA-invented out-of-set scenarios (1 `[adversarial]`, 3 mislabeled `[AC-8]`) were treated as ship-blockers despite the locked AC set committed in `2d6916d`. Initial plan: build a deterministic filter between the quality gate and the Oracle that reclassifies out-of-scope failing tests and overrides `scenario_fail_count` / `scenario_total` / `pass_rate` before the floor check and Oracle see them.

**Status: built, unit-tested, NOT committed to the pipeline wiring.**

- `darkfactory/pipeline/scope_filter.py` (~280 lines, pure functions):
  - Rule 4.6c.1 — `partition_failing_by_scope`: drop failing tests whose `[AC-N]/[FR-N]/[EC-N]` tag isn't in the spec's AnchorManifest, or which carry no tag.
  - Rule 4.6c.2 — `check_literal_input_membership`: extract literal args to the detected subject function and reclassify tests whose literals aren't in the spec markdown. Property-style exemption (>3 distinct literals).
- `tests/unit/test_scope_filter.py` — 14 tests using verbatim F-U1 iter2 failing names; end-to-end test confirms all 4 known failures reclassify out-of-scope. 14/14 green.
- Wired into `_step_gate` in `iteration.py`; `IterationState` gained `scenario_fail_count_raw`, `scenario_total_raw`, `pass_rate_raw`, `out_of_scope_failing`.

**Why this is not being committed as-is.** After 4.5a, adversarial review by Liotta and neo (and an explicit self-adversarial review of the "revert + SA linter" recommendation that followed) converged on the following findings:

1. **4.6c mutates Oracle decision inputs.** The existing gate→Oracle chain does monotone summarization (aggregation, satisfaction score, floor check). 4.6c reclassifies *individual scenario outcomes*, changing the input set the Oracle reasons over. That is a category difference, not an aesthetic one. Reference-impl Validator sanitizes presentation but doesn't edit counts; 4.6c does edit counts. "Result laundering" is the accurate description.
2. **4.6c.2 was designed against n=1.** F-U1 has an exhaustive §5.5 literal table; CRUD specs (F-1b, everything Yassine shipped) don't. On those specs 4.6c.2 either no-ops or misfires by eating legitimate adversarial inputs (SQLi strings, oversized payloads, unicode). The >3-literal property exemption is a hack, not a principle.
3. **"Fix structurally upstream" via an SA linter is also n=1 speculative infrastructure.** The reference impl shipped 7 CRUD features with no SA linter and no scope filter. Prior should be "the untouched pipeline is closer to the reference than either proposed intervention."
4. **An SA *prompt nudge* is n=1-defensible.** A mechanical SA *linter tool* is not. Conflating them was part of the original framing error.
5. **Keeping the code default-off strictly dominates reverting it.** scope_filter.py + tests are ~300 lines of working pure functions. Flagging the wiring off preserves optionality for one config toggle away, and lets the F-1b run produce clean empirical signal.

**Revised goal for 4.6c.** Do not ship the filter as a committed pipeline intervention. Ship it as a config-gated capability that is default-off, so that a future n=2 failure can re-enable it with a single toggle and empirical justification instead of doctrine.

##### Phase 4.6c.A — Gate the filter behind a default-off config flag

- [ ] **4.6c.A.i** Add `enable_scope_filter: bool = False` to `PipelineConfig` (or equivalent). No TOML surface required yet; code-level default only.
- [ ] **4.6c.A.ii** Wrap the `_apply_scope_filter(spec_run, iter_state, gate_result, project_dir)` call in `_step_gate` with `if config.enable_scope_filter:`. When false, gate_result and iter_state pass through untouched — identical behavior to the pre-4.6c pipeline.
- [ ] **4.6c.A.iii** Verify the `IterationState.*_raw` fields still default to 0 and are only populated when the filter runs. Downstream consumers (trajectory analysis, oracle prompt builder, reports) must not read `*_raw` when the filter is disabled.
- [ ] **4.6c.A.iv** Confirm `scope_filter.py` + `test_scope_filter.py` remain green. These are kept as-is.
- [ ] **4.6c.A.v** Commit: "Gate 4.6c scope filter behind default-off config flag (pending n=2 evidence)".

##### Phase 4.6c.B — SA prompt nudge (no linter, no tool)

- [ ] **4.6c.B.i** Add one concise sentence to `agents/scenario-architect.md` (or the SA prompt template) instructing the SA to never tag a test `[AC-N]` unless the test body exercises AC-N's actual predicate, and to use `[adversarial]` for cross-cutting robustness tests. Near-zero cost, defensible from n=1 as a prompt nudge.
- [ ] **4.6c.B.ii** Commit separately from 4.6c.A so its effect on F-1b is attributable.

##### Phase 4.6c.C — Attribution rubric (pre-registered, committed before F-1b run)

**Binding requirement.** Before running F-1b, commit a short rubric that pre-classifies the expected failure modes so the post-run report produces a real signal instead of a narrative.

- [ ] **4.6c.C.i** Write `docs/pipeline-run-reports/phase-gates/2026-04-08-phase-4.6c-f1b-attribution.md` with:
  - **4.5a defect recurring** — Oracle escalates with stable failures that, by the spec's AnchorManifest, shouldn't block ship. Signal: failing test names carry tags not in the manifest, or literal inputs clearly outside any spec section.
  - **SA mislabeling (subtype of above)** — failing tests tagged `[AC-N]` whose body does not exercise AC-N's stated predicate.
  - **Genuine impl bug** — failing test carries a valid in-manifest tag, body matches the predicate, the implementation is wrong.
  - **Surface-complexity failure** — CRASH, `/tmp` scripts, subprocess tests, timing asserts, log format asserts. Same class 4.5a was designed to isolate.
  - **Novel failure mode** — anything that doesn't fit the four above. Triggers a halt and replan before any further intervention.
- [ ] **4.6c.C.ii** Commit the rubric before starting the F-1b run. Post-hoc edits to the rubric are a plan failure.

##### Phase 4.6c.D — Run F-1b cold (default-off filter + SA prompt nudge only)

- [ ] **4.6c.D.i** Reset budget-brain-v2 to clean state.
- [ ] **4.6c.D.ii** Run `dark-factory run 2026-03-28-api-bootstrap` with `enable_scope_filter=False`. The only live changes vs. the 03:07 baseline are the SA prompt nudge and any downstream fixes already shipped in Phase 3.6.
- [ ] **4.6c.D.iii** Classify the outcome against the Phase 4.6c.C rubric. Do NOT edit the rubric to fit the outcome.
- [ ] **4.6c.D.iv** Commit the run report with explicit rubric attribution.

##### Phase 4.6c.E — Decision gate based on F-1b outcome

| Outcome | Criteria | Next phase |
|---|---|---|
| **SHIP** | F-1b ships end-to-end under its iteration budget | → Phase 5. Delete 4.6c.A wiring entirely in a follow-up (code is unused); keep scope_filter.py as backlog for a future classifier redesign if ever justified. 4.6c was premature; 4.5a was a diagnostic that produced actionable negative evidence. |
| **4.5a-PATHOLOGY (n=2)** | F-1b escalates AND the rubric attributes ≥1 stable failure to SA mislabeling or out-of-manifest tags (same smoking gun as F-U1) | → re-enable the filter via `enable_scope_filter=True`, re-run F-1b once, observe whether it ships. If it ships on the second pass, 4.6c is empirically justified and becomes a committed feature with a migration plan. If not, escalate to 4.6d. |
| **SURFACE-COMPLEXITY** | F-1b escalates with CRASH / `/tmp` / subprocess / timing-assert failures | → the 4.5a diagnosis ("surface complexity dominates") is reconfirmed on a CRUD target. 4.6c is not the right fix. Route to Phase 4.6b.1 scenario linter (real category of bug, deterministic rules derivable from the observed pattern). |
| **GENUINE-IMPL-BUG** | F-1b escalates with in-scope failures that are real implementation defects | → the pipeline is working correctly; the blocker is the implementer model's capability on this spec shape. Escalate to user for a spec-scoping conversation. Neither 4.6c nor 4.6b is the right move. |
| **NOVEL** | Failure mode doesn't fit the rubric | → halt. Replan. Do not patch. |

**Cost ceiling:** ≤1 F-1b run in 4.6c.D + optional 1 re-run in the 4.5a-PATHOLOGY branch of 4.6c.E. Tripwire = 2× the 03:07 F-1b cost.

**Meta-rule reinforcement.** The fifth binding meta-rule is softened from the original draft: *"Any intervention between the quality gate and the Oracle that changes the input set the Oracle reasons over (not just its summary) requires n≥2 empirical justification from distinct specs, pre-registered attribution, and a committed rollback path. A single failure does not justify committing such an intervention."* This is the lesson from 4.6c's build-then-defer cycle.

#### Phase 4.6d — Non-mutating Defect Classifier (RETIRED — promoted into Phase 4.6b.0)

**Retired.** The Defect Classifier is no longer a contingency behind 4.6c. It has been promoted to **Phase 4.6b.0** as the first-class primary-path move, ahead of both the SA split and the scope filter. The reasoning in the earlier 4.6d framing was correct ("this is the 11th Yassine agent we don't have"), but its positioning was wrong — it was drafted as a safety net for a scope filter, when it is actually the structural fix that makes the scope filter unnecessary. See Phase 4.6b.0 above.

##### Phase 4.6e — Closed-set deterministic tests (CONTINGENCY, superseded priority)

**Liotta's hidden 5th option.** Compile §5.5 wire contracts + EC table into deterministic test files (parallel to Tier-1 contracts), and restrict SA to property-style scenarios only (no LLM-invented literal inputs). Removes the structural ability to invent comma-edge cases entirely.

**Entry conditions:** Phase 4.6d lands AND a subsequent pipeline run still produces SA literal-input invention failures on a real spec. Lower priority than 4.6d now that the filter is default-off rather than committed — the classifier is the cheaper contingency to reach first.

#### Phase 4.7 — Measurement infrastructure (CONTINGENCY, not roadmap)

**This phase is deleted from the plan if 4.5/4.6a/4.6b.1 ships the spec.** It is NOT a planned step. It exists only as a documented prerequisite in case we later decide we need the quarantine bucket. Entering Phase 4.7 requires explicit user approval to re-enter the plan.

Requirements (if ever entered):
- Fixed Oracle eval set: 10-20 hand-labeled historical failures with ground-truth classifications. Oracle scored against this every run; drift > 5pp blocks ship.
- Reviewer calibration against human labels; agreement ≥70%.
- Failure-mode variance productionized as a per-run metric.

#### Phase 4.8 — Quarantine ship gate (CONTINGENCY, not roadmap)

**This phase is deleted from the plan if 4.5/4.6a/4.6b.1 ships the spec.** It is NOT a planned step. Re-entering this phase requires explicit user approval.

If ever entered, it must include:
- `quarantined_ratio < 0.10` hard cap
- **AC-blocking rule**: any AC with ≥1 quarantined verifying scenario blocks ship regardless of ratio
- `defects.md` written into the Architect's **input manifest** (structural, not sidecar)
- Oracle drift detector (from 4.7.1) green
- Post-ship human audit: any hidden bug discovered in a quarantined scenario → quarantine rolled back, replan

### Phase 5 — Ship

Whatever path gets us here. The spec ships with a documented pipeline-state report showing: which validation gates were passed, each gate's committed `phase-gate.json` hash, the effective pass rate, and the per-AC coverage table.

- [ ] **5.1** Confirm shipping run artifact includes all committed phase-gate hashes.
- [ ] **5.2** Update plan with final outcome summary and link to the successful run report.

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

---

## Phase 4.6b.1h — Oracle feedback-writer must consume defects-iterN.json

**Trigger:** Run 11 (2026-04-09 05:17 UTC) revealed the Oracle feedback-writer
never received structured defect classifications, producing feedback disconnected
from the authoritative defect classifier output.

**Root cause (two bugs):**

1. **Pipeline bug:** `_step_oracle` in `darkfactory/pipeline/iteration.py:1698-1714`
   constructs the Oracle prompt pointing at four files (spec, review, gate summary,
   environment.toml). It never includes `defects-iter{N}.json`. `agents/oracle.md`
   Input #9 says to read it, but the orchestrator never tells the agent where to
   look. The Oracle therefore has no structured defect data.

2. **Data fidelity bug:** The Oracle is pointed at `quality-gate-summary.md` which
   is a bare PASS/FAIL table with no counts, no failure detail, no pass rates per
   gate. When the Oracle sees "Self-Tests: FAIL / Blind Scenarios: FAIL", it
   confabulates explanations ("missing vitest", "missing @eslint/js") contradicted
   by the detailed `quality-gate.md`. The pass rate IS in the prompt text but the
   Oracle gives more weight to the FAIL/FAIL in the summary table.

**Run 11 evidence:**
- Defect classifier: 1 IMPL_BUG (AC-1 structured JSON logging) every iteration.
- Gate: 97% blind scenarios (33/34), Self-Tests 94% (80/85). vitest clearly ran.
- Oracle iter1 feedback: "Missing test infrastructure, Self-Tests FAIL" — hallucinated.
  Classified 11 categories including gate names, not spec sections.
- Oracle iter2 feedback: "Missing @eslint/js, missing vitest" — demonstrably false.
  **Flipped AC-1 from IMPL_BUG to ENV_ISSUE.** The implementer was sent fixing
  deps that were already working for 3 iterations; never told about AC-1 log format.
- Oracle violated agents/oracle.md line 208: "this is a mechanical lookup, not a
  classification decision" — by inventing classifications wholesale.

**Impact:** The feedback loop is broken. Every run with IMPL_BUG defects produces
feedback the implementer can't act on. Pipeline successes happen despite the Oracle,
not because of it. This class of bug defeats the entire iteration loop.

### Tasks

- [x] **1h.i** Add defects-iterN.json path to Oracle prompt in `_step_oracle`
  (`iteration.py:~1714`). One line:
  ```python
  f"Defect classifications: /project/.dark-factory/reports/"
  f"{spec_run.spec_name}-defects-iter{spec_run.iteration}.json. "
  ```

- [x] **1h.ii** Inline defect classifications summary in the Oracle prompt
  (belt + suspenders — matches codebase precedent: crash names inlined at
  1740-1749, server error at 1753-1767, stable failures at 1770-1796, anchor
  gaps at 1839-1856). Read defects JSON from disk, format a terse per-defect
  summary (test_name, category, rationale truncated to ~100 chars) directly
  into the prompt text. This ensures the Oracle sees the data even if it
  doesn't reliably read the file. Guard: if defects file missing or `error`
  non-null, include a notice that classification is unavailable. Size guard:
  if defects array exceeds 50 entries, truncate and add "... and N more
  (see file for full list)".

- [x] **1h.iii** Inline scenario counts from `iter_state` into a gate-outcome
  summary in the Oracle prompt. `iter_state` already has
  `scenario_pass_count`, `scenario_fail_count`, `scenario_total`,
  `scenario_crashed`, and `hard_gates_pass`. Format as:
  ```
  Gate outcomes: hard gates PASS, Blind-Scenarios FAIL (33/34, 1 crashed).
  ```
  NOTE (per neo review): individual self-test pass/fail counts are NOT
  available in any structured form today — `GateResult` only tracks
  `hard_gates_pass` as a boolean. Settle for `Self-Tests FAIL` without
  counts for now. Adding self-test count plumbing is out of scope.
  Do NOT parse files — `iter_state` is already populated from `gate_result`
  at iteration.py:1336-1345.

- [x] **1h.iv** Unit tests:
  - Test `_step_oracle` prompt includes defects JSON path.
  - Test `_step_oracle` prompt includes inlined defect summary when defects
    file exists.
  - Test `_step_oracle` prompt includes gate-outcome counts.
  - Test graceful degradation when defects file is missing or has error.

- [x] **1h.v** Commit as single commit referencing Run 11 as trigger.

### Non-goals

- NOT changing `agents/oracle.md` — the agent definition already correctly
  specifies Input #9 and the mechanical rollup rule. The Oracle would do the
  right thing if the orchestrator gave it the data.
- NOT adding a post-hoc feedback consistency validator (YAGNI if the Oracle
  receives authoritative data). Revisit if feedback quality remains poor
  after this fix.
- NOT switching from `quality-gate-summary.md` to `quality-gate.md` in the
  file path — the detailed report can be very long. Inlining counts is
  cheaper and more reliable.

---

## Out of scope for this plan

- Manifest-command dispatcher refactor (backlog).
- Consumer-lint implementation (backlog).
- Implementer-side install behavior (implementer's `:rw` container already handles its own install; Bug #6 is about the gate container specifically).
- Full language-matrix unit test suite for quality-gate.sh (`dark-factory gate` on existing checkouts is the honest smoke test; synthetic fixtures would rot).
