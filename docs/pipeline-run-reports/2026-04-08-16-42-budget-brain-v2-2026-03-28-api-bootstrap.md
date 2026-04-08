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
