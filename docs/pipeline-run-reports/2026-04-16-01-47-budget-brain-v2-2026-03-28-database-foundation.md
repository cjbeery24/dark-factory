# Pipeline Run Report — 2026-03-28-database-foundation

- **Target project:** `~/sites/budget-brain-v2/`
- **Run ID:** `1d5253faec3a4ec9`
- **Start:** 2026-04-16T07:39:02Z
- **End:** 2026-04-16T08:47:02Z
- **Wall time:** ~68 minutes (4,080 s)
- **Outcome:** `SPEC_ESCALATED` (2 iterations exhausted, both NO-SHIP)
- **Dark-factory HEAD at run start:** `b4570a3` (coverage-gap architect-amend + file_map shape fix)

## State file excerpt

```
spec=2026-03-28-database-foundation
run_id=1d5253faec3a4ec9
spec_hash=00ccb823ffbd817cd2b7e9cbf58d68407cceab87b398aede2ce7ab2cda6cc3e3
completed_step=2
iteration=2
server_error=
smoke_result=passed
pass_rate=46
gate_exit_code=1
last_decision=NO-SHIP
phase=oracle_complete
status=escalated
```

## Agent timeline

| Phase | Role | Wall (s) | Outcome |
|-------|------|----------|---------|
| 0 | spec-validator | 115 | PASS · 94/100 · LOW divergence · blockers=0 |
| 0 | qa-holdout | 263 | complete |
| 0 | test-engineer (generate) | 190 | complete |
| 0 | test-engineer (validate pass 1) | 450 | re-prompt |
| 0 | test-engineer (validate pass 2) | 332 | COVERAGE_PASS (28 anchors) + COVERAGE_DUPLICATE_TAGS |
| 1 | implementer (attempt 1) | 629 | FILES_CHANGED |
| 1 | implementer (attempt 2) | 315 | FILES_CHANGED; **BARRIER_VIOLATION** (`.git`) |
| 1 | gate | — | install/build/lint/typecheck/self-tests PASS; scenarios FAIL (18/32 = 56%) |
| 1 | defect-classifier | 135 | complete |
| 1 | reviewer | 289 | complete |
| 1 | oracle | 172 | NO-SHIP |
| 2 | implementer | 242 | FILES_CHANGED |
| 2 | gate | — | same gates PASS; scenarios FAIL (15/32 = 46%) — **regression** |
| 2 | defect-classifier | 87 | complete |
| 2 | reviewer | 367 | complete |
| 2 | oracle (pass 1) | 284 | NO-SHIP |
| 2 | oracle (pass 2) | 90 | NO-SHIP, escalate |
| 3 | escalation | 93 | report written |

## Quality gate summary (iteration 2)

```
| Gate                         | Status |
|------------------------------|--------|
| Install                      | PASS   |
| Build                        | PASS   |
| Lint                         | PASS   |
| Type Check                   | PASS   |
| Self-Tests                   | PASS   |
| Blind Scenarios (executable) | FAIL   |
| Mutation Testing             | SKIP   |

Test Files  6 failed (6)
Tests      17 failed | 15 passed (32)
```

Scenario counts: `SCENARIO_TOTAL=32 PASSED=15 FAILED=17 ASSERT_FAILED=17 CRASHED=0 SKIPPED=0 RATE=46`.

### Two representative test failures

Both failures implicate the test-engineer, not the implementer:

**1. AC-12 fail-closed assertion (`expected '' to be null`)**

```ts
// .dark-factory/scenarios/.../tenant-isolation.test.ts:163
const result = await connection`SELECT current_setting('app.tenant_id', true) AS tenant_id`;
expect(result[0].tenant_id).toBeNull();  // ← actual: ""
```

PostgreSQL `current_setting(name, true)` returns the empty string when the GUC
is unset in session scope (and the second arg is `true` / missing_ok), not
NULL. The spec's AC-12 is written as "returns NULL", but at the wire level the
postgres.js driver surfaces the empty string. The scenario should assert
falsy/empty, or route the query through `SELECT NULLIF(current_setting(...), '')`.

**2. FR-2 `tx.execute` gets wrong SQL flavor (`query.getSQL is not a function`)**

```ts
// .dark-factory/scenarios/.../tenant-isolation.test.ts:179
const beforeGuc = await tx.execute(connection`SELECT current_setting('app.tenant_id', true) AS before_tenant`);
```

The scenario passes a **postgres.js tagged-template result** (a `PendingQuery`)
to drizzle's `tx.execute`, which expects a `drizzle-orm` `SQL` object with
`getSQL()`. The two `sql` tags are incompatible. This is the exact mistake the
spec's Wire Contract at lines 46-58 warns against — but the warning is aimed
at the implementer, not the test-engineer.

These are scenario-authorship defects in the blind tests. The implementer
writing correct code cannot pass these scenarios.

## Substrate findings

