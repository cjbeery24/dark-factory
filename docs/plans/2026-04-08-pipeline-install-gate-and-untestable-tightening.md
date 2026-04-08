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
- [ ] **4.6b.0.v** Remove classification-population code paths from the Oracle output parser. `classification_counts`, `escalation_category`, `failure_feedback_iterations` are now populated from the classifier's JSON, not the Oracle's prose.
- [ ] **4.6b.0.vi** **GATE (unit-test level):** existing Oracle tests split into (a) classifier-output tests that run against fixture `defects.json` + gate result → expected SHIP/NO-SHIP, (b) classifier tests that run against fixture failing tests → expected JSON output. Both green before any pipeline run.
- [ ] **4.6b.0.vii** Bug #9 sub-check: cross-validate the classifier's `SCENARIO_CRASH` count against `gate_result.scenario_crashed`. Mismatch is a classifier error, not an Oracle disagreement. Log + fail the iteration.
- [ ] **4.6b.0.viii** Linus review.
- [ ] **4.6b.0.ix** Commit. Does not touch scope_filter.py or the SA.

##### Phase 4.6b.1 — Scenario Architect role split (Test Engineer + QA-Holdout)

**Only after 4.6b.0 commits and the unit-test gate is green.** The two splits are sequenced so attribution is clean: if F-1b behavior changes after 4.6b.0 commit, that delta is attributable to the classifier split alone. Subsequent F-1b behavior after 4.6b.1 is attributable to the SA split.

**Goal.** Match Yassine's topology: one agent writes the visible test suite the Developer can read, constrained to AC predicates. A separate agent writes adversarial blind holdouts the Developer cannot read. Separate agent definitions, separate context windows, parallel invocation.

**Tasks:**
- [ ] **4.6b.1.i** Add `AgentRole.TEST_ENGINEER` and `AgentRole.QA_HOLDOUT` to `darkfactory/models/agent.py`.
- [ ] **4.6b.1.ii** Write `agents/test-engineer.md` — owns AC-aligned visible tests. The Implementer *can* read these. Strict rule: every test must exercise an AC/FR/EC predicate from the spec's AnchorManifest. No adversarial exploration.
- [ ] **4.6b.1.iii** Write `agents/qa-holdout.md` — owns adversarial blind holdouts in `.dark-factory/scenarios/<spec>/holdout/`. The Implementer cannot read these (barrier already covers this). Explicitly encouraged to explore inputs the spec doesn't enumerate — SQL injection, oversized payloads, unicode edge cases, boundary values. Tests are tagged `[adversarial]` or `[holdout]`, never `[AC-N]`.
- [ ] **4.6b.1.iv** Split barrier configs in `docker/barriers.py`: Test Engineer has same barrier as current SA but sees the spec's AnchorManifest explicitly. QA-Holdout has the same barrier plus an additional read of the spec §5.5 table (if present) for legitimate literal inputs, but is forbidden from reading source code (unchanged from SA).
- [ ] **4.6b.1.v** Update `_step_scenarios` in `iteration.py` to invoke both agents in parallel (asyncio or sequential — parallelism is cost optimization, not correctness). Merge outputs into the scenarios directory.
- [ ] **4.6b.1.vi** Update the Implementer's barrier config: `test-engineer` output path is `:ro`-readable; `qa-holdout` output path remains masked.
- [ ] **4.6b.1.vii** Retire `agents/scenario-architect.md` (move to `agents/_retired/` with a pointer comment).
- [ ] **4.6b.1.viii** **GATE (unit-test level):** scenario-generation unit tests updated so Test Engineer fixtures produce only AC-tagged tests and QA-Holdout fixtures produce only `[adversarial]` or `[holdout]` tests. Cross-contamination is a test failure.
- [ ] **4.6b.1.ix** Linus review.
- [ ] **4.6b.1.x** Commit.

##### Phase 4.6b.2 — F-1b cold run under realigned topology

**Entry conditions:** 4.6b.0 and 4.6b.1 committed. 4.6c.A–E is **not entered before 4.6b.2.** The scope filter stays default-off. Neither the SA prompt nudge (former 4.6c.B) nor the attribution rubric (former 4.6c.C) is required up front — the classifier + SA split subsumes them. The rubric is still required before the run, but its categories are the classifier's output schema, not a hand-drafted list.

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

## Out of scope for this plan

- Manifest-command dispatcher refactor (backlog).
- Consumer-lint implementation (backlog).
- Implementer-side install behavior (implementer's `:rw` container already handles its own install; Bug #6 is about the gate container specifically).
- Full language-matrix unit test suite for quality-gate.sh (`dark-factory gate` on existing checkouts is the honest smoke test; synthetic fixtures would rot).
