# 13:50 run fixtures

These two files are **real production data** captured from the
2026-04-28 13:50 pipeline run against `budget-brain-v2` for the
`2026-03-28-database-foundation` spec.

**Do NOT edit these files.** They are evidence pinning a specific
historical failure mode (the persisted-but-not-consumed cascade-
detection gap that motivated Phase 4 of the higher-priority queue
arc — see `docs/HIGHER-PRIORITY-QUEUE-PLAN.md` and the run report
at `docs/pipeline-run-reports/2026-04-28-13-50-budget-brain-v2-database-foundation-three-gaps-validation.md`).

**Do NOT use these as templates** for new test data. The classifier's
`evidence` field paraphrases the substrate finding's `message`
field — that paraphrase is the bug Phase 4 fixes by reading from
the canonical artifact instead. Hand-crafted test data should use
the canonical message verbatim if the goal is to test happy-path
behavior; these fixtures are useful precisely BECAUSE they exhibit
the paraphrase-loss behavior we're guarding against.

## What's here

- **`defects-iter1.json`** — classifier output from iter-1 of the
  13:50 run. 18 defects total, 18/18 carrying structured
  `upstream_cause` field. Two distinct primitives:
  `substrate-bootstrap-not-applied` (11 defects) and
  `substrate-guc-unregistered` (7 defects).

- **`substrate-findings.json`** — substrate-phase verifier output
  from the same iteration. Two findings whose `code` fields match
  the defects' `upstream_cause.primitive` values.

## Why these are committed as-is

Per Linus belated review (2026-04-29 Phase 4 code review), the
"longest-wins evidence" heuristic he initially suggested isn't
sufficient — the canonical source was the right architectural
fix. These fixtures pin both:
1. The fact that the classifier paraphrases (the bug we're guarding against)
2. The fact that mechanical injection from `substrate-findings.json`
   recovers the canonical message verbatim (the fix's load-bearing
   property)

If the classifier output schema changes, these fixtures may go stale.
That's intended — staleness will surface as test failures and force
a deliberate review of the contract change against the canonical-
source design. Don't auto-regenerate these fixtures.
