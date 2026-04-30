# Feature Specification: Tenant Tables RLS (T2 fixture)

<!-- dark-factory-metadata
feature-id: F-X2
spec_type: feature
-->

> Test fixture. T2 quantitative_bound_mismatch. Two clauses bound `policy count per tenant table` with disjoint sets.

## 2. Functional Requirements

### FR-1: Enforce row-level security on every tenant-scoped table

- **Input:** Each tenant-scoped table created by feature migrations (F-2 onwards).
- **Processing:** Each tenant-scoped table MUST have row-level security enabled and at least one policy declared in `pg_policies` per table. Policy count ≥ 1 per tenant-scoped table is the structural invariant.
- **Output:** A migration that enables RLS and creates the tenant_isolation policy for each table.
- **Side effects:** `pg_policies` contains ≥ 1 row per tenant-scoped table after migrations apply.

## 8. Acceptance Criteria

| ID | Acceptance Criterion | Blocking |
|----|----------------------|----------|
| AC-3 | GIVEN the F-1c baseline migration WHEN `db:migrate` runs THEN exactly zero policies exist in `pg_policies` for any tenant-scoped table — policies are introduced exclusively by per-feature migrations starting at F-2, never by the baseline | yes |