```json
{"status": "fail",
 "findings": [{"severity": "error",
               "code": "substrate-manifest-missing",
               "message": "spec triggers substrate rules but architect produced no substrate manifest at /manifest.toml; re-run architecture or fix the spec's `## Substrate Primitives` block",
               "path": "/manifest.toml", "line": 0}]}
```

The spec's `## Substrate Primitives` block marks both `[[rls_table]]` and
`[[tenant_isolation]]` as `verifiable = false` (F-1c is documentation-only; no
concrete RLS tables exist yet). The architect reasonably did not emit a
substrate manifest, but the substrate verifier treated absence as failure.
The `.dark-factory/architecture/substrate-manifests/` directory does not
exist at all — architect never created it. Advisory finding; did not block the
pipeline, but is noise worth fixing.

## Notable events

- `COVERAGE_PASS` at 08:01:34Z — anchor_count=28.
- `COVERAGE_DUPLICATE_TAGS` at 08:01:34Z — `{"FR-1":2,"AC-1":2,"AC-12":2}`. Multiple scenarios tag the same anchor; tolerable but the coverage check is noisy.
- `BARRIER_VIOLATION` at 08:17:19Z — `{"role":"implementer","path":".git"}`. Implementer attempted to read `.git/`; masked tmpfs returned empty, so the barrier held. No `BARRIER_PATH_ECHO_DETECTED` events.
- `SPEC_ESCALATED` at 08:47:02Z — `{"iterations":2}`.

## Architect amend (this session's change) — NOT exercised

The coverage-gap architect-amend feature shipped at commit b4570a3 was **not
triggered** during this run. The architect's first output already listed both
`apps/api/src/server.ts` and `apps/api/src/db/health-check.ts` as entry
points, so `_enforce_coverage_floor` found zero `architect-entry-point`
gaps and the permit-one-more path was never entered. No `ARCHITECT_AMEND_*`
events in events.jsonl. This is neutral evidence for the amend feature — no
regression from its presence, but also no real-world validation of the
happy path.

## Audit log spot-check

`scenario-architect` / `scenarios` tokens appear 10× in
`.dark-factory/audit/2026-03-28-database-foundation-1d5253faec3a4ec9.log`,
all within the scenario-architect's own turn output (no scenarios-path reads
by other roles). No barrier-path echoes. `SOURCE_DIRS=["apps/api/src"]`
masks applied correctly.

## Iteration-1 vs iteration-2 scenario pass regression

| Iter | Pass rate | Category 00 |
|------|-----------|-------------|
| 1    | 56 % (18/32) | `CAT_00_PASSED=18 TOTAL=32` |
| 2    | 46 % (15/32) | `CAT_00_PASSED=15 TOTAL=32` |

Iter 2 passed fewer scenarios than iter 1. The implementer regressed the
working cases while fixing others — a classic defect-classifier failure mode
where the feedback loop lacks stable signal because the tests themselves are
broken (see above).

## Root cause (my read)

The pipeline escalated because the **test-engineer produced blind scenarios
that are themselves wrong** — specifically (a) asserting NULL when
postgres.js returns empty string, and (b) mixing postgres.js and drizzle-orm
`sql` tags. The implementer has no way to satisfy these scenarios without
modifying them, which it cannot see or edit (barrier). Every iteration
flailed against phantom defects.

Secondary noise:

- Substrate verifier fails on specs where substrate is documentation-only.
- Coverage-duplicate tags (FR-1/AC-1/AC-12) inflate the scenario count.
- Smoke test showed a stale lockfile mismatch at some point (`ERR_PNPM_OUTDATED_LOCKFILE`) but the state file records `smoke_result=passed`, so this was transient between iterations.

## Escalation report self-assessment

The Sonnet-written escalation report (`.dark-factory/reviews/...-escalation.md`) contains
fabricated findings:

- "Docker daemon failures (500 Internal Server Error)" — zero docker errors in
  `/tmp/df-run-2026-03-28-database-foundation.log`; containers started cleanly.
- "HIGH severity prototype pollution in health.ts via Object.assign" — health.ts
  does not use `Object.assign`; the reviewer flagged an unrelated concern.
- "Implementation Score: 76/100" — this number appears nowhere in the telemetry.

The escalation agent is hallucinating under pressure. The real signal is in
the quality-gate and scenario reports, not the escalation narrative.

## Recommended follow-ups (not for this session)

1. **Test-engineer wire-contract compliance** — when the spec's Wire Contract
   section explicitly distinguishes two `sql` tag implementations, the
   test-engineer prompt should be reminded to import the same flavor the
   implementer uses. Parse the Wire Contract block at test-generation time
   and surface it as a hard constraint.
2. **AC precision on DB sentinels** — AC-12 should say "returns empty/null"
   not "returns NULL". Spec wording bled through as an incorrect assertion.
3. **Substrate verifier `verifiable = false` handling** — treat `verifiable
   = false` entries as advisory so the verifier does not hand back
   `substrate-manifest-missing` for documentation-only specs.
4. **Escalation agent prompt** — forbid unsupported claims; require every
   finding to cite a telemetry file or log line.
