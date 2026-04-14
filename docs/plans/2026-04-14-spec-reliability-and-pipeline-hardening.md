# Spec Reliability + Pipeline Hardening — Plan

**Date:** 2026-04-14
**Motivation:** Post-mortem on `2026-03-28-database-foundation` escalation (see `docs/pipeline-run-reports/2026-04-14-04-45-budget-brain-v2-2026-03-28-database-foundation.md`). Three orthogonal failure modes compounded:
- Test-engineer authored scenarios asserting library internals it could not typecheck
- Architect emitted an env contract contradicting spec §Constraints
- Trajectory classifier couldn't distinguish "stuck due to bad scenarios" from "implementer ignoring feedback"

This plan addresses the structural gaps behind those failures. It deliberately does **not** pursue spec decomposition — the post-mortem ruled that out. It also does **not** modify `darkfactory/scenarios/detection.py:165-170`'s blocker-gate logic; the in-line comment there documents intentional design (retracted from an earlier draft of recommendations).

## Non-goals

- Spec decomposition of F-1c or any other spec
- Rewriting `detection.py:165-170` (intentional design)
- Changing the `--dangerously-skip-permissions` security model
- Expanding the oracle's filesystem surface beyond what's already exposed

## Shape

Two parallel tracks with independent file surfaces. Can be worked concurrently or sequenced.

**Track A — Spec reliability** (catches issues before the pipeline burns iterations). Home: `darkfactory/spec/`, `darkfactory/architecture/`, `darkfactory/scenarios/`.

**Track B — Pipeline hardening** (reduces cost and false framings when a spec does enter the pipeline). Home: `darkfactory/pipeline/`, `darkfactory/scenarios/trajectory.py`, oracle/escalation agent prompts.

## Track A — Spec reliability

### A1 · Architect-spec consistency check *(ship first)*

**Target:** `darkfactory/architecture/environment.py` — post-architect hook, runs after env.toml is produced and before implementer is invoked.

**What it does:** Deterministic keyword-pair check against a curated table. If spec §Constraints contains any LHS token from a known contradictory pair and the architect-emitted `environment.toml` (or `manifest.yaml`) contains the RHS, fail pre-implementer with a structured error.

**Seed table** (grows from post-mortems):
| Spec contains | Env MUST NOT contain |
|---|---|
| `migrate-only`, `CLI-only`, `never auto-applied`, `db:migrate` | `drizzle-kit push`, `auto-migrate`, `push-on-startup` |
| `read-only`, `read-only project mount` | db mutations in `commands.setup` |
| `idempotent setup` | destructive commands (`drop`, `truncate`, `reset`) |
| `deferred`, `lazy connection` | assertions that construction throws |

**Not auto-derived from §Constraints** — semantic extraction is a C1-scope LLM job we decided to skip. Manual curation wins here; each escalation adds at most one row.

**Acceptance:** On this run's artifacts (`.dark-factory/architecture/environment.deltas/0005-2026-03-28-database-foundation.toml` setting `drizzle-kit push` against spec §Constraints "migrate-only"), the check fails with a citation of both the spec phrase and env command.

**Effort:** low. One file, ~80 LOC including the table.

---

### A2 · SQL syntax lint on spec code fences *(ship first)*

**Target:** `darkfactory/spec/precheck.py` — add `lint_sql_fences()`.

