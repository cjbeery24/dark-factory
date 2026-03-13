# Database Migration Specification: [Migration Name]

<!-- dark-factory-metadata
feature-id: F-X
depends-on: []
plan:
profile:
-->

**Author:** [name]
**Date:** [YYYY-MM-DD]
**Database:** [e.g., PostgreSQL 16, MySQL 8.0, SQLite 3.x]
**Data Volume:** [e.g., ~10M rows / ~2 GB in affected tables]
**Downtime Budget:** [zero / X minutes]
**Status:** DRAFT | VALIDATED | IN PROGRESS | COMPLETE

---

## 1. Motivation

**Why is this migration needed?**
[Describe the business or technical reason: new feature requirement, performance issue, data model correction, etc.]

**What happens if we do nothing?**
[Describe the consequence of deferring this migration]

**What does success look like?**
[Describe the database state after a successful migration]

---

## 2. Current Schema

**Affected Tables/Collections:**

```sql
-- Table: [table_name]
CREATE TABLE [table_name] (
    id          UUID PRIMARY KEY,
    [column1]   [TYPE] [CONSTRAINTS],
    [column2]   [TYPE] [CONSTRAINTS],
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_[table_name]_[column] ON [table_name] ([column]);

-- Foreign keys
ALTER TABLE [table_name] ADD CONSTRAINT fk_[name]
    FOREIGN KEY ([column]) REFERENCES [other_table]([column]);
```

**Current Data Characteristics:**
| Table | Row Count | Size on Disk | Growth Rate |
|-------|-----------|--------------|-------------|
| [table_name] | [~X rows] | [~X GB] | [~X rows/day] |

---

## 3. Target Schema

```sql
-- Table: [table_name] (after migration)
CREATE TABLE [table_name] (
    id          UUID PRIMARY KEY,
    [column1]   [TYPE] [CONSTRAINTS],
    [column2]   [TYPE] [CONSTRAINTS],
    [new_col]   [TYPE] [CONSTRAINTS],
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- New/modified indexes
CREATE INDEX idx_[table_name]_[new_column] ON [table_name] ([new_column]);
```

**Schema Diff Summary:**
| Change | Object | Details |
|--------|--------|---------|
| ADD COLUMN | [table].[column] | [Type, default, nullable] |
| DROP COLUMN | [table].[column] | [Reason for removal] |
| ADD INDEX | idx_[name] | [Columns, type (btree/gin/etc.)] |
| ALTER TYPE | [table].[column] | [Old type -> New type] |
| ADD TABLE | [table_name] | [Purpose of new table] |
| DROP TABLE | [table_name] | [Reason for removal] |

---

## 4. Migration Steps

**Execute these steps in order. Each step must leave the database in a consistent state.**

| Step | Action | Reversible | Locks Required | Estimated Duration |
|------|--------|------------|----------------|--------------------|
| 1 | [e.g., Add new column with NULL default] | Yes | ACCESS EXCLUSIVE (brief) | [~X seconds] |
| 2 | [e.g., Backfill new column in batches of 10,000] | Yes | ROW-level only | [~X minutes] |
| 3 | [e.g., Add NOT NULL constraint] | Yes | ACCESS EXCLUSIVE (brief) | [~X seconds] |
| 4 | [e.g., Create new index CONCURRENTLY] | Yes | None (concurrent) | [~X minutes] |
| 5 | [e.g., Drop old column] | No | ACCESS EXCLUSIVE (brief) | [~X seconds] |

**Batching Strategy (if applicable):**
- Batch size: [e.g., 10,000 rows]
- Throttle: [e.g., 100ms pause between batches]
- Progress tracking: [e.g., log every 100,000 rows]

---

## 5. Rollback Plan

**Rollback window:** [e.g., 24 hours after migration / before step 5]

| Step | Rollback Action | Data Loss Risk |
|------|-----------------|----------------|
| 1 | [e.g., DROP COLUMN new_col] | None |
| 2 | [e.g., No action needed, column reverts with step 1 rollback] | None |
| 3 | [e.g., ALTER COLUMN SET NULL] | None |
| 4 | [e.g., DROP INDEX idx_name] | None |
| 5 | [e.g., NOT REVERSIBLE — old column data is lost] | [Describe loss] |

**Point of no return:** [Step number after which rollback is not possible without data loss]

**Rollback verification:** [How to confirm the rollback was successful]

---

## 6. Data Transformation

**Transformation Rules:**
| Source | Target | Transformation |
|--------|--------|----------------|
| [table.old_column] | [table.new_column] | [e.g., UPPER(old_column)] |
| [table.column_a, table.column_b] | [table.computed_column] | [e.g., column_a \|\| '-' \|\| column_b] |
| [N/A (new data)] | [table.new_column] | [e.g., DEFAULT 'unknown'] |

