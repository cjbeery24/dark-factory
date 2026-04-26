# Dark Factory Forward Plan

> Captures the path from the current state (post-Stage-4-follow-up) to a
> framework-grade architecture that supports both greenfield and
> brownfield project adoption. Decisions are drawn from the
> 2026-04-25 architecture review thread (§4a enforcement bug →
> densify-vs-TE asymmetry → adapter catalogue depreciation → adapter
> layering violation → barrier-integrity verification → tooling scope).
>
> Reviewed by: Claude (assistant) + neo (independent). Approved by:
> Caleb.
> Last updated: 2026-04-25.

## Where we are

- §4a `forbidden_test_shapes` enforcement landed at gate phase (`scripts/lib/scenario_lint.py` + `quality-gate.sh:1337-1341`) but doesn't bite on database-foundation because:
  - **H2 (primary)**: §4a regexes target SQL syntax; TE emits TypeScript. 0/0 hits in iter-2 archive.
  - **H3 (secondary)**: Lint failures wrote a FAIL row but didn't gate. **Reverted in `5130935`** (one-line change to align with validator-bridge principle).
- Adapter content under `darkfactory/adapters/postgres/` mixes **library-wire knowledge** (universal) with **project conventions** (budget-brain-v2-specific) — the layering violation neo and Claude reached consensus on.
- Empirical barrier check confirmed QA Holdouts cannot read contracts (`barriers.py:128`: `qa_masked = [*source_dirs, _SCENARIOS, _GIT]`); the contracts-in-project move does not introduce a new barrier hole.
- dark-factory is committed to the **framework path** (Caleb confirmed). Two use cases:
  - **Greenfield**: `/df-write-plan` → `/df-write-spec`/`/df-densify-spec` → `dark-factory run`
  - **Brownfield**: setup tooling → `/df-write-spec`/`/df-densify-spec` → `dark-factory run`

## Where we need to go

A framework that:

1. Does not gate the Implementer on test-shape concerns (TE-phase enforcement instead).
2. Does not encode any one project's conventions in framework-shipped code.
3. Has narrow, foot-gun-focused tooling for project-side helper authoring (not a five-phase population pipeline).
4. Has explicit deprecation/retirement signals for content that depreciates with model capability.
5. Has process safeguards (authorization model) that prevent the "tactical fix promoted to gating change" failure mode that produced `d2fecbf`.

## Plan structure

Four phases, dependency-ordered. Phase boundaries are decision points:
do not enter the next phase until the current phase's exit criteria are
met.

---

## Phase 1 — Authorization model + barrier hardening (1 week)

**Status (2026-04-25): COMPLETE.** All Phase 1 deliverables landed in a single commit on branch `experiment/phase-4-product-owner`. See "Phase 1 completion notes" below for the two scope deviations made under the authorization model itself.

**Goal**: Prevent the next `d2fecbf`-class mistake. Lock in the
process and protect the barrier with a regression test before any
larger restructuring lands.

| Item | Description | Estimate | Status |
|---|---|---|---|
| **B3** | `docs/AUTHORIZATION-MODEL.md` with two triggers (revised, see notes below): (a) **audience escalation** (string previously read only by humans/Oracle/Reviewer becomes read by barrier-restricted agent — severity escalation is the canonical mechanism, folded in as a sub-bullet), (b) **changed-surfaces declaration** (commits touching `barriers.py`, `quality-gate.sh` report rows, or scenario-lint output formatting must declare audience in commit body). Includes `d2fecbf` worked example. Referenced from CLAUDE.md. | 0.5 day | DONE — 116 lines |
| **B-bar-test** | `tests/unit/test_barriers.py` extended (file already existed) with one new test class `TestPhase1BarrierInvariants` and a Phase 1 anchor comment. Pins all three barrier invariants; the `_SCENARIOS in implementer_masked` assertion was inverted to its correct form (see notes below). | 0.5 day | DONE — 3557 passed (was 3556) |

**Exit criteria**: AUTHORIZATION-MODEL.md is referenced from CLAUDE.md and from agent prompts that perform tactical fixes. Barrier regression test (3 assertions) passes. Plan committed as `dark-factory/docs/ROADMAP.md` (mechanical). **MET** — CLAUDE.md callout at top of file; agent-prompt references deferred to Phase 2 (where TE pre-acceptance authoring picks up the doc anyway).

