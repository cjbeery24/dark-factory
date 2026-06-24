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
