# Wire the blind holdout gate (binary) — barrier-design decision

**Status:** DESIGN for Neo (barrier-safety) + Linus (code) review. AUTHORIZATION-MODEL
pause-and-ask: this is a d2fecbf-class severity escalation (a previously-non-running,
non-gating signal becomes gating + reaches the Implementer). **User has signed off on
the direction (binary gate); reviews pending.**

## Finding (confirmed 4 ways incl. the produced run artifact)
The quality gate NEVER executes `.dark-factory/holdouts/<spec>/`. `SCENARIOS_DIR` is
hardcoded to the VISIBLE Test-Engineer dir (`quality-gate.sh:122`); the project's
vitest `include` lists only `scenarios/`; every `holdouts/` reference in the gate is
mask/strip/leak-audit, never a runner; and spec-5's executed-test results contain zero
holdout test names. The QA-Holdout's blind adversarial suite is generated and discarded.
This is why spec-5's `.trim()` bug shipped: a faithful catching holdout existed
(`auth-header-boundary.test.ts` — padded valid token → expect 401; buggy impl returns
200, confirmed deterministically) but never ran; the variance-prone reviewer was the
only backstop, and it approved the bug (3 runs, 3 behaviors).

## Decision (Codex-recommended, Neo-concurred on wiring, user-approved): BINARY gate
Run the holdouts and gate ship/no-ship on them, but feed the Implementer **only a binary
signal** ("blind adversarial validation failed") — NO per-failure detail, NO spec
anchors, NO categories, NO expected/actual, NO names/paths/assertions. Rationale: this
framework's holdouts are explicitly ADVERSARIAL; for adversarial holdouts, expected-vs-
actual (Yassine's validator bridge) or even spec-anchored feedback leaks the attack
vector and enables iterative gaming (semantic leak, undetectable by a substring scanner).
The binary signal is provably non-leaking. Richer fixability, if binary proves too weak,
is a measured FOLLOW-UP via strengthening VISIBLE coverage (a builder-readable test) —
never via leaking the holdout.

## No server/container wiring needed (verified)
The holdout's `vitest.config.ts` uses `globalSetup: [apps/api/src/global-setup.ts]`,
which boots the API server in-process on :3001 (and no-ops if one is already up). The
visible scenarios use the SAME globalSetup and already run against an in-process server.
The gate already provisions the DB (`Setup: pnpm db:migrate`). So wiring = "run the
holdouts dir with its own vitest.config"; no `test_server_cmd`, no A/B containers.

## Mechanism (where + how)
1. **Run holdouts as a SEPARATE pass, AFTER the visible executable scenarios**
   (`quality-gate.sh`, after the `Blind Scenarios (executable)` block ~1700). Add
   `HOLDOUTS_DIR=".dark-factory/holdouts/${SPEC_NAME}"`. Gate on `has_scenario_files`
   + sentinel (no false hole on pure-library/no-holdout specs). Reuse the EXISTING
   executable-runner dispatch (`detect_executable_languages`, the vitest/pytest case)
   pointed at `HOLDOUTS_DIR`, using the holdout's OWN vitest.config (`_df_resolve_vitest_config`
   already resolves a spec config; holdouts ship their own — resolve to it).
2. **Gate, count-only row:** emit `| Blind Holdouts | PASS/FAIL | N failed |` (count
   only — NO names). Holdout FAIL ⇒ `GATE_FAILED=true`.
3. **CRITICAL barrier-routing — keep holdouts OUT of the feedback funnel.** The leak
   funnel is `_write_per_test_results` → `failing_test_names` → defect classifier →
   composed `feedback-iterN.md` (the only Implementer-readable artifact; reports +
   holdouts are masked from the Implementer container). The holdout pass MUST NOT call
   `_write_per_test_results` and MUST NOT add to `defects-iterN.json` / `failing_test_names`.
   Its raw runner output (which contains holdout names+assertions) must NOT reach
   `$SUMMARY_FILE` (route it only to the reports-masked full report, or discard). The
   existing summary-strip (`quality-gate.sh:1800-1805`, strips `.dark-factory/holdouts/`
   + assertion lines) is a backstop, not the primary control.
