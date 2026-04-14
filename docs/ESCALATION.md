# Escalation Workflow

## Overview

Escalation occurs when the Dark Factory pipeline reaches **iteration 3 with a NO-SHIP decision**. At this point, the automated pipeline has exhausted its retry budget and human judgment is required to determine the next step.

The pipeline enforces a maximum of 3 iterations by design. This limit exists to prevent infinite loops of failing implementations and to surface systemic problems early. When you reach escalation, the issue is almost never "the AI just needs one more try" -- it is a structural problem with the spec, the approach, or the scenarios.

This document provides a systematic framework for diagnosing the root cause and re-entering the pipeline effectively.

---

## Decision Tree

When an escalation occurs, work through these questions in order. Stop at the first one that applies.

### 1. Is this a spec problem?

**Symptoms:** The implementer produces reasonable code that does not match what the scenarios expect. Feedback mentions ambiguous or missing requirements. Different iterations interpret the same requirement differently.

**Diagnosis:** Compare the spec against the failing scenarios. If the spec does not clearly define the expected behavior that the scenarios are testing, the spec is incomplete or ambiguous.

**Action:** Revise the specification to clarify the ambiguous sections. Re-validate the spec with the Spec Validator agent, then re-enter the pipeline.

### 2. Is this an approach problem?

**Symptoms:** The implementation hits fundamental technical limitations. The architecture cannot satisfy the requirements as written. Each iteration restructures the code significantly but still fails.

**Diagnosis:** The spec may be technically correct but prescribes (or implies) an approach that does not work. Alternatively, the spec may leave the approach open, but the implementer consistently chooses a flawed architecture.

**Action:** Revise the spec to add or change architectural constraints, or explicitly specify the approach. If the current approach is fundamentally wrong, document why and specify the correct approach in the spec. Re-validate and re-enter the pipeline.

### 3. Is this a scenario problem?

**Symptoms:** The implementation appears correct based on a careful reading of the spec, but scenarios test behavior not described in the spec. Scenarios make assumptions about implementation details (internal state, specific error messages not in the spec, etc.).

**Diagnosis:** Review the failing scenarios against the spec. If a scenario tests something that cannot be derived from the spec alone, the scenario is out of scope.

**Action:** Review and potentially regenerate scenarios. Use `--fresh` when re-entering the pipeline to clear existing scenarios. Ensure the Scenario Architect agent works strictly from the spec.

### 4. Is scope too large?

**Symptoms:** The implementation partially works but cannot handle all requirements within a single pass. The spec covers many distinct behaviors. The implementer addresses some scenarios but breaks others in each iteration.

**Diagnosis:** Count the distinct behaviors or capabilities in the spec. If there are more than 5-7 distinct functional areas, the spec may be too large for a single pipeline run.

**Action:** Decompose the spec into multiple smaller specs, each covering a cohesive subset of functionality. Order them by dependency (build foundational specs first). Run each through the pipeline independently.

---

## Common Failure Patterns

| Pattern | Likely Cause | Recommended Action |
|---------|--------------|-------------------|
| Same scenario fails identically across all 3 iterations | Spec does not describe the expected behavior, or feedback is not actionable | Revise the spec to explicitly define the behavior. Enrich Oracle feedback detail. |
| Different scenarios fail each iteration (whack-a-mole) | Spec is too large or requirements conflict with each other | Decompose into smaller specs. Check for contradictory requirements. |
| All scenarios fail every iteration | Fundamental approach mismatch or spec is far from implementable | Re-evaluate the architectural approach. Verify the spec is technically feasible. |
| Only edge case scenarios fail | Core logic is sound but boundary conditions are under-specified | Add explicit edge case behavior to the spec (empty inputs, max values, error conditions). |
| Timeout failures (tests do not complete) | Performance requirements not specified, or implementation has infinite loops/deadlocks | Add performance constraints to the spec. Check for concurrency issues in the approach. |

---

## Re-Entry Procedures

After diagnosing and fixing the root cause, follow these steps to re-enter the pipeline.

### 1. Re-validate the spec

If the spec was modified, it **must** be re-validated before re-entering the pipeline. Run the Spec Validator agent on the updated spec.

```bash
claude "You are the Spec Validator. Read the agent definition at
agents/spec-validator.md, then validate the specification at
.dark-factory/specs/my-feature.md. Produce the validation report
exactly as specified in your agent definition."
```

