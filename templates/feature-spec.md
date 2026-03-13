# Feature Specification: [Feature Name]

<!-- dark-factory-metadata
feature-id: F-X
depends-on: []
plan:
profile:
-->

**Author:** [name]
**Date:** [YYYY-MM-DD]
**Status:** DRAFT | VALIDATED | IN PROGRESS | COMPLETE

---

## 1. Problem Statement

**What problem exists today?**
[Describe the current pain point in concrete terms]

**Who is affected?**
[Users, systems, or processes that experience this problem]

**What is the impact?**
[Quantify: revenue lost, time wasted, errors per day, user complaints, etc.]

**What does success look like?**
[Describe the world after this feature is implemented]

---

## 2. Functional Requirements

### FR-1: [Verb + Behavior]
- **Input:** [Exact format with types]
- **Processing:** [Step-by-step logic]
- **Output:** [Exact format with types]
- **Side effects:** [State changes, events, logs, or "none"]

### FR-2: [Verb + Behavior]
- **Input:** [...]
- **Processing:** [...]
- **Output:** [...]
- **Side effects:** [...]

[Add more as needed]

---

## 3. Non-Functional Requirements

### Performance
- [Specific metric with target, e.g., "p95 response time < 200ms for payloads < 1MB"]

### Security
- [Specific controls, e.g., "All user inputs must be parameterized in database queries"]

### Scalability
- [Specific target, e.g., "Must handle 500 concurrent users without degradation"]

### Accessibility
- [Specific standard, e.g., "WCAG 2.1 AA compliance for all new UI elements"]

### Observability
- [Logging/metrics requirements, e.g., "Log all errors with request ID, emit latency histogram"]

---

## 4. Constraints

### Technology
- **Language/Runtime:** [e.g., "TypeScript 5.x, Node.js 20+"]
- **Framework:** [e.g., "Use existing Express.js server in src/server.ts"]
- **Dependencies:** [e.g., "Use zod for validation (already in package.json)"]

### Patterns
- [e.g., "Follow repository pattern in src/repositories/"]
- [e.g., "Use existing error handling middleware pattern"]

### Integration Points
- [e.g., "Must integrate with existing auth middleware at src/middleware/auth.ts"]
- [e.g., "Must emit events to existing event bus at src/events/"]

### File Locations
- [e.g., "Create new files under src/features/[feature-name]/"]
- [e.g., "Add routes to src/routes/index.ts"]

---

## 5. Interface Contract

### Types / Data Structures

```
[Define all types with all fields and their types]
```

### Function Signatures / API Endpoints

```
[Define every public function signature or API endpoint]
[Include request/response shapes for APIs]
[Include all possible status codes for APIs]
```

---

## 6. Edge Cases

| ID | Condition | Expected Behavior |
|----|-----------|-------------------|
| EC-1 | [Specific condition] | [Exact expected behavior] |
| EC-2 | [Specific condition] | [Exact expected behavior] |
| EC-3 | [Specific condition] | [Exact expected behavior] |

[Add rows as needed. Common categories: empty inputs, boundary values, unicode, concurrent access, missing fields, type mismatches]

---

## 7. Error Handling

| Condition | Response/Behavior | Error Code | Logging |
|-----------|-------------------|------------|---------|
| [Error condition] | [Exact response] | [Code] | [What to log] |
| [Error condition] | [Exact response] | [Code] | [What to log] |

---

## 8. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN [precondition] WHEN [action] THEN [expected result] |
| AC-2 | GIVEN [precondition] WHEN [action] THEN [expected result] |
| AC-3 | GIVEN [precondition] WHEN [action] THEN [expected result] |

---

## 9. Examples

### Example 1: [Happy path description]
```
Input: [exact input]
Expected Output: [exact output]
```

### Example 2: [Error case description]
```
Input: [exact input]
Expected Output: [exact output/error]
```

### Example 3: [Edge case description]
```
Input: [exact input]
Expected Output: [exact output]
```

---

## 10. Dependencies

### Existing Code
- [File/module path] — [What it provides]

### External Services
- [Service name] — [What it provides, how to connect]

### Libraries
- [Library name] — [Version, purpose, already installed?]

---

## 11. Out of Scope

The following are explicitly NOT part of this specification:
- [Thing 1 — brief reason why]
- [Thing 2 — brief reason why]
- [Thing 3 — brief reason why]
