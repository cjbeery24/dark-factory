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

| ID | Criterion |
|----|-----------|
| AC-1 | All existing tests pass without modification |
| AC-2 | New structure matches the target architecture diagram |
| AC-3 | No dead code remains (all deleted files/functions are truly unused) |
| AC-4 | Import paths are clean (no circular dependencies introduced) |
| AC-5 | [Performance metric] is within [X%] of pre-refactoring baseline |

---

## 8. Examples

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

## 9. Out of Scope

- [Feature changes disguised as refactoring]
- [Performance optimization beyond structural changes]
- [Test refactoring (unless tests must move with their source)]
