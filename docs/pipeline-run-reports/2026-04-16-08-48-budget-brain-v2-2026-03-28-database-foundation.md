# Pipeline Run — 2026-03-28-database-foundation (budget-brain-v2)

- **Run ID**: `5600d8d5d0fd4031`
- **Start**: 2026-04-16T15:29:55Z
- **Interrupted**: 2026-04-16T~15:48Z (user-requested kill after diagnosis was conclusive)
- **Outcome**: **INTERRUPTED** — run was on rails to repeat the 2026-04-16 failure mode; killed by operator once the root cause was confirmed live.
- **Iterations**: 1 (impl attempt 2 of iter 1 in flight at kill time)
- **Wall time at kill**: ~1300s (~21.5 min)
- **Iter-1 pass rate**: N/A (gate never ran)

## Why this run matters

This was the **success-measure run** deferred from `docs/plans/2026-04-16-wire-contracts-plan.md`. The plan shipped 5 steps (commits `44d557d` → `80635cf`) specifically to close the 2026-04-16 escalation where postgres.js wire semantics (`current_setting(name, true)` returns `''`, not `NULL`) produced unsatisfiable `toBeNull()` assertions.

**Result: the wire-contracts work did not fire at all**, and the run regressed the exact same failure mode that motivated it. The five implementation steps are individually correct, but the **integration layer** between them and the orchestrator was missing. All three missing wires are diagnosable from this run.

## Headline finding

Line 162 of the generated scenario:
```ts
// .dark-factory/scenarios/2026-03-28-database-foundation/tenant-context.test.ts:161
const gucResult = await db.execute(`SELECT current_setting('app.tenant_id', true) as tenant_id`);
expect(gucResult[0].tenant_id).toBeNull();
```

This is the identical unsatisfiable predicate that triggered the 2026-04-16 escalation, re-emitted by the test-engineer on a fresh run with a PASS-validated spec at `spec-hash=00ccb823`. The entire wire-contracts regime was supposed to prevent this — either via the spec-validator addendum lint, or via staged helpers the TE could import instead of hand-rolling. Neither defense fired.

## State file excerpt

```
spec=2026-03-28-database-foundation
run_id=5600d8d5d0fd4031
spec_hash=00ccb823ffbd817cd2b7e9cbf58d68407cceab87b398aede2ce7ab2cda6cc3e3
phase=
smoke_result=
pass_rate=
gate_exit_code=
server_error=
status=interrupted
iteration=1
completed_step=2
```

## Event timeline (18 total events, kill before gate)

| Elapsed | Iter | Event | Role | Attempt |
|---|---|---|---|---|
| 0s | — | SPEC_START | — | — |
| 0s | — | AGENT_START | spec-validator | 1 |
| 320s | — | AGENT_COMPLETED | spec-validator | — |
| 320s | — | AGENT_START | qa-holdout | 1 |
| 560s | — | AGENT_COMPLETED | qa-holdout | — |
| 561s | — | AGENT_START | test-engineer | 1 |
| 734s | — | AGENT_COMPLETED | test-engineer | — |
| 735s | — | COVERAGE_GAP (14/14 anchors uncovered) | — | 1 |
| 735s | — | COVERAGE_REGEN | — | 1 |
| 735s | — | AGENT_START | test-engineer (regen) | 1 |
| 908s | — | AGENT_COMPLETED | test-engineer | — |
| 908s | — | BARRIER_VIOLATION (path-echo) | test-engineer | — |
| 908s | — | COVERAGE_PASS | — | — |
| 909s | 1 | AGENT_START | implementer | 1 |
| 1296s | 1 | AGENT_COMPLETED | implementer | — |
| 1296s | — | BARRIER_VIOLATION (path-echo `.git`) | implementer | — |
| 1296s | 1 | FILES_CHANGED (count=10) | — | — |
| 1297s | 1 | AGENT_START | implementer (attempt 2) | 2 |
| **~1300s** | | **user-requested kill** | | |

**Notable absences**:
- Zero `AGENT_START role=architect` events — architect did not run for this spec.
- Zero `CONTRACTS_STAGED` events — `stage_adapter_contracts()` never fired because no postgres adapter was active.
- No `GATE_COMPLETED`, `SMOKE_COMPLETED` — terminated before first gate.

