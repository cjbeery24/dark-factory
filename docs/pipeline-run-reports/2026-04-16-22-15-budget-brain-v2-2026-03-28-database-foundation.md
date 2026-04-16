# Pipeline Run Report — 2026-03-28-database-foundation (post-SA-fix run #4)

**Target project:** `budget-brain-v2`
**Spec:** `2026-03-28-database-foundation`
**Run ID:** `a50960f781c14244`
**Spec hash:** `1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662`
**Launched:** 2026-04-16 22:15 UTC
**Interrupted:** 2026-04-16 ~23:20 UTC (operator kill during iter 3 implementer)
**Wall time:** ~65 min
**Preceded by:** Run #3 (hand-written scenarios, 95% pass rate, diagnostic)

## Purpose

First pipeline run after the Scenario Architect fixes committed in `0373a74`:
1. Addendum rewritten — RLS tests MUST use non-superuser NOBYPASSRLS role; helpers changed from "Prefer" to "MUST use"
2. Hard Rule 13 added to test-engineer.md — contract helper enforcement
3. Validator Check 18 (contract import enforcement, hard fail) and Check 19 (vi.stubGlobal warning) added
4. Check 9 enhanced with declare-module stubs from architecture manifest

## Outcome: INTERRUPTED (operator kill) — 66% pass rate stable across 2 iterations

State file (final):

```
spec=2026-03-28-database-foundation
run_id=a50960f781c14244
spec_hash=1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662
last_decision=NO-SHIP
status=interrupted
iteration=2
completed_step=2
phase=review
smoke_result=passed
pass_rate=66
gate_exit_code=1
```

## Results

| iter | pass_rate | gate result | build | type check | oracle |
|-----:|----------:|:-----------:|:-----:|:----------:|:------:|
| 1 | 66 | FAIL (10/30) | FAIL | FAIL | NO-SHIP |
| 2 | 66 | FAIL (10/30) | PASS | PASS | NO-SHIP |
| 3 | — | — (killed during implementer) | — | — | — |

### Iter 1 build/type error (fixed in iter 2)

```
src/db/health-check.ts(18,51): error TS2339: Property 'timeout' does not exist on type 'PendingQuery<Row[]>'.
```

Implementer used `.timeout()` on a postgres.js query object. Fixed in iter 2 — build and type check both pass from iter 2 onward.

### Iter 2 test results (20 passed, 10 failed, 0 skipped)

| Test | Status |
|------|:------:|
| tenant-context.test.ts (4 tests) | PASS |
| ac-9-database-health-check-error.test.ts (3 tests) | PASS |
| fr-1-drizzle-infrastructure.test.ts (5 tests) | PASS |
| health-check.test.ts (2 tests) | PASS |
| error-conditions.test.ts (2 tests) | PASS |
| ac-4-migration-application.test.ts — idempotent (1 test) | PASS |
| **rls-patterns.test.ts — AC-7 strict RLS** | **FAIL** |
| **rls-patterns.test.ts — AC-8 nullable RLS** | **FAIL** |
| **rls-patterns.test.ts — AC-10 cross-tenant** | **FAIL** |
| **substrate-guc.test.ts — AC-12 unset GUC** | **FAIL** |
| **substrate-guc.test.ts — AC-5 SET LOCAL** | **FAIL** |
| **substrate-guc.test.ts — AC-10 concurrent tx** | **FAIL** |
| **edge-cases-ec-1-to-10.test.ts — AC-12 GUC nullish** | **FAIL** |
| **ac-2-missing-database-url.test.ts — AC-2 exit** | **FAIL** |
| **ac-4-migration-application.test.ts — AC-4 apply** | **FAIL** |
| **fr-1-drizzle-infrastructure.test.ts — AC-3 schema files** | **FAIL** |

Failures are identical across iter 1 and iter 2.

## What the SA fixes achieved

### Validator improvements (working)

| Check | Result | Evidence |
|-------|--------|----------|
| Check 18 (contract import) | PASS | 3 files import from `_contracts/`: `substrate-guc.test.ts`, `edge-cases-ec-1-to-10.test.ts`, `schema-helpers.test.ts` |
| Check 19 (vi.stubGlobal) | PASS | No files use `vi.stubGlobal()` — the 2026-04-15 anti-pattern eliminated |
| Check 9 (tsc) | PASS (skipped) | tsc not available in gate container — stubs generated but couldn't run |

### TE behavioral changes (partial)

| Prior behavior | Post-fix behavior |
|----------------|-------------------|
| 0/14 files imported contract helpers | 3/14 files import contract helpers |
| vi.stubGlobal used in health-check test | No vi.stubGlobal usage |
| Superuser pool for all substrate tests | **Still superuser pool for RLS tests** |
| No NOBYPASSRLS role anywhere | **Still no NOBYPASSRLS role** |

