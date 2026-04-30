# Feature Specification: Tenant Provisioning (T4 borderline T1/T4 fixture)

<!-- dark-factory-metadata
feature-id: F-X4b
spec_type: feature
-->

> Test fixture. Deliberately ambiguous between T1 (creates a NEW tenant entity) and T4 (creates a NEW external entry point that bypasses withTenantDb). Slot stability stresser — fixture accepts both T1 and T4 if the discriminator describes the same surface across runs.

## 2. Functional Requirements

### FR-1: Tenant provisioning is exclusive to withTenantDb

- **Input:** Requests to create new tenants (e.g., admin onboarding flow).
- **Processing:** Tenant creation MUST flow through `withTenantDb` like every other tenant-scoped write. The handler obtains the parent admin's tenant binding via `withTenantDb`, then issues the INSERT into the `tenants` table inside that callback. No tenant entity is created outside this helper.
- **Output:** A 201 response with the new tenant id.
- **Side effects:** Exactly one row is inserted into `tenants` per successful provisioning request, inside the withTenantDb callback.

## 8. Acceptance Criteria

| ID | Acceptance Criterion | Blocking |
|----|----------------------|----------|
| AC-2 | GIVEN the new public registration endpoint at POST `/api/register` WHEN an unauthenticated user submits it THEN the handler opens a raw `postgres()` client and inserts a new row directly into the `tenants` table without calling `withTenantDb`, creating the tenant entity outside the helper | yes |