## Wire-contracts fix status: **DARK**

Evidence that none of the 5 plan-step artifacts reached runtime:

### 1. Scenario-architect addendum never injected

The postgres scenario-architect addendum (`darkfactory/adapters/postgres/scenario_architect_addendum.md`) opens with:

> Loaded by `PostgresAdapter.scenario_architect_addendum()`. The
> orchestrator ONLY injects this addendum when the substrate manifest at
> `.dark-factory/architecture/substrate-manifests/<spec>.toml` exists for
> the current spec, so the prose can assume the manifest is present.

Observed:

```
$ ls .dark-factory/architecture/substrate-manifests/
ls: No such file or directory
```

No substrate manifest → addendum returns `""` → test-engineer never saw the "Wire-contract assertion helpers" section advertising `assertUnsetGucIsNullish` et al.

### 2. No `_contracts/<adapter>/` staging tree

```
$ ls .dark-factory/scenarios/2026-03-28-database-foundation/
database-connection.test.ts
schema-migration.test.ts
tenant-context.test.ts
# no _contracts/ subdirectory
```

`darkfactory/pipeline/adapter_staging.py:stage_adapter_contracts` iterates the **active adapter list** and calls `adapter.contracts_dir()` on each. Per `.dark-factory/project-hints.toml`:

```toml
[detected]
active_adapters = [
    "typescript-node",
]
```

Postgres is not active → staging loop skips it → no `_contracts/postgres/` ever copied in.

### 3. No `CONTRACTS_STAGED` event

```
$ grep CONTRACTS_STAGED .dark-factory/events.jsonl
# empty
```

Confirms `stage_adapter_contracts` was either never called with the postgres adapter in the list, or the adapter's `contracts_dir()` returned `None` (which for `PostgresAdapter` requires the adapter not being instantiated at all — its override is unconditional).

### 4. Spec-validator addendum lint did not (apparently) fire

Spec-validator returned PASS:
```
verdict=PASS
score=94
divergence=LOW
blockers=0
```

But the failing predicate (`expect(...).toBeNull()` against `current_setting(name, true)`) is exactly what the Step-1 addendum (`darkfactory/adapters/postgres/spec_validator_addendum.md`, section "Wire-accurate predicate language") was meant to reject. Two interpretations:

- **(a) Addendum is active but spec never contained the bad wording** — plausible: commit `d90c618` retitled the spec with `"use implicit GUC with current_setting(..., true)"` which may not trigger the rule. The bad wording was invented by the **test-engineer**, not copied from the spec. In that case Step 1 did its job at spec-validation — but Step 5 (the TE-facing addendum) was the only backstop for TE-invented language, and that never fired either.
- **(b) Addendum also conditional on postgres-active** — if the spec-validator addendum follows the same `PostgresAdapter.spec_validator_addendum()` injection path, it wouldn't fire without postgres being an active adapter.

Either way, the TE-facing defense (Step 5) was dark, and there is no backstop between a clean spec and a TE that invents wire-unaware predicates.

## Generated code (what the implementer was about to ship)

Implementer attempt 1 completed with exit_code=0 (200 turns, 386s). FILES_CHANGED event reports 10 files. The DB layer:

```ts
// apps/api/src/db/index.ts
import { drizzle } from 'drizzle-orm/postgres-js';
import { sql } from 'drizzle-orm';
import postgres from 'postgres';
// ...
export const connection = postgres(env.DATABASE_URL, { max: env.DB_POOL_MAX });
export const db = drizzle(connection, { schema });
```

Stack: **drizzle-orm/postgres-js + postgres.js 3.4.0** — identical to the 2026-04-16 escalation. Against the TE's `expect(gucResult[0].tenant_id).toBeNull()`, postgres.js surfaces the empty string — the exact wire-mismatch the plan was designed to prevent.

Implementer attempt 2 was starting at kill time, indicating the first attempt hit the 200-turn cap before claiming completion.

## Audit log spot-check: docker masks

Test-engineer container `docker run` argv:

