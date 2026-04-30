# Feature Specification: Migration Counts (refinement counter-example)

<!-- dark-factory-metadata
feature-id: F-Xref-t2-superset
spec_type: feature
-->

> Test fixture. Refinement counter-example for T2 quantitative bounds — bounds are not disjoint (specific is inside general's range). MUST NOT trigger.

## 2. Functional Requirements

### FR-1: Tenant table policy minimums

- **Input:** Each tenant-scoped table created across all migrations.
- **Processing:** Every tenant-scoped table has at least one row-level security policy declared. The minimum is 1; tables may carry more policies if their access patterns require finer-grained rules.
- **Output:** Each tenant-scoped table has ≥1 policy in `pg_policies`.
- **Side effects:** `pg_policies` rows match the per-table policy declarations.

## 8. Acceptance Criteria

| ID | Acceptance Criterion | Blocking |
|----|----------------------|----------|
| AC-1 | GIVEN the `users` table specifically WHEN migrations have applied THEN exactly 3 policies exist in `pg_policies` for `users` (one each for SELECT, INSERT, UPDATE) | yes |
