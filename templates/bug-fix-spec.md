# Bug Fix Specification: [Brief Bug Description]

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

| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN [the reproduction steps] WHEN [action] THEN [correct behavior, not the bug] |
| AC-2 | GIVEN [normal operation] WHEN [action] THEN [behavior is unchanged from before the bug] |
| AC-3 | GIVEN [edge case from EC section] WHEN [action] THEN [correct behavior] |

---

## 7. Regression Prevention

**What test should exist to prevent this from recurring?**
[Describe the test that, if it had existed, would have caught this bug]

**Root cause category:** [Missing validation / Race condition / Off-by-one / Null handling / Type error / Logic error / Other]

---

## 8. Examples

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

---

## 9. Out of Scope

- [Related issue that should be a separate fix]
- [Refactoring that is not part of this fix]
- [Feature request disguised as a bug fix]