### Phase 1 completion notes (deviations from spec, made under authorization model)

1. **Three triggers → two triggers (B3).** First-pass review found that the proposed "severity escalation" trigger was a strict subset of "audience escalation" — both fired identically on the `d2fecbf` worked example. Severity escalation is now scoped as the canonical mechanism inside the audience-escalation rule, not a separate trigger. The mechanical "did your diff change which strings end up in a barrier-readable directory?" test replaces the previous "if you have to read barriers.py, the change qualifies" heuristic. Two triggers do the same protective work as three with less ambiguity.

2. **`_SCENARIOS in implementer_masked` (B-bar-test) was wrong.** Phase 4.6b.1 design intentionally exposes the Test Engineer's visible AC-aligned scenarios to the Implementer as TDD targets (`barriers.py:55-73`). Pre-existing `test_implementer_masks_holdouts_not_visible_scenarios` already pins the correct form (`_SCENARIOS NOT in implementer_masked` AND `_HOLDOUTS in implementer_masked`). The new `TestPhase1BarrierInvariants` class adds only the missing third invariant (`_SCENARIOS in qa_masked`); a Phase 1 anchor comment above the existing test cross-references the other two so ROADMAP.md and AUTHORIZATION-MODEL.md can point at them. Net: +1 new test, +9 lines of anchor docstring.

**Why this phase first**: cheap, atomic, prevents recurrence of the
exact failure that motivated this whole thread. Nothing in later
phases blocks on it, but every later phase benefits from the
process backstop.

---

## Phase 2 — Move §4a enforcement to TE pre-acceptance phase (3-4 weeks)

**Goal**: Fix the architectural home of `forbidden_test_shapes`
enforcement. The Implementer should never see a §4a lint failure;
TE retries on §4a hits before scenarios cross the barrier.

**Architectural constraint**: `te_pre_acceptance_check` runs **on the
host outside the TE container**, reading scenarios from the host
filesystem. It does NOT modify the TE container's mount manifest.
This avoids collision with Phase 3's `barriers.py` / orchestrator
container-mount changes (B9d), keeping the two phases on disjoint
code paths.

