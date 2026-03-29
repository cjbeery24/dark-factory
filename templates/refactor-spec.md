# Refactoring Specification: [What Is Being Refactored]

<!-- dark-factory-metadata
feature-id: F-X
depends-on: []
plan:
profile:
-->

**Author:** [name]
**Date:** [YYYY-MM-DD]
**Risk Level:** HIGH | MEDIUM | LOW
**Status:** DRAFT | VALIDATED | IN PROGRESS | COMPLETE

---

## 1. Motivation

**Why refactor now?**
[Concrete reason: tech debt blocking a feature, performance bottleneck, maintainability crisis, etc.]

**What is the current state?**
[Describe the current architecture/code structure and its problems]

**What will the code look like after?**
[Describe the target architecture/code structure]

**What does NOT change?**
[External behavior, API contracts, data formats — the refactoring is invisible to consumers]

---

## 2. Scope

### Files to Modify
| File | Change Type | Description |
|------|-------------|-------------|
| [path] | Restructure | [What changes and why] |
| [path] | Extract | [What is being extracted and where] |
| [path] | Inline | [What is being simplified] |
| [path] | Rename | [Old name → New name] |
| [path] | Delete | [Why this file is no longer needed] |

### Files to Create
| File | Purpose |
|------|---------|
| [path] | [Why this new file is needed] |

### Files NOT to Touch
| File | Reason |
|------|--------|
| [path] | [Why this is explicitly excluded from the refactoring] |

---

## 3. Behavioral Invariants

**These behaviors MUST remain identical before and after the refactoring:**

| ID | Invariant | How to Verify |
|----|-----------|---------------|
| INV-1 | [External API response format unchanged] | [Run existing API tests] |
| INV-2 | [Database schema unchanged] | [Compare schema before/after] |
| INV-3 | [Function X returns same output for same input] | [Run existing unit tests] |
| INV-4 | [Event emissions unchanged] | [Verify event payloads] |

---

## 4. Structural Changes

### Current Architecture
```
[ASCII diagram or description of current structure]
```

### Target Architecture
```
[ASCII diagram or description of target structure]
```

### Migration Steps (ordered)
1. [Step 1: e.g., "Create new module structure"]
2. [Step 2: e.g., "Move function X from A to B"]
3. [Step 3: e.g., "Update imports in all consumers"]
4. [Step 4: e.g., "Delete old file A"]
5. [Step 5: e.g., "Run full test suite to verify"]

---

## 5. Constraints

- **Zero behavior change:** All existing tests must pass without modification
- **Incremental:** Each migration step should leave the codebase in a working state
- **No new dependencies:** Unless explicitly listed here: [list or "none"]
- **Naming conventions:** [Follow existing conventions or specify new ones]
- **Pattern to follow:** [e.g., "Use the same pattern as src/features/users/"]

---

## 6. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| [Breaking existing tests] | [Low/Med/High] | [Low/Med/High] | [Run tests after each step] |
| [Missing import update] | [Low/Med/High] | [Low/Med/High] | [Use IDE/grep to find all references] |
| [Performance regression] | [Low/Med/High] | [Low/Med/High] | [Benchmark before/after] |

---

## 7. Acceptance Criteria

### Tier 1: Test Contracts (compiled deterministically)

These contracts are compiled to test code mechanically by the pipeline. They
produce identical tests every run. Use for verifiable structural changes.

```yaml
contracts:
  - id: TC-1
    fr: FR-1
    type: command
    given: "refactored code"
    when:
      cmd: "[test suite command]"
    then:
      exit_code: 0
    invariant: true    # already verified by self-tests gate

  - id: TC-2
    fr: FR-1
    type: file_exists
    given: "after refactoring"
    when:
      check: "[new file location]"
    then:
      exists: true
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
| AC-1 | No dead code remains (all deleted files/functions are truly unused) |
| AC-2 | Import paths are clean (no circular dependencies introduced) |
| AC-3 | [Performance metric] is within [X%] of pre-refactoring baseline |

---

## 8. Scenario Intent

Declare what scenarios should verify for each requirement. The Scenario Architect
compiles these intents into executable test code — it does not invent scenarios
beyond what is declared here plus a small cross-cutting set.

### Behavioral Invariants
| # | Intent | Layer |
|---|--------|-------|
| 1 | Public API returns identical results before and after | contract |
| 2 | Error responses are unchanged for all documented error conditions | contract |
| 3 | Event emissions and side effects remain identical | contract |
| 4 | Data format and serialization output is unchanged | contract |
| 5 | Edge case inputs produce the same outputs as before | edge_case |

### Structural Quality
| # | Intent | Layer |
|---|--------|-------|
| 1 | No circular dependencies introduced | property |
| 2 | Performance does not regress beyond threshold | property |
| 3 | All existing tests pass without modification | contract |
| 4 | No dead code remains after refactoring | property |
| 5 | Import paths resolve correctly after restructuring | happy_path |

Layer values: `contract`, `happy_path`, `edge_case`, `error`, `state`, `security`, `property`, `adversarial`

---

## 9. Examples

### Example 1: Happy path — behavior preserved
```
Input: [typical input to the refactored code]
Expected output: [same output as before refactoring]
```

### Example 2: Error case — error behavior preserved
```
Input: [input that triggers an error condition]
Expected output: [same error response/behavior as before refactoring]
```

### Example 3: Edge case — boundary behavior preserved
```
Input: [edge case input]
Expected output: [same output as before refactoring]
```

---

## 10. Out of Scope

- [Feature changes disguised as refactoring]
- [Performance optimization beyond structural changes]
- [Test refactoring (unless tests must move with their source)]

---

## 11. Free Variables (implementation choices — DO NOT assert in scenarios)

List implementation decisions this spec intentionally leaves unconstrained.
The Scenario Architect will treat these as DO-NOT-ASSERT directives.

| ID | Variable | What Is Constrained | What Is Free |
|----|----------|---------------------|--------------|
| FV-1 | [e.g., Internal module structure] | [e.g., Public API must remain identical] | [e.g., How internal helpers are organized] |

If no implementation choices are free, write "None — all observable behaviors are fully constrained."
