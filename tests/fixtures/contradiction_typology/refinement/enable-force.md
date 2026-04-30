# Feature Specification: RLS Hardening (refinement counter-example)

<!-- dark-factory-metadata
feature-id: F-Xref-enable-force
spec_type: feature
-->

> Test fixture. Refinement counter-example, MUST NOT trigger T1 (or any slot). Source: typology SSOT refinement table — ENABLE + FORCE RLS are a stricter restatement of the same direction; both must hold.

## 2. Functional Requirements

### FR-1: Row-level security on tenant tables

- **Input:** Each tenant-scoped table created by the migration set.
- **Processing:** Every tenant-scoped table has `ENABLE ROW LEVEL SECURITY` declared in its CREATE TABLE migration. The migration also declares `FORCE ROW LEVEL SECURITY` on each such table — owner role does not bypass policies.
- **Output:** Migrations apply with both ENABLE and FORCE issued for each tenant-scoped table.
- **Side effects:** `pg_class.relrowsecurity = true` and `pg_class.relforcerowsecurity = true` for every tenant-scoped table.

## 8. Acceptance Criteria

| ID | Acceptance Criterion | Blocking |
|----|----------------------|----------|
| AC-1 | GIVEN the migration set has applied WHEN `pg_class` is inspected THEN every tenant-scoped table has `relrowsecurity = true` (ENABLE row-level security) | yes |
| AC-2 | GIVEN the migration set has applied WHEN `pg_class` is inspected THEN every tenant-scoped table also has `relforcerowsecurity = true` (FORCE row-level security; the owner role does not bypass) | yes |
