# Feature Specification: Bootstrap Sequence (refinement counter-example)

<!-- dark-factory-metadata
feature-id: F-Xref-t3-allowed-after
spec_type: feature
-->

> Test fixture. Refinement counter-example for T3 — same direction expressed two ways (runs_before / precedes are synonyms). MUST NOT trigger.

## 2. Functional Requirements

### FR-1: Health-check registration ordering

- **Input:** The Fastify server bootstrap routine.
- **Processing:** Health-check registration runs before route registration during bootstrap. This ordering ensures `/health` is responsive even if a later route registration fails. The bootstrap orchestrator enforces the order.
- **Output:** The server starts with health-check routes attached before any feature route.
- **Side effects:** The Fastify instance has health routes mounted first.

## 8. Acceptance Criteria

| ID | Acceptance Criterion | Blocking |
|----|----------------------|----------|
| AC-1 | GIVEN the bootstrap routine starts WHEN it executes its registration steps THEN health-check registration precedes feature-route registration | yes |
