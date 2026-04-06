# Pipeline Run Report — 2026-04-06 18:58 UTC

- Repo: `budget-brain-v2`
- Spec: `2026-03-28-api-bootstrap`
- dark-factory HEAD: `0e0a0be` (Pass fresh=True from validate so stale pipeline state never blocks)

## Setup
- Reset budget-brain-v2 (`git restore`, `git clean -fd .dark-factory/`, removed `reports/audit/state/scenarios/failures/events.jsonl`).
- Cleared all `__pycache__` under dark-factory.
- Refreshed `.dark-factory/agents` and `.dark-factory/templates` from dark-factory repo.
- `pip install -e ~/sites/dark-factory` — clean.
- `dark-factory build node` — all images cached, built successfully.
- Cleaned 0 orphan containers / 0 orphan networks.

## Run 1 — `dark-factory run 2026-03-28-api-bootstrap`

### Outcome
**BLOCKED at spec pre-check.** Pipeline never reached any agent. State file:
```
status=interrupted
iteration=1
completed_step=0
```

### Pre-check failure (excerpt from /tmp/df-run.log)
```
ERROR: error: spec pre-check failed:
Pre-check FAILED. 10 issues, 3 ambiguity warnings.
Scenario Intent anchor coverage: 10 missing
  - AC-1 declared but missing from Scenario Intent section
  - AC-2 declared but missing from Scenario Intent section
  ... (AC-3 through AC-10) ...
Ambiguity warnings: 3
  - line 223: "appropriate" in: | EC-9 | CORS request from disallowed origin | ...
  - line 235: "etc." in: | Non-Error thrown (string, object, etc.) | ...
  - line 425: "etc." in: | FV-5 | AppError inheritance | ...
```

### Diagnosis
The Scenario Intent section (`## 9. Scenario Intent`, lines 259–312 of the spec) is
organized by **`FR-N` headings with numbered intent rows + a `Cross-Cutting` block**, not
by `AC-N` references. Example:

```
### FR-1: Create Fastify server with environment validation
| # | Intent | Layer |
|---|--------|-------|
| 1 | Server starts and listens on configured PORT with valid env vars | happy_path |
...
```

The pre-check (introduced by commit `18c7325` — "Replace regex shape-claim and AC checks
with markdown-AST model") requires every `AC-N` declared in §8 Acceptance Criteria to
also appear textually in §9 Scenario Intent. The current spec template / authoring style
maps ACs to FRs (which the Scenario Intent rows are grouped under) rather than re-citing
each `AC-N` token, so all 10 ACs are flagged as "missing" even though each is covered by
intent rows under the corresponding FR group.

This looks like a **pre-check / spec-format contract mismatch**, not a defect in the
spec content. The spec was previously runnable; nothing in budget-brain-v2 changed
between runs except dark-factory pipeline code being updated.

### Ambiguity warnings (3)
Two are tokens inside parenthetical examples ("etc.", "object, etc.") and one is an EC
description that uses "appropriate" to refer to standard CORS-header behavior. These are
warnings, not blockers, but worth flagging — they indicate the ambiguity heuristic is
also catching innocuous tokens inside Edge Case / Free Variable tables.

### Logs / artifacts
- `/tmp/df-run.log` — full CLI output
- `.dark-factory/state/2026-03-28-api-bootstrap.state` — `status=interrupted, completed_step=0`
- No `events.jsonl`, no agent containers spawned, no quality-gate report generated.

## Conclusion
Pipeline run aborted at step 0. Per instructions, no fix attempted. Two issues to track:

1. **Pre-check `AC-N` coverage rule is incompatible with FR-grouped Scenario Intent
   layout** used by the existing api-bootstrap spec (and likely other 2026-03-28 specs
   in this repo). Either the precheck needs to recognize FR-N→AC-N coverage transitively
   via §8, or the spec template needs to require literal `AC-N` tokens in Scenario Intent
   rows. This is a regression vs. earlier runs where the same spec progressed past
   pre-check.
2. **Ambiguity heuristic flags `etc.` / `appropriate` inside example/free-variable
   prose**, which is noisy. Consider scoping ambiguity scan to FR/AC/EC requirement
   columns only.
