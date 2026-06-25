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

### Tier 1: Test Contracts (compiled deterministically)

These contracts are compiled to test code mechanically by the pipeline. They
produce identical tests every run. Use for verifiable migration artifacts.

```yaml
contracts:
  - id: TC-1
    fr: FR-1
    type: file_exists
    given: "after implementation"
    when:
      check: "migrations/[timestamp]_[name].sql"
    then:
      exists: true

  - id: TC-2
    fr: FR-1
    type: command
    given: "migration file exists"
    when:
      cmd: "[migration dry-run or syntax check command]"
    then:
      exit_code: 0
```

**Contract types and `when`/`then` schemas:**
- `file_exists` — `when: { check: "path" }` → `then: { exists: true }`
- `file_contains` — two modes:
  - Structured: `when: { file: "path", parse: "json"|"yaml", path: "dot.path" }` → `then: { exists: true }`
  - Pattern: `when: { file: "path", parse: "text", pattern: "regex" }` → `then: { matches: true }`
- `command` — `when: { cmd: "..." }` → `then: { exit_code: 0 }`
- `http` — `when: { method, path, body (request) }` → `then: { status, body (assertions) }`
  - `then.body` values use operators: `{ type: "string" }`, `{ equals: "value" }`, `{ contains: "sub" }`
- `import` — `when: { module: "path", symbols: ["name", ...] }` → `then: { exports: true }`

`invariant` (optional, default `false`): when `true`, the pipeline skips this contract —
it documents that the build/lint/test gate already covers the check.
`given` is freeform text used as the test description in compiled output.

### Tier 2: Adversarial Criteria (SA-generated)

These criteria guide the SA's creative adversarial testing (layers 3-8: edge
cases, error conditions, state, security, property-based, adversarial). The SA
does not re-test what Tier 1 contracts already cover.

| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN the migration is applied WHEN counting rows THEN no data loss |
| AC-2 | GIVEN the rollback is executed WHEN querying schema THEN it matches pre-migration state |
| AC-3 | GIVEN production data volume WHEN measuring lock duration THEN no lock exceeds constraint |

## Non-Negotiable Acceptance Criteria

List the AC / EC / FR / NFR IDs whose failure must veto SHIP, regardless of
overall pass rate. The pipeline's stable-failure SHIP_WITH_GAPS promotion is
vetoed when any listed ID has a failing scenario.

Leave empty only when the spec has no release-blocking invariants — in that
case a `Justification:` paragraph is required.

- **AC-N** — [one-line reason, e.g. "tenant-isolation invariant"]
- **EC-N** — [one-line reason]

<!-- If no AC is blocking, replace the bullets with a justification paragraph:
Justification: this spec is a pure refactor/documentation/style change with no
runtime-exposed invariants beyond existing coverage. -->

---

## 10. Scenario Intent

Declare what scenarios should verify for each requirement. The Scenario Architect
compiles these intents into executable test code — it does not invent scenarios
beyond what is declared here plus a small cross-cutting set.

### Migration Execution
| # | Intent | Layer | ac_id |
|---|--------|-------|-------|
| 1 | Migration completes without errors on target schema | happy_path | — |
| 2 | Row count matches pre-migration baseline (no data loss) | contract | — |
| 3 | Data validation queries from Section 6 all pass | contract | — |
| 4 | Schema matches target definition exactly after migration | happy_path | — |
| 5 | Backfill handles NULL source values per transformation rules | edge_case | — |

### Rollback & Recovery
| # | Intent | Layer | ac_id |
|---|--------|-------|-------|
| 1 | Rollback restores original schema exactly | contract | — |
| 2 | Rollback preserves all data written during migration window | contract | — |
| 3 | Migration interrupted mid-batch leaves data consistent | state | — |
| 4 | Re-running migration after interruption resumes safely | state | — |
| 5 | Concurrent writes during migration do not corrupt data | state | — |

### Operational Safety
| # | Intent | Layer | ac_id |
|---|--------|-------|-------|
| 1 | No lock exceeds the duration constraint from Section 7 | property | — |
| 2 | Application functions correctly with new schema under normal traffic | happy_path | — |
| 3 | Foreign key references remain intact after migration | contract | — |
| 4 | Replication lag stays within threshold during migration | property | — |
| 5 | Migration is idempotent (safe to re-run) | state | — |

Layer values: `contract`, `happy_path`, `edge_case`, `error`, `state`, `security`, `property`, `adversarial`

---

## 11. Examples

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

## 12. Dependencies

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

## 13. Out of Scope

The following are explicitly NOT part of this migration:
- [Data archival or purging of old records]
- [Performance tuning of existing queries not related to schema changes]
- [Application code refactoring beyond schema compatibility]
- [Other table migrations that will be handled separately]

---

## 14. Free Variables (implementation choices — DO NOT assert in scenarios)

List implementation decisions this spec intentionally leaves unconstrained.
The Scenario Architect will treat these as DO-NOT-ASSERT directives.

| ID | Variable | What Is Constrained | What Is Free |
|----|----------|---------------------|--------------|
| FV-1 | [e.g., Migration file naming] | [e.g., Must be sequential and reversible] | [e.g., Timestamp format, prefix convention] |

If no implementation choices are free, write "None — all observable behaviors are fully constrained."
