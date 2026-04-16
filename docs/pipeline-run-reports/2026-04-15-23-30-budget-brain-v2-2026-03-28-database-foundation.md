# Pipeline run — 2026-04-15 23:30 — budget-brain-v2 / 2026-03-28-database-foundation

## Outcome

**INTERRUPTED** at scenario coverage gate. Iteration 1, `completed_step=1`. Pipeline reached test-engineer but could not clear the coverage floor on AC-9 after 2 regen attempts. Never reached implementer / quality gate / reviewer / oracle.

## Wall time

~1418s (~24 min).

## Run ID

`e8949105d910413c`

## Pipeline path (what worked)

| t | agent | outcome |
|---|-------|---------|
| 0s  | spec-validator | **PASS** (score 92/100, blockers=0, divergence=LOW) |
| 200s | architect | manifest written, 121s wall |
| 321s | qa-holdout | blind tests written, 230s wall |
| 551s | test-engineer (round 1) | 3 test files + COVERAGE-GAPS.md, 19 ACs uncovered |
| 748s | test-engineer (round 2) | regen, still 19 uncovered — no progress |
| 1087s | test-engineer (round 3) | regen, 18 ACs filled; **1 uncovered (AC-9)** |
| 1418s | — | `COVERAGE_FLOOR_VIOLATION` → pipeline exit |

## The fixes we shipped this session — status report

- **Parser fix (commit `29a46a8`, detection.py — blockers authoritative)** — WORKS. Prior run halted here on `blockers=1`. This run validator emitted `verdict=PASS, blockers=0` on the first try and the pipeline advanced to architect. No "re-run validation" loop.
- **Validator prompt refinement (library-API-inline bullet)** — WORKS. Score bumped 87 → 92, divergence MEDIUM → LOW on the same validator (sonnet-4). The `sql.raw` paranoia is gone; no spurious blockers from demonstrated API use-sites.
- **Spec fix (commit `d90c618` in budget-brain-v2, Option C)** — WORKS. Dropping `ALTER DATABASE` and relying on `current_setting('app.tenant_id', true)` resolved CI-1. sql_lint still warns about the line in the documentation fence (pattern example), but it's no longer a blocker-worthy defect.
- **Substrate verifier (commit `33fcbe2`)** — STILL NOT EXERCISED. Zero substrate events in `events.jsonl`, no `substrate-findings.json` produced, no `substrate-manifests/` subdir emitted by the architect. The wiring didn't fire at all on this run — not "ran and produced empty findings," but "never invoked."

## Why substrate verifier didn't run

Two candidate causes, not yet narrowed:

1. **`project-hints.toml` regression (reproduced again)** — `dark-factory init --update` dropped `active_adapters = ["typescript-node", "postgres"]` down to just `["typescript-node"]`. If the substrate phase is gated by adapter membership, no postgres adapter means no postgres substrate rules to verify, and the phase short-circuits.
2. **Pipeline wiring** — the substrate phase may require the architect to emit a `substrate-manifests/<spec>.toml` file before the verifier runs. The architect emitted `architecture/2026-03-28-database-foundation-manifest.yaml` (normal file_map manifest) but nothing in `substrate-manifests/`. Unclear whether that's adapter-gated or template-gated.

Architect log does contain a relevant warning from the manifest validator:
> `manifest validation: database service(s) [postgres] declared but no migration/schema command found in commands.install or commands.setup — tests that depend on database tables will silently skip`

So postgres *is* appearing in the architect's environment somewhere (probably from the spec's Substrate Primitives section), but downstream the plumbing doesn't carry it all the way to the verifier.

## AC-9 — why the coverage floor couldn't be cleared

AC-9 from the spec:
> GIVEN the database is unreachable WHEN `GET /health` is called THEN the response body `data.checks.database` contains `{ "status": "error", "message": "..." }` and `data.status` is `"degraded"` (per F-1b health aggregation: `{ data: { status, uptime, checks: { database: { ... } } } }`)

This is an end-to-end contract between two spec'd surfaces — the database health check (this spec) and the health aggregation endpoint (F-1b, already implemented). Unit-testable *only* if a test can spin up Fastify + the health plugin + the db plugin + simulate "db unreachable." That's integration testing, which exceeds the test-engineer's scope.

The test-engineer correctly flagged this in COVERAGE-GAPS.md:
> ## FR-4: Register database health check
> **Reason:** No health check entry point declared — the architect manifest's `test_integration` section does not expose the health check registration module, preventing direct import for testing

The other 18 ACs got covered in round 3 after the test-engineer adapted. AC-9 is structurally resistant.

## Advisory warnings (noted, not blockers)

```
WARNING: spec declares ID-form depends-on entry F-1b — ignoring for standalone run
WARNING: test_server_cmd is set but test_base_url is empty; server will default to port 8000
WARNING: library_symbols: 3 undeclared import(s) in spec §5 (drizzle-orm/postgres-js, postgres, drizzle-kit)
WARNING: manifest validation: database service(s) [postgres] declared but no migration/schema command found in commands.install or commands.setup
```

The library-symbols warnings are expected (spec's purpose is to add those deps). The manifest-validation warning is the substrate-verifier signal mentioned above.

## What to do next

Two independent threads, in priority order:

1. **Lower the coverage floor OR move AC-9 to a vehicle the test-engineer can hit.** Options:
   - Add a `vehicle: integration` annotation to AC-9 so the test-engineer's anchor manifest excludes it (similar to the `vehicle: subprocess` pattern in spec-validator.md). The spec is already bumping against the unit-test ceiling for multi-plugin contracts.
   - Reduce the coverage floor from "100% of anchors" to some threshold that allows one or two structurally-integration ACs to pass.
   - Have the architect export a `health` entry point from the manifest's `test_integration` section so the test-engineer can import and spin up a minimal Fastify instance.

2. **Investigate why substrate verifier doesn't run.** The fix we shipped in `33fcbe2` hasn't been observed executing end-to-end. A targeted test or a minimal spec that exercises *only* the substrate phase would be faster to iterate on than full pipeline runs. Also worth: fix the `init --update` hints regression so postgres adapter persists.

## State file

```
spec=2026-03-28-database-foundation
run_id=e8949105d910413c
spec_hash=00ccb823ffbd817cd2b7e9cbf58d68407cceab87b398aede2ce7ab2cda6cc3e3
status=interrupted
iteration=1
completed_step=1
```

## Quality gate summary

N/A — pipeline never reached the gate.

## Substrate verification

N/A — verifier was never invoked. `reports/<spec>-substrate-findings.json` does not exist.
