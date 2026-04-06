# Pipeline Run Report — budget-brain-v2 / 2026-03-28-api-bootstrap

**Date:** 2026-04-06 05:17 UTC
**Target repo:** `~/sites/budget-brain-v2`
**Spec:** `2026-03-28-api-bootstrap`
**run_id:** `5cd9f8cec3df417f`
**Final status:** `ESCALATED` after 2 iterations (max iterations = 3 → iter 3 was the escalation agent)

## Pre-run setup
- Reset repo: `git restore .`, `git clean -fdx -e node_modules -e .env -e .env.test`, removed `.dark-factory/{scenarios,reports,reviews,audit,state,failures,events.jsonl}`
- Cleared `__pycache__` across dark-factory
- `pip install -e ~/sites/dark-factory` → reinstalled 1.0.0
- Synced agents/ and templates/ into `budget-brain-v2/.dark-factory/`
- `dark-factory build node` → all layers cached, images ok
- Orphan docker containers/networks cleaned

### Spec pre-check warnings (did not block)
```
WARNING: ambiguous term "appropriate" on line 223 (EC-9 CORS)
WARNING: ambiguous term "etc." on line 235 (Non-Error thrown)
WARNING: ambiguous term "etc." on line 425 (FV-5 AppError)
WARNING: spec pre-check: 3 ambiguity warnings (proceeding)
```

## Pipeline timing (from events.jsonl)

| Phase | iter | wall_s |
|---|---|---|
| architect | – | ~130 |
| scenario-architect | – | 151.6 |
| implementer (iter1, 3 attempts hitting 200-turn cap) | 1 | 196.8 + 212.0 + 122.3 |
| smoke | 1 | passed (attempt 2) |
| gate | 1 | pass_rate **57** (19/33), exit 1 |
| reviewer | 1 | 87.1 |
| oracle | 1 | 79.4 → **NO-SHIP** |
| implementer | 2 | 123.8 |
| smoke | 2 | passed (attempt 1) |
| gate | 2 | pass_rate **72** (24/33), exit 1 |
| trajectory | 2 | IMPROVING |
| reviewer | 2 | 177.8 |
| oracle (2 calls) | 2 | 99.7 + 62.4 → **NO-SHIP** |
| escalation agent | 3 | 66.7 → **SPEC_ESCALATED** |

Total wall clock: ~31 min (05:17 → 05:48Z).
Iter cost estimates: iter1 = $126 (600 turns), iter2 = $84 (400 turns).

## Quality gate summary (iter2)
```
Build PASS | Lint PASS | Type Check PASS | Self-Tests SKIP | Mutation SKIP
Result: PASS   (gate itself is green; the "exit 1" comes from scenario pass rate)
```

## Scenario results

- **iter1**: 19/33 passed (57%), 14 failed, 0 crashed
- **iter2**: 24/33 passed (72%), 9 failed, 0 crashed
- All 9 iter2 failures were a subset of iter1 failures → **stable failure set**

Stable failing scenarios (iter2):
1. AppError details field is included in response when present
2. ZodError is caught and returns 400 with field-level details
3. ZodError with multiple field errors returns all in details array
4. Unknown Error returns 500 with generic message and no stack trace
5. Non-Error thrown value returns 500 with generic message
6. Error response never leaks file paths or internal details
7. Very large request body is handled gracefully
8. Empty request body to endpoint expecting JSON
9. Health endpoint with query parameters

## Oracle classification (iter2 feedback)

Oracle classified 6 of 8 listed failures as **BRITTLE** or **SPEC_AMBIGUOUS**, 2 as SCENARIO_CRASH (despite `scenario_crashed=0` in gate events — mismatch between oracle narrative and telemetry):

| Section | Oracle class |
|---|---|
| FR-2 Logging | BRITTLE |
| FR-3 Error Handling | BRITTLE |
| AC-1 Server Startup | SCENARIO_CRASH |
| AC-7 Log Redaction | BRITTLE |
| EC-1 PORT Validation | BRITTLE |
| EC-2 | BRITTLE |
| EC-5 Nested Field Redaction | SPEC_AMBIGUOUS |
| Health Check Registry | SCENARIO_CRASH |

Escalation trigger (from stdout): `1 SPEC_AMBIGUOUS + 5 BRITTLE out of 8 failures`.

## Anomalies / notable pipeline observations

