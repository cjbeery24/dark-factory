# Pipeline Run Report — 2026-04-08 16:42 UTC

**Repo:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Topology:** Phase 4.6b.1 (TE + QA Holdout split), enable_scope_filter=False
**Baseline:** 03:07 F-1b attribution baseline
**Rubric:** docs/pipeline-run-reports/phase-gates/2026-04-08-phase-4.6b-f1b-attribution.md (not yet committed — noted as rubric gap; see Observations)

## Context

First F-1b cold run under the realigned TE + QA Holdout topology from Phase 4.6b.1.v. Prior SA-based run at 2026-04-07 23:57 is the last known baseline for comparison. The run is scoped as 4.6b.2 in `docs/plans/2026-04-08-pipeline-install-gate-and-untestable-tightening.md`, but per user instruction we are running it before 4.6b.2.i (rubric pre-registration). Documenting this as a deviation.

## Run 1 — run_id `e43c1cff482f4719`

### Pre-run setup

- `git restore .` + `git clean -fdx -e .dark-factory -e node_modules` → removed `apps/api/dist/`, `apps/api/src/common/`, `apps/api/src/server.ts`, `apps/api/tests/`, `apps/api/vitest.config.ts`, `packages/shared/dist/`, `packages/shared/src/parse-amount.ts`
- Wiped `.dark-factory/{scenarios,holdouts,reports,audit,state,failures,events.jsonl}` and the entire `.dark-factory/architecture/environment.{toml,deltas}` (per prompt — bootstrap delta regenerates on next run)
- `find … __pycache__` cleared
- `dark-factory init --update` — agents in `budget-brain-v2/.dark-factory/agents/` now include `test-engineer.md` and `qa-holdout.md`, no `scenario-architect.md` (4.6b.1.vii took effect)
- `pip install -e ~/sites/dark-factory` re-ran editable install
- `dark-factory build node` — all layers CACHED, image `dark-factory-playwright-node:latest` rebuilt
- No orphan `df-run-id` containers or `df-*` networks

### Pipeline outcome: **ABORT AT STEP 1 (spec validation)** — run never reached TE / QA Holdout

State file:

```
spec=2026-03-28-api-bootstrap
run_id=e43c1cff482f4719
spec_hash=ff42f734228fdcb4ba029324ca695c00e4b16bb412dc06b89fa16181ce1ed293
status=interrupted
iteration=1
completed_step=0
```

`completed_step=0` — the pipeline never advanced past spec validation. Zero TE invocations, zero QA Holdout invocations, zero signal on 4.6b.1.v behavior under F-1b.

### Spec validator verdict

```
verdict=FAIL
score=78
divergence=MEDIUM
blockers=2
```

Terminal error: `ERROR: error: spec validation failed: verdict=FAIL (expected PASS)`

The validator itself called it **"CONDITIONAL PASS"** in prose (score 78/100, MEDIUM divergence, 2 critical blockers) — the orchestrator treats anything short of a clean PASS as FAIL and aborts.

The two blockers (from `reviews/2026-03-28-api-bootstrap-validation.md`):

1. **ZodError Transformation Precision Gap** (FR-3 / AC-5, lines 50, 234, 251) — spec does not specify how nested-path ZodErrors (`path: ["users", 0, "address", "zipCode"]`) transform into the required `{field: string, message: string}` shape. Different implementers will diverge.
2. **F-1a Dependency Implementation Issues** — the validator claims the upstream F-1a (monorepo tooling) has `pnpm lint` and `pnpm typecheck` failures despite being stamped PASS earlier, and concludes F-1b cannot be implemented on top of a broken F-1a. **This is notable** because our run scoped `depends-on: F-1a` as an ID-form that "cannot be resolved outside a plan context" and ignored it for the standalone run (see stdout warning) — so the validator is reading current on-disk state for F-1a, not relying on the orchestrator's plan context. The validator's finding is therefore a live snapshot of whatever F-1a debris survived `git restore` + `git clean` (i.e. nothing, since we deleted `apps/api/dist/`, `apps/api/src/common/`, etc. in setup). Worth double-checking whether the validator is reading stale shipped-scenarios or metrics artifacts under `.dark-factory/` we did not wipe.

Non-blocker issues the validator also flagged (documented here because they will surface again on re-run unless the spec is amended): CORS production config gap, `apps/api/src/server.ts` vs `index.ts` file-structure mismatch, sensitive-data redaction scope, hardcoded rate limit, LOG_LEVEL optional-vs-default ambiguity, 404 log level, health-check timeout gap.

### Resource usage (Run 1, spec validator only)

```
AGENT_COMPLETED spec-validator
  turns: 200
  exit_code: 0
  model_id: claude-sonnet-4-20250514
  est_input_tokens: 800000
  est_output_tokens: 400000
  wall_seconds: 481.52
```

**200 turns** and **~1.2M tokens** burned on spec validation alone — the validator appears to have hit the per-agent turn ceiling (200 is suspiciously round). Exit code 0 from Claude but a FAIL verdict in the report — the validator finished with an opinion, the orchestrator rejected it.

### Observations / findings

