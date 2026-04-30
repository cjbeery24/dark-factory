# Feature Specification: Tenant DB Access (T4 refinement counter-example)

<!-- dark-factory-metadata
feature-id: F-X4r
spec_type: feature
-->

> Test fixture. T4 refinement counter-example — must NOT trigger. Source: typology SSOT refinement table. Describes withTenantDb's INTERNAL mechanism, not a new external entry point.

## 2. Functional Requirements

### FR-1: All tenant-scoped database access flows through withTenantDb

- **Input:** Any code path that issues a SQL query touching a tenant-scoped table.
- **Processing:** Tenant-scoped database access is permitted ONLY through the `withTenantDb(tenantId, callback)` helper. All other paths to tenant-scoped tables are forbidden.
- **Output:** Every read or write against a tenant-scoped table returns rows visible only to the tenant bound by the active callback.
- **Side effects:** No SQL reaches the database outside of a `withTenantDb` callback's transaction.

## 8. Acceptance Criteria

| ID | Acceptance Criterion | Blocking |
|----|----------------------|----------|
| AC-9 | GIVEN a `withTenantDb(tenantId, cb)` invocation WHEN the helper enters its transaction THEN it issues `SELECT set_config('app.tenant_id', $1, true)` against the transaction client `tx` as its first statement, before yielding `tx` to the callback | yes |
