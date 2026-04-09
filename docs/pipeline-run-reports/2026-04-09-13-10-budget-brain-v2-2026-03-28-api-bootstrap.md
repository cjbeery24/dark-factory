# Pipeline Run Report — 2026-04-09 13:10 UTC

**Repo:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Framework commit:** 45769c0 (Phase 4.6b.1h)
**Run ID:** c89d1c20f2724189

Trigger: validate 4.6b.1h (Oracle consumes defects-iterN.json) and
4.6b.1g (regex narrowing, invariant scope guard, duration strip)
end-to-end.

## Run 12 — SHIPPED (iter 2, ship-with-gaps)

**Outcome:** First successful SHIP on this spec. Oracle correctly
identified AC-1 as a non-critical IMPL_BUG and shipped with the
single stable gap logged in the defect registry.

### Timeline
| Phase | Status |
|---|---|
| spec-validator | PASS |
| architect | PASS |
| qa-holdout | PASS |
| test-engineer | PASS — 34 scenarios |
| smoke | PASS |
| iter 1 | pre-gate PASS, smoke PASS, gate 97%, classify 1 IMPL_BUG, review PASS, Oracle NO-SHIP |
| iter 2 | pre-gate PASS, smoke PASS, gate 97%, ship-with-gaps eligible (1 stable failure, 97% >= 80%), classify 1 IMPL_BUG, review PASS, Oracle **SHIP** |

### Framework validations

**4.6b.1h (Oracle defects inlining) — VALIDATED.**

Iter2 Oracle output (verbatim from oracle-output-iter2.md):
> AC-1 logging format (IMPL_BUG): Implementation uses human-readable
> format in development instead of required structured JSON

Compare Run 11 iter2:
> Missing @eslint/js, missing vitest [...] AC-1: ENV_ISSUE

Night and day. The Oracle consumed the defect classifier's authoritative
`IMPL_BUG` classification, correctly described the actual defect (logging
format, not infrastructure), and made a rational ship-with-gaps decision.

**Iter1 feedback was still noisy.** The iter1 Oracle wrote "systematic
blind scenario execution failure" and "near-universal failure" despite
33/34 passing. It acknowledged the defect data ("1 minor IMPL_BUG") but
then overrode it with ENV_ISSUE classifications in the CLASSIFICATIONS
block. By iter2, the ship-with-gaps eligibility prompt brought the
Oracle back to reality. This suggests:
- The defect data inlining alone isn't sufficient to prevent iter1
  hallucination — the Oracle still exercises judgment over the
  classifier on first pass.
- Ship-with-gaps is the real corrective mechanism: once the pipeline
  mechanically determines eligibility, the Oracle prompt explicitly
  says "you SHOULD output SHIP" and the Oracle complies.
- If we want iter1 feedback to be accurate (not just the SHIP
  decision), we'd need to code-generate the CLASSIFICATIONS block
  from defects.json rather than trusting the Oracle to copy it.
  Filed as candidate Phase 4.6b.1i (non-blocking — SHIP works).

**4.6b.1g (regex + invariant + duration strip) — VALIDATED.**
- No Oracle feedback barrier violations (regex narrowing held).
- Gate telemetry:
  ```
  iter1: reported_failed=1 reported_total=34 parsed_failed_rows=1
  iter2: reported_failed=1 reported_total=34 parsed_failed_rows=1
  ```
  Invariant held. No iter0 spurious firing (scope guard).
- Duration strip cannot be verified from telemetry alone (stable-failure
  detection fired at iter2 via ship-with-gaps, which requires plateau
  detection to work). The fact that ship-with-gaps activated confirms
  `detect_stable_failures` found the invariant set — which requires
  test names to match across iterations. Duration strip is working.

**4.6b.1d + 4.6b.1f — still healthy.** Gate parser, manifest parser
both held. No regressions.

### Ship-with-gaps mechanism

This is the first live use of ship-with-gaps. The mechanism worked
exactly as designed:
1. Iter1: 97%, 1 IMPL_BUG, Oracle NO-SHIP (makes sense — first pass)
2. Iter2: 97% again, same 1 IMPL_BUG, plateau detected, stable failure
   count = 1, `pass_rate 97% >= min_ship_pass_rate 80%` → eligible
3. Oracle received ship-with-gaps prompt: "you SHOULD output SHIP.
   Only NO-SHIP for critical regression, security, or data integrity."
4. Oracle correctly assessed AC-1 logging format as non-critical → SHIP

This validates that the full iteration loop, plateau detection,
stable-failure set intersection, and ship-with-gaps eligibility all
work end-to-end.

### New findings

**Finding 1 (non-blocking): Oracle iter1 feedback quality.**
Despite having defect data inlined, the iter1 Oracle still hallucinated
"systematic failure" and overrode IMPL_BUG → ENV_ISSUE. The data
availability fix (4.6b.1h) is necessary but not sufficient for iter1
feedback accuracy. Ship-with-gaps corrects the final outcome but the
iter1 feedback the implementer receives is still misleading. Candidate
4.6b.1i: code-generate the CLASSIFICATIONS block from defects.json
instead of trusting the Oracle.

### Status summary

| Concern | Status |
|---|---|
| 4.6b.1h Oracle defects inlining | VALIDATED — iter2 feedback accurate |
| 4.6b.1g regex narrowing | VALIDATED — no false positives |
| 4.6b.1g invariant scope guard | VALIDATED — no iter0 spurious fire |
| 4.6b.1g duration strip | VALIDATED — ship-with-gaps activated |
| 4.6b.1d gate parser | still healthy |
| 4.6b.1f manifest parser | still healthy |
| Ship-with-gaps mechanism | first live validation — works as designed |
| New: iter1 feedback quality | non-blocking, candidate 4.6b.1i |
| 4.6b.1e deferred control-flow fix | still pending |
