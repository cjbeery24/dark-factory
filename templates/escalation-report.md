# Escalation Report: [Spec Name]

**Spec:** [spec-name]
**Date:** [YYYY-MM-DD]
**Iterations Attempted:** 3
**Pipeline Run ID:** [from audit log]

---

## Per-Iteration History

### Iteration 1
- **What was implemented:** [summary]
- **Quality gate result:** [PASS/FAIL with details]
- **Failing scenarios:** [list]
- **Oracle decision:** NO-SHIP
- **Feedback given:** [summary]

### Iteration 2
- **What was implemented:** [summary]
- **Quality gate result:** [PASS/FAIL with details]
- **Failing scenarios:** [list]
- **Oracle decision:** NO-SHIP
- **Feedback given:** [summary]

### Iteration 3
- **What was implemented:** [summary]
- **Quality gate result:** [PASS/FAIL with details]
- **Failing scenarios:** [list]
- **Oracle decision:** NO-SHIP
- **Feedback given:** [summary]

---

## Persistent Issues

Issues that were NOT resolved across all 3 iterations:

| # | Issue | Iterations Present | Scenario(s) Affected |
|---|-------|--------------------|---------------------|
| 1 | [description] | [1,2,3] | [scenario names] |

---

## Root Cause Analysis

**Most likely root cause category:** (check one)
- [ ] Spec ambiguity or incompleteness
- [ ] Wrong architectural approach
- [ ] Scenarios testing beyond spec scope
- [ ] Feature too complex for single spec
- [ ] External dependency issue
- [ ] Test infrastructure problem
- [ ] Other: [describe]

**Evidence:**
[Why you believe this is the root cause]

---

## Recommended Action

- [ ] Revise specification and re-enter pipeline
- [ ] Decompose into smaller specs
- [ ] Regenerate scenarios (spec was correct, scenarios were flawed)
- [ ] Manual human implementation needed
- [ ] Architectural redesign required

**Details:**
[Specific recommendations]

---

## Artifact Locations

- Spec: `.dark-factory/specs/[name].md`
- Scenarios: `.dark-factory/scenarios/[name]/`
- Quality gate reports: `.dark-factory/reports/[name]-quality-gate.md`
- Reviews: `.dark-factory/reviews/[name]-review.md`
- Feedback files: `.dark-factory/reviews/[name]-feedback-iter[N].md`
- Audit log: `.dark-factory/audit/[name]-[run-id].log`
- State file: `.dark-factory/state/[name].state`