1. **We have no F-1b attribution signal from this run.** Phase 4.6b.1.v ships TE + QA Holdout wiring; this run aborted before the TE step was ever invoked. The run is a **no-op against the F-1b rubric** — it cannot be classified PROGRESS / SOFT-PROGRESS / NO-PROGRESS against the 03:07 baseline because the pipeline never reached scenario generation, implementation, or gate. The rubric (4.6b.2.i, not yet committed) is not needed for this report.
2. **Blocker #1 (ZodError precision gap) is a genuine spec defect** — not a pipeline regression. The spec needs amendment before any 4.6b.2 attempt can produce a clean F-1b run. This is the same class of failure the prior 2026-04-07 23:57 run hit — spec-level ambiguity around FR-3.
3. **Blocker #2 (F-1a "implementation issues") is suspicious** — we wiped all of F-1a's on-disk debris in setup. Either the validator is reading an artifact directory we missed (most likely: `.dark-factory/metrics.jsonl`, `shipped-scenarios/`, or `reviews/` from a prior run that we intentionally preserved), or the validator is speculating about F-1a state from the spec's prose. Investigation TODO: re-read validator prompt, confirm whether `reviews/` / `shipped-scenarios/` / `metrics.jsonl` are mounted into the spec-validator container and whether it legitimately needs them.
4. **200 turns / 1.2M tokens on spec validation is concerning as a cost profile.** If this is the new per-run floor just for validation, we are burning meaningful budget on a step that should be deterministic. Flag for the cost review.
5. **Spec validator FAIL is a "real breaking issue" (criterion: pre-flight refuses to launch).** Per step 8, aborting here is correct; per step 10, we are not fixing the spec.

### Diagnostic sources checked

- **State file** (above)
- **Events JSONL** — only `SPEC_START`, one `AGENT_START` (spec-validator), one `AGENT_COMPLETED` (spec-validator). No `SMOKE_COMPLETED`, no gate events, no barrier-echo events.
- **Container logs** — no `reports/{spec}/*.log` because the pipeline never reached any step that writes them
- **Quality gate summary** — does not exist for this run
- **Audit log** — `audit/2026-03-28-api-bootstrap-e43c1cff482f4719.log` exists; only spec-validator entries
- **Validation status / report** — inspected above

### Bug reports (for future fix sessions)

- **BUG-?: Spec validator burns 200 turns / 1.2M tokens per validation.** Likely the validator is doing unbounded exploration instead of a bounded checklist sweep. Revisit validator prompt + tool budget.
- **BUG-?: Spec validator reports "F-1a has implementation issues" on a freshly-reset repo.** Either a real mount leak or a prompt-level hallucination about upstream features the validator shouldn't be judging. Needs investigation.
- **BUG-?: Spec 2026-03-28-api-bootstrap FR-3 nested-path ZodError transformation is genuinely underspecified.** This is a spec bug, not a pipeline bug, but it permanently blocks F-1b under the current validator gate. The spec needs an amendment (exact transformation algorithm for nested paths + array indices) before 4.6b.2 can produce signal.

## Run 2 — run_id `862e056510c246b5` (standalone `dark-factory validate`, same spec content)

User re-ran `dark-factory validate 2026-03-28-api-bootstrap` without modifying the spec. `spec_hash` identical to Run 1 (`ff42f734228fdcb4ba029324ca695c00e4b16bb412dc06b89fa16181ce1ed293`). Second-run verdict:

```
verdict=PASS
score=88
divergence=LOW
blockers=0
```

State: `completed_step=1`, `status=interrupted` (expected — `validate` is step 1 only).

### The important finding: **spec validator is non-deterministic on identical input**

| Run | Score | Divergence | Blockers | Verdict |
|---|---|---|---|---|
| 1 | 78 | MEDIUM | 2 | FAIL |
| 2 | 88 | LOW | 0 | PASS |

Ten-point score swing, divergence classification shifted one tier, blocker count dropped from 2 → 0, verdict flipped FAIL → PASS — on a byte-identical spec, ten minutes apart, in fresh containers.

### Reframed findings from Run 1

- **Blocker #1 (ZodError precision gap) was most likely a hallucination.** Run 2 did not flag FR-3 / AC-5 at all. The precision gap the Run 1 validator "found" does not reproduce.
- **Blocker #2 (F-1a has implementation issues) was definitely a hallucination.** Nothing on disk changed between Run 1 and Run 2; there is no way a real F-1a state delta could explain Run 2's PASS.
- **The earlier cost observation (200 turns / 1.2M tokens) is worse than I wrote it up.** The validator is not just expensive — it is sampling a different blocker list on each invocation. Two runs cost ~2.4M tokens and produced contradictory verdicts.

### Upgraded bug reports

- **BUG (NEW, HIGH): Spec validator is non-deterministic on byte-identical input.** Flipped FAIL → PASS across two runs, ten minutes apart, same `spec_hash`. Root cause likely the validator's open-ended 200-turn exploration sampling different critique paths each run. This directly undermines every downstream gate: the orchestrator treats validator verdict as a boolean precondition for the entire pipeline, so a non-deterministic validator means every pipeline run is a coin flip on whether it starts at all. Fix candidates: (a) pin `temperature=0` in the validator's model call if not already, (b) replace open-ended review with a bounded deterministic checklist sweep, (c) run the validator ≥2x and require unanimity before accepting a verdict.
- **BUG (UPGRADED): Spec validator hallucinates blockers.** The F-1a claim in Run 1 could not correspond to any on-disk state. This is not just "maybe a mount leak" — it is the validator fabricating upstream-feature failures. Prompt needs a hard constraint: the spec validator MUST NOT judge upstream features, only the spec file's own text.
- **Previously-filed bug about FR-3 ZodError gap:** downgrade from "real spec defect" to "unreproducible validator finding, probably a hallucination." The spec may still benefit from a nested-path algorithm for clarity, but it is not what blocked Run 1.

### Conclusion