4. **Binary Implementer feedback:** when the gate fails on holdouts (regardless of
   scenario state), the composed feedback says exactly:
   `Blind adversarial validation failed. Continue implementing the locked specification
   and the visible scenario suite only. No holdout names, paths, assertions,
   expected/actual values, spec anchors, categories, or input shapes are available.`
   with `<!-- CLASSIFICATIONS: {} -->`. (Codex's string.)
5. **Threshold:** start STRICT binary — any holdout failure ⇒ NO-SHIP (we can relax to
   ≥90% later if flakiness shows; deterministic TS/PG holdouts don't need 3×/2-of-3 —
   make reruns config-gated, default 1).

## Sentinel leak-check (the deterministic barrier proof — both reviewers mandate it)
A test that injects a holdout containing a unique literal (`WHITESPACE_PADDED_BEARER_SENTINEL`),
forces it to fail, runs the gate, and asserts that literal (and any `holdouts/` path /
`*.test.*` name / assertion-shape) appears in ZERO Implementer-readable artifacts:
`reviews/*-feedback-iter*.md`, `$SUMMARY_FILE`, and any prompt/history consumed by
`_step_implementation`. One match fails the build. Converts "is the barrier leaking?"
from judgment to a deterministic gate. (Caveat both noted: this catches LITERAL leakage;
the binary-feedback choice is what handles SEMANTIC leakage — there is no detailed
holdout feedback to semantically leak.)

## Authorization (AUTHORIZATION-MODEL)
- Severity escalation: holdout FAIL now sets `GATE_FAILED` → NO-SHIP. The new row +
  binary feedback are Implementer-readable on iter N+1. → `Audience:` declaration +
  this documented decision (done).
- Audience line for the commit: `Audience: Implementer-readable on iter-N+1 = BINARY
  "blind adversarial validation failed" only; holdout names/assertions/paths/anchors
  never reach the Implementer (kept out of per-test/defects/classifier; sentinel
  leak-check enforces). Restores the blind-holdout barrier per docs/goals/.`
- Changed surfaces: `scripts/quality-gate.sh` (new row + runner), the feedback
  composition path, `agents/qa-holdout.md`/orchestrator docstring (revise the false
  "supplementary / non-gating" claim).

## Deterministic measurement (phase tests; held buggy impl = oracle)
- **Arm A (catch):** gate on the held buggy spec-5 impl → `Blind Holdouts | FAIL` →
  NO-SHIP (was a clean SHIP). Deterministic.
- **Arm B (no false-positive):** corrected impl (whitespace check before `.trim()`) →
  `Blind Holdouts | PASS` → SHIP-eligible.
- **Arm C (no false hole):** a no-holdout/sentinel spec → no holdout row, verdict
  unchanged.
- **Arm D (barrier proof):** grep all Implementer-readable artifacts for the sentinel
  literal + holdout names/paths/assertions → ZERO matches.
- **Phase test (the open question):** with binary feedback, can the Implementer close
  the gap from the binary signal + the spec alone across iterations? If NO → the
  measured next step is the visible-coverage bridge (NOT leakage).

## Neo barrier-safety review (2026-06-24) — 5 REQUIRED changes (folded in; leak-proof only with these)
The mechanical/classifier funnel is closed by the plan, BUT two real gaps + three guards:
1. **Oracle echo is the residual leak (count-only-in-report is necessary but NOT sufficient).**
   The Oracle is UNMASKED — it can read `holdouts/` directly and *paraphrase* ("the blind
   suite found 3 more failures than visible", "the bearer-token edge case is failing"); the
   `_LEAKAGE_PATTERNS` scanner catches the literal `N/M failed` but NOT paraphrase. **MUST add
   an explicit `agents/oracle.md` directive:** on a `Blind Holdouts | FAIL` signal, do NOT read
   `.dark-factory/holdouts/`, do NOT echo the holdout count/names/intent or infer what the blind
   suite probed; emit exactly the fixed binary blocker. (Same pattern as the existing
   substrate/cascade directives at the Oracle call site.)
2. **Carry the signal on a MASKED SIDECAR, not report prose.** Write `holdout_failed=N` to e.g.
   `reports/<spec>-holdout-result.env` (reports plane = implementer-masked) → parse into a new
   `iter_state.holdout_failed`. Feed Python the count; feed the Oracle only the binary fact +
   the no-echo directive. Don't put the count on any Oracle-authored surface.
3. **HARD-ISOLATE the holdout runner from the scenario count machinery (this WILL break otherwise):**
   - Trap A — `_step_classify` enforces `expected_total = gate_result.scenario_failed` and RAISES
     `DarkFactoryError` if record count ≠ expected. If holdouts inflate `scenario_failed`/
     `scenario_total`, the classifier aborts EVERY failing iteration. Holdouts must touch NEITHER.
   - Trap B — keep the holdout pass a STANDALONE function (NOT `run_gate`, NOT `_parse_scenario_counts`,
     NOT `_write_per_test_results`); must NOT write `-scenario-counts-executable.env` or
     `test-results-iter<N>.json`; must NOT touch `_LAST_GATE_OUTPUT` (the per-test parsers consume it);
     run AFTER `_assert_scenario_count_invariant` (quality-gate.sh:~1732) and never re-invoke the parsers.
4. **Binary feedback MUST still contain a `<!-- CLASSIFICATIONS: {} -->` block** or
   `validate_oracle_feedback` re-prompts on "missing CLASSIFICATIONS".
5. **Strengthen the sentinel test — assert ALL of:** (a) sentinel literal + holdout paths/names in
   ZERO implementer-readable artifacts (feedback-iter<N>.md + any implementer-reachable run log);
   (b) the count `N/M` does NOT appear in feedback-iter<N>.md; (c) the quality-gate.sh:1800
   SUMMARY-strip WARNING did NOT fire (proves nothing leaked into SUMMARY in the first place —
   the strip is a backstop, not the control); (d) `test-results-iter<N>.json` + `defects-iter<N>.json`
   contain no holdout names; (e) classifier `expected_total` == the VISIBLE failing count (proves
   holdouts didn't inflate `scenario_failed`).

Verdict: architecture sound; leak-proof ONLY with items 1–5. Linus reviews the actual code next.

## Implementation progress (branch `feat/wire-blind-holdout-gate`)
- ✅ **Surface (a) bash runner + (e) config-path fix — BUILT + VERIFIED + committed (`4d9115e`).**
  `_df_run_holdout_gate` (scripts/lib/scenarios.sh) + wired in quality-gate.sh after the
  scenario count invariant. Gate-owned wrapper (imports project vitest config → in-process
  server globalSetup + aliases, OVERRIDES include to the holdout dir) sidesteps the LLM
  config's off-by-one. Count-only, raw output discarded, masked sidecar
  `reports/<spec>-holdout-result.env`, isolated from scenario count machinery, GATE_FAILED on
  failure, sentinel/no-files guard, can't-execute→FAIL. **VERIFIED end-to-end vs the held bb
  buggy impl: GATE_FAILED=true, caught 11/64 blind failures (whitespace→401 + cross-tenant
  leakage), ZERO PII/name/path/assertion leak into report/summary.**
- ✅ **Surface (c) oracle.md no-echo directive — committed (`6cff0c0`-style, in `agents/oracle.md`).**
- ✅ **Surface (b) Python deterministic binary feedback — BUILT + tested (`f5483d3`).**
  GateResult.holdout_failed/holdout_ran + `_parse_holdout_result` (reads the masked sidecar,
  independent of scenario counts, at both count-parse sites) + IterationState thread + a
  deterministic blind-holdout NO-SHIP floor in `_step_oracle` (holdout_failed != 0 → NO-SHIP)
  + a deterministic binary-feedback injection in the NO-SHIP path (ensures the fixed blocker
  is in feedback-iter<N>.md AFTER the leak-validation loop; preserves scenario feedback in the
  mixed case; CLASSIFICATIONS:{}). 5 unit tests (floor fail/cant-run/clean; binary feedback
  leak-free vs a token blacklist; mixed-append).
- ✅ **Linus review — SHIP-WITH-FIXES; all three applied (`f8b8154`).** Barrier isolation +
  leak-safety + feedback ordering + floor verified correct by Linus. **C1** (critical): the
  holdout call was trapped in the single-run dispatch arm → hoisted to run UNCONDITIONALLY
  after the whole scenario dispatch (sentinel / multi-run / single-run / no-scenarios all
  covered). **Q1**: a holdout failure at the cap now suppresses STABLE_FAILURE → escalates
  (no "clean gap" laundering). **Q2**: holdout wrapper registered for EXIT-trap cleanup.
  +1 test. Full unit suite 4629 green.
- ⏳ **Surface (d) FULL gate-run sentinel test — REMAINING** (Python-level leak-free feedback
  test + manual end-to-end zero-leak verification DONE; the automated gate-run-level sentinel
  — sentinel literal in ZERO implementer-readable artifacts + SUMMARY-strip-didn't-fire +
  JSON-no-holdout-names + classifier expected_total == visible failing count — needs the
  Docker gate harness, part of the phase tests).
- ⏳ **Phase tests (Arms A–D) — REMAINING** (a real Docker gate run; Arm A buggy→NO-SHIP +
  the leak-free count-only rows already proven at the runner level by the end-to-end
  verification: GATE_FAILED=true, 11/64 caught incl. cross-tenant leakage, zero PII/name/path
  leak in report/summary). The full automated Surface-(d) sentinel test lands with these.
- ⚠ **NOT for main until the phase tests + Surface-(d) automated sentinel land** + Linus's
  confirm of C1/Q1/Q2. Code-complete + unit-verified on the branch; the remaining gate is the
  Docker gate-run validation.

## Explicitly rejected
- Yassine expected-vs-actual feedback (leaks the adversarial vector for attack-class
  holdouts; unprovable-clean).
- Spec-anchored holdout feedback via the defect classifier (leaks the blind section;
  semantic leak undetectable; the visible-vs-blind distinction makes the "same channel
  as scenarios" argument unsound).
- Auto-deriving a server-start command (per-stack treadmill; in-process globalSetup
  makes it unnecessary).
- A new Validator-Bridge agent (its function is already split across gate + classifier;
  adding it re-creates coordination surface).