### 2. Optionally regenerate scenarios

If the spec changed substantively, existing scenarios may no longer be valid. Regenerate them with a fresh Scenario Architect session.

```bash
claude "You are the Scenario Architect. Read the agent definition at
agents/scenario-architect.md, then read the validated specification at
.dark-factory/specs/my-feature.md. Generate comprehensive blind test
scenarios. Write executable test files to .dark-factory/scenarios/my-feature/.
You MUST NOT read any implementation source code."
```

### 3. Clear previous state and re-enter

Use the `--fresh` flag to clear previous iteration state and start from iteration 1.

```bash
dark-factory run my-feature --fresh
```

Without `--fresh`, the pipeline resumes from the last iteration count, which means it may immediately escalate again if it was already at iteration 3.

---

## Stale Scenario Warning

If a spec is modified after scenarios have already been generated, the existing scenarios may be **outdated**. They were derived from the original spec and may test behaviors that have changed or been removed.

**Always regenerate scenarios when:**
- Requirements were added, removed, or changed in the spec
- The interface contract (inputs, outputs, error handling) was modified
- The scope of the spec was expanded or reduced

**Scenarios can be reused when:**
- Only the approach or architectural constraints changed (not the external behavior)
- Clarifications were added that do not change expected behavior
- Only non-functional requirements (performance, logging) were modified

When in doubt, regenerate. Stale scenarios produce misleading results.

---

## Force-Ship Guidance

Dark Factory does not support a force-ship mechanism by design. The quality gates and iteration limits exist to ensure that shipped code meets specification requirements. Bypassing them undermines the framework's core value.

However, there are situations where manual intervention and shipping may be necessary (deadline pressure, partial functionality acceptable, etc.). If you must proceed without a SHIP decision:

1. **Manually review all artifacts.** Read the spec, the implementation, all scenario results, and all reviewer feedback. Understand every failing scenario and why it fails.

2. **Document known issues.** For each unresolved failure, document what does not work, under what conditions, and what the expected behavior should be.

3. **Create follow-up specs.** For every known issue, create a new spec that describes the fix. These specs enter the pipeline as normal work items.

4. **Mark the commit.** Tag or annotate the commit to indicate it did not pass the full Dark Factory pipeline. This provides traceability for future debugging.

5. **File an escalation report.** Use the template at `templates/escalation-report.md` to create a structured record of what happened. This is essential for post-mortem analysis and improving future specs.

This process is intentionally manual and heavyweight. It should feel like a last resort, not a convenient shortcut.

---

## Escalation Report

When an escalation occurs, fill out the structured escalation report template to create a permanent record.

**Template location:** `templates/escalation-report.md`

The escalation report captures:
- Recommended action for resolution (each bullet cites an artifact path)
- Artifact locations for full traceability

Per-iteration history, persistent-issues prose, and root-cause narrative
were removed from the template on 2026-04-14 after a confabulation
incident on the 2026-03-28-database-foundation run, where the agent
fabricated scenario counts, pass-rate math, and scores that did not
exist in the pipeline's artifacts. The human reads the artifacts
directly for that context. See B4 in
`docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md`.

Completed escalation reports should be stored alongside the spec artifacts for future reference and process improvement.

---

## Calibration Dataset (E2)

**Final step of every post-mortem — SHIP or escalated:** append one
calibration row to `docs/post-mortem-dataset.jsonl`. Over time this
dataset becomes input for validator-score calibration, A1
spec↔env pair-table growth, and validator prompt shaping.

```bash
dark-factory post-mortem append \
  --spec <spec-id> \
  --run-id <pipeline-run-id> \
  --date YYYY-MM-DD \
  --target-project <name> \
  --outcome {shipped|escalated|aborted} \
  --iterations <N> \
  --validator-verdict {PASS|FAIL} \
  --validator-score <0-100> \
  --validator-divergence {LOW|MEDIUM|HIGH} \
  --root-causes <code1>,<code2>,... \
  --notes "<one-liner>"
```

`dark-factory post-mortem list-codes` prints the canonical
`ROOT_CAUSE_CODES` vocabulary. New codes are added by editing
`darkfactory/calibration/post_mortem.py` — the dataset's value
depends on codes meaning the same thing across runs, so free-form
strings are rejected by design. See E2 in
`docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md`.
