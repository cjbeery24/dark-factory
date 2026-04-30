# Feature Specification: Tenant DB Access (T4 true-positive fixture)

<!-- dark-factory-metadata
feature-id: F-X4
spec_type: feature
-->

> Test fixture. T4 behavioral_exclusivity_violation. FR exclusivity claim "ONLY through withTenantDb"; AC defines a NEW external entry point that bypasses it.

## 2. Functional Requirements

### FR-1: All tenant-scoped database access flows through withTenantDb

- **Input:** Any code path that issues a SQL query touching a tenant-scoped table.
- **Processing:** Tenant-scoped database access is permitted ONLY through the `withTenantDb(tenantId, callback)` helper. All other paths to tenant-scoped tables are forbidden — direct `db.execute()` calls, raw `postgres.js` clients outside the helper's transaction scope, and ORM queries issued without an active `withTenantDb` callback all MUST NOT reach tenant-scoped tables.
- **Output:** Every read or write against a tenant-scoped table returns rows visible only to the tenant bound by the active callback.
- **Side effects:** No SQL reaches the database outside of a `withTenantDb` callback's transaction.

## 8. Acceptance Criteria

| ID | Acceptance Criterion | Blocking |
|----|----------------------|----------|
| AC-7 | GIVEN the new admin export endpoint at `/admin/export/tenants` WHEN an admin invokes it THEN the handler opens its own raw `postgres()` client and runs `SELECT * FROM tenant_data` directly against the connection pool without entering a `withTenantDb` callback, returning the full multi-tenant dataset as CSV | yes |
