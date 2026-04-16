# Plan: wire-contract parity for test-engineer & implementer (Postgres)

**Date:** 2026-04-16
**Motivation:** The 2026-04-16 01:47 run of `2026-03-28-database-foundation` escalated because the test-engineer produced scenarios that (a) asserted `toBeNull()` against `current_setting('x', true)` when postgres.js wire-surfaces an empty string, and (b) mixed drizzle-orm and postgres.js `sql` tags inside `tx.execute()`. The implementer had no barrier-respecting path to satisfy these scenarios.

**Root cause:** test-engineer and implementer consume the spec through different predicates. Implementer reads the whole spec for behavior; test-engineer reads anchor-sentence + architect manifest (syntactic handshake only). Wire-level library truths living inside FR prose are invisible to the test-engineer.

**Shape:** two independent layers.

1. **Upstream lint** catches specs that use SQL-semantic language ("returns NULL") over library surfaces where the wire truth differs ("returns '' when missing_ok=true"). Fails at spec-validator, cheapest to fix.
2. **Executable contracts** give the test-engineer a library of runnable assertion helpers whose bodies encode the wire truth. Adapter upgrades propagate automatically; no TOML fact sheets to decay.

Defer rehearsal (tsc typecheck against stubbed SUT) and manifest `wire_contracts` routing until (1) + (2) are measured.

## Non-goals

- Supporting Python/psycopg contracts (future).
- Rewriting specs in a two-register observable/predicate form.
- Adding a new agent role (brief-producer, semantic-architect).
- Publishing an npm package.

---

## Step 1 — Wire-language lint (spec-validator addendum extension)

