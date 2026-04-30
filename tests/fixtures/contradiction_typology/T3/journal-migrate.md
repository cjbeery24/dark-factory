# Feature Specification: Drizzle Migration Bootstrap (T3 fixture)

<!-- dark-factory-metadata
feature-id: F-X3
spec_type: bootstrap
-->

> Test fixture. T3 lifecycle_ordering_inversion. Two clauses assert opposite orderings of (seed_journal, drizzle_kit_migrate).

## 2. Functional Requirements

### FR-1: Bootstrap drizzle's migration journal before invoking drizzle-kit

- **Input:** A fresh database with no existing migrations and no `apps/api/src/db/migrations/meta/_journal.json` file.
- **Processing:** The substrate-prep step seeds an empty journal at `apps/api/src/db/migrations/meta/_journal.json` with content `{"version":"7","dialect":"postgresql","entries":[]}`. The journal-seed step MUST run before any invocation of `drizzle-kit migrate`. Without the seeded journal in place first, drizzle-kit hangs indefinitely on its first read of the journal file.
- **Output:** The journal file exists at the documented path with the empty-entries shape, ready for `drizzle-kit migrate` to populate.
- **Side effects:** The journal file is created on disk.

## 8. Acceptance Criteria

| ID | Acceptance Criterion | Blocking |
|----|----------------------|----------|
| AC-4 | GIVEN a fresh database with no journal file WHEN `pnpm db:migrate` is run THEN drizzle-kit migrate creates the `_journal.json` file as part of its first-run side effect, before any subsequent migrate invocation can read it | yes |