| Item | Description | Estimate |
|---|---|---|
| **B1** | New pipeline phase `te_pre_acceptance_check` between Scenario Architect emission and gate prep. Host-side process invokes `scenario_lint.py --spec-path <spec>` against fresh scenarios. On hit: TE retries (separate retry budget from Implementer iteration cap). On TE-budget exhaustion: pipeline aborts with classification `scenario_author_retry_exhausted`. Audit to `.dark-factory/audit/te-acceptance-<spec>-iter<n>.json` (already masked from Implementer). | 2 weeks |
| **B1-cls-audit** | Audit existing classification/abort routing surfaces to confirm `scenario_author_retry_exhausted` flows correctly through: (a) abort routing (the `1230234d`-run abort→archive routing gap from `project_pipeline_unresolved_bugs.md`), (b) Oracle decision logic (does TE-exhaustion count as no-ship or abort?), (c) `analyze_trial.py` classifier, (d) failure-archive serializer (Phase D serializer gap, same memory). Sub-item of B1 — debt we already know about from prior incidents. | (included in B1's 2 weeks) |
| **B2** | Sanitize Scenario Lint report row at gate phase → count-only summary; verbose body to `.dark-factory/audit/scenario-lint-<spec>-iter<n>.txt`. Audit dir already masked from Implementer. Defense-in-depth even after B1: built-in lint rules still write to gate report. | 0.5 weeks |
| **B8** (moved from P5) | CI test asserting Implementer's container view of `.dark-factory/reports/<latest>` contains no string matching any `forbidden_test_shapes` regex from active specs OR any built-in foot-gun regex from B5's library (when B5 lands; until then, the framework's existing `_PEER_CLIENT_GUC_READ_RE` etc.). This is the regression test for B2's sanitization work — belongs here, not in P5. | 0.5 weeks |
| **B-tests** | Unit tests: TE retry budget bookkeeping; phase-transition state machine; classification taxonomy for `scenario_author_retry_exhausted` (cross-checked against B1-cls-audit findings); gate-report sanitization format. **Plus**: update `df-pipeline-run` skill to know about the TE pre-acceptance phase (it surfaces in pipeline events the skill consumes). | 0.5 weeks |

**Exit criteria**: A spec with deliberately-violating §4a patterns (canonical fixture) triggers TE retry, not gate failure. Gate report contains no test-shape information regardless of lint outcome. All existing pipelines (postgres + non-postgres specs) continue to ship under the new flow. B1-cls-audit findings either fixed or filed as separate tickets with explicit rationale.

**Why second**: B1+B2+B8 together close the validator-bridge weakness flagged by neo. Phase 1's authorization model is the *process* safeguard; this phase is the *structural* fix.

**Risk**: Phase ordering change is the highest-risk item in the plan. Required mitigations:
- Feature flag: `TE_PRE_ACCEPTANCE_CHECK = false` by default for the first release; flip to true after one full pipeline run on a representative spec.
- Roll back is one config flip, not a code revert.

---

## Phase 3 — Adapter restructuring (3-4 weeks)

**Goal**: Move project conventions out of the framework. Universal
helpers stay; project-convention helpers move to
`<project>/.dark-factory/contracts/<adapter>/project/`. Framework
ships starters, never magic defaults.

| Item | Description | Estimate |
|---|---|---|
| **B9d** | Project-side staging infrastructure. New ABC method `Adapter.project_contracts_dir()` defaults to `<project>/.dark-factory/contracts/<adapter_name>/`. New orchestrator code path that mounts project-contracts read-only into TE container under `_contracts/<adapter>/project/` (path-namespacing, not extends-merge — collisions structurally impossible). QA Holdout mask unchanged. **Owns all `barriers.py` / TE-mount-manifest changes for this plan** (Phase 2's pre-acceptance check is host-side, by design, to avoid collision). Integration testing across both adapter-shipping and adapter-empty cases. | 2 weeks |
| **B9a** | Migration of three convention helpers. **Compressed 2-minor-version clock** (vs. governance doc's 3-minor for live framework code) — convention helpers ship as starter templates, not as live framework code, so are not subject to the same governance: <ul><li>v(N): introduce project-side staging path (B9d); ship convention helpers as starter templates under `templates/contracts/postgres/project-starters/`. Live framework continues shipping the helpers; `dark-factory contracts init` (P4) copies starters into `<project>/.dark-factory/contracts/<adapter>/`.</li><li>v(N+1): framework deletes convention helpers from live code. Universal three remain. Project-side staging path now load-bearing.</li></ul> Resolves the "second postgres-stack project mid-migration" failure mode: any new adopter at v(N+1) gets starters via `init`, not a stale DeprecationWarning pointing at someone else's project. | 1 week (spread over 2 minor releases) |
| **B9b** | `retire_after: <model-version>` field on universal helpers in framework. Automated re-eval at each model bump: each helper has a canonical-violation fixture; framework runs vanilla prompt against new model; if N/N pass, auto-open removal PR. | 1 week |
| **B9e** | Audit other adapter content for layering violations: `mechanism-tokens.toml`, `substrate-rules.toml`, the agent addenda. Classify each entry as universal vs project-specific. Migrate project-specific to project. | 0.5 weeks |

**Exit criteria**: Universal three helpers (`assertUnsetGucIsNullish`, `assertTxGucRoundTrips`, `assertRlsEnabled`) remain in framework. Convention three live in budget-brain-v2's contracts dir. CI test confirms staging mounts both paths into TE container, neither into Implementer. `retire_after` runner exists; first run produces a baseline. **Migration test**: budget-brain-v2's database-foundation spec ships under the new flow (full pipeline run, end-to-end) — this is the budget-brain-v2-specific checkpoint that confirms the migration didn't break the in-flight target project.

**Why third**: depends on Phase 1's authorization model (this is a multi-file structural change that should pause-and-ask if it grows scope). Can run in parallel with Phase 2 once both kick off, since they touch different code paths (Phase 2: orchestrator + lint; Phase 3: adapter staging).

---

## Phase 4 — Tooling + validators (2-3 weeks)

**Goal**: Build the contracts-population tooling at the right scope
(narrow silent-wrong-detection, not five-phase pipeline). Plus the
spec-side validators that prevent regex-dialect drift at authoring
time.

| Item | Description | Estimate |
|---|---|---|
| **B4** | Round-trip validator for `forbidden_test_shapes` regex migrations. New spec-validator rule: each pattern in §4a must include either a `canonical_violation` field (TS code snippet the regex MUST match) or a fixture reference (`templates/forbidden-shape-fixtures/<pattern_name>.ts`). Validator runs each regex against its fixture; spec rejected if no match. Optional `canonical_clean` field for negative tests. | 1 week |
| **B5** | Shared regex library with dialect tags. `scripts/lib/foot_gun_patterns.py` with `{name, ts_pattern, sql_pattern, py_pattern, reason, fix_hint}`. `scenario_lint.py` (built-in rules) + `analyze_trial.py:KNOWN_BUGS` + `/df-densify-spec` import from it. Single source of truth for foot-gun shapes. | 1 week |
| **Contracts CLI v0** | `dark-factory contracts init <adapter>` copies a starter set into `<project>/.dark-factory/contracts/<adapter>/`. Per-adapter README documents the helper list. Loud-wrong helpers (5 of 6 for postgres) are README-driven; developer hand-writes from copy-paste examples. | 0.5 weeks |
| **Contracts CLI v1** | `dark-factory contracts verify <adapter>` runs the developer's helpers against canonical foot-gun fixtures (defined per silent-wrong helper). Rejects helpers that don't catch the foot-gun. Same pattern as B4, applied to project-side helpers. | 1 week |
| **B6** | database-foundation §4a regex correction via `/df-write-spec` (failing-report path). Patterns rewritten for TS idiom: allow `[A-Z_]+!?`, replace SQL operators with `=== !== toBe(...)`, widen span limits. Routes through skill, not direct edit. | 0.5 days (mechanical) |

**Exit criteria**: B4+B5 land. database-foundation §4a regexes correctly bite (verified against canonical TS fixture). Contracts CLI v0+v1 work end-to-end on budget-brain-v2 (greenfield bootstrap simulation: delete contracts dir, run `init` + `verify`, run pipeline). Silent-wrong fixture catches a deliberately-buggy helper.

**Why fourth**: B4+B5 require Phase 3's adapter restructuring (foot-gun knowledge needs a clean home). Contracts CLI requires Phase 3's project-side staging path. B6 requires B4 (round-trip validator must pass before re-entering pipeline).

**Brownfield-specific tooling deferred**: `dark-factory contracts inventory <adapter>` (read project source, draft `contracts.toml` profile, walk developer through review/edit) — defer to v2 once we have signal on whether the v0+v1 flow is sufficient. Building inventory tooling before adoption signal is over-engineering. See "Open questions deferred → Brownfield onboarding tooling depth" below for the explicit follow-up.

---

## Phase 5 — Defense in depth + runtime introspection (2-4 weeks, optional)

**Goal**: Long-term architectural moves that retire content rather
than maintain it. Optional in the sense that Phases 1-4 are sufficient
for framework correctness; Phase 5 is the "what does dark-factory look
like in 2 years" investment. (B8 was originally listed here; moved to
Phase 2 since it's the regression test for B2's sanitization work,
not a defer-able future-improvement.)

| Item | Description | Estimate |
|---|---|---|
| **B7** | Audit other report-row surfaces for latent H4-style leaks (rule names, assertion messages embedded in any artifact the Implementer reads). Sweep deliverable + per-surface fixes. | 1-2 weeks |
| **B9c** | Runtime introspection prototype on `assertTxGucRoundTrips`. Orchestrator runs the probe at substrate-prep time, captures result, injects as TE-prompt fact. If it works, helper retires entirely. | 1 week |
| **B9c-extension** | Generalize runtime introspection across the wire-only helper category. By the end of this phase, the framework's contract catalog should be ~50% smaller than at Phase 4 exit. | 1-2 weeks |

**Exit criteria**: At least one helper retired entirely via runtime introspection. Other-surface leak audit produces a written report with concrete fix-or-mask recommendations per surface.

**Why fifth**: this is the architectural future, not the architectural present. Land Phases 1-4 first; let real adoption signal decide whether B7/B8/B9c-extension justify the effort.

---

## Decisions captured

These are the foundational decisions reached during the review thread.
Recorded so future readers don't re-derive them:

1. **dark-factory is a framework, not a forkable script.** Two use cases: greenfield (write-plan → write-spec → run) and brownfield (setup tooling → write-spec → run). Resolved 2026-04-25.

2. **Project conventions belong in the target project, not the framework.** The decisive argument is the deprecation clock — adapter helpers are public surface under semver, so changing `app.tenant_id` → `app.org_id` should not require a framework release. Path-namespaced staging (framework helpers + project helpers in separate subdirs of `_contracts/<adapter>/`) avoids extends-merge silent-override risk.

3. **Validator-bridge sanctity is non-negotiable.** No code change may convert a row read by Oracle/Reviewer into a row read by the Implementer without explicit authorization (severity-escalation trigger of AUTHORIZATION-MODEL.md). The `d2fecbf` commit was a violation; the revert (`5130935`) restores intent.

4. **Adapter content depreciates unevenly across 4 categories.** Library-wire mechanics (cat 3) decays with model capability and gets a `retire_after` pin. Cross-library collisions (cat 2) decay slowly because library-pair cross-products grow. Project conventions (cat 1) and determinism-as-architecture (cat 4) never decay.

5. **Tool the foot-guns, not everything.** Silent-wrong helpers (~5 across the foreseeable catalog) get tooling via canonical-violation fixtures. Loud-wrong helpers (~15) stay README-driven.

6. **QA Holdouts are end-to-end barrier-isolated from the contracts surface.** Verified empirically. The "contracts as new barrier hole" concern was wrong on closer examination. The contracts-in-project move can proceed.

7. **Pre-acceptance retry budgets are per-phase, not shared.** TE retry budget for §4a hits is separate from the Implementer iteration cap. Each agent owns its own retry quota; the orchestrator routes failures to the agent that owns them. Generalizes beyond §4a — any future pre-acceptance check (architecture validation, holdout-author validation) gets its own budget.

8. **Path-namespacing beats extends-merge for adapter content composition.** Where adapter content composes (framework defaults + project overrides + per-spec additions), separate physical paths beat any merge-with-precedence scheme. Collisions are structurally impossible at different paths; merge schemes invite silent overrides that hide framework-version drift. Applies beyond contracts — to mechanism-tokens, substrate-rules, agent addenda, and any future per-spec adapter customization.

## Open questions deferred

- **Brownfield onboarding tooling depth**: do we need `dark-factory contracts inventory` (read project source, draft `contracts.toml` profile, walk developer through review/edit)? Defer until Phase 4's v0+v1 flow ships and adoption signal lands.
- **Adapter governance for `retire_after` PRs**: who reviews auto-opened removal PRs for retired helpers? Defer to Phase 3 implementation.
- **Per-stack-tuple naming**: postgres adapter is secretly a `postgres × postgres.js × drizzle × vitest` stack adapter. Renaming is honest but breaks user expectations. Defer until a second postgres-stack project demonstrates the conflict.

## Sequencing summary

```
Phase 1 ────────────────────────────────────────────► (1 week)
        AUTHORIZATION-MODEL.md + barrier test

Phase 2     ──────────────────────────────► (3-4 weeks)
            §4a → TE pre-acceptance + lint sanitization + B8 leak-regression test

Phase 3     ──────────────────────────────────► (3-4 weeks, parallel with Phase 2)
            Adapter restructuring + project-side staging

Phase 4                                ────────────► (2-3 weeks)
                                       Validators + contracts CLI

Phase 5                                          ────────────► (2-4 weeks, optional)
                                                 Defense-in-depth + runtime introspection
```

Phase 2 and Phase 3 can run in parallel after Phase 1 lands. Phase 4
requires both. Phase 5 is optional and can lag indefinitely.

## Total estimate

Updated estimates after neo's review (B1 bumped to 2 weeks for classification-taxonomy audit; B9d bumped to 2 weeks for integration testing; B8 moved into Phase 2; deprecation clock compressed from 3-minor to 2-minor):

- **Minimum viable** (Phases 1-4): ~10-15 weeks calendar (with parallelism on Phases 2+3).
- **Architectural ideal** (Phases 1-5): ~13-20 weeks calendar.

Wide range reflects the dependency-graph complexity and the
brownfield-tooling-depth open question.
