# Pipeline Run Report — 2026-04-09 16:21 UTC

**Repo:** budget-brain-v2
**Spec:** 2026-03-28-database-foundation
**Framework commit:** 9724682 (atexit fix)
**Run ID:** 6bb449c9ce634c72

Trigger: first complex spec after two clean SHIPs (api-bootstrap, shared-parse-amount).
Database foundation involves Drizzle ORM, PostgreSQL schema, Docker Compose, migrations.

## Run 14 — ESCALATED (3 iterations, 37% plateau)

**Outcome:** Hard plateau at 37% (3/8 scenarios) across all 3 iterations.
5 scenarios consistently fail due to database infrastructure requirements
(PostgreSQL not available in test container) and migration tooling issues.
Pipeline wall time ~65 minutes.

### Timeline
| Phase | Status |
|---|---|
| spec-validator | PASS — 92% score, LOW divergence, 1 blocker |
| architect | PASS — complex manifest (Drizzle, migrations, schema, health, tenant context) |
| qa-holdout | PASS — 4 holdout files |
| test-engineer | PASS — 5 scenario files, 8 total scenarios |
| smoke | PASS — all scenarios fail cleanly pre-implementation |
| iter 1 | pre-gate PASS, smoke PASS, gate 37%, 5 failures (2 ENV_ISSUE, 3 IMPL_BUG), Oracle NO-SHIP |
| iter 2 | pre-gate PASS, smoke PASS, gate 37%, 5 failures (all reclassified IMPL_BUG), Oracle NO-SHIP |
| iter 3 | pre-gate PASS, smoke PASS, gate 37%, escalation triggered |

### Failure analysis

**Consistent 5/8 failures across all iterations:**

| Test | Iter 1 Category | Iter 2 Category | Root Cause |
|---|---|---|---|
| FR-1: Drizzle ORM pool config | IMPL_BUG* | IMPL_BUG | Fails to execute DB queries — no PostgreSQL |
| FR-1: DATABASE_URL missing | IMPL_BUG* | IMPL_BUG | Server creation doesn't fail as expected |
| AC-3: db:generate migration | IMPL_BUG | IMPL_BUG | Drizzle generate fails — no schema dir structure |
| AC-4: db:migrate apply | ENV_ISSUE | IMPL_BUG | PostgreSQL connection refused on port 5432 |
| EC-9: db:migrate idempotent | ENV_ISSUE | IMPL_BUG | PostgreSQL connection refused on port 5432 |

*Iter 1 had different FR-1 test names (simpler). Iter 2 defect classifier
reclassified the AC-4/EC-9 failures from ENV_ISSUE to IMPL_BUG (they're
actually both — the implementation can't work without a database, and the
test environment doesn't provide one).

**3/8 passing tests:** These are likely the non-database-dependent tests
(schema definitions, tenant context middleware setup, config validation
that doesn't require actual DB connection).

**Ship-with-gaps NOT eligible:** 37% < 80% minimum, so the ship-with-gaps
path was never considered.

### Framework validations

**Atexit fix (9724682) — VALIDATED.**
State file correctly reads `status=escalated`, not `interrupted`. First
confirmed fix since the bug was discovered in Run 13.

**Defect classifier — WORKING but category drift observed.**
Iter 1 correctly classified AC-4/EC-9 as ENV_ISSUE (database connection
refused). Iter 2 reclassified them as IMPL_BUG. Both are defensible
readings — the connection failure is an environment issue, but the
implementer should arguably handle the missing database gracefully.
The classifier is doing reasonable work.

**Plateau detection — WORKING.**
All 3 iterations showed identical 37% (3/8). The same 5 tests failed
every iteration. `detect_stable_failures` should have identified the
stable failure set at iter 2, but ship-with-gaps requires >= 80% pass
rate, so it was irrelevant.

**Escalation agent — PRODUCED report.**
The escalation report was generated (reviews/...-escalation.md). It
correctly identified the root cause as infrastructure/environment issues
blocking database test coverage.

### Findings

**Finding 1 (spec/pipeline design): database specs need PostgreSQL in test env.**
The database-foundation spec requires a live PostgreSQL instance for
migration and connection tests. The Docker test container doesn't include
PostgreSQL. This is a fundamental mismatch between the spec's requirements
and the pipeline's test infrastructure.

Options:
- a) Add Docker Compose with PostgreSQL to the quality gate container
- b) Modify the spec to require mock/in-memory database tests instead
  of live PostgreSQL
- c) Add a `services` section to config.toml that the quality gate
  spins up (docker-compose-based test dependencies)
- d) Accept that database specs require human intervention for the
  PostgreSQL dependency and use the pipeline only for non-DB-dependent
  components

This is the first spec to hit the "test environment doesn't match
production dependencies" wall. It's expected and needs a design decision.

**Finding 2 (positive): pipeline stability under stress.**
Despite 3 full iterations with 37% pass rate and complex Drizzle/migration
code, the pipeline:
- Never crashed
- Produced accurate telemetry at every step
- Generated useful escalation report
- Correctly wrote terminal state (atexit fix confirmed)
- Produced valid defect classifications all 3 iterations

The pipeline is mechanically sound. The failure is a spec/environment
mismatch, not a framework bug.

**Finding 3 (non-blocking): iter1 defect classification more accurate than iter2.**
Iter 1 correctly classified AC-4/EC-9 as ENV_ISSUE (PostgreSQL connection
refused). Iter 2 reclassified them as IMPL_BUG. The iter 1 classification
was more accurate — the root cause is genuinely an environment issue (no
database available), not an implementation bug. This reinforces the
candidate 4.6b.1i concern about classifier consistency across iterations.

### Status summary

| Concern | Status |
|---|---|
| Atexit fix (9724682) | VALIDATED — state file says escalated |
| Pipeline mechanical stability | healthy — no crashes across 3 iters |
| Defect classifier | working, minor category drift observed |
| Plateau detection | working (same 5 failures all 3 iters) |
| Ship-with-gaps | not applicable (37% < 80%) |
| Escalation agent | produced report |
| All prior fixes (4.6b.1d-1h) | no regressions observed |
| New: database specs need live PostgreSQL | design decision needed |
| 4.6b.1e deferred control-flow fix | not triggered (escalation path changed in recent refactors) |