**Data Validation Queries:**

```sql
-- Run BEFORE migration: record baseline
SELECT COUNT(*) AS total_rows FROM [table_name];
SELECT COUNT(*) AS null_count FROM [table_name] WHERE [column] IS NULL;

-- Run AFTER migration: verify integrity
SELECT COUNT(*) AS total_rows FROM [table_name]; -- Must match baseline
SELECT COUNT(*) AS orphaned FROM [table_name] WHERE [new_column] IS NULL; -- Must be 0
```

---

## 7. Constraints

- **Downtime budget:** [zero-downtime required / up to X minutes acceptable]
- **Data integrity:** [No rows may be lost or corrupted during migration]
- **Backward compatibility:** [Application code must work with both old and new schema during rollout]
- **Lock duration:** [No table lock may exceed X seconds]
- **Connection limits:** [Migration must not exhaust connection pool]
- **Replication lag:** [Must not cause replication lag exceeding X seconds]

---

## 8. Edge Cases

| ID | Condition | Expected Behavior |
|----|-----------|-------------------|
| EC-1 | [Migration interrupted mid-batch (e.g., process killed)] | [Partially migrated data is consistent; re-running migration resumes safely] |
| EC-2 | [Concurrent writes during backfill] | [New rows get correct default; backfill does not overwrite concurrent updates] |
| EC-3 | [Table contains rows with NULL in source column] | [Transformation handles NULLs explicitly per rule in Section 6] |
| EC-4 | [Existing foreign key references to modified rows] | [Referential integrity maintained; cascading updates applied correctly] |
| EC-5 | [Replication lag exceeds threshold during migration] | [Migration pauses until lag recovers] |

---

## 9. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN the migration is applied WHEN querying the target table THEN the schema matches Section 3 exactly |
| AC-2 | GIVEN the migration is applied WHEN counting rows THEN the row count matches the pre-migration count (no data loss) |
| AC-3 | GIVEN the migration is applied WHEN running data validation queries from Section 6 THEN all checks pass |
| AC-4 | GIVEN the rollback is executed WHEN querying the target table THEN the schema matches Section 2 exactly |
| AC-5 | GIVEN the migration runs on production data volume WHEN measuring lock duration THEN no lock exceeds the constraint from Section 7 |
| AC-6 | GIVEN the application is deployed with the new schema WHEN processing normal traffic THEN no errors related to schema changes occur |

---

## 10. Examples

### Example 1: Happy path — full migration
```sql
-- Before migration
SELECT column_a, column_b FROM users LIMIT 3;
-- Results:
-- | column_a | column_b |
-- |----------|----------|
-- | 'alice'  | 'smith'  |
-- | 'bob'    | 'jones'  |
-- | 'carol'  | NULL     |

-- After migration (new computed column added)
SELECT column_a, column_b, full_name FROM users LIMIT 3;
-- Results:
-- | column_a | column_b | full_name     |
-- |----------|----------|---------------|
-- | 'alice'  | 'smith'  | 'alice smith' |
-- | 'bob'    | 'jones'  | 'bob jones'   |
-- | 'carol'  | NULL     | 'carol'       |
```

### Example 2: Error case — migration fails mid-batch
```
-- Migration processing batch 5 of 100 when database connection drops
-- State: Rows 1-50,000 have been transformed; rows 50,001+ have not
-- Re-run: Migration detects progress marker and resumes at batch 6
-- Result: All rows correctly transformed, no duplicates or gaps
```

### Example 3: Edge case — rollback after partial migration
```sql
-- Migration completed steps 1-3 but step 4 (index creation) failed
-- Rollback steps 3, 2, 1 in reverse order:
ALTER TABLE users ALTER COLUMN full_name DROP NOT NULL;
-- Verify: Column exists but is nullable, no data lost
-- Application continues to function with old code path
```

---

## 11. Dependencies

### Database
- [Database engine and minimum version required]
- [Required extensions, e.g., "pg_trgm for trigram indexes"]

### Application Code
- [File/module] -- [Must be updated to read/write new schema]
- [File/module] -- [Must handle both old and new schema during rollout]

### Infrastructure
- [Sufficient disk space for migration: ~X GB temporary]
- [Migration tool/framework, e.g., "Flyway 9.x", "Alembic", "knex migrations"]

---

## 12. Out of Scope

The following are explicitly NOT part of this migration:
- [Data archival or purging of old records]
- [Performance tuning of existing queries not related to schema changes]
- [Application code refactoring beyond schema compatibility]
- [Other table migrations that will be handled separately]
