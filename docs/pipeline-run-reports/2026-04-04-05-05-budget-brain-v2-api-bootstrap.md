# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date (UTC):** 2026-04-04 05:05
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Run ID:** aee3be068d4e4af9
**Outcome:** ESCALATED after 3 iterations (NO-SHIP plateau at 95%)
**Total wall time:** ~30 minutes (05:05:46 — 05:35:46)
**Estimated cost:** $210 (iter1 $84 + iter2 $42 + iter3 $84)

## Pre-Run State

- Previous run (run_id `8a60a78e7a0c4f5e`) ended with `status=interrupted`, `iteration=4`, `last_decision=NO-SHIP`, `pass_rate=77`, `gate_exit_code=2`
- Full repo reset: tracked files restored, untracked deleted, all `.dark-factory/` runtime artifacts removed
- Docker images rebuilt (all cached)
- Agents/templates synced

## Setup Phase Timeline

| Agent | Wall (s) | Notes |
|-------|----------|-------|
| Spec Validator | 84 | PASS (score=95, divergence=LOW, 0 blockers) |
| Architect | 140 | Generated architecture manifest |
| Scenario Architect | 127 | Generated 22 scenarios across 4 files |

**Scenario files:** `01-health-endpoint`, `02-error-handling`, `03-security-rate-limiting`, `04-response-envelopes`

Compared to prior run: fewer scenarios (22 vs 31), different file groupings (4 files vs 5), no dedicated edge-cases file.

---

## Iteration 1

### Implementation
- Attempt 1: 294s (hit turn limit)
- Attempt 2: 89s
- Total agent wall: 383s

### Quality Gate
```
| Gate           | Status |
|----------------|--------|
| Build          | PASS   |
| Lint           | PASS   |
| Type Check     | PASS   |
| Self-Tests     | SKIP   |
| Mutation       | SKIP   |
```

**Scenario results: 21/22 passed (95%), 0 crashed**

### Failing Scenario
```
DF-SCENARIO: health endpoint returns 200 with ok status when no external checks FAILED
  Detail: Step 1: body missing field 'data.uptime'
```

### Review
- Reviewer: 92s — scored 91/100, recommended APPROVE
- Oracle attempt 1: 103s, attempt 2: 57s — decided **NO-SHIP**

### Feedback (iter1)
- Classified FR-1 as IMPL_BUG, FR-2 as SPEC_AMBIGUOUS
- Cited: missing server entry point, test infrastructure absence, startup log format

---

## Iteration 2

### Implementation
- Single attempt: 176s (200 turns) — improvement over iter 1's 400 turns

### Quality Gate
**Same result: 21/22 passed (95%), same single failure**

```
DF-SCENARIO: health endpoint returns 200 with ok status when no external checks FAILED
  Detail: Step 1: body missing field 'data.uptime'
```

- **Trajectory: PLATEAU** — "Fundamentally different approach needed"
- Cost: $42 (half of iter 1 — only 200 turns)

### Review
- Reviewer: 193s — significantly longer than iter 1 (92s)
- Oracle attempt 1: 69s, attempt 2: 58s — decided **NO-SHIP**

### Feedback (iter2)
- Classified FR-1 as SPEC_AMBIGUOUS, FR-2 as IMPL_BUG
- Cited: redundant `level` field in startup log, request lifecycle logging verification
- Notably did NOT mention the `data.uptime` health check issue — the feedback focused on log format

---

## Iteration 3

### Implementation
- Attempt 1: 140s, Attempt 2: 72s (total: 212s)

### Quality Gate
**Same result: 21/22 passed (95%), same single failure**

- Anomaly detected: `turns=1000 > max=800`
- Trajectory: PLATEAU (3rd consecutive)

### Escalation
- Escalation agent: 57s — completed successfully (unlike previous run where it hit rate limit)
- Produced a detailed escalation report

---

## Final State File
```
spec=2026-03-28-api-bootstrap
run_id=aee3be068d4e4af9
last_decision=NO-SHIP
server_error=
smoke_result=passed
phase=review
pass_rate=95
gate_exit_code=1
status=interrupted
iteration=4
completed_step=2
```

---

## Analysis & Observations

### 1. Significant Improvement Over Prior Run (77% → 95%)

| Metric | Prior run (8a60a78e) | This run (aee3be06) |
|--------|---------------------|---------------------|
| Scenarios generated | 31 | 22 |
| Iter 1 pass rate | 77% (7 failures) | 95% (1 failure) |
| Iter 2 pass rate | 77% (7 failures) | 95% (1 failure) |
| Static gates | All PASS | All PASS |
| Trajectory | PLATEAU | PLATEAU |
| Outcome | ESCALATED (rate limited) | ESCALATED (clean) |
| Est. cost | $252 | $210 |

The improvement appears driven by better scenario quality — 22 tighter scenarios instead of 31 that included several debatable edge cases (CORS preflight without `Access-Control-Request-Method`, health with query params, malformed JSON body parsing).