1. **Implementer iter1 ran 3 sequential 200-turn sessions** before moving to smoke/gate (total 600 turns, ~531s wall). Events log shows three `AGENT_START role=implementer attempt=1` in succession for iter1. iter2 ran only a single implementer pass (200 turns). Not obviously broken, but worth investigating whether a retry/continuation loop is intended at attempt=1.
2. **`ANOMALY_DETECTED spec_turns` at iter2**: `detail="turns=1000 > max=800"`. Cumulative spec turn budget exceeded but pipeline continued.
3. **Oracle agent ran twice at iter2** (`AGENT_START role=oracle attempt=1` appears twice in a row, both completed exit 0). Only one decision was recorded. Possible retry-on-parse-failure path; worth checking whether this is duplicating cost.
4. **Escalation report is wildly over-the-top** (language about "Pipeline Review Board", "24 hour shipping decision", cost analyses in "person-days"). Functional but the tone/hallucinated process may mislead downstream readers.
5. **Spec ambiguity pre-check fires but does not gate.** EC-5 "nested field redaction" was ultimately flagged by the oracle as `SPEC_AMBIGUOUS` — the pre-check did not surface this particular issue; it caught three unrelated "appropriate"/"etc." occurrences. Suggests the ambiguity scanner and oracle classifier use different heuristics.
6. **Oracle classified crashes as `SCENARIO_CRASH` despite `scenario_crashed=0`** in `GATE_COMPLETED` telemetry. Classification appears to be free-form LLM text not grounded in the structured scenario counts.
7. **State file `phase=review` during iter2 reviewer step** but no write to `phase=oracle_running` / `phase=oracle_complete` until after oracle. State progression is coarse-grained relative to events.

## State file excerpts

End of run:
```
status=interrupted
iteration=3
completed_step=2
last_decision=NO-SHIP
phase=oracle_complete
smoke_result=passed
pass_rate=72
gate_exit_code=1
```
Note: `status=interrupted` on a natural escalation termination is confusing — a successful escalation should probably set `status=escalated`.

## Events excerpts

```
05:24:39 AGENT_COMPLETED scenario-architect wall=151.6
05:24:40 AGENT_START implementer iter=1 attempt=1
05:27:57 AGENT_COMPLETED implementer iter=1 turns=200 wall=196.8
05:27:59 AGENT_START implementer iter=1 attempt=1    # re-entry #1
05:31:31 AGENT_COMPLETED implementer iter=1 turns=200 wall=212.0
05:32:53 AGENT_START implementer iter=1 attempt=1    # re-entry #2
05:34:55 AGENT_COMPLETED implementer iter=1 turns=200 wall=122.3
05:34:59 SMOKE_COMPLETED success=true attempt=2
05:35:05 GATE_COMPLETED exit=1 pass_rate=57 scenario_total=33 scenario_crashed=0
05:35:05 ITER_COST turns=600 est=$126
...
05:41:27 GATE_COMPLETED exit=1 pass_rate=72 scenario_total=33 scenario_crashed=0
05:41:27 ANOMALY_DETECTED spec_turns detail="turns=1000 > max=800"
05:41:27 TRAJECTORY_CLASSIFIED IMPROVING
05:44:25 AGENT_COMPLETED reviewer iter=2 wall=177.8
05:44:25 AGENT_START oracle iter=2
05:46:05 AGENT_COMPLETED oracle iter=2 wall=99.7
05:46:05 AGENT_START oracle iter=2   # duplicate start
05:47:08 AGENT_COMPLETED oracle iter=2 wall=62.4
05:47:08 AGENT_START escalation iter=3
05:48:14 AGENT_COMPLETED escalation
05:48:14 SPEC_ESCALATED iterations=2
```

## Conclusion

- The pipeline ran to completion and produced a coherent escalation on a genuinely stable failure set. The trajectory (57 → 72, IMPROVING) is the intended happy path for NO-SHIP → escalation.
- **Main issues to investigate (not fixed in this run):**
  1. Implementer iter1 executed 3 back-to-back 200-turn sessions — is this expected re-entry behavior or an unintended loop that tripled iter1 cost?
  2. Oracle iter2 recorded two `AGENT_START/COMPLETED` pairs — duplicate oracle invocation.
  3. `status=interrupted` final state on a successful escalation is wrong; should be `escalated`.
  4. Oracle's natural-language failure classifications (`SCENARIO_CRASH`) contradict structured telemetry (`scenario_crashed=0`). Oracle classification should be grounded in the gate's counts.
  5. Spec ambiguity pre-check missed EC-5 nested redaction — an issue the oracle downstream flagged explicitly.
  6. Iter2 crossed the spec-turn budget (`1000 > 800`) without any circuit-breaker action beyond logging `ANOMALY_DETECTED`.
  7. Escalation report quality: the agent fabricates org process (review boards, 24-hour SLAs, person-day estimates). Tone is unprofessional for a report artifact.
- **Not investigated / blocked:** whether the 9 stable failures are actually BRITTLE vs real implementation gaps — that requires reading source + scenarios, which is outside the scope of this test run.