```
-v ~/sites/budget-brain-v2:/project:ro
-v /.../scenarios:/project/.dark-factory/scenarios:rw
-v /.../reports:/project/.dark-factory/reports:rw
--tmpfs /project/apps/api/src:ro,size=0
--tmpfs /project/.dark-factory/holdouts:ro,size=0
--tmpfs /project/.git:ro,size=0
```

Information barriers are sound — source (`apps/api/src`), holdouts, and `.git` are all empty tmpfs. **No postgres-adapter contracts mount appears** — not because the mount is broken, but because `stage_adapter_contracts` never wrote anything for there to be a mount from.

## The three gaps (root-cause)

| # | Gap | Mechanism | Affected plan steps |
|---|-----|-----------|---------------------|
| **G1** | `active_adapters = ["typescript-node"]` — postgres never activated despite `services.postgres` + `DATABASE_URL` in env | `dark-factory init` / project-hint generation has no heuristic to infer postgres-adapter from substrate-bearing services | Steps 2, 3, 4, 5 (all postgres-adapter gated) |
| **G2** | No substrate manifest at `.dark-factory/architecture/substrate-manifests/<spec>.toml` | Downstream of G3: architect didn't run → substrate-manifest emission step (whoever owns it) never executed | Step 5 opt-in condition |
| **G3** | Architect agent did not run (zero `AGENT_START role=architect` events) | Orchestrator apparently treats existing `environment.toml` + `manifest.yaml` (residue from prior runs on this spec) as cache-hit and skips architect | G2 downstream |

These are **wiring gaps outside the five implemented steps**, not defects in the steps themselves. Each of the five commits (`44d557d` → `80635cf`) is individually correct and passes its own unit tests; the failure is that the pipeline never calls into any of them for this project on this spec.

## Implications for the plan

The plan's "Success measure" was defined as:

> run `2026-03-28-database-foundation` end-to-end to validate artifact presence, helper usage, AC-12 wire-language, sql-tag coherence, pass-rate non-regression

That measure **cannot be met on this project** until the adapter-activation, architect-run-per-spec, and substrate-manifest-generation wiring are addressed. Any future run on this target will silently regress to the 2026-04-16 mode regardless of how well-tested the five steps are.

## Recommended next actions

1. **Proposed Step 6**: add an "adapter activation" seam so that projects with substrate-bearing services get the matching adapter auto-listed. Minimal form: `dark-factory init` scans `environment.toml`'s `[services.*]` blocks and infers activations; explicit form: allow projects to declare `active_adapters` in `config.toml` or policy.
2. **Architect-rerun policy**: clarify in orchestrator whether architect should always re-evaluate per spec, or whether substrate-manifest generation needs to be idempotent and checked independently of architect being skipped on cache.
3. **Substrate-manifest presence test**: add a preflight check that warns (or fails) when a spec mentions postgres-substrate concepts but no substrate manifest exists at scenario-gen time — would have caught this run at step 1.

## Artifact inventory (for future diff)

Implementer-written files (unstashed, still on disk — did NOT reset after kill):

```
M  .dark-factory/architecture/environment.toml
M  .dark-factory/architecture/environment.toml.lock
M  .dark-factory/project-hints.toml
M  apps/api/package.json          (added: postgres ^3.4.0, drizzle-orm ^0.44.x, drizzle-kit)
M  apps/api/src/common/config.ts   (added: DATABASE_URL env validation)
M  apps/api/src/server.ts          (added: DB pool init)
?? apps/api/drizzle.config.ts
?? apps/api/src/db/{index.ts, health-check.ts, tenant-context.ts, migrations/, schema/}
?? apps/api/tests/
?? apps/api/tsconfig.tsbuildinfo
```

These should be reset before re-running (same procedure as step 0 of `/df-pipeline-run`).

## Cost/resource summary

- Agents invoked: spec-validator (320s), qa-holdout (240s), test-engineer ×2 (173s + 173s), implementer ×1 (386s)
- Agent wall-seconds total: ~1290s
- Turns: implementer attempt-1 hit 200-turn cap (evidence of truncation, not success)
- Est. tokens: 800k in / 400k out (implementer attempt 1 only; other agents not logged)
- Terminal states reached: none — killed pre-gate

## Notes

This run is **load-bearing evidence** that the plan's implementation layer was complete but the integration layer was missing. Before committing further work on the wire-contracts surface, close G1–G3.