**Run 1 produced no usable 4.6b.2 data.** The pipeline aborted at step 1 on a preexisting spec defect that has nothing to do with the 4.6b.1.v TE / QA Holdout split. Phase 4.6b.1.v is therefore **not validated by this run** — its code path was never executed on the live pipeline. We cannot claim SHIP, NO-SHIP, PROGRESS, or REGRESSION against the F-1b baseline. The correct next action per the plan is either (a) amend the spec to close blocker #1, reset, and re-run; or (b) run the new topology against a less blocker-heavy spec first (e.g. `2026-04-08-shared-parse-amount`) to get a smoke-test of the wiring itself, then return to F-1b once the spec is amended. Option (b) is explicitly *not* 4.6b.2 but would give us end-to-end coverage of the 4.6b.1.v rewrite before staking an F-1b attribution run on it.

## Run 3 — run_id `7f255b9614a84bec` (full pipeline re-attempt after Run 2 PASS)

Per user "yes", re-ran full `dark-factory run 2026-03-28-api-bootstrap` after Run 2 demonstrated the spec validator would now PASS. Spec content and `spec_hash` unchanged from Runs 1/2.

### Timeline

| Time (UTC) | Event | Wall (s) | Turns |
|---|---|---:|---:|
| 17:08:25 | SPEC_START | — | — |
| 17:08:25 → 17:11:38 | spec-validator (PASS, score 95, 0 blockers) | 192.2 | 200 |
| 17:11:38 → 17:16:30 | qa-holdout generate (5 holdout files + NOTES.md) | 292.1 | 200 |
| 17:16:30 → 17:19:26 | test-engineer generate | 175.3 | 200 |
| 17:19:26 → 17:24:25 | test-engineer validate | 298.7 | 200 |
| 17:24:25 | COVERAGE_GAP — all 23 anchors (FR-1..4, AC-1..10, EC-1..9) uncovered | — | — |
| 17:24:29 → 17:27:59 | test-engineer coverage_regen attempt 1 | 210.2 | 200 |
| 17:27:59 | COVERAGE_GAP — all 23 still uncovered | — | — |
| 17:27:59 → 17:31:35 | test-engineer coverage_regen attempt 2 | 215.7 | 200 |
| 17:31:35 | **COVERAGE_FLOOR_VIOLATION** — pipeline abort | — | — |

Final state: `completed_step=1, status=interrupted`. Terminal error: `scenario coverage floor violation: anchors with zero scenarios after 2 regen attempts`.

### What Run 3 validated

- **4.6b.1.v wiring is mechanically correct end-to-end.** QA Holdout invoked first, wrote to `.dark-factory/holdouts/<spec>/`. Test Engineer invoked second (generate → validate → regen), wrote to `.dark-factory/scenarios/<spec>/`. Barrier masking, sequencing, idempotency, and the new `_invoke_qa_holdout` step all behaved as designed. No SA invocations. This is the first live confirmation that the orchestrator rewrite works.
- **Spec validator non-determinism reconfirmed and widened.** Same `spec_hash` as Runs 1/2, third verdict (PASS, score 95, 0 blockers). Wall time 192s vs Run 1's 481s — non-determinism is not just on verdict but on cost. Three runs on identical input: FAIL/78/2 → PASS/88/0 → PASS/95/0. Score ceiling drifted upward each time.

### The blocking regression: TE output format ↔ coverage scanner mismatch

After two successful TE invocations and two regen attempts (~900s of TE wall time), the coverage scanner reported **zero anchors covered** — not "some covered, some gaps", but literally every FR/AC/EC anchor unmatched. This is not a TE quality failure; TE wrote 6 scenario files with correct `[FR-N]` / `[AC-N]` / `[EC-N]` bracket tags in their `# Scenario:` headers. It is a scanner/format mismatch.

**Root cause** (confirmed by reading `darkfactory/scenarios/coverage.py`):

- `agents/test-engineer.md` instructs TE to tag scenarios inline, e.g.:
  `# Scenario: CORS request from allowed origin succeeds [FR-1]`
- `_extract_anchors_from_markdown_scenario` in `darkfactory/scenarios/coverage.py` uses:
  ```
  _MARKDOWN_REQUIREMENT_RE = re.compile(r"(?m)^\s*#\s*Requirements?\s*:\s*(.+)$", re.IGNORECASE)
  ```
  This matches only `# Requirement: FR-1` or `# Requirements: FR-1, AC-2` as the header text itself. It does **not** match bracketed tags inside other headers. So every `.scenarios.md` file TE produced parsed as zero anchors.
- The scanner's code-file path (`_extract_anchors_from_text` + `_DESCRIBE_CALL_RE`) *does* honor bracket tags inside `describe(...)` / `it(...)` / `test(...)` string literals, which is likely why the format looked correct to whoever wrote the TE agent prompt — the mismatch is specifically at the markdown scenario-file parser.

This is a **4.6b.1 regression**. The retired Scenario Architect produced markdown files using `# Requirement: FR-N` headers (matching the regex). The new Test Engineer prompt switched to bracketed headers without updating the scanner. Coverage regen cannot recover from this — the agent is following its prompt correctly, the scanner can never see any anchors, and attempts burn ~200 turns apiece for zero progress.

### Implications for 4.6b.2

- Run 3 produced **no F-1b attribution signal.** The pipeline aborted at the coverage floor gate before implementation ever ran. TE / QA Holdout split cannot be evaluated against the 03:07 baseline from this run.
- Phase 4.6b.2 (F-1b cold run) is **blocked** until the format mismatch is resolved. This is not a spec bug and not a 4.6b.2 scope item — it is a 4.6b.1 regression that must be fixed before any 4.6b.2 attempt can generate signal.
- The plan item 4.6b.1.v still counts as shipped: the orchestrator rewire is correct. What is broken is the contract between `agents/test-engineer.md` and `darkfactory/scenarios/coverage.py`, which is a separate surface.