**What it does:** Extract every ```` ```sql ```` block from the spec markdown, parse with `pg_query` (Python binding over libpg_query). If all blocks parse, the spec's SQL is valid. If a block fails to parse, flag it as a deterministic finding.

**Why it matters beyond catching bad SQL:** the current validator has flagged *valid* PG16 SQL as "critical syntax error" (e.g., `ALTER DATABASE CURRENT`). Running A2 alongside the validator and deflating any LLM "critical SQL" finding that contradicts A2's deterministic verdict calibrates the validator's score.

**Acceptance:** On this run's spec, every SQL fence parses clean, the validator's hallucinated "ALTER DATABASE CURRENT invalid" finding is suppressed.

**Effort:** low. `pg_query` is pip-installable; dependency consideration (does it work in all target-project languages? For non-DB specs A2 is a no-op).

---

### A3 · Library-symbol existence check *(scoped down from original)*

**Target:** `darkfactory/spec/contracts.py`.

**What it does:** Scan spec §5 Interface Contract + adjacent prose for library API references (token patterns like `tx.execute`, `postgres()`, `fastify.register`, `PgColumn`). For each token, try to resolve against the project's pinned versions via `node_modules/<lib>/package.json` → `main`/`types` → parsed exports. If the symbol doesn't exist in the pinned version's public exports, flag.

**Deliberately does NOT check call shape.** Shape-check against prose specs is re-implementing a typechecker via regex; that ambition belongs to A6. Symbol-existence alone catches version-skew and removed-API bugs (~60% of the F1 class at a fraction of the complexity).

**Fallback policy:** Missing types → check for `@types/<lib>`. Still missing → warn-not-fail. Workspace packages → skip with note.

**Acceptance:** On a spec that references `drizzle-orm`'s `tx.execute`, the symbol resolves. On a spec that references `tx.executeQuery` (nonexistent), the check fails with version + resolution trace.

**Effort:** medium. The node_modules resolution has edge cases (pnpm, yarn PnP, workspaces).

---

### A6 · Embedded-stub round-trip *(strongest spec-side gate — ship second)*

**Target:** `darkfactory/spec/precheck.py` — add `compile_interface_contract_stub()`.

**What it does:**
1. Extract spec §5 Interface Contract block (already has a structured header convention).
2. Emit a `.d.ts` stub capturing the declared signatures.
3. Run `tsc --noEmit` against the target project's `tsconfig.json` with the stub appended to the project's type roots.
4. Fail if tsc reports errors.

**Why this beats A3's original call-shape ambition:** tsc is the ground truth. Instead of regex-guessing whether the spec's `tx.execute(sql)` matches drizzle's generic signature, we hand the expression to the compiler.

**Dependencies:** needs A3 to run first (bail if symbol doesn't even exist before invoking tsc). Needs the target project to have TypeScript installed; non-TS projects skip.

**Acceptance:** A spec referencing `tx.execute({sql, values})` (wrong drizzle shape) fails tsc and gets rejected pre-pipeline. A spec referencing `tx.execute(sql\`SELECT 1\`)` (correct) passes.

**Effort:** medium-high. Stub extraction needs a disciplined §5 format; tsc invocation is straightforward but cross-project config is fiddly.

---

### C2 · Proposal-test typecheck rehearsal *(ship third — complements A6)*

**Target:** new hook in `darkfactory/scenarios/` — runs after architect/validator, before full scenario generation.

**What it does:** Spawn a lightweight LLM agent that, for each AC/FR/EC in the spec, writes ONE proposal test — name, imports, single assertion. Write all proposals to a scratch file. Run `tsc --noEmit` against the project's `tsconfig`. If ≥30% fail typecheck OR any AC proposal fails with a banned pattern (library-internal `_` access, `process.exit`, assertions that require forking subprocess), **abort before full scenario generation**.

**Why it's still needed alongside A6:** A6 validates the spec's *declared* shape. C2 validates whether anyone can actually *write tests* that exercise that shape. An AC can have a valid signature but be untestable at the unit level (e.g., AC-2 "server exits with code 1").

**Cost:** one lightweight agent run per spec, ~$1–3. Cheap vs a 3-iter escalation at $126.

**Acceptance:** On this run's spec, proposals for AC-2 (`postgres('').toThrow()`), FR-2 schema helper `_` access, and edge cases using `tx.execute({sql, values})` would all fail typecheck/banned-pattern check → scenario generation aborts → human sees the exact ACs that are untestable as authored.

**Effort:** medium. Agent prompt, typecheck harness, banned-pattern list.

---

### E2 · Calibration dataset *(start now, don't gate)*

**Target:** new `docs/post-mortem-dataset.jsonl` + append step in every `docs/pipeline-run-reports/*.md` workflow.

**What it does:** Each pipeline run (SHIP or escalate) appends one JSONL row capturing: spec ID, validator score, validator blockers, actual outcome, iteration count, stuck-set categories, post-mortem root cause codes. Over time this becomes training/calibration data for:
- Validator score calibration (B4-like deflation based on outcome history)
- A1 pair-table growth (each escalation surfaces new pair candidates)
- Future validator prompts (feed recent post-mortems as context)

**Not a gate.** Pure data capture. Pays off in 10+ runs.

**Acceptance:** This run (database-foundation) appends a row capturing `verdict=PASS, score=89, outcome=escalated, root_causes=[broken_scenarios, architect_spec_drift, slow_implementer]`.

**Effort:** low. One schema, one append helper, one-line addition to the run-report workflow.

---

## Track B — Pipeline hardening

### B1 · Refined trajectory escalation

**Target:** `darkfactory/pipeline/iteration.py:1483` (current `iteration >= 3` gate) + `darkfactory/scenarios/trajectory.py`.

**What it does:** Split the PLATEAU signal by whether the implementer made effort:
- PLATEAU + failing set stable + **implementer diff non-empty** (tried, failed) → escalate at iter-2. Probably test-engineer-side bug.
- PLATEAU + failing set stable + **implementer diff ≈ empty** (didn't try) → hard-prompt retry at iter-3 with "you ignored prior feedback." Do not escalate yet.
- Non-PLATEAU trajectories: unchanged.

**Why this wasn't in the first recommendation set:** a blunt "escalate on PLATEAU at iter-2" would have prevented iter-3's partial fix from landing in this run. The split gives us early escalation when scenarios are broken (cheap exit) without cutting off recovery when implementer finally wakes up.

**Dependencies:** needs a git-diff helper to measure implementer effort between iterations (currently not tracked per-iter). This is the biggest subcomponent of this item.

**Acceptance:** On this run's iter-2 state (stable failing set, non-empty implementer diff), B1 triggers escalation at iter-2 → save ~$42 + one iteration of wall-clock.

**Effort:** medium. Straightforward once per-iter diff tracking exists.

---

### B2 · Oracle reads prior iteration feedback + diff

**Target:** oracle agent prompt in `agents/oracle.md` + orchestrator hook in `darkfactory/pipeline/`.

**What it does:** Pass the previous iteration's feedback file + a `changes_since_last_iter` diff into the oracle prompt. Oracle must produce a "confirmed-fixed" or "still-broken" verdict per prior-iteration blocker before proposing new blockers.

**Why even though oracle was correct this run:** The gap is structural. Oracle re-derives the world each iteration. Next time we hit a run where the implementer *did* fix iter-1 in iter-2, oracle will need this to not re-prosecute.

**Barrier:** low risk. Oracle already reads `reviews/` and `reports/`; the feedback file lives in `reviews/`.

**Acceptance:** Oracle output at iter-N contains an explicit mapping from iter-(N-1)'s blockers to their current fixed/still-broken status.

**Effort:** low-medium. Mostly prompt engineering + structured output validation.

---

### B3 · Classifier `count_mismatch` hard-fail

**Target:** `darkfactory/pipeline/` defect-classifier invocation + schema validation.

**What it does:** Today `count_mismatch` errors are silently swallowed and the classifier emits empty classifications. Change: classifier errors become a hard fault, pipeline retries the classifier once, if still failing the iteration fails with a clear cause rather than continuing with degraded oracle input. Also: file a reproducer — what input shape causes count_mismatch?

**Acceptance:** On an input that currently triggers `count_mismatch`, either (a) the classifier completes cleanly after the fix, or (b) the pipeline stops with a clear "classifier contract violation" rather than proceeding with empty defect data.

**Effort:** low. Error-handling change; repro investigation is the uncertain part.

---

### B4 · Escalation-agent grounding (or prune its narrative)

**Target:** `agents/escalation.md` (or wherever the escalation prompt lives).

**Options:**
- **Option 1 (stronger):** Feed gate event payloads, test-results JSON, iteration state, trajectory classification directly into the prompt. Require output sections to cite those inputs.
- **Option 2 (faster):** Prune the "Executive Summary / Iteration Analysis" sections from the escalation report template entirely. Keep only "Required Actions" (which was correct in this run). The model stops getting asked to narrate iteration history.

**Recommend Option 2 first** — cheaper, ships today, removes a proven hallucination surface. Option 1 becomes the follow-up if we want a narrative at all.

**Acceptance:** Escalation report for this run does not contain fabricated scenario counts, scores, or iteration-analysis prose. Required-actions section preserved.

**Effort:** low. Prompt edit + template change.

---

### B5 · Preflight-only in-iteration retry

**Target:** `darkfactory/pipeline/iteration.py` pre-gate retry loop.

**What it does:** Today preflight flags export-contract / test-presence drift as advisory, proceeds to full gate. Change: on preflight-only failures (no other gate issue), bound a single in-iteration implementer retry with the preflight findings as the sole prompt input. If the retry clears preflight, proceed to full gate. If not, fail the iteration.

**Why:** in this run, iter-3's export-contract violation (undeclared `DrizzleTx` + `initializeDatabase`) triggered a full downstream chain. A single preflight-only retry would have saved the full gate+classifier+reviewer+oracle cycle for a mechanical fix.

**Acceptance:** On a simulated preflight-only failure, the implementer retries once within the same iteration, clears preflight, and the full gate runs on clean input.

**Effort:** medium. Iteration-loop state machine change.

---

## Sequencing

**Sprint 1 — ship the trivial wins (Tranche 1):**
1. A1 (spec↔env check)
2. A2 (SQL lint)
3. B4-Option-2 (prune escalation narrative)
4. E2 (start the dataset)

All four are low-effort, high-visibility, no cross-dependencies. Four small PRs.

**Sprint 2 — the structural gates (Tranche 2):**
5. A3 (scoped, symbol-existence only)
6. A6 (stub round-trip) — the highest-leverage single item
7. B2 (oracle reads prior feedback)

**Sprint 3 — the complex items (Tranche 3):**
8. C2 (proposal-test rehearsal) — depends on A6 being stable
9. B1 (refined trajectory escalation) — requires per-iter diff tracking
10. B3 (classifier hard-fail + repro)
11. B5 (preflight-only retry)

## Success criteria

A dry-run of this run's artifacts against the built pipeline should produce:
- **A1** fails post-architect: spec says "migrate-only", env says `drizzle-kit push`
- **A2** confirms all SQL in spec is valid, deflates validator's hallucinated "invalid SQL" finding
- **A6** rejects spec: `tx.execute({sql, values})` shape doesn't compile against installed drizzle-orm 0.44
- **C2** rejects: proposal tests for AC-2, FR-2 schema-helper, EC-4 all fail typecheck
- **B1** escalates at iter-2 instead of iter-3, saving one full iteration
- **B4** escalation report contains no fabricated iteration analysis
- **E2** row captured with root-cause codes

If all seven apply, the pipeline would have caught this spec's weaknesses pre-implementer or pre-full-scenario — no post-mortem required.

## Open questions

1. **A6 §5 format convention.** Current specs have §5 Interface Contract as prose + code blocks. To reliably extract a `.d.ts` stub, we may need a stricter subsection (e.g., a `## 5.1 Declared TypeScript surface` block with a fenced `ts` code block). Does the templates/ set need an update?
2. **B1 per-iter diff tracking.** Need to add `iteration_start_sha` / `iteration_end_sha` to the run state. Is git the right mechanism inside a Docker container, or do we capture file-hash manifests?
3. **A2 non-TS projects.** Skip-on-no-SQL is trivial. Is there an analogous deterministic lint for Python (AST parse) and Go (vet)? If so, A2 becomes "spec code-fence syntax lint" with language dispatch.
4. **C2 LLM budget.** Per-spec cost ~$1–3 for the rehearsal agent. Acceptable baseline? Or should C2 be opt-out for cheap specs?
5. **E2 schema.** What fields exactly? Proposed minimum: `spec_id, run_id, started_at, outcome, iterations, validator_score, validator_blockers, validator_divergence, pass_rate_per_iter[], stuck_set[], root_cause_codes[], post_mortem_path`. Extensible later.

## Not in this plan, deferred

- **B3 spec-validator calibration** from E2 dataset — waits for 10+ rows.
- **Scenario-architect gets `node_modules/**/*.d.ts`** — barrier adjustment needed; enables A3/A6/C2 structurally. May require docker/barrier work before Sprint 2.
- **A5 complexity heuristic / decomposition rationale** — rejected for this round; friction-not-gate, low leverage.
- **B2 "how would you test this?" probe** — Neo flagged it as redundant with C2. Drop unless C2 proves insufficient.

## Retracted / explicitly out-of-scope

- **C1 post-architect LLM re-validation.** Overlaps with C2 (any AC that can't be expressed as a passing proposal test already fails C2). Ship if post-C2 runs show unreached semantic drift.
- **Spec decomposition of F-1c or any current spec.** Post-mortem ruled it out.
- **`detection.py:165-170` modification.** Intentional design; in-line comment documents rationale.
- **A3's original call-shape ambition.** A6 is the honest version.
