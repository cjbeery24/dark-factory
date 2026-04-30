# Feature Specification: Tenant-Switch Audit Logging (T1 synthetic fixture)

<!-- dark-factory-metadata
feature-id: F-X1
spec_type: feature
-->

> Test fixture. Synthetic — exercises T1 with the `produces_record_in` predicate-family closure rule (typology worked example, claim 4: same subject, predicates `creates` vs `produces_record_in`, polarity mismatch).

## 2. Functional Requirements

### FR-2: Tenant-switch endpoint records nothing audit-side

- **Input:** A POST `/api/tenants/switch` request with the new `tenantId` UUID in the body.
- **Processing:** Validate the tenant binding, rotate the session's tenant claim, and return the updated session token. The handler creates no audit log entry and writes no record to the audit table — audit logging is the responsibility of a downstream batch job (F-7), not this endpoint.
- **Output:** A 200 response with the new session token in JSON.
- **Side effects:** The session store is updated with the new tenant claim. No audit-side state is mutated.

## 8. Acceptance Criteria

| ID | Acceptance Criterion | Blocking |
|----|----------------------|----------|
| AC-12 | GIVEN a valid tenant switch request WHEN the handler completes THEN the audit table records one new row with `event_type = 'tenant.switched'`, the prior `tenant_id`, and the new `tenant_id` | yes |