**Files touched:**
- `darkfactory/adapters/postgres/spec_validator_addendum.md` — append new section, respecting 80-non-blank-line governance cap (verify with contract harness `test_spec_validator_addendum_within_governance_line_cap`)
- `tests/unit/test_postgres_adapter.py` — new tests asserting the addendum prose contains the wire-language rule, cites both trap names, and the rendered composition (prose + taxonomy) still fits the cap
- `tests/unit/test_postgres_spec_validator_wire_lint.py` (new) — **negative test**: synthesize a spec with `toBeNull()` over `current_setting('x', true)` and assert the validator prompt contains the CRITICAL_ISSUE-triggering language required to reject it (prompt-side assertion; can't actually run the LLM in unit tests, so we assert the addendum text that instructs the rejection exists and is coherent)

**Addendum content (new section, ~25 lines):**

```
## Wire-accurate predicate language for library-mediated surfaces

When an AC/EC asserts a predicate over a library call result, the
predicate MUST be stated in the wire-level vocabulary the test runner
will observe — not the SQL-semantic vocabulary that would be true at
the database layer.

Common traps (Postgres / postgres.js):

1. `current_setting(name, true)` — at SQL semantics returns NULL when
   the GUC is unset; at the postgres.js wire surface the query result
   row exposes the field as the empty string `''`. ACs must say
   "returns empty string or null" (nullish), not "returns NULL".

2. `sql` tagged-template identity — drizzle-orm's `sql` tag produces
   a `SQL` object with `.getSQL()`; postgres.js's tagged-template
   produces a `PendingQuery`. `tx.execute(...)` from drizzle-orm
   requires the drizzle `SQL` shape; passing a postgres.js
   `PendingQuery` raises at runtime. Specs that mix both in one
   statement MUST be flagged — the test-engineer will faithfully
   encode the mix.

### Verdict

If an AC/EC references `current_setting(..., true)` and asserts NULL
(not nullish), mark a **CRITICAL_ISSUE** naming the anchor ID.

If a single code block or inline contract references both
`drizzle-orm`'s `sql` and `postgres.js`'s tagged template within one
`execute` call, mark a **CRITICAL_ISSUE** naming the anchor ID.
```

**Why this placement:** the validator addendum is already the adapter's spec-entry gate. Extending it costs one commit, touches no pipeline core, and blocks the category-4 class at the cheapest possible phase.

**Exit criteria:**
- Contract harness passes (`test_spec_validator_addendum_within_governance_line_cap`)
- New unit test verifies the rendered composition contains both traps
- Manual eye-check the composed prompt (run `PostgresAdapter().spec_validator_addendum()`, inspect)

---

## Step 2 — Contracts module scaffold + `assertUnsetGucIsNullish`

**Files created:**
- `darkfactory/adapters/postgres/contracts/ts/package.json` — declares `"name": "@df/contracts-postgres"`, no npm publish; zero external runtime deps; `type: "module"` for ESM
- `darkfactory/adapters/postgres/contracts/ts/index.ts` — barrel exporting assertion helpers
- `darkfactory/adapters/postgres/contracts/ts/guc.ts` — `assertUnsetGucIsNullish(client, name)` encoding postgres.js wire-surface truth (accepts `null | ''`)
- `darkfactory/adapters/postgres/contracts/ts/README.md` — documents: what each helper guarantees, why it exists (bug it prevents), barrier semantics
- `darkfactory/adapters/postgres/contracts/ts/tsconfig.json` — standalone, no inheritance from target project; `strict: true`, `moduleResolution: "NodeNext"`, `noEmit: true` (helpers are imported as `.ts` under vitest's loader in the gate)

**Why a standalone TS package with its own tsconfig/package.json:** the target project's tsconfig/typecheck runs against `apps/api/src/**`, not `.dark-factory/scenarios/<spec>/_contracts/**`. The contracts must be self-typecheckable in isolation. Scenario files import helpers directly; vitest's on-the-fly TS loader handles compilation at scenario runtime.

**Helper signature (pinned):**
```typescript
import type { Sql } from 'postgres';  // peer type, not runtime dep

export async function assertUnsetGucIsNullish(
  client: Sql,
  gucName: string,
): Promise<void> {
  const rows = await client`SELECT current_setting(${gucName}, true) AS v`;
  const value = rows[0]?.v;
  if (value !== null && value !== '') {
    throw new Error(
      `assertUnsetGucIsNullish: GUC ${gucName} expected nullish ` +
      `(null | ''), got ${JSON.stringify(value)}`
    );
  }
}
```

**Why take the `postgres.Sql` type as peer, not bundled:** the helper must interop with the client the test-engineer's scenario already opened against `DATABASE_URL`. Bundling `postgres` would create two different Sql instances in one suite.

**Testing (revised per Neo):** **do NOT ship `.test.ts` files.** Vitest staged into scenarios would pick them up and run every pipeline, adding noise and runtime cost; "pipeline proves them out" is not a test suite. Instead:

- `tests/unit/test_postgres_contracts_smoke.py` — one Python test that:
  1. Asserts the expected files exist.
  2. Asserts `package.json` parses as JSON and declares `type: "module"`.
  3. Asserts `index.ts` source text exports every helper name the plan promises (substring check).
  4. **If** `shutil.which("tsc")` resolves, run `tsc --noEmit -p darkfactory/adapters/postgres/contracts/ts` in a subprocess and assert exit 0. If `tsc` is not on PATH, `pytest.skip("tsc not available")` — this keeps CI green in minimal environments while pinning typecheck when a developer has it.

**Exit criteria:**
- `assertUnsetGucIsNullish` exists, exported from `index.ts`
- README documents the wire truth the helper encodes and cites the 2026-04-16 bug
- `test_postgres_contracts_smoke.py` passes (and runs `tsc --noEmit` when available)

---

## Step 3 — `assertRlsEnabled` + `assertTenantIsolated`

**Files modified / created:**
- `darkfactory/adapters/postgres/contracts/ts/rls.ts` (new) — `assertRlsEnabled(client, tableName)` queries `pg_tables.rowsecurity` AND `pg_policies` count; distinguishes the three failure modes (`table-missing`, `rls-disabled`, `no-policies`) in the thrown error message, mirroring the substrate-verifier's `_check_rls_tables` helper
- `darkfactory/adapters/postgres/contracts/ts/isolation.ts` (new) — split into two primitives so the positive/negative split is visible at the scenario-call layer (Reviewer/Oracle read scenario names to classify coverage; embedding both checks in one helper hides the distinction):
  ```typescript
  export async function assertTenantSeesOwnRows(
    client: Sql,
    table: string,
    tenant: string,
  ): Promise<void>;  // SET LOCAL app.tenant_id = '<tenant>'; SELECT * FROM <table>; all rows' tenant_id === tenant

  export async function assertTenantCannotSeeOtherRows(
    client: Sql,
    table: string,
    self: string,
    other: string,
  ): Promise<void>;  // SET LOCAL app.tenant_id = '<self>'; SELECT * FROM <table> WHERE tenant_id = '<other>'; row count === 0
  ```
  Failure messages cite the wire truth being asserted — "tenant A saw tenant B's rows" vs "tenant A did not see its own rows" — so defect-classifier feedback sharpens.
- `darkfactory/adapters/postgres/contracts/ts/index.ts` (modified) — re-export new helpers
- No `.test.ts` files — same rationale as Step 2.

**Why these three specifically:** they cover the three failure modes from the 04-16 run (`current_setting` wire truth, RLS policy presence, tenant isolation positive/negative). Each corresponds 1:1 to a substrate-rule trigger in `substrate-rules.toml` (`custom_guc`, `rls_table`, `tenant_isolation`), so the scenario-architect addendum maps taxonomies → helpers cleanly.

**Exit criteria:**
- `tests/unit/test_postgres_contracts_smoke.py` extended to assert all three helpers exported
- `tsc --noEmit` passes on the contracts dir (when tsc is available)
- Error messages from each helper cite the wire truth being asserted (substring-tested from Python side)

---

## Step 4 — Orchestrator staging

**Design decision:** contracts are staged per-spec into `.dark-factory/scenarios/<spec>/_contracts/<adapter.name>/`. The test-engineer writes imports as `./_contracts/postgres` (relative from flat scenarios root). Nested scenarios (`acceptance/*.test.ts`) use `../_contracts/postgres`. The gate container sees the same filesystem so resolution works identically.

**Why not a bind-mount at a fixed path (`/df-contracts/...`)?**
1. Forces every adapter to coordinate on container-path conventions.
2. Bind-mounts require modifying `darkfactory/docker/` container composition for every role that touches scenarios — test-engineer, gate, reviewer.
3. Per-spec staging localizes the contracts to exactly the scope that uses them and leaves no orphan mounts in containers that don't need them.

**Why not a tsconfig path alias (`@df/contracts/postgres`)?** The target project's tsconfig controls scenario resolution (vitest runs under that config). We would have to mutate the target project's tsconfig per pipeline run — invasive, fragile. Relative imports from a known flat layout are the simpler contract.

**ABC extension:** add a **concrete (non-abstract)** method on `EcosystemAdapter`:

```python
def contracts_dir(self) -> Path | None:
    """Absolute path to a directory of library-wire-contract helpers
    the orchestrator stages under
    .dark-factory/scenarios/<spec>/_contracts/<self.name>/ before the
    scenario-architect runs.

    Default ``None`` means "this adapter has no contracts to stage"
    — the orchestrator skips staging for this adapter. Additive,
    non-breaking extension point: existing adapters inherit the
    default without code changes (see docs/ADAPTER-GOVERNANCE.md —
    abstract-method changes trigger deprecation cadence; concrete
    additive methods do not).
    """
    return None
```

**PostgresAdapter override (`darkfactory/adapters/postgres/adapter.py`):**
```python
def contracts_dir(self) -> Path | None:
    return Path(__file__).parent / "contracts" / "ts"
```

**Orchestrator wiring (exact slot):** In `darkfactory/pipeline/orchestrator.py`, between line 292 (where `active_adapters = load_active_adapters(...)` binds the list) and line 558 (`_step_scenario_generation(...)` call). Add a new helper call before `_step_scenario_generation`:

```python
# Stage adapter-owned wire-contract helpers into the per-spec scenarios
# dir so the test-engineer can import them via relative path. Must run
# BEFORE _step_scenario_generation launches the test-engineer container.
if spec_run.completed_step < 2:
    _stage_adapter_contracts(
        scenarios_dir, active_adapters, events_file=events_file,
    )
```

**New module:** `darkfactory/pipeline/adapter_staging.py` — houses `_stage_adapter_contracts(scenarios_dir, adapters, *, events_file)`. Logic:

```python
def _stage_adapter_contracts(
    scenarios_dir: Path,
    adapters: list[EcosystemAdapter],
    *,
    events_file: Path | None = None,
) -> None:
    """Copy each adapter's contracts_dir() into
    scenarios_dir / "_contracts" / adapter.name. Idempotent — removes
    stale dest before copying. Called after active_adapters are bound
    and before _step_scenario_generation."""
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    for adapter in adapters:
        src = adapter.contracts_dir()
        if src is None:
            continue
        if not src.is_dir():
            log.warning(
                "adapter %s.contracts_dir() returned %s, but it is not a "
                "directory; skipping stage",
                adapter.name, src,
            )
            continue
        # adapter.name is the registry kebab-case key; filesystem-safe
        # (pinned by test_adapter_name_is_filesystem_safe in contract harness).
        dest = scenarios_dir / "_contracts" / adapter.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        if events_file is not None:
            emit_event(events_file, "CONTRACTS_STAGED", {
                "adapter": adapter.name,
                "src": str(src),
                "dest": str(dest),
            })
```

**Files modified:**
- `darkfactory/adapters/base.py` — add concrete `contracts_dir()` → `None`
- `darkfactory/adapters/postgres/adapter.py` — override returning `contracts/ts` path
- `darkfactory/pipeline/adapter_staging.py` (new) — `_stage_adapter_contracts`
- `darkfactory/pipeline/orchestrator.py` — import + call `_stage_adapter_contracts` in the slot above
- `tests/adapters/test_adapter_contract.py`:
  - `test_contracts_dir_default_returns_none` (pins default)
  - `test_contracts_dir_when_set_resolves_to_directory` (for adapters that override)
  - `test_adapter_name_is_filesystem_safe` (pins kebab-case; `re.fullmatch(r'[a-z][a-z0-9-]*[a-z0-9]', adapter.name)`)
- `tests/unit/test_postgres_adapter.py` — assert PostgresAdapter override returns `contracts/ts` path and it exists
- `tests/unit/test_adapter_staging.py` (new):
  - Stage into tmp scenarios_dir, assert files present at `_contracts/postgres/index.ts`
  - Re-stage is idempotent (second call overwrites, no stale leftovers)
  - Adapter returning `None` contributes nothing
  - Adapter returning a non-existent path emits a warning, does not raise

**Exit criteria:**
- Adapter contract test passes for all three existing adapters
- Staging test asserts `_contracts/postgres/index.ts` present after call
- Staging is idempotent
- Staging is called before `_step_scenario_generation` (assert by reading the orchestrator source — integration test would be heavier than needed)

**Barrier verification (Neo's hidden dependency check):**
- `darkfactory/docker/barriers.py` lines 75–80: scenario-architect's `sa_masked = [*source_dirs, _GIT]` does NOT mask `.dark-factory/scenarios/` — confirmed. Test-engineer writes to it, which already requires read+write.
- Gate container mounts `.dark-factory/scenarios/` (it runs the tests). Re-verify by reading the gate's Docker compose logic before Step 4 lands; if masked, revise staging destination.

---

## Step 5 — scenario-architect addendum update

**File modified:** `darkfactory/adapters/postgres/scenario_architect_addendum.md`

**New section (stays within 80-line cap by trimming a redundant example):**

```
### Wire-contract assertion helpers

When writing substrate-layer or library-mediated assertions, prefer
the staged contract helpers under `_contracts/postgres/` over
hand-rolled matchers. From a test file at the scenarios root:

  import { assertUnsetGucIsNullish, assertRlsEnabled,
           assertTenantSeesOwnRows, assertTenantCannotSeeOtherRows
         } from './_contracts/postgres';

From a test file in a subdirectory (e.g. `acceptance/foo.test.ts`),
adjust the relative path accordingly (`../_contracts/postgres`).

These helpers encode wire-level truths the postgres.js + drizzle
layer surfaces — e.g. `current_setting(name, true)` returns the empty
string at the wire, not NULL. Hand-rolled assertions against these
surfaces repeatedly produce false-negatives the implementer cannot
satisfy (2026-04-16 database-foundation escalation).

Helper → substrate-rule trigger:
  [[guc]]              → assertUnsetGucIsNullish
  [[rls_table]]        → assertRlsEnabled
  [[tenant_isolation]] → assertTenantSeesOwnRows (positive scenario)
                         + assertTenantCannotSeeOtherRows (negative
                         scenario) — one helper per scenario in the
                         isolation pair

Use the helpers for the primary assertion; add hand-rolled secondary
assertions only for behavior not covered by a helper.
```

**Exit criteria:**
- Governance-cap contract test passes (`test_scenario_architect_addendum_within_governance_line_cap`)
- New unit test asserts the addendum prose references all three helper names and the `./_contracts/postgres` import path string (so any Step-4 staging-path rename is caught)
- The import path matches the staging destination in Step 4 (pinned by cross-file assertion in the Step-4 staging test)

---

## Out of scope (explicitly deferred)

- **Architect manifest `wire_contracts` block (routing map).** Would give the architect classification responsibility ("which surface does this AC touch?"). Skip for v1 — test-engineer can consult substrate-rule taxonomy (already in manifest) to pick a helper. Revisit if v1 shows the test-engineer fails to select helpers.
- **Proposal-rehearsal typecheck against stubbed SUT.** Requires generating `.d.ts` from `entry_points[*].exports`. Real value as a forcing function, but only worth building if Steps 1–5 leave residual wire-contract failures. Measure first.
- **Python/psycopg contracts.** `contracts/py/` directory can be added symmetrically when the first Python+Postgres pipeline run hits a wire-truth mismatch.
- **Escalation-agent hallucination (independent issue).** Separate track — require every finding to cite a telemetry file+line. Not blocking this plan.

## Success measure

Run `2026-03-28-database-foundation` end-to-end after Step 5. Pass criteria:

1. **Artifact check:** `.dark-factory/scenarios/2026-03-28-database-foundation/_contracts/postgres/index.ts` is present after orchestrator runs (read from the target project after `completed_step=2`).
2. **Helper-usage check:** grep the generated `.test.ts` files for `assertUnsetGucIsNullish` / `assertRlsEnabled` / `assertTenantSeesOwnRows` / `assertTenantCannotSeeOtherRows`. At least one substrate-rule-triggered scenario must reference the matching helper. Falsifier: substrate rule fires, but scenarios still use hand-rolled `=== '' || === null` — the nudge failed even if the run is green.
3. **AC-12 wire-language:** no scenario asserts `toBeNull()` against a `current_setting(..., true)` result. If the spec's AC-12 still says "returns NULL" (lint didn't fire because the spec was authored before Step 1 shipped), re-run the validator on this spec — expect it now emits a CRITICAL_ISSUE on AC-12.
4. **Sql-tag coherence:** no scenario mixes drizzle-orm `sql` and postgres.js tagged templates in a single `tx.execute(...)` call (substring grep: `tx.execute(client\`` → fail).
5. **Pass-rate non-regression:** iter 1 → iter 2 pass rates non-decreasing (no implementer regression caused by stale helpers).
6. **Negative spec-validator test:** separately, author a minimal spec with AC-12 saying `toBeNull()` against `current_setting('x', true)`. Run `dark-factory validate <spec>`. Expect blockers ≥ 1 with a finding that names the wire-language rule.

If the pipeline escalates on a new wire-truth class not covered by Steps 1–5, add a catalog entry + a helper; do not build speculative infrastructure.

## Sequencing

Steps 1 → 2 → 3 → 4 → 5 in order. Each commits independently. Linus review after each step. Step 1 is upstream-only and safe to ship even if 2–5 stall. Steps 2–3 ship adapter-owned code that has no effect on the pipeline until Step 4 stages it. Step 4 wires everything. Step 5 tells the test-engineer to use what Step 4 staged.

**Rollback:** any step can be reverted in place. Revert Step 5 to stop nudging; revert Step 4 to stop staging; keep Steps 1–3 as dormant adapter code.