### 2. Phantom Scenario Failure — `data.uptime` Missing

The single blocking failure is **puzzling**. The implementation at `apps/api/src/common/health.ts:53-57` clearly returns:
```typescript
return reply.status(200).send({
  data: {
    status: overallStatus,
    uptime,    // <-- clearly present
    checks,
  },
});
```

The `uptime` variable is computed at line 28: `const uptime = (Date.now() - serverStartTime) / 1000;`

**Possible explanations:**
1. **Serialization issue**: The `uptime` value might be serialized as a string or in an unexpected format that the scenario runner doesn't recognize as a valid field
2. **Route registration timing**: The health plugin might not be properly registered when the scenario runs
3. **Response envelope interference**: The error handler or a hook could be wrapping/transforming the response
4. **Scenario runner bug**: The `"Body contains field"` assertion might have a parsing issue with numeric values at the top level of `data`

The fact that the companion scenario `"health endpoint includes uptime as a number"` PASSES (it separately tests `GET /health` and checks `data.uptime`) while this combined scenario fails suggests the `data.uptime` assertion failure is dependent on the other assertions in the same scenario, or there's an ordering/timing issue in the multi-assertion check.

**This is the key pipeline improvement opportunity**: A single scenario with 6 assertions fails on assertion 4 (`data.uptime`) even though a separate scenario testing the same endpoint and field passes. The scenario runner may be evaluating assertions in a way that causes false failures when multiple fields are checked in a single scenario.

### 3. Oracle NO-SHIPs at 95% Despite Reviewer APPROVE

The reviewer scored 91/100 and recommended APPROVE. The oracle overruled this and issued NO-SHIP — three times. At 95% pass rate with only 1 scenario failing and the reviewer approving, the oracle is being overly strict. The 100% pass rate requirement forces 3 full iteration cycles ($210) for what may be a scenario runner issue rather than an implementation bug.

### 4. Feedback Doesn't Address the Actual Failure

Neither the iter1 nor iter2 feedback mentions the `data.uptime` health endpoint failure. Instead, they focus on:
- Startup log format (`level` field)
- Request lifecycle logging
- Test infrastructure

This means the implementer is receiving feedback about issues that aren't causing scenario failures, while the actual scenario failure (missing `data.uptime`) isn't communicated. The feedback system is misdiagnosing the blocking issue.

### 5. Agent Turn Consumption

| Phase | Iter 1 | Iter 2 | Iter 3 |
|-------|--------|--------|--------|
| Implementer turns | 400 | 200 | 400 |
| Implementer attempts | 2 | 1 | 2 |
| Oracle attempts | 2 | 2 | 1 |
| Total iter cost | $84 | $42 | $84 |

Iter 2 was more efficient (single implementer attempt, 200 turns). The anomaly detector flagged total turns at 1000 > max 800.

### 6. Escalation Quality

Unlike the prior run where the escalation agent hit rate limits and produced `"You've hit your limit"`, this run's escalation completed cleanly (57s) and produced a comprehensive report. However, the escalation report has a factual error — it claims the final iteration was "APPROVED" when it was actually NO-SHIP like every other iteration.

---

## Comparison Across Runs (This Spec)

| Run | Date | Pass Rate | Failures | Outcome | Cost |
|-----|------|-----------|----------|---------|------|
| 7405926e | ~Apr 3 | 79% | 7/31 | ESCALATED (rate limit) | ~$252 |
| 8a60a78e | Apr 4 04:22 | 77% | 7/31 | ESCALATED (rate limit) | ~$252 |
| aee3be06 | Apr 4 05:05 | 95% | 1/22 | ESCALATED (clean) | ~$210 |

Progress: scenario generation improved significantly (fewer, better scenarios). Implementation quality is high (reviewer 91/100). The bottleneck is now a single ambiguous scenario failure that the feedback system can't communicate to the implementer.

---

## Recommendations for Pipeline Improvement

1. **Investigate scenario runner multi-assertion behavior**: The `data.uptime` failure while a separate `uptime` scenario passes suggests a bug or edge case in how multi-assertion scenarios evaluate. Should be reproducible by running the scenario runner against the current implementation.

2. **Oracle should consider SHIP at very high pass rates with reviewer approval**: At 95% with 91/100 reviewer score, burning 3 iterations for 1 ambiguous failure is wasteful. Consider a threshold (e.g., 95%+ with reviewer APPROVE = SHIP with caveats).

3. **Feedback must reference actual scenario failures**: The feedback system should translate scenario assertion failures into spec-anchored hints. Currently it's providing feedback about issues the reviewer notices but that aren't causing gate failures.

4. **Reduce oracle turn consumption**: The oracle ran twice in iters 1 and 2 (hitting turn limit), adding ~50-100s each time. The oracle prompt may need tightening to fit within a single invocation.