### Cost profile (Run 3 only)

~6 agent invocations × 200 turns × ~1.2M tokens each ≈ **~7.2M tokens burned** for zero pipeline progress. The spec-validator 200-turn ceiling observed in Run 1 is not an anomaly — every agent in Run 3 hit 200 turns. This suggests the 200-turn ceiling is either the per-agent hard cap or every agent is exploring to exhaustion.

### Upgraded / new bug reports

- **BUG (NEW, HIGH, 4.6b.1 REGRESSION): Coverage scanner cannot parse TE output format.**
  - Location: `darkfactory/scenarios/coverage.py::_extract_anchors_from_markdown_scenario` and `_MARKDOWN_REQUIREMENT_RE`.
  - Symptom: Every `.scenarios.md` file TE writes parses as zero anchors, causing COVERAGE_FLOOR_VIOLATION after max regen attempts regardless of TE output quality.
  - Fix options: (a) update `agents/test-engineer.md` to emit `# Requirement: FR-N` headers (match old SA format); (b) extend `_extract_anchors_from_markdown_scenario` to also scan bracket tags in `# Scenario:` / `# Test:` headers and/or body text; (c) both. Option (b) is more forgiving and matches what code-file scanning already supports.
  - Blocks: all 4.6b.2 work and any pipeline run on a spec that uses a markdown-native test engineer.

- **BUG (UPGRADED, HIGH): Spec validator non-determinism — third data point.** Three runs on `spec_hash=ff42f734228fdcb4ba029324ca695c00e4b16bb412dc06b89fa16181ce1ed293`: FAIL/78 → PASS/88 → PASS/95. Wall time also non-deterministic (192s–481s). The validator is effectively a stochastic classifier on identical input.

- **BUG (NEW, MEDIUM): Every agent hits the 200-turn ceiling.** spec-validator, qa-holdout, test-engineer (generate), test-engineer (validate), and both regen attempts all reported `turns: 200`. Either the cap is too low, the agents are under-constrained and exploring to exhaustion, or both.

### Conclusion

**Phase 4.6b.1.v is mechanically validated but 4.6b.1 as a whole is not shippable.** The orchestrator correctly invokes TE and QA Holdout in sequence with the right barriers; the TE-produced scenario files are structurally well-formed; the QA Holdout files are written to the correct location. But the new TE format is invisible to the coverage scanner, so every pipeline run will deterministically abort at the coverage floor gate. No amount of TE quality improvement will fix this — it is a parser contract bug.

**Recommended next actions** (not executed, per this session's document-don't-fix mandate):

1. Fix the TE format ↔ coverage scanner mismatch (extend the scanner to accept bracket tags — safest).
2. Land spec validator determinism fix (`temperature=0` or bounded checklist prompt) before any further F-1b cold runs.
3. Re-attempt Run 4 against the same spec (no content changes needed) to finally collect F-1b signal under the 4.6b.1 topology.
4. Only then proceed to 4.6b.2.i (rubric pre-registration) and the actual F-1b cold run.

Three runs, ~10M tokens total, zero F-1b attribution data. The 4.6b.1.v wiring is correct but the surrounding scaffolding (validator determinism, coverage parser) is blocking us from seeing whether the wiring improves outcomes.

## Run 4 — run_id `92ae798fab604b04` (after coverage scanner fix landed)

Re-run after committing `darkfactory/scenarios/coverage.py` fix (commit `6bf2cf2`) that extended `_extract_anchors_from_markdown_scenario` to recognize `@covers` tags and bracket-tagged headers (`# Scenario: ... [FR-1]`). Fresh reset per protocol: `git restore` + `git clean`, wiped all `.dark-factory/{scenarios,holdouts,reports,audit,state,failures,events.jsonl,architecture,precheck}` and prior validation/review artifacts, `dark-factory init --update`, editable reinstall, `dark-factory build node`.

### Timeline

| Time (UTC) | Event | Wall (s) | Turns |
|---|---|---:|---:|
| 18:03:25 | SPEC_START | — | — |
| 18:03:26 → 18:05:11 | spec-validator (PASS) | 105.0 | 200 |
| 18:05:12 → 18:07:48 | architect | 156.0 | 200 |
| 18:07:50 → 18:15:50 | qa-holdout generate | 480.2 | 200 |
| 18:15:51 → 18:19:15 | test-engineer generate | 204.5 | 200 |
| 18:19:16 → 18:24:05 | test-engineer validate #1 | 289.1 | 200 |
| 18:24:05 → 18:29:46 | test-engineer validate #2 | 340.9 | 200 |
| 18:29:46 | COVERAGE_GAP attempt 1 — uncovered: FR-3, EC-1, EC-4, EC-5, EC-8 (5/23) | — | — |
| 18:29:48 → 18:36:53 | test-engineer coverage_regen attempt 1 | 424.4 | 200 |
| 18:36:53 | **COVERAGE_PASS** — all 23 anchors covered | — | — |
| 18:37:19 → 18:42:09 | implementer iter 1 | 290.8 | 200 |
| 18:42:09 → 18:43:16 | server smoke check iter 1 — **failed** (ERR_MODULE_NOT_FOUND) | ~67 | — |
| 18:43:16 → 18:44:46 | GATE_COMPLETED — 0% pass, exit 2 | ~90 | — |
| 18:44:46 | **ABORT:** scenario infrastructure failure — 11 scenario files exist, 0 scenarios executed | — | — |

