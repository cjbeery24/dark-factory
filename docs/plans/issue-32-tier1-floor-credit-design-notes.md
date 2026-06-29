# #32 — Tier-1 contract credits the coverage floor: design notes (pre-Neo)

> Status: design notes only. Needs Neo design pass + Linus/Codex review before any code.
> Captured during the frontend-bootstrap capstone (run ef8d9c58), where the residual surfaced.

## What the capstone actually showed (re-scopes #32)
The clean `2026-03-28-frontend-bootstrap` spec passed the coverage floor (`COVERAGE_PASS
anchor_count=17`) with AC-11 (no-secret-bundle) handled by **`disc:invariant` +
`vehicle: subprocess` + a behavioral Tier-1 `command` contract (TC-1)**. Key facts:
- `disc:invariant` EXCLUDES AC-11 from BOTH the floor's required-anchor set AND the
  QA-holdout (verified: the holdout report has no AC-11 probe). So the exclusion path is
  self-consistent across floor + holdout.
- TC-1 is the real verifier; it runs in the gate's tier-1 step (Build + Blind-Scenarios
  PASS ⇒ TC-1 green).
- **So #32 is NOT a blocker** — the `disc:invariant`+TC path already works. #32 is a
  *cleanliness* improvement for the case where the author wants a SCORED anchor credited
  by a Tier-1 contract without the `disc:invariant` exclusion dance.

## The mechanism (precise)
The floor (`darkfactory/scenarios/coverage.py::compute_coverage`) already WALKS the
compiled `00-tier1-contracts.test.ts` and credits any anchor token found in it; Tier-1
files are exempt from behavioral-credit (`_is_tier1_contract`, line ~515). The gap is
purely the **emit side**: the compiler does not put the contract's `covers:` anchors into
the generated test in a creditable form.

- Emit point: `darkfactory/spec/contracts.py::_compile_vitest` line **336**:
  ```python
  test_cases.append(
      f"  it('{_escape_js(c.id)}: {_escape_js(c.given)}', {arrow} {{\n{body}\n  }});\n"
  )
  ```
  The `it()` name is `'TC-1: <given>'` — no `covers` anchors. The floor scans `it()` names
  for `[AC-N]` markers, so injecting the covers as bracket tags would credit them:
  ```python
  covers_tag = "".join(f" [{a}]" for a in c.covers)   # e.g. " [AC-11]"
  f"  it('{_escape_js(c.id)}{covers_tag}: {_escape_js(c.given)}', ...)"
  ```
  Mirror in `_compile_pytest` (line ~598, the `00_tier1_contracts.py` path).

## The two candidate fixes (Neo to choose)
1. **Emit covers as `[AC-N]` in the Tier-1 it-name** (above). Smallest change. BUT a
   SCORED anchor credited this way still draws a QA-holdout demand it can't satisfy (a
   build-artifact invariant has no unit-holdout). So this fix is incomplete UNLESS the
   QA-holdout machinery also learns to skip anchors whose only verifier is a Tier-1
   contract — otherwise the anchor floor-passes but holdout-fails. (This is the
   holdout-exclusion subtlety; `disc:invariant` already solves it by excluding both.)
2. **Fix Phase-0 `is_behavioral_command` instead** — the real friction in the capstone was
   NOT the floor (the `disc:invariant` path passed it) but that a clean
   `exit_code: 0` + command-embedded `! grep` looked vacuous to `is_behavioral_command`,
   forcing a decoy `then: {file, pattern}` (the `<!doctype html>` check) to make Phase-0
   accept TC-1. A targeted fix: teach `is_behavioral_command` to recognize a command that
   embeds its own assertion (a `grep`/`test`/`!`-negated pipeline in the `when.command`),
   so the clean `! grep` form passes Phase-0 without the decoy. Narrower, no
   holdout-exclusion entanglement.

## Recommendation to Neo
Prefer **(2)** — it removes the actual capstone friction (the decoy `then`) with no
holdout-exclusion coupling, and keeps `disc:invariant` as the (working) floor+holdout
exclusion mechanism. Pursue **(1)** only if a concrete spec wants a Tier-1-credited SCORED
anchor; if so, it MUST be paired with the holdout-skip, or it trades a floor-fail for a
holdout-fail. Either way: corpus-check that no existing Tier-1 contract's `given` text
already contains a stray `[AC-N]` that would over-credit.
