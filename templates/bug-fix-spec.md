# Bug Fix Specification: [Brief Bug Description]

<!-- dark-factory-metadata
feature-id: F-X
depends-on: []
plan:
profile:
-->

**Author:** [name]
**Date:** [YYYY-MM-DD]
**Severity:** CRITICAL | HIGH | MEDIUM | LOW
**Status:** DRAFT | VALIDATED | IN PROGRESS | COMPLETE

---

## 1. Bug Description

**What is happening?**
[Describe the incorrect behavior with exact details]

**What should be happening?**
[Describe the correct behavior with exact details]

**How to reproduce:**
1. [Step 1]
2. [Step 2]
3. [Step 3]
4. Observe: [what you see]
5. Expected: [what you should see]

**Frequency:** [Always / Intermittent (X% of the time) / Only under specific conditions]

**Impact:**
[Who is affected, how many users, business impact, data integrity concerns]

---

## 2. Root Cause Analysis

**Where is the bug?**
- **File(s):** [exact file paths]
- **Function(s):** [exact function names]
- **Line(s):** [approximate line numbers or code block]

**Why does it happen?**
[Explain the logical error, race condition, missing validation, etc.]

**When was it introduced?**
[Commit hash, PR, or "unknown"]

---

## 3. Fix Requirements

### FR-1: [What must change]
- **Current behavior:** [exact current behavior]
- **Required behavior:** [exact required behavior]
- **Affected code:** [file:function]

### FR-2: [What must change]
- **Current behavior:** [...]
- **Required behavior:** [...]
- **Affected code:** [...]

---

## 4. Constraints

- **Must not change:** [List behaviors that must remain unchanged]
- **Must not break:** [List dependent features/systems]
- **Backward compatibility:** [Any API/data compatibility requirements]
- **Maximum scope:** [Limit the fix to specific files/functions — no refactoring]

---

## 5. Edge Cases (Related to the Fix)

| ID | Condition | Expected Behavior |
|----|-----------|-------------------|
| EC-1 | [Condition that triggered the bug] | [Correct behavior after fix] |
| EC-2 | [Related edge case] | [Expected behavior] |
| EC-3 | [Adjacent functionality that must not break] | [Expected behavior] |

---

## 6. Acceptance Criteria

### Tier 1: Test Contracts (compiled deterministically)

These contracts are compiled to test code mechanically by the pipeline. They
produce identical tests every run. Use for verifiable facts about the fix.

```yaml
contracts:
  - id: TC-1
    fr: FR-1
    type: command
    given: "fix is applied"
    when:
      cmd: "[command that reproduces the bug — should now succeed]"
    then:
      exit_code: 0

  - id: TC-2
    fr: FR-1
    type: command
    given: "fix is applied"
    when:
      cmd: "[full test suite command]"
    then:
      exit_code: 0
    invariant: true    # already verified by self-tests gate
```

**Contract types and `when`/`then` schemas:**
- `file_exists` — `when: { check: "path" }` → `then: { exists: true }`
- `file_contains` — two modes:
  - Structured: `when: { file: "path", parse: "json"|"yaml", path: "dot.path" }` → `then: { exists: true }`
  - Pattern: `when: { file: "path", parse: "text", pattern: "regex" }` → `then: { matches: true }`
- `command` — `when: { cmd: "..." }` → `then: { exit_code: 0 }`
- `http` — `when: { method, path, body (request) }` → `then: { status, body (assertions) }`
  - `then.body` values use operators: `{ type: "string" }`, `{ equals: "value" }`, `{ contains: "sub" }`
- `import` — `when: { module: "path", symbols: ["name", ...] }` → `then: { exports: true }`

`invariant` (optional, default `false`): when `true`, the pipeline skips this contract —
it documents that the build/lint/test gate already covers the check.
`given` is freeform text used as the test description in compiled output.

### Tier 2: Adversarial Criteria (SA-generated)

These criteria guide the SA's creative adversarial testing (layers 3-8: edge
cases, error conditions, state, security, property-based, adversarial). The SA
does not re-test what Tier 1 contracts already cover.

| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN [edge case from EC section] WHEN [action] THEN [correct behavior] |
| AC-2 | GIVEN [regression scenario] WHEN [action] THEN [behavior unchanged] |

## Non-Negotiable Acceptance Criteria

List the AC / EC / FR / NFR IDs whose failure must veto SHIP, regardless of
overall pass rate. The pipeline's stable-failure SHIP_WITH_GAPS promotion is
vetoed when any listed ID has a failing scenario.

Leave empty only when the spec has no release-blocking invariants — in that
case a `Justification:` paragraph is required.

- **AC-N** — [one-line reason, e.g. "tenant-isolation invariant"]
- **EC-N** — [one-line reason]

<!-- If no AC is blocking, replace the bullets with a justification paragraph:
Justification: this spec is a pure refactor/documentation/style change with no
runtime-exposed invariants beyond existing coverage. -->

---

## 7. Scenario Intent

Declare what scenarios should verify for each requirement. The Scenario Architect
compiles these intents into executable test code — it does not invent scenarios
beyond what is declared here plus a small cross-cutting set.

### Fix Verification
| # | Intent | Layer | ac_id |
|---|--------|-------|-------|
| 1 | Bug no longer reproduces with original reproduction steps | happy_path | — |
| 2 | Regression: related functionality still works | contract | — |
| 3 | Fix handles the root-cause condition correctly | happy_path | — |
| 4 | Edge cases from EC section are handled | edge_case | — |
| 5 | Null/empty inputs near the bug boundary behave correctly | edge_case | — |

### Side-Effect Prevention
| # | Intent | Layer | ac_id |
|---|--------|-------|-------|
| 1 | Dependent features listed in Constraints still work | contract | — |
| 2 | Public API returns identical results for non-buggy inputs | contract | — |
| 3 | Error responses match expected format post-fix | error | [AC-N] |
| 4 | No new error paths introduced by the fix | error | [AC-N] |
| 5 | Performance does not regress beyond NFR threshold | property | — |

Layer values: `contract`, `happy_path`, `edge_case`, `error`, `state`, `security`, `property`, `adversarial`

---

## 8. Regression Prevention

**What test should exist to prevent this from recurring?**
[Describe the test that, if it had existed, would have caught this bug]

**Root cause category:** [Missing validation / Race condition / Off-by-one / Null handling / Type error / Logic error / Other]

---

## 9. Non-Functional Requirements

### Performance
- [e.g., "Fix must not degrade p95 latency by more than 5%"]

### Security
- [e.g., "Fix must not introduce new attack vectors"]

---

## 10. Interface Contract

**Does this fix change any public interfaces?** Yes / No

If yes:
```
[Define any changes to function signatures, API responses, data types]
```

---

## 11. Error Handling (Post-Fix)

| Condition | Response/Behavior | Error Code |
|-----------|-------------------|------------|
| [Error condition related to the fix] | [Expected behavior after fix] | [Code] |

---

## 12. Examples

### Example 1: Bug reproduction
```
Input: [exact input that triggers the bug]
Current (buggy) output: [what happens now]
Expected (fixed) output: [what should happen]
```

### Example 2: Normal operation (must not change)
```
Input: [normal input]
Expected output: [same as before the fix]
```

### Example 3: Edge case related to fix
```
Input: [edge case input near the bug boundary]
Expected output: [correct behavior]
```

---

## 13. Out of Scope

- [Related issue that should be a separate fix]
- [Refactoring that is not part of this fix]
- [Feature request disguised as a bug fix]

---

## 14. Free Variables (implementation choices — DO NOT assert in scenarios)

List implementation decisions this spec intentionally leaves unconstrained.
The Scenario Architect will treat these as DO-NOT-ASSERT directives.

| ID | Variable | What Is Constrained | What Is Free |
|----|----------|---------------------|--------------|
| FV-1 | [e.g., Fix approach] | [e.g., Must produce correct output for input X] | [e.g., Whether fix is in validation layer or data layer] |

If no implementation choices are free, write "None — all observable behaviors are fully constrained."