Final state: `completed_step=2, status=interrupted, phase=review`. Terminal error: `scenario infrastructure failure: 11 scenario file(s) exist but 0 scenarios were executed — likely a format mismatch or runner crash.`

### What Run 4 validated

- **Coverage scanner fix works.** First run of this spec that ever reached the coverage floor gate and passed it. Scanner correctly credited anchors from TE's `# Scenario: ... [FR-N]` / `@covers FR-N` format. First attempt: 18/23 covered. Regen closed the remaining 5 in a single pass.
- **4.6b.1.v topology reaches implementer.** First pipeline run against this spec (across all four runs) where the implementer was ever invoked. Phase 4.6b.1 wiring is now empirically validated end-to-end through the scenario-generation surface.
- **Spec validator non-determinism data point #4: PASS, 105s** — fastest yet. Three of four runs PASS; Run 1 remains the only FAIL. The earlier framing of "validator non-determinism blocks attribution" is further weakened — it's a rare false-FAIL, cost-wasteful but not project-blocking.

### The new blocker: scenario runner cannot execute TE output

After implementer iteration 1, the smoke test and quality gate aborted with:

> `ERROR: scenario infrastructure failure: 11 scenario file(s) exist but 0 scenarios were executed — likely a format mismatch or runner crash.`

Scenario files in `.dark-factory/scenarios/2026-03-28-api-bootstrap/`:
```
cors-rate-limiting.scenarios.md
ec-1-port-validation.scenarios.md
ec-4-undefined-routes.scenarios.md
ec-5-nested-field-redaction.scenarios.md
ec-8-rate-limit-exceeded.scenarios.md
error-handling.scenarios.md
fr-3-error-handling.scenarios.md
health-endpoint.scenarios.md
logging-security.scenarios.md
server-startup.scenarios.md
COVERAGE-GAPS.md
```

All 10 functional scenario files are markdown (`.scenarios.md`). The quality-gate scenario runner expects executable test files (`.spec.ts`, `.test.ts`, etc.) and parses zero test cases out of markdown. This is a **4.6b.1 design-contract gap**, not a bug in the fix we just landed:

- The old Scenario Architect emitted the same markdown format, but under the pre-split flow the Implementer was responsible for translating those spec-style scenarios into executable tests as part of implementation.
- The new Test Engineer role is supposed to emit **AC-aligned visible tests** that the Implementer can read (`agents/test-engineer.md`), i.e. these should be executable test files, not spec-style markdown.
- Either TE is following an inherited Scenario Architect prompt pattern that mis-specifies the output format, or the orchestrator still configures TE to emit markdown, or both. Either way, the contract between TE and the scenario runner is broken.

There is also a separate, smaller implementer bug:

- **Implementer wrote `dist/index.js` that imports `dist/server.js`, but did not emit `dist/server.js`.** `ERR_MODULE_NOT_FOUND` at server smoke. This is not a pipeline bug — it's a single-iteration implementer mistake that would normally be caught and corrected on iteration 2. The iteration loop never got to iteration 2 because the scenario runner aborted first.

### Cost profile (Run 4)

~9 agent invocations × 200 turns × ~1.2M tokens ≈ **~10.8M tokens**. Same pattern as Runs 1–3: every agent hits the 200-turn ceiling. The cost-ceiling observation from earlier runs is reconfirmed; no agent in any run has finished under budget.

### Bug reports

