# Pipeline Run Report — 2026-04-09 05:17 UTC

**Repo:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Framework commit:** eca4cc1 (Phase 4.6b.1f)
**Run ID:** 456d0a493e9744db

Trigger: validate 4.6b.1d (gate parser + fail-loud invariant) and
4.6b.1f (manifest parser guards) end-to-end after Run 10's crash.

## Run 11 — ESCALATED after 3 iterations

**Outcome:** full loop executed cleanly three times. Plateau at 97%
(33/34 scenarios) on a single stuck IMPL_BUG. Early escalation fired
on trajectory=PLATEAU.

### Timeline
| Phase | Status |
|---|---|
| spec-validator | PASS |
| architect (iter 1) | PASS — manifest parsed cleanly (4.6b.1f win) |
| qa-holdout | PASS |
| architect (iter 2) | PASS |
| test-engineer | PASS — 34 scenarios across 8 files |
| smoke | 0 crash, 29 tautological → SA repair triggered |
| iter 1 | pre-gate PASS, smoke PASS, gate 97%, review PASS, Oracle NO-SHIP |
| iter 2 | pre-gate FAIL(1), smoke PASS, gate 97%, review PASS, Oracle NO-SHIP |
| iter 3 | pre-gate PASS, smoke PASS, gate 97% → ESCALATED |

### Framework validations

**4.6b.1f (manifest parser) — VALIDATED.** Architect ran twice
(04:17, 04:51) and produced a manifest that parsed cleanly both
times. No `AttributeError: 'str' object has no attribute 'get'`.
Run 10 crash is fixed.

**4.6b.1d (gate parser + fail-loud) — VALIDATED.** Telemetry:
```
iter1: reported_failed=1 reported_total=34 parsed_failed_rows=1
iter2: reported_failed=1 reported_total=34 parsed_failed_rows=1
iter3: reported_failed=1 reported_total=34 parsed_failed_rows=1
```
`reported_failed == parsed_failed_rows` for all three iterations —
the fail-loud invariant never tripped, meaning the shell parser and
per-test writer stayed in agreement. Gate produced a real 97%
figure, not a 0%/100% collapse. Run 9 failure mode is fixed.

**4.6b.1c (defect classifier + telemetry) — still healthy.**
Classifier produced real defects.json (1 IMPL_BUG per iter) and
per-agent telemetry jsonl was written.

### The plateau — single stuck defect

All three iterations failed the same scenario:
```
Server startup and environment validation >
  [AC-1] starts successfully with valid env vars and logs startup message
```
Defect classification (iter 2):
```json
{
  "test_name": "...[AC-1] starts successfully...logs startup message",
  "category": "IMPL_BUG",
  "cited_anchor_ids": ["AC-1"],
  "rationale": "Test exercises AC-1's structured JSON logging
    requirement; implementation outputs human-readable format instead."
}
```
The implementer consistently produced human-readable startup logs
instead of the structured JSON format AC-1 requires. Three iterations
of Oracle NO-SHIP feedback did not move the needle. Trajectory
classifier detected PLATEAU and escalated early at iter 3.

This is the intended failure mode — the framework correctly
identified a stuck defect and escalated rather than burning more
cycles. Whether the root cause is spec ambiguity, implementer
weakness on logging-format specificity, or feedback-generation
fidelity is a question for the escalation analysis, not a framework
bug.

### New finding — Oracle feedback barrier regex false positive

```
WARNING: oracle feedback failed validation (attempt 2/3):
  Feedback violated 1 barrier rule(s):
  pattern '\\b(?:all|every|none|no)\\s+(?:\\d+\\s+)?scenarios?\\b':
  ['all scenarios']
```

The anti-leak regex that prevents Oracle from referencing concrete
scenario counts or identities tripped on the benign phrase "all
scenarios" in iteration-N Oracle feedback. Two retries burned
before a compliant draft emerged. Not a crash — the guardrail
worked as designed — but the regex is over-eager: generic English
("all scenarios passed the structural checks") is not a barrier
leak in any meaningful sense, since "all" conveys no count or
identity information.

Regex lives at `darkfactory/audit/checker.py:74`:
```python
re.compile(r'\b(?:all|every|none|no)\s+(?:\d+\s+)?scenarios?\b', re.IGNORECASE)
```
Its sibling at line 70 catches explicit `N/M scenarios` and line 72
catches percentages, so line 74's real job is quantified-count
leakage like `"all 73 scenarios"` plus verb-based leaks like
`"no scenarios passed"`. The bare phrase `"all scenarios"` with no
number and no pass/fail verb leaks no count information.

**Candidate 4.6b.1g — two-part, not a single regex swap:**
1. Make `\d+` mandatory in the line-74 pattern (drops the bare
   `"all scenarios"` false positive, preserves `"all 73 scenarios"`).
2. Add a companion pattern to retain verb-based leak coverage:
   `\b(?:all|no|none)\s+scenarios?\s+(?:passed|failed|pass|fail)\b`.

Dropping line 74 outright OR removing the no-digit branch without
the companion pattern would let `"no scenarios passed"` through —
that IS a real leak shape. Neo caught this on review; reject any
simpler-looking one-line fix.

### Iter-0 telemetry baseline (not a finding)

`gate-telemetry.jsonl` iter0 row shows
`reported_failed=0, reported_total=0, parsed_failed_rows=5`. This
looks like an invariant violation at a glance, but it is the
pre-implementation smoke-repair baseline (the 29 tautological
scenarios the SA rewrote) — there is no gate run at iter0, so the
fail-loud check does not apply. Worth confirming the check is
scoped to `iter>=1` in `_assert_scenario_count_invariant`; if it
ever fires on iter0, that's a separate bug.

### Latent drift — defect test_name serialization

Neo noted: iter1 defect `test_name` is `"[AC-1] starts successfully..."`
while iter2 is `"Server startup and environment validation > [AC-1]
starts successfully..."` — same test, different string. If
stable-failure / plateau detection anywhere keys on exact
`test_name` match across iterations, that drift defeats plateau
detection on runs where the classifier serialization flips.
Run 11's PLATEAU fired correctly, so either the detector is
tolerant or the drift didn't matter here — but the shape is
fragile. Worth a grep before the next escalation-logic change.

### Status summary

| Concern | Status |
|---|---|
| Run 10 manifest crash (4.6b.1f) | FIXED — verified live |
| Run 9 gate parser collapse (4.6b.1d) | FIXED — verified live across 3 iters |
| 4.6b.1c defect classifier + telemetry | still healthy |
| New: Oracle feedback regex false positive | documented, candidate 4.6b.1g (two-part) |
| New: iter0 telemetry row confirm iter>=1 scoping | low-priority audit |
| New: defect test_name serialization drift | latent — audit before next escalation-logic change |
| New: implementer stuck on AC-1 structured logging | escalated correctly |
| 4.6b.1e deferred control-flow fix | still pending |
