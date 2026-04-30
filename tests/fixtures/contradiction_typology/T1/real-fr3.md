# Feature Specification: Database Foundation (T1 fixture excerpt)

<!-- dark-factory-metadata
feature-id: F-1c
spec_type: bootstrap
-->

> Test fixture. Source: bb-v2 `2026-03-28-database-foundation.md` at commit `fc45778`. Excerpts FR-3 + AC-24 — the canonical T1 entity_creation_polarity_mismatch case the typology SSOT pins as its worked example.

## 2. Functional Requirements

### FR-3: Establish tenant-scoped access-control invariants at the data layer

- **Input:** Initial SQL migration file under `apps/api/src/db/migrations/`.
- **Processing:** Create a baseline migration file that documents (in SQL comments) the reusable patterns for enforcing tenant-scoped access control at the database layer. The baseline migration itself creates no tables and applies no policies — it records the patterns as SQL-comment templates that future migrations reference.
- **Output:** A baseline migration file in `apps/api/src/db/migrations/` documenting the tenant-scoped access-control patterns.
- **Side effects:** The `__drizzle_migrations` tracking table is created in the database when the baseline migration is applied; the baseline's hash is recorded there. No tenant-scoped tables and no row-filtering policies are created at this scope.

## 8. Acceptance Criteria

| ID | Acceptance Criterion | Blocking |
|----|----------------------|----------|
| AC-24 | GIVEN the baseline migration file at `apps/api/src/db/migrations/0000_baseline.sql` WHEN `pnpm db:migrate` applies it THEN the file applies without error, no tenant-scoped tables are created, and `__drizzle_migrations` records the baseline's hash | no |