- **BUG (NEW, HIGH, 4.6b.1 REGRESSION): Test Engineer emits markdown scenario files that the scenario runner cannot execute.**
  - Location: contract between `agents/test-engineer.md` (prompt telling TE what to produce) and the scenario runner invoked by the quality gate.
  - Symptom: markdown `.scenarios.md` files exist under `.dark-factory/scenarios/<spec>/`, but the runner parses zero executable tests and aborts with "11 scenario files exist but 0 scenarios were executed".
  - Root cause (likely): TE's prompt is modeled on the former Scenario Architect's markdown spec format (Format A / Format B from the retired SA docs). Under the old flow, the Implementer wrote the real executable tests; under 4.6b.1 the TE is supposed to emit the executable tests directly (see plan item 4.6b.1.ii which explicitly calls them "AC-aligned visible tests under `.dark-factory/scenarios/<spec>/`").
  - Fix options: (a) rewrite `agents/test-engineer.md` to require executable `.test.ts` / `.spec.ts` files, not markdown; (b) extend the scenario runner to execute markdown scenarios by translating them at runtime (fragile, NOT recommended); (c) reintroduce an intermediate "translate markdown to executable tests" step (adds a role, against the direction of 4.6b.1's simplification).
  - Blocks: all 4.6b.2 work. Every pipeline run will deterministically abort at the gate until this is resolved.

- **Deferred (implementer iter 1 defect, not a pipeline bug):** implementer wrote `dist/index.js` importing an un-emitted `dist/server.js`. Expected to self-heal on iteration 2, but the pipeline never got there. Not filed as a pipeline bug because it's exactly the kind of first-pass error the iteration loop exists to correct.

### Conclusion

**The coverage scanner fix did its job.** The pipeline made meaningful forward progress for the first time under 4.6b.1: scenario coverage crossed, implementer ran, gate ran. The next blocker is one layer deeper — TE's output format is incompatible with the quality gate's scenario runner.

**4.6b.2 remains blocked**, but the remaining work is now well-scoped: fix the TE prompt (or runner contract) so TE produces executable tests. Once that lands, Run 5 should be the first run capable of producing F-1b attribution signal.

**Net progress across Runs 1–4:** step 1 (Runs 1–2) → step 1 PASS + failed coverage floor (Run 3) → step 1 PASS + coverage floor PASS + implementer + failed runner format (Run 4). Each run surfaces exactly one more blocker. If that pattern holds, Run 5 should either ship or reveal the next layer.

---

## Run 5 — spec-validator non-determinism FAIL

### Pre-run setup

- Committed TE prompt rewrite (`0ef83b6` — native executable test contract, `.test.ts` / pytest / etc.; explicit ban on `.scenarios.md` Given/When/Then prose). Templates updated to reflect TE as the new visible-test producer.
- Same reset protocol as prior runs.
- `dark-factory init --update` + `pip install -e`.

### Pipeline outcome: **ABORT AT STEP 1 (spec validation)**

Spec-validator returned FAIL with "unresolved contracts" on FR-1 (CORS_ORIGIN type, rate-limit field shapes) and FR-4 (HealthCheck timeout). **Same spec_hash** as Runs 2–4 which all passed the validator. Non-deterministic.

Validator determinism dataset now 5 points on identical spec_hash: FAIL/78 · PASS/88 · PASS/95 · PASS/95 · FAIL. Per earlier scoping decision (most runs pass; treat as occasional waste, not a project-blocker), re-ran as Run 6 without intervention.

### Bug reports

- **No new bug.** Re-confirmation of known spec-validator non-determinism.

---

## Run 6 — run_id `baxa3bssl`

### Pipeline outcome: **ABORT AT TE VALIDATION (step 1, iter 1)** — `status=interrupted, completed_step=1`

### Timeline

| Step | Duration | Result |
|---|---|---|
| spec-validator | 166s | PASS |
| architect | 145s | PASS |
| qa-holdout | 493s | PASS |
| test-engineer generate | 321s | PASS — emitted 6 `.test.ts` files (`cors.test.ts`, `server-startup.test.ts`, `error-handling.test.ts`, `rate-limiting.test.ts`, `logging.test.ts`, `health-endpoint.test.ts`) + `COVERAGE-GAPS.md` per the new 4.6b.1.v contract |
| test-engineer validate #1 | 273s | FAIL |
| test-engineer validate #2 | 323s | FAIL |
| test-engineer validate #3 | — | FAIL → abort |

### Root cause: **4.6b.1.v contract gap in `darkfactory/scenarios/validator.py`**

TE correctly produced `.test.ts` files on the first generate call. The scenario validator then rejected them because of this hard-fail gate at `darkfactory/scenarios/validator.py:288`:

```python
if has_api_contracts and not markdown_files:
    check = CheckResult(
        name="behavioral_scenarios_required",
        passed=False,
        detail=(
            "Architecture manifest has api_contracts and a test server is "
            "configured, but no .scenarios.md files were generated. "
            "HTTP endpoints require behavioral scenarios in Format A."
        ),
    )
```

Mechanics:

1. TE emits 6 native executable `.test.ts` files (correct per new prompt).
2. Validator sees `has_api_contracts=true` + zero `.scenarios.md` → hard FAIL: "HTTP endpoints require behavioral scenarios in Format A."
3. Retry feedback tells TE "produce Format A `.scenarios.md` files."
4. TE complies on retry, emits 5 narrative markdown files (`error-handling.scenarios.md`, `health-endpoint.scenarios.md`, `server-startup.scenarios.md`, `logging-security.scenarios.md`, `edge-cases.scenarios.md`).
5. `_validate_markdown_only` runs the `parseable` check (looking for `# Scenario:` Given/When/Then blocks at `validator.py:449-477`) → FAIL, because the new TE prompt **explicitly forbids** Given/When/Then prose.
6. TE is wedged between two mutually-exclusive contracts. After 3 attempts → abort.

The new TE prompt and the existing scenario validator enforce **mutually exclusive output formats**. TE cannot pass validation on any spec with `has_api_contracts=true`, regardless of how correctly it follows its instructions.

### Bug reports

- **BUG (NEW, HIGH, 4.6b.1 REGRESSION): `darkfactory/scenarios/validator.py` still enforces the retired Scenario Architect's Format A contract for HTTP API specs.**
  - Location: `darkfactory/scenarios/validator.py:287-301` (`behavioral_scenarios_required` check) and `darkfactory/scenarios/validator.py:449-477` (`parseable` check in `_validate_markdown_only`).
  - Symptom: TE produces the expected native executable `.test.ts` files per 4.6b.1.v. Validator rejects them with "HTTP endpoints require behavioral scenarios in Format A." Retry loop forces TE into an unwinnable oscillation between the two formats.
  - Root cause: the 4.6b.1.v rewrite updated `agents/test-engineer.md` and `templates/project-claude-addition.md` but did not update `validator.py`. The `has_api_contracts` gate is a holdover from the SA/http-scenario-runner world where HTTP APIs required Format A markdown.
  - Fix options:
    - (a) Delete the `behavioral_scenarios_required` gate entirely — TE now writes executable tests that hit the API via the project's native test framework (supertest/fastify-inject/pytest-httpx/etc.). The coverage scanner (fixed Run 4) already checks that API contracts have anchor coverage; this gate is duplicative.
    - (b) Gate conditional on a topology signal distinguishing http-scenario-runner from native test runner. Signal does not currently exist.
    - (c) Extend validator to accept `.test.ts` / `*.test.*` / `test_*.py` etc. as satisfying `has_api_contracts` coverage — effectively (a) with an explicit allowlist.
  - **Recommendation: (a).** Anchor coverage is now the correct place to enforce API contract testing, not file-extension heuristics.
  - Blocks: all 4.6b.2 work. Every pipeline run on a spec with `has_api_contracts=true` will deterministically abort at TE validation until resolved.

### Cost profile (Run 6)

~8 agent invocations (spec-validator + architect + qa-holdout + TE generate + 3× TE validate), most hitting 200-turn ceiling. Same ~10M-token envelope.

### Conclusion — Runs 1 through 6

Each run has surfaced exactly one next blocker in the 4.6b.1 topology:

| Run | Blocker | Resolution |
|---|---|---|
| 1–2 | Spec validator non-determinism | Accepted as rare non-blocker |
| 3 | Coverage scanner didn't credit TE's anchor tags | Fixed (`6bf2cf2`) |
| 4 | TE emitting narrative markdown, not executable tests | Fixed (`0ef83b6`) |
| 5 | Spec validator non-determinism (again) | Re-ran as Run 6 |
| 6 | `validator.py` still enforces retired SA Format A contract | **Documented — not fixed** per document-don't-fix mandate |

**Run 6 is the closest the pipeline has come to producing F-1b attribution signal under 4.6b.1.** TE correctly emits the intended artifact. The only thing between the pipeline and the coverage floor / implementer / gate is a single obsolete validation gate.

**4.6b.2 remains blocked** on the `validator.py` fix. Scope is narrow: delete or neutralize the `behavioral_scenarios_required` gate (and its downstream `parseable` consequence) for HTTP API specs. Awaiting user decision on whether to proceed with the fix or pause here.

---

## Run 7 — 2026-04-08 ~20:17 UTC (post Phase 4.6b.1a Format A retirement)

**Commit under test:** `e9722e7` (Phase 4.6b.1a — Format A retired)
**Outcome:** ❌ Crashed in iteration 1 with an `AttributeError` in `iteration.py` defect-classifier branch. Not a content/barrier failure — latent code bug in the Phase 4.6b.0 classifier wiring.

### Phase progression (what worked)
- Spec Validator → Architect → QA Holdout → Test Engineer (3 TE invocations; TE↔validator loop converged naturally) → Implementer → pre-gate PASS → server smoke PASS → quality gate executed
- **Phase 4.6b.1a fix held**: no Format A mutually-exclusive-contracts trap. TE wrote native executable tests, validator accepted them, no mixed-format retry feedback fired. The Run 6 blocker is fully resolved.
- Quality gate ran end-to-end and produced a legitimate **26% pass rate** — first real implementer-behavior signal under the new TE/QA-Holdout architecture.

### Soft warning (non-fatal)
```
WARNING: barrier violations detected in test-engineer output: ['apps/api/src']
```
TE output contained a string mention of `apps/api/src`. Path-string match, not necessarily a real leak — TE writes integration tests importing from workspace packages, so path references can be legitimate. Flag for Run 8: verify false positive vs. real prompt-leak of source structure.

### Blocker — iteration.py AttributeError

```
File "darkfactory/pipeline/iteration.py", line 1022, in run_iteration_loop
    if (gate_result.scenario_fail_count + gate_result.scenario_crashed) > 0:
AttributeError: 'GateResult' object has no attribute 'scenario_fail_count'.
Did you mean: 'scenario_failed'?
```

**Root cause — attribute drift between two dataclasses with different names for the same concept.** The canonical `GateResult` field is `scenario_failed` (`quality_gate.py:174`). The canonical `IterationState` field is `scenario_fail_count` (`models/pipeline.py:37`). `iteration.py:1338` is the bridge that copies one into the other. Three sites in `iteration.py` reach for the `IterationState` name on a `GateResult` object:

- `iteration.py:1022` — defect classifier gate condition (the crash site)
- `iteration.py:1587` — `expected_total` computation
- `iteration.py:1597` — classifier prompt template (`scenario_fail_count: {...}`)

Wired in Phase 4.6b.0.iii/iv; never exercised end-to-end because prior runs never reached iter-1 quality-gate completion with `pass_rate < 100`. Run 7 is the first with `26% > 0` triggering `_step_classify`.

**Fix shape** (one token × 3 sites): `gate_result.scenario_fail_count` → `gate_result.scenario_failed`. Design follow-up (separate ticket, not this run): unify the naming across `GateResult` and `IterationState` so the bridge at `iteration.py:1338` disappears.

Per the test-run protocol's document-don't-fix mandate ("DO NOT attempt to fix issues with the pipeline itself … Bug reports, not bug fixes"), Run 7 is concluded without a code change. Run 8 awaits explicit user approval.

### What Run 7 validates
1. Phase 4.6b.1a is correct — Format A retirement unblocked the TE/validator loop with zero regressions.
2. New TE/QA-Holdout architecture executes end-to-end through implementation and smoke.
3. Quality gate produces real pass-rate signal against natively-executable tests.
4. Defect classifier path is untested — needs rename + integration test triggering iter-1 with `pass_rate < 100`.

### Recommended next actions
1. Fix the three `scenario_fail_count` → `scenario_failed` references in `iteration.py`.
2. Add a test exercising `_step_classify` with a mocked `GateResult` to catch attribute drift at CI time.
3. Investigate TE barrier-violation warning — classify false positive or tighten prompt.
4. Run 8: rerun spec after the fix to reach review/oracle phases.

| Run | Phase reached | Blocker | Disposition |
|-----|---------------|---------|-------------|
| 7 | iteration 1 / defect classifier | `iteration.py:1022` attribute drift | Fixed in Phase 4.6b.1b (`d6e83f9`) |

---

## Run 8 — 2026-04-08 ~22:30 UTC (post Phase 4.6b.1b defect-classifier fix)

**Commit under test:** `aa77076` (Phase 4.6b.1b — `GateResult` attribute drift fixed + regression test)
**Outcome:** ❌ NO-SHIP after 3 full iterations → stable-failure escalation (designed terminal state). Exit code 2 = normal escalation, not a crash.

### Headline — first run to complete the full iteration loop

Run 8 is the first run in this campaign to:
- traverse every agent role in the realigned Phase 4.6b.1 topology (Spec Validator → Architect → QA Holdout → Test Engineer → Implementer → Defect Classifier → Reviewer → Oracle)
- execute all 3 iterations of the retry loop
- reach escalation normally (trajectory=PLATEAU at iter 3)

Phase 4.6b.1b fix (`GateResult.scenario_failed`) held end-to-end — defect classifier dispatched cleanly on every iteration with no AttributeError.

### Stable signal — implementer plateau at 6% pass rate

- 20 total scenarios, 1 passing, 19 assertion failures, **0 crashes** (every iteration)
- Identical pass rate iter 1 / iter 2 / iter 3 → pure plateau, zero improvement under NO-SHIP feedback
- `category_00` only (tier 0, weight 1) — all 20 scenarios in one category
- Architecture manifest, TE scenarios (7 files), QA holdouts (8 files) all produced normally

This is the attribution signal Phase 4.6b.2 was designed to measure. It is partially corrupted by issue #1 below — the classifier never produced a real defect breakdown, so we cannot see *why* the 19 are failing without opening the test output by hand.

### Issue 1 — Defect classifier writes no file (every iteration)

```
WARNING: defect classifier wrote no file — stubbing
WARNING: defects.json unusable (classifier_no_output); classifications are empty this iteration —
         Oracle is required to NO-SHIP per 4.6b.0.iv
```

All three iterations produced the `classifier_no_output` stub. `defects-iter1.json` / `defects-iter2.json` are both:
```json
{"iteration": N, "schema_version": 1, "error": "classifier_no_output", "defects": []}
```

Not a flake. The classifier agent exits 0 but leaves no file at `.dark-factory/reports/<spec>-defects-iter<N>.json`. `_step_classify` at `iteration.py:1641` correctly detects the missing file and stamps the error stub (that safety rail worked).

**Impact on pipeline:** Under safety rule 4.6b.0.iv, an empty/errored defects.json forces Oracle to NO-SHIP regardless of reviewer verdict. This is correct behavior — the rule exists so a broken classifier can't silently let a bad implementation ship. But it means **Run 8's NO-SHIP verdict is structurally determined**, not earned. The real attribution signal from the 19 failing tests is hidden behind the classifier's no-output bug.

**Probable causes (untested, to investigate):**
- Classifier container write path: `defects_path` is computed via `project_dir / ".dark-factory" / "reports" / ...`, but the classifier's container mount may be pointing somewhere else. Check the barrier-config for `DEFECT_CLASSIFIER` and verify `.dark-factory/reports/` is bind-mounted `:rw`.
- Prompt contract mismatch: the prompt (`iteration.py:1589-1612`) tells the agent to write to `/project/...`. Verify the workdir inside the container is `/project`.
- Agent behavior: classifier agent definition (`agents/defect-classifier.md`) may instruct the agent to emit inline JSON instead of writing a file. Diff against the prompt contract.

### Issue 2 — Oracle feedback echoes pass-rate numerals (barrier leakage)

```
WARNING: oracle feedback contains 1 leakage patterns
WARNING: oracle feedback failed validation (attempt 1/3):
         Feedback violated 1 barrier rule(s): pattern '\b\d+%\s*(?:pass|fail)': ['6% pass']
```

Oracle's NO-SHIP feedback mentioned `6% pass` literally. The `audit/checker.py:72` pattern `\b\d+%\s*(?:pass|fail)` caught it as a semantic-leakage barrier violation, triggering the validation retry loop (1 of 3 attempts). The leakage rule exists so the Implementer (downstream consumer of Oracle feedback) cannot observe the pass rate.

**Impact on pipeline:** soft. The re-prompt loop corrects the feedback. But the fact that Oracle repeatedly reaches for pass-rate numerals suggests the Oracle agent's prompt or role definition does not make the no-numerals rule sufficiently prominent. Fix is prompt-side, not code-side.

### What Run 8 validates
1. **Phase 4.6b.1b fix is correct.** Zero AttributeErrors. Defect-classifier dispatch works across all iterations.
2. **Phase 4.6b.1 topology works end-to-end.** QA Holdout, Test Engineer, Defect Classifier, Reviewer, Oracle all execute, all barriers hold.
3. **The iteration/escalation machinery is working.** Plateau detection correctly triggered early escalation in iter 3 after the quality gate, skipping the no-value-add classifier/reviewer/oracle cycle for a run that wasn't going to improve.
4. **Pass-rate attribution is still blocked** — not by pipeline wiring, but by the classifier-no-output bug and (secondarily) the Oracle pass-rate-echo loop. Both are narrow, fixable issues.

### Phase 4.6b.2 gate — still blocked

4.6b.2 was defined to produce F-1b attribution signal for budget-brain-v2 under the realigned topology. Run 8 produced *stable plateau signal* (implementer cannot improve against 19 scenarios) but no *attribution signal* (cannot see which spec sections the failures map to) because the defect classifier is dark. Phase 4.6b.2 cannot be closed on Run 8 alone.

| Run | Phase reached | Blocker | Disposition |
|-----|---------------|---------|-------------|
| 8 | Escalation after iter 3 (full loop, first time) | Classifier no-output + Oracle pass-rate leakage | **Documented — awaiting fix plan** |