## Root cause analysis — why 10 tests still fail

### Category 1: RLS superuser bypass (3 failures)

`rls-patterns.test.ts` creates tables as the `postgres` superuser and queries through the same connection. Superusers bypass RLS unconditionally. The TE did NOT:
- Create a NOBYPASSRLS role
- Open a second client as that role
- Import `assertTenantSeesOwnRows` / `assertTenantCannotSeeOtherRows` from `_contracts/`

The addendum says "MUST" but the TE ignored it for this specific file. Check 18 passed because *other* files imported helpers — the check is "at least one file imports" not "every substrate-relevant file imports the right helper."

### Category 2: GUC wire-level mismatch (3-4 failures)

`substrate-guc.test.ts` imports `assertUnsetGucIsNullish` from contracts (good) but also has hand-rolled GUC assertions that appear to fail. Need detailed investigation — the contract helper handles `''|null` ambiguity but the hand-rolled assertions may not.

### Category 3: Subprocess/CLI tests (2 failures)

AC-2 (server exit on missing DATABASE_URL) and AC-4 (drizzle-kit migrate) require subprocess execution. These were flagged as `spec-vehicle-mismatch` coverage gaps but the regen TE wrote tests for them anyway. The tests likely fail because the gate container environment doesn't match assumptions.

### Category 4: Schema file existence (1 failure)

AC-3 asserts schema files exist for migration generation — likely a path or naming mismatch.

## Scenario validation report (final)

All checks PASS:

```
file_count:           PASS — 7 files (min 3) [note: 14 test files from 7 suites]
assertions:           PASS
trivial:              PASS
empty_bodies:         PASS
typescript_compilation: PASS (tsc not available, skipped)
assertion_upper_bound: PASS
no_source_reads:      PASS
no_indexof_ordering:   PASS
intent_alignment:     PASS (skipped)
no_hardcoded_paths:   PASS
cwd_invariant_paths:  PASS
contract_helper_usage: PASS — contracts imported
vi_stub_global:       no vi.stubGlobal usage detected (warning check)
```

## Events (histogram)

```
 17 AGENT_START
 16 AGENT_COMPLETED
  3 FILES_CHANGED
  2 SMOKE_COMPLETED
  2 GATE_COMPLETED
  2 ITER_COST
  1 SPEC_START
  1 SPEC_ADAPTER_ACTIVATED
  1 CONTRACTS_STAGED
  1 COVERAGE_SPEC_VEHICLE_MISMATCH
  1 COVERAGE_ARCHITECT_AMEND
  1 COVERAGE_ARCHITECT_AMEND_COMPLETED
  1 COVERAGE_GAP
  1 COVERAGE_REGEN
  1 COVERAGE_PASS
  1 TRAJECTORY_CLASSIFIED
```

No `BARRIER_VIOLATION`. No `BARRIER_PATH_ECHO_DETECTED`.

## Compared to prior runs

| axis | run #2 (pre-fix TE) | run #3 (hand-written) | run #4 (post-fix, this one) |
|------|---------------------|----------------------|----------------------------|
| pass_rate (best) | 57% (iter 3) | 95% (iter 1) | 66% (iter 1-2) |
| tests failing | 17 | 2 | 10 |
| vi.stubGlobal | yes | no (hand-fixed) | no (TE obeyed) |
| contract imports | 0 files | 6 files (hand-written) | 3 files (TE partial) |
| NOBYPASSRLS role | no | yes (hand-written) | no (TE ignored) |
| RLS structural bug | yes | no | yes |
| impl quality | compromised (globalThis) | clean | clean (build fixed iter 2) |

## Implications

1. **Check 18 needs per-primitive enforcement.** "At least one file imports contracts" is too weak. The check should cross-reference substrate manifest entries against the import graph: if `[[rls_table]]` or `[[tenant_isolation]]` exists, at least one file must import the corresponding helper (`assertRlsEnabled`, `assertTenantSeesOwnRows`, etc.).

2. **The addendum MUST language is necessary but not sufficient.** The TE partially complied — 3/14 files use helpers vs 0/14 before. But `rls-patterns.test.ts` ignored the NOBYPASSRLS requirement entirely. Prompt-level enforcement has ~70% compliance for substrate-specific rules.

3. **The validator is the real enforcement point.** Prompt compliance is probabilistic. The validator catching structural bugs mechanically is the only reliable path. The diagnostic run (hand-written scenarios, 95%) proves the pipeline works when scenarios are correct. The gap is purely scenario quality enforcement.

4. **Progress is real.** 57% → 66% pass rate, 17 → 10 failures. vi.stubGlobal eliminated. Contract imports went from 0 to 3 files. The fixes moved the needle but didn't close the gap.
