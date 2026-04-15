# Substrate Verification — implementation plan

**Date:** 2026-04-15
**Origin:** failure of spec `2026-03-28-database-foundation` after 3 iterations on 2026-04-15. Implementer shipped a baseline migration containing 19 lines of comments and zero executable SQL while every AC was nominally satisfied by code inspection. Reviewer rated 38/60 "substantially complete"; oracle NO-SHIPped on substrate-blind scenarios.
**Prior analysis:** `docs/dev/2026-04-15-neo-review-pipeline-fundamentals.md` (root-cause + structural patterns)
**Consensus reviewer:** neo:neo (two rounds, agentIds `a685575de0fcf0e5f` and `ad86feec55fea9360`)

## Goal

Make substrate-thin implementations un-shippable by the pipeline. A spec that asserts behavior dependent on stateful substrate (custom GUCs, RLS policies, registered schemas) must *also* express the substrate primitives in structured form, the architect must translate them into a verifiable manifest, the scenario architect must generate scenarios that exercise them at the database layer, and the gate must execute the manifest as a phase before scenarios run.

No human-in-loop additions to the pipeline. Spec authoring (the human's actual job) gets sharper prompts; everything downstream stays autonomous.

## Non-goals

- Generic "substrate verification" abstraction across all ecosystems. Postgres-only initially. New ecosystems mirror the same five-piece pattern in their own adapter when needed.
- A `SubstrateContract` dataclass hierarchy. We need *one function* per adapter and a config table — not a type system.
- Reviewer rubric overhaul. The reviewer-vs-oracle 80-point delta is real, but Path B′ closes the gap upstream (validator) and downstream (gate phase) so the reviewer's blind spot stops being load-bearing.

## Five pieces

Each piece has one job. No piece compensates for another's failure.

### Piece 1 — `substrate-rules.toml` (taxonomy)

**Lives at:** `darkfactory/adapters/postgres/substrate-rules.toml`

**Job:** declare the conditional taxonomy of triggers → required substrate primitives.

**Initial contents (sketch):**
```toml
[[rule]]
trigger = "custom_guc"
match_patterns = ["SET LOCAL app\\.[a-z_]+", "current_setting\\('app\\."]
required_primitives = ["guc_name", "guc_default"]
required_assertions = [
  "current_setting('{guc_name}', true) IS NOT NULL",
  "current_setting('{guc_name}') = '{guc_default}'",
]

[[rule]]
trigger = "rls_policy"
match_patterns = ["ENABLE ROW LEVEL SECURITY", "CREATE POLICY"]
required_primitives = ["table_name", "policy_name"]
required_assertions = [
  "SELECT rowsecurity FROM pg_tables WHERE tablename = '{table_name}'",
  "SELECT count(*) FROM pg_policies WHERE tablename = '{table_name}' AND policyname = '{policy_name}'",
]

[[rule]]
trigger = "tenant_isolation"
match_patterns = ["tenant.*isolat", "multi[- ]tenant"]
required_primitives = ["tenant_table"]
required_scenarios = ["positive_own_tenant_visible", "negative_other_tenant_invisible"]
```

**Acceptance:** taxonomy is data, not code. New rules are TOML edits. The adapter's job is to *load and dispatch* the table; rule shape changes are a public-surface change governed by `docs/ADAPTER-GOVERNANCE.md`.

### Piece 2 — Validator addendum

**Lives at:** `darkfactory/adapters/postgres/spec_validator_addendum.md` (new file, mirrors the existing `architect_addendum.md` pattern)

**Wired through:** new abstract method `EcosystemAdapter.spec_validator_addendum() -> str` in `darkfactory/adapters/base.py`, with the same governance cap (`GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP`). Spec-validator agent invocation composes addenda across active adapters, identical to how architect addenda are composed today.

**Job:** thin shim. Tells the validator: "consult `substrate-rules.toml`. For each triggered rule, the spec MUST contain a `## Substrate Primitives` section with the rule's `required_primitives` named in structured form. If absent, mark CRITICAL_ISSUE."

**Why the addendum is thin and the taxonomy is in TOML:** the adapter owns Postgres knowledge; the validator stays generic. Encoding rules in the validator prompt would re-encode adapter knowledge in the validator and break the boundary (neo's correction in round 2).

**Acceptance:** running `dark-factory validate 2026-03-28-database-foundation` against the *current* spec text fails with specific demands like:
> CRITICAL_ISSUE: spec mentions `SET LOCAL app.tenant_id` but `## Substrate Primitives` section is missing or doesn't declare `guc_name = "app.tenant_id"` and `guc_default`. See postgres-adapter substrate-rules.

### Piece 3 — Architect addendum (substrate-manifest emission)

**Lives at:** `darkfactory/adapters/postgres/architect_addendum.md` (extend existing file; respect 80-line cap or split into a second packaged file)

**Job:** when active rules trigger, the architect emits `.dark-factory/architecture/substrate-manifests/<spec-id>.toml` alongside the existing environment delta. Format:

```toml
spec_id = "2026-03-28-database-foundation"
postgres_version = "15"

[[guc]]
name = "app.tenant_id"
default = ""
registration_check = "current_setting('app.tenant_id', true) IS NOT NULL"
default_check = "current_setting('app.tenant_id') = ''"

[[rls_table]]
name = "users"
expected_policies = ["users_tenant_isolation"]
rowsecurity_check = "SELECT rowsecurity FROM pg_tables WHERE tablename = 'users'"
policy_count_check = "SELECT count(*) FROM pg_policies WHERE tablename = 'users'"

[[isolation_pair]]
table = "users"
positive = "as tenant A, SELECT * FROM users returns A's rows only"
negative = "as tenant A, SELECT * FROM users returns 0 of B's rows"
```

**Why the architect produces this and not the validator:** the architect already emits environment deltas; this is the same shape. The validator's job is gate-or-pass on spec quality; producing artifacts is the architect's job.

**Determinism amendment (per neo round 3):** to keep the architect's role a near-deterministic TOML-to-TOML transform rather than an LLM judgment call, the validator addendum (Piece 2) requires the spec's `## Substrate Primitives` section be authored as a **fenced TOML block** with schema defined by the adapter's `substrate-rules.toml`. The architect then parses that block, stamps in `services.postgres.image` for `postgres_version`, and writes the manifest. This eliminates the "architect judges substrate" failure mode flagged in the prior neo review (§4 of `2026-04-15-neo-review-pipeline-fundamentals.md`).

**Acceptance:** running architect against a re-validated db-foundation spec produces a non-empty `substrate-manifests/2026-03-28-database-foundation.toml` whose entries are derivable from the spec's `## Substrate Primitives` fenced TOML block via near-mechanical transform (no semantic inference required).

### Piece 4 — Scenario-architect addendum

**Lives at:** `darkfactory/adapters/postgres/scenario_architect_addendum.md` (new) wired via new `EcosystemAdapter.scenario_architect_addendum() -> str` (symmetric with piece 2).

**Job:** tell the SA: "for each entry in the spec's substrate-manifest, generate a scenario that executes the assertion *as raw SQL against the sidecar database connection*, not through the application's ORM/DI/test-helper stack. Use the substrate-test fixture (provided by adapter) that exposes a direct `psycopg`/`pg` client against `DATABASE_URL`."

**Why this is non-optional:** without it, the SA converts substrate-prose into app-level tests that route through the ORM and never touch real substrate (neo's missing piece in round 1). The application layer is exactly where stub-but-look-correct implementations hide.

**Acceptance:** a generated scenario file under `.dark-factory/scenarios/` for db-foundation contains at least one test that imports a raw DB client and runs `current_setting('app.tenant_id', true)` directly.

### Piece 5 — `verify_substrate()` gate phase

**Lives at:** new method `PostgresAdapter.verify_substrate(sidecar_url: str, manifest_path: Path) -> list[Finding]` in `darkfactory/adapters/postgres/adapter.py`. Called by the gate orchestrator between `setup` and `self_tests`.

**Surface:** new ABC method `EcosystemAdapter.verify_substrate(sidecar_url, manifest_path) -> list[Finding] | None`. Returns `None` for adapters that don't produce a substrate manifest (TypeScript, Python-FastAPI today). Skip is silent — same precedent as `proposal_rehearsal_typecheck`.

**Distinguishability gap to close in Piece 5 (per Linus review of Piece 3):** the sketch below treats a missing manifest as silent skip (`return None`). That collapses two cases the gate must report differently — "no substrate rules fired against this spec, so no manifest was expected" (legitimate skip) vs. "substrate rules fired but the architect failed to emit the manifest" (silent failure of the exact bug Path B′ exists to prevent). Resolution: `verify_substrate()` MUST consult `triggered_rules(spec_text)` against the spec text and, when rules fired but the manifest is absent, return an `error` Finding (`substrate-manifest-missing`) instead of `None`. This re-routes the post-architect gap (deferred from Piece 3 to avoid coupling the orchestrator to PostgresAdapter) into the gate phase that already runs Postgres-aware code. The spec text path needs to flow into the call signature, e.g. `verify_substrate(sidecar_url, manifest_path, spec_text)` — a deliberate ABC-shape decision that should be made when Piece 5 lands, not retrofitted later.

**Implementation outline (~150 lines):**
```python
def verify_substrate(self, sidecar_url, manifest_path):
    if not manifest_path.exists():
        return None
    manifest = tomllib.loads(manifest_path.read_text())
    findings = []
    with psycopg.connect(sidecar_url) as conn:
        for guc in manifest.get("guc", []):
            row = conn.execute(guc["registration_check"]).fetchone()
            if not row or not row[0]:
                findings.append(Finding(
                    severity="error",
                    code="substrate-guc-unregistered",
                    message=f"GUC {guc['name']} not registered after setup; "
                            f"check `{guc['registration_check']}` failed",
                    path=str(manifest_path),
                ))
        for rls in manifest.get("rls_table", []):
            # rowsecurity_check, policy_count_check ...
        # isolation_pair entries are scenarios, not gate-phase checks
    return findings
```

**Gate report integration:** new `Substrate Verification | PASS/FAIL/SKIP` row in `quality-gate-summary.md`. Failure exit code matches existing soft-fail (1).

**Isolation-only-manifest semantics (decided in Wave 1 of Piece 5):** a manifest containing only `[[isolation_pair]]` entries (no `[[guc]]`/`[[rls_table]]`) returns `[]` from `verify_substrate()` — the helper short-circuits before opening a psycopg connection. The orchestrator (Wave 2) MUST report this as **PASS**, not SKIP: the manifest existed and verify_substrate was actually called; what's missing is gate-phase work to do (isolation_pairs are scenario-layer, exercised by Piece 4 against the live app stack). Reporting SKIP would conflate "no manifest emitted, nothing to check" (legitimate skip) with "manifest emitted, gate-phase scope is empty" (legitimate pass) — same reason the missing-manifest case had to be disambiguated via `triggered_rules()`. The empty-list return value is the affirmative "ran, no findings" signal the gate should mirror downstream.

**Acceptance:** if the implementer ships an empty migration, `verify_substrate()` returns Findings on the GUC-unregistered + RLS-missing checks, gate fails with code 1 in iter 1 with a precise diagnosis like:
> Substrate Verification | FAIL
> - substrate-guc-unregistered: GUC app.tenant_id not registered after setup
> - substrate-rls-missing: pg_tables.rowsecurity = false for table 'users'

This is the deterministic-iter-1-failure property neo's analysis identifies as the structural guarantee.

## Re-cycle for the existing failing spec

Once pieces 1–5 land:

0. **Git hygiene first.** budget-brain-v2's working tree contains the failed-run diff. Before re-cycling, either `git stash -u` or branch the in-flight state (`git checkout -b failed-run/2026-04-15-database-foundation` then return to clean main). Otherwise the validator-FAIL output collides with uncommitted work and the re-cycle is non-reproducible.
1. `dark-factory validate 2026-03-28-database-foundation` → expected FAIL (no `## Substrate Primitives` fenced TOML block)
2. `/df-write-spec 2026-03-28-database-foundation` → revise mode reads failing report, conducts focused interview on substrate primitives only
3. `dark-factory validate ...` → PASS
4. `dark-factory run --fresh 2026-03-28-database-foundation`
5. Architect emits substrate manifest; implementer must produce a populated migration to pass gate phase 5; SA scenarios verify behavior at substrate layer; oracle ships.

Estimated probability spec ships in 1–2 iterations after re-cycle: ~85% (neo's estimate, which I accept).

## What we explicitly decided not to do

- **`SubstrateContract` dataclass hierarchy.** Type system for what should be a config table.
- **"Always require all 5 assertions" in the validator.** Over-fits to db-foundation; conditional taxonomy is durable.
- **Validator runtime AC-text parsing for SQL patterns.** Brittle. Architect translates structured primitives → manifest; runtime checks operate on manifest.
- **Path A (human-assist with `/df-implement`) for db-foundation.** User explicitly rejected: "I would rather let the pipeline ship the spec than admit defeat."
- **Forbidden_patterns rule for `CREATE POLICY IF NOT EXISTS`.** Worthwhile but orthogonal to substrate verification. Ship in a separate change.

## Cost estimate

| Piece | Lines (impl + tests) | Risk |
|---|---|---|
| 1. `substrate-rules.toml` | ~40 + 30 | low (data) |
| 2. Validator addendum + ABC method | ~80 + 60 | low (mirrors `architect_addendum`) |
| 3. Architect addendum extension | ~40 + 60 | medium (architect must reliably extract primitives) |
| 4. SA addendum + ABC method | ~70 + 50 | medium (substrate-test fixture wiring) |
| 5. `verify_substrate()` + gate wiring | ~150 + 100 | medium (orchestrator integration) |
| **Total** | **~380 + 300** | — |

~1 day implementation + ~half day test/integration. Updates to `docs/ADAPTER-GOVERNANCE.md` and `docs/RELEASE-CADENCE.md` for the new public-surface additions.

## Risk register

| Risk | Mitigation |
|---|---|
| Architect fails to translate `## Substrate Primitives` → manifest | Architect addendum includes a worked example; precheck step verifies manifest exists when validator addendum triggered |
| `verify_substrate()` false positives (sidecar timing, transaction ordering) | Run substrate verification *after* setup completes successfully; transaction-scoped checks where applicable |
| Spec author writes minimal `## Substrate Primitives` to satisfy validator without engaging | Validator addendum required-primitives list forces concrete naming (GUC name, table name); not satisfiable with hand-waves |
| New Postgres rule needed mid-flight | TOML edit + bump postgres-adapter minor version per `docs/RELEASE-CADENCE.md` |
| Pattern doesn't generalize to other ecosystems (Mongo, Redis) | Accept the per-adapter symmetry cost when the second ecosystem hits the same failure mode; don't pre-build the abstraction |

## Acceptance criteria for the framework change

1. Contract harness (`tests/adapters/test_adapter_contract.py`) gains tests for new ABC methods (`spec_validator_addendum`, `scenario_architect_addendum`, `verify_substrate`) and the same governance line cap.
2. **Non-Postgres opt-out test** (per neo round 3): contract harness asserts `typescript-node` and `python-fastapi` return falsy (`""` for addenda, `None` for `verify_substrate`) for all three new methods. Cheap guardrail against accidental Postgres knowledge bleeding into other adapters.
3. Postgres adapter test coverage includes: `substrate-rules.toml` load, manifest schema validation, `verify_substrate()` against a real ephemeral PG sidecar with both populated and empty migration cases.
4. End-to-end: rerun db-foundation pipeline produces a populated migration in ≤2 iterations.
5. Other shipping adapters (`typescript-node`, `python-fastapi`) continue to pass without modification.

## Governance and tier deliverables

Per neo round-3 amendments, the following must ship alongside the code:

- **`docs/ADAPTER-GOVERNANCE.md` update.** Add an entry declaring `substrate-rules.toml` schema as a semver-governed public surface — same status as `architect_addendum.md` and `EcosystemAdapter` ABC shape. Schema changes (adding/removing fields on rules) trigger the deprecation cadence in `docs/RELEASE-CADENCE.md`. Adding new rules to the table is a minor-version bump; removing or renaming existing rules is a major-version bump with the standard deprecation window.
- **Adapter-tier statement.** Postgres adapter is currently `core` tier (per `docs/ADAPTER-GOVERNANCE.md` line 29). This work *extends* the core surface; it does not promote a tier. Update the Postgres adapter class docstring with a `# Substrate verification added in <commit-sha>` note and reference this plan, mirroring the existing promotion-note convention.
- **Validator-addendum prose discipline.** Re-read `darkfactory/adapters/postgres/spec_validator_addendum.md` before merge to confirm the prose references the TOML *by path* (`substrate-rules.toml`), not by inline naming of specific primitives like `guc_name` or `table_name`. Any inline naming re-encodes Postgres knowledge in the validator and breaks the boundary on the first copy-paste to a future adapter.
