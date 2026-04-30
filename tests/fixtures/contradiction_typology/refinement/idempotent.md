# Feature Specification: Tenant Onboarding Idempotency (refinement counter-example)

<!-- dark-factory-metadata
feature-id: F-Xref-idempotent
spec_type: feature
-->

> Test fixture. Refinement counter-example, MUST NOT trigger any T1-T5 slot. Source: typology SSOT refinement table — "function is idempotent" + "first call creates, second returns existing" is a valid implementation, not a contradiction.

## 2. Functional Requirements

### FR-1: Idempotent tenant onboarding

- **Input:** A POST to `/api/tenants/onboard` with a tenant `name` and an idempotency key.
- **Processing:** The handler is idempotent for a given idempotency key. The first call with a fresh key creates a new tenant row and returns it; the second call with the same key returns the existing tenant row without creating a duplicate. Idempotency is observable by the caller: identical request body and key yields identical response.
- **Output:** A 200 response with the tenant record.
- **Side effects:** At most one tenant row is created per idempotency key.

## 8. Acceptance Criteria

| ID | Acceptance Criterion | Blocking |
|----|----------------------|----------|
| AC-1 | GIVEN a fresh idempotency key WHEN the onboarding handler is called THEN a new tenant row is created and returned | yes |
| AC-2 | GIVEN the same idempotency key as AC-1 WHEN the handler is called a second time THEN the existing tenant row is returned without a new row being created | yes |
