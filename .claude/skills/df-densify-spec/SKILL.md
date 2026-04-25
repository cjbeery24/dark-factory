---
name: df-densify-spec
description: Autonomously densify an existing spec to 30-50+ acceptance criteria with mechanism-full detail (Yassine Product Owner pattern — Phase 4 pivot).
argument-hint: "<spec-name>"
---

> **No Docker isolation.** Skills run in your local Claude Code session without container-enforced filesystem barriers. This skill edits `.dark-factory/specs/<spec>.md` — an input artifact consumed by later barrier-enforced pipeline agents. It never writes implementation code.

# Densify Specification (Product Owner Pattern)

## Purpose

Dark Factory's Phase 0–3 experiments (0 clean ships in 8 scored trials) traced to a single structural gap: **dark-factory has no Yassine-style Product Owner agent**, so spec quality is bounded by how much domain detail a human volunteers during a `/df-write-spec` interview. Yassine's reference pipeline (`docs/goals/agent-details.md`) describes a Product Owner that produces **55 measurable ACs from a human trigger**; dark-factory's current specs typically land at 10–20 tagged requirements. Thin specs push decision burden downstream to the architect / implementer, who can't compensate reliably at temp=0.

This skill is the MVP replacement: take an existing thin spec and autonomously expand it to 30–50+ ACs with concrete tool names, exact wire contracts, explicit invariants, and structural exclusion of known foot-gun patterns. **Mechanism-full is the goal.** Phase 2's P4 mechanism-token gate has been disabled (`PipelineConfig.p4_mechanism_token_gate_enabled = False`) — the spec may and should name drizzle-kit, postgres, current_setting, RLS policies, etc. directly.

You are functioning as the Product Owner for this densification pass. Decide what the spec should name. The human confirms the result; they do not fill in detail you should already know.

## Pre-flight Checks

Before proceeding, verify ALL of the following. STOP with a clear error if any fails.

1. **Spec exists:** `.dark-factory/specs/<spec-name>.md` is present. If absent, suggest `/df-write-spec <spec-name>` to author an initial stub.
2. **Templates accessible:** `.dark-factory/templates/` exists and contains the spec type templates. If missing, tell the user to run `dark-factory init --update`.
3. **P4 gate is disabled** (`config.toml` → `p4_mechanism_token_gate_enabled = false`, or the default in this branch). If P4 is enabled, warn the user that densified specs will contain mechanism tokens the gate will reject — suggest flipping the flag off first.
4. **Advisory — validation state:** if a prior validation report exists at `.dark-factory/reviews/<spec>-validation.*` and shows failures, read it — the densification should address those failures while adding new density.

## Mode: Densify

The skill runs autonomously — it drafts the entire densified spec in one pass, then presents a summary for user confirmation. The user does not answer 30 questions; they either ship, revise named sections, or redirect the approach.

### Step 1: Read inputs

1. Read the target spec at `.dark-factory/specs/<spec-name>.md`.
2. Read `.dark-factory/project-hints.toml` — note `[detected].active_adapters` to know which ecosystems apply.
3. Read the adapter addenda for each active adapter:
   - `<dark-factory-repo>/darkfactory/adapters/<adapter-name>/architect_addendum.md`
   - `<dark-factory-repo>/darkfactory/adapters/<adapter-name>/scenario_architect_addendum.md`
   - `<dark-factory-repo>/darkfactory/adapters/<adapter-name>/spec_validator_addendum.md`

   Adapter addenda carry ecosystem-specific rules (e.g., postgres's RLS-via-migration invariant, drizzle-kit's migration tool discipline). Use them as the domain knowledge source; don't reinvent what they encode.

4. Read the project source tree at a shallow depth (`ls apps/`, `cat apps/*/package.json`) to understand what's already adopted vs. what the spec will introduce. This is a strictly read-only inspection.

### Step 2: Gap analysis

Measure the current spec's density and surface what's missing:

- **AC / FR / EC / NFR count** — report current vs. target (30–50 ACs).
- **Mechanism-token coverage** — scan for whether concrete tool/tech names appear (drizzle-kit, postgres, current_setting, RLS, etc.). A spec that names no tools is thin by construction.
- **Wire-contract coverage** — identify every "shape-bearing" claim (JSON response, error envelope, log line, CLI flag) and check whether the exact shape is fenced in a code block. Unshaped claims are ambiguity sources.
- **Invariant coverage** — for domain-specific invariants (tenant isolation, data integrity, concurrent-write safety), check whether each is asserted as a Non-Negotiable Acceptance Criterion.
- **Forbidden-pattern exclusion** — for known foot-gun patterns that live in lint rules (`darkfactory/adapters/<name>/scenario_architect_addendum.md` or similar), check whether the spec explicitly names them as forbidden. If not, scenarios will be authored to allow them.

Output a single-paragraph gap summary:
```
Current: 14 reqs (4 FR, 10 EC). Target: 40 ACs.
Missing: concrete tool names (drizzle-kit, postgres pinned versions); journal
seed mechanism; RLS invariants (rowsecurity, force); withTenantDb callback
signature; forbidden-pattern exclusions (peer_client_guc_read, parameterized_
set_local). Will draft ~26 additional ACs across these dimensions.
```

### Step 3: Autonomous draft

Densify in one pass. For each existing FR, expand into 3–5 ACs covering:

- **Happy path** — the normal success case with exact expected values
- **Error handling** — at least one AC per error class (invalid input, missing dep, auth failure, upstream unavailable)
- **Edge cases** — boundary conditions (empty, null, max, duplicate, concurrent)
- **Observability** — log lines, metrics, health endpoints the system exposes
- **Invariants** — what must ALWAYS be true (tenant isolation, no silent data loss, no privilege escalation)

Add new sections if missing:
- **Wire Contracts** — fenced code blocks for every shape-bearing claim
- **Tool Adoption** — explicit `depends-on` tools with pinned versions
- **Non-Negotiable Acceptance Criteria** — populate with every security / data-integrity / contract-breaking AC (default most ACs to `Blocking: no`, flag only the genuinely release-critical ones)
- **Forbidden Patterns** — structural exclusion ACs, phrased as "tests MUST NOT use <pattern> — <reason>"

**Mechanism-full is mandatory.** Name tools (`drizzle-kit`, `postgres.js`, `Fastify`), exact identifiers (`app.tenant_id`, `DATABASE_URL`), exact file paths (`apps/api/src/db/migrations/meta/_journal.json`), exact SQL fragments (`ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY`), and exact command forms (`pnpm drizzle-kit migrate`). The P4 gate is off; be explicit.

Preserve metadata: `feature-id`, `depends-on`, `plan`, `spec_type` must round-trip verbatim.

### Step 3.5: Migrate accreted negative-space content (Phase 5 Stage 2c)

After densifying FRs / ACs / wire contracts, populate the spec's
**§4a Negative Space, Invariants, and Lifecycle** section by migrating
content from the project's accreted adapter surfaces. This is the
"don't make every spec author re-derive the same rules" mechanism —
the project already encodes its policy in `project-hints.toml` and
the active adapters' addenda; the densifier should pull that content
into the spec's structured fields once.

**Source 1 — `project-hints.toml` `[[policy.consistency_rules]]`.**
Read the project's `.dark-factory/project-hints.toml`. For each
`[[policy.consistency_rules]]` entry:

- `forbidden_env` regexes → §4a `forbidden_commands` entries.
- `description` → the entry's `reason`.
- `env_paths` → `applies_to`. The validator recognizes
  `commands.setup`, `commands.install`, `commands.server_start`. A
  hint of `commands.*` (the project-hints convention) means "every
  command field" — emit each pattern with `applies_to: []`
  (empty = all scopes). When `env_paths` lists specific paths, map
  them directly.

Example: the `migrate-mode-vs-push-mode` rule with
`forbidden_env = ['\bdrizzle-kit\s+push\b', '\bdb:push\b']` and
`env_paths = ['commands.*']` becomes:

```yaml
forbidden_commands:
  - pattern: '\bdrizzle-kit\s+push\b'
    reason: "Spec mandates CLI-driven migrations (db:migrate); push-mode auto-applies and bypasses the migration journal."
    applies_to: []
  - pattern: '\bdb:push\b'
    reason: "Spec mandates CLI-driven migrations (db:migrate); push-mode auto-applies and bypasses the migration journal."
    applies_to: []
```

**Source 2 — active adapter foot-gun patterns.** For each adapter in
`[detected].active_adapters`, look up the canonical foot-gun
patterns. These are the patterns the gate's scenario-lint rules
already enforce project-wide; migrating them into the spec's §4a
makes the spec self-documenting and lets us retire the project-wide
rules once every active spec carries the declarations.

For `postgres` (the only adapter with foot-gun patterns today),
emit these `forbidden_test_shapes` entries when the spec touches
tenant-scoped database access:

```yaml
forbidden_test_shapes:
  - pattern: 'postgres\(process\.env\.[A-Z_]+\)[\s\S]{0,400}?`SELECT current_setting\(''app\.'
    reason: "peer_client_guc_read: a second postgres() client outside withTenantDb cannot read app.* GUCs (transaction-scoped). Use assertAppSetsTxGuc helper instead."
    applies_to: [scenarios, holdouts]
  - pattern: '\btx\s*`[^`]*?SET\s+LOCAL[^`]*?\$\{'
    reason: "parameterized_set_local: Postgres SET LOCAL cannot bind parameters; the value placeholder becomes $1 and the server rejects with 'could not determine data type of parameter $1'. Use SELECT set_config('app.x', ${val}, true) instead."
    applies_to: [scenarios, holdouts]
  - pattern: '\bCREATE\s+POLICY\b(?![\s\S]{0,200}\bFORCE\s+ROW\s+LEVEL\s+SECURITY\b)'
    reason: "owner_bypass_missing_force: declaring CREATE POLICY without ALTER TABLE ... FORCE ROW LEVEL SECURITY allows the table owner (and superuser) to bypass every policy. Tests authored as the owner role pass trivially without the schema's intended isolation."
    applies_to: [scenarios, holdouts]
  - pattern: 'current_setting\(''app\.[a-z_]+''(?:,\s*true)?\)\s*(?:=|==|IS NULL|IS NOT NULL|\?\?)\s*(?:''''|null|undefined|NULL)'
    reason: "strict_null_empty_guc_check: comparing current_setting() against literal '' or NULL conflates 'unset' with 'set to empty'. Pass an explicit tenantId via the helper API and assert the round-tripped value, not the absence."
    applies_to: [scenarios, holdouts]
```

For substrate-clean specs (no DB, no tenant context), `postgres`
foot-gun patterns are NOT relevant — skip them. Detect this from the
spec's Constraints section ("Database / ORM | None") or absence of
substrate-rule triggers in the spec body.

For ecosystems with no published foot-gun list, skip Source 2
silently.

**Source 3 — required_lifecycle_sequences for substrate-prereq tools.**
When the spec's tool adoption introduces a tool with documented
ordering prerequisites, encode them. The canonical example for
postgres+drizzle-kit:

```yaml
required_lifecycle_sequences:
  - name: "migrate-needs-journal"
    sequence: ["seed_journal", "drizzle-kit migrate"]
    rationale: "drizzle-kit migrate hangs indefinitely if apps/api/src/db/migrations/meta/_journal.json is missing — the implementer must seed an empty journal {\"version\":\"7\",\"dialect\":\"postgresql\",\"entries\":[]} before invoking migrate."
    enforcement: substrate-agent + gate-Setup-phase
```

**Source 4 — ecosystem_invariants for cross-cutting positive-form
contracts.** When the spec asserts behaviors enforceable as
ecosystem-level invariants (e.g., RLS-via-migration), declare them:

```yaml
ecosystem_invariants:
  - name: "rls-via-migration"
    invariant: "Row-level security policies are declared in SQL migration files, never in runtime application code."
    enforcement: architect.amend_check
  - name: "tenant-binding-via-callback"
    invariant: "Tenant-scoped database access ONLY through withTenantDb / request.withDb callbacks."
    enforcement: scenarios.lint
```

**Source 5 — `wire_contracts.test_server` when the spec exposes
HTTP endpoints.** If the spec has FRs that describe HTTP responses
(status codes, response bodies, headers), declare the test-server
contract under §5b Wire Contracts:

```yaml
test_server:
  command: "<the project's server-start command, e.g. 'cd apps/api && pnpm start'>"
  base_url: "http://localhost:3000"   # or whatever port the project actually uses
  startup_timeout_seconds: 10
```

Detect "spec exposes HTTP" from: presence of curl examples, mention
of HTTP status codes, response/request body sections, or API
contract fences. For pure-library or pure-database specs, skip §5b
test_server entirely (the spec has no HTTP surface).

**Acceptance criterion for Step 3.5:** §4a is non-empty (at least
one of the four list blocks has ≥1 entry) OR the spec carries an
explicit `Justification:` paragraph below the §4a heading explaining
why no negative-space rules apply (e.g., "Pure library spec, no
runtime invariants beyond existing coverage"). An empty §4a without
a Justification fails the spec self-check at the end of Step 5.

**Idempotency rule:** if the spec already carries a §4a section
with content, MERGE rather than overwrite. Each block's entries
are keyed by `pattern` (forbidden_*) or `name` (everything else);
existing entries are preserved verbatim, new entries are appended.
Document the merge in Step 4's summary so the user knows which
entries are pre-existing vs. newly migrated.

### Step 4: Present + confirm (high autonomy)

Show the user a structured summary (NOT the full spec — they can read it themselves):

```
Densified 2026-03-28-database-foundation
--------------------------------------------
Before: 14 reqs (4 FR, 10 EC)
After:  42 reqs (8 FR, 18 AC, 12 EC, 4 NFR)

Added coverage:
  - Tool adoption: drizzle-orm ^0.44, drizzle-kit ^0.31, postgres ^3.4
  - Journal bootstrap: explicit AC for seeded _journal.json before first migrate
  - Tenant binding: withTenantDb callback signature (Tier 1 contract)
  - RLS invariants (Non-Negotiable): rowsecurity=true, force=true, policy count ≥ 1 per tenant table
  - Forbidden patterns: peer postgres() outside withTenantDb, parameterized SET LOCAL
  - Wire contracts: DATABASE_URL format, withTenantDb return shape, health endpoint response JSON

Non-Negotiable ACs: 5 (tenant isolation, RLS force, migration-cli-only, health 503 on disconnect, env validation fail-fast)
Blocking: no ACs: 37

Preview diff? (y/N)  Ship? Or revise <section>?
```

Accept user responses:
- **"ship"** → write the spec (Step 5)
- **"diff"** / **"y"** → show the full unified diff; then re-prompt
- **"revise <section>"** → reopen that section (e.g., "revise forbidden patterns") and iterate ONLY on that section
- **"start over"** → ditch the draft and re-interview (fall back to `/df-write-spec` interactive)

Default to ship; do not require confirmation to write if the user accepts the summary.

### Step 5: Write spec + suggest validate

1. Write the densified spec to `.dark-factory/specs/<spec-name>.md`, preserving the HTML-comment metadata block verbatim.
2. Run a quick self-check (mental validation):
   - AC count in target range (30–50+)
   - Every FR has ≥3 ACs
   - Every shape-bearing claim has a Wire Contract
   - Non-Negotiable Acceptance Criteria section is populated (or has a Justification)
   - Metadata block intact
   - **§4a Negative Space, Invariants, and Lifecycle is present and non-empty** (at least one of `forbidden_commands` / `forbidden_test_shapes` / `ecosystem_invariants` / `required_lifecycle_sequences` has ≥1 entry), OR the section carries a `Justification:` paragraph
   - When the spec exposes HTTP endpoints (Step 3.5 Source 5 detection), §5b includes a `test_server` block with `command` and `base_url`
3. Suggest next step: `dark-factory validate <spec-name>` — the densified spec must still pass the validator.

## Density targets + quality bar

| Dimension | Minimum | Target |
|---|---|---|
| Total tagged requirements (FR + AC + EC + NFR) | 30 | 40–50 |
| ACs per FR | 3 | 4–5 |
| Wire Contracts | 1 per FR with a shape claim | every shape-bearing claim |
| Non-Negotiable Acceptance Criteria | ≥1 OR documented Justification | every security / data-integrity AC |
| Forbidden-pattern ACs | ≥1 per known lint rule in the active adapter | every known foot-gun addressed |
| Tool names with pinned versions | 100% of adopted tools | all in Tool Adoption section |

A densified spec under 30 ACs is a failure — stop and reopen the draft.

## Ecosystem expansions — Postgres + drizzle-kit + RLS (concrete example)

When the spec targets a project with `active_adapters = ["typescript-node", "postgres"]` and the problem domain involves tenant-scoped data, expand as follows:

**Tool Adoption section (required):**
- `drizzle-orm` pinned `^0.44` (ORM; `drizzle.config.ts` at workspace root)
- `drizzle-kit` pinned `^0.31` (migration tool; `drizzle-kit migrate` runs as `commands.setup`)
- `postgres` (postgres.js) pinned `^3.4` (driver; connection pool)
- Postgres service at `postgres:16` image

**Migration discipline ACs:**
- AC-N: Migrations are CLI-only via `pnpm db:migrate` — never auto-applied on server startup
- AC-N+1: First migration run seeds `apps/api/src/db/migrations/meta/_journal.json` with shape `{"version":"7","dialect":"postgresql","entries":[]}` (drizzle-kit ≥0.20 convention)
- AC-N+2: Baseline migration `0000_<name>.sql` creates tenant-scoped tables with RLS enabled

**Tenant binding ACs:**
- AC-N: `withTenantDb(tenantId: string, cb: (tx: PgTransaction) => Promise<R>): Promise<R>` is the ONLY public entry point for tenant-scoped database access
- AC-N+1: `withTenantDb` wraps `cb` in a transaction and issues `SET LOCAL app.tenant_id = <tenantId>` on the first statement, then commits if `cb` resolves or rolls back if it rejects
- AC-N+2: `tenantId` MUST be UUID-validated before any SQL is issued; malformed UUIDs reject with `INVALID_TENANT_ID` and never reach the database
- AC-N+3: `current_setting('app.tenant_id', true)` inside the `cb` returns the same `tenantId` that was passed (with `missing_ok=true` to return NULL on unbound access, not raise)

**RLS invariants (Non-Negotiable):**
- AC-N (blocking): Every tenant-scoped table has `rowsecurity = true` in `pg_tables` after migrations
- AC-N+1 (blocking): Every tenant-scoped table has `relforcerowsecurity = true` in `pg_class` (force RLS; owner does NOT bypass)
- AC-N+2 (blocking): Every tenant-scoped table has ≥1 policy in `pg_policies` using `current_setting('app.tenant_id')::uuid` in the `USING` clause
- AC-N+3 (blocking): A query with no `app.tenant_id` set returns zero rows from every tenant-scoped table (not all rows, not an error — zero)

**Forbidden-pattern ACs (structural exclusion):**
- AC-N: Tests MUST NOT open a raw `postgres()` client outside the `withTenantDb` callback and expect it to read `app.tenant_id` — peer clients are unbound (the GUC is transaction-scoped). Addresses `peer_client_guc_read`.
- AC-N+1: Tests MUST NOT use parameterized interpolation inside `SET LOCAL` (e.g., `tx\`SET LOCAL app.tenant_id = ${value}\``) — Postgres does not substitute parameters inside `SET`. Use `tx\`SELECT set_config('app.tenant_id', ${value}, true)\`` instead. Addresses `parameterized_set_local`.
- AC-N+2: Tests MUST NOT assume owner-privileged queries bypass RLS — `FORCE ROW LEVEL SECURITY` is declared above, owner bypass is disabled. Addresses `owner_bypass_missing_force`.
- AC-N+3: Tests MUST NOT read GUC via permissive paths that treat NULL or empty string as "the current tenant" — always pass an explicit tenantId. Addresses `strict_null_empty_guc_check`.

**Wire Contracts (required when named in ACs):**
- `DATABASE_URL` format: `postgresql://<user>:<pass>@<host>:<port>/<db>?sslmode=<mode>`
- `withTenantDb` return shape: resolved value is the callback's return; rejected error re-raises (preserving stack)
- Health endpoint response: `200 {"status":"ok","db":"connected"}` / `503 {"status":"fail","db":"disconnected"}`

**Env validation:**
- AC-N: `DATABASE_URL` MUST be present and parseable at startup. Missing or malformed → `process.exit(1)` with a stderr line `ERROR: DATABASE_URL is required and must be a valid postgres URL`. No fallback to localhost, no default port.

**Substrate manifest (if the template supports it):**
- A `## Substrate Primitives` section with TOML fenced block listing all GUCs (`app.tenant_id`) and RLS tables (with `rowsecurity_check`, `policy_count_check`, `force_check`). See the postgres adapter's architect_addendum for the schema.

For adapters OTHER than postgres / for domains that aren't tenant-scoped, produce a comparable density using the active adapter's addendum as the source of tool-specific knowledge. The template above is illustrative, not prescriptive.

## Coverage dimensions per FR (general rubric)

For each FR, ensure ACs cover:

| Dimension | Example |
|---|---|
| Happy path | "Given valid input X, when Y is called, then Z returns with shape {...}" |
| Boundary | "Given input at boundary (empty, max, zero, negative), handle <specific> way" |
| Error — input validation | "Given invalid X, reject before side effects, return error code <code>" |
| Error — dependency failure | "Given the DB is unreachable, return 503 with <body>, do not retry" |
| Concurrency | "Given 2 concurrent writes with same key, <one wins / both queued / specific behavior>" |
| Observability | "On <event>, log line matches <pattern> with fields <list>" |
| Invariant | "After any sequence of operations, <invariant> holds (MUST never violate)" |

Not every FR needs all 7 dimensions — use judgement. But a FR with only 1 AC (happy path) is under-densified.

## Completeness checklist (mental validation before writing)

- Metadata block intact: `feature-id`, `depends-on`, `plan`, `spec_type` preserved verbatim
- Total tagged reqs ≥ 30
- Every FR has ≥ 3 ACs
- Every shape-bearing claim has a Wire Contract
- Tool Adoption section present with pinned versions
- Non-Negotiable Acceptance Criteria populated (or has Justification paragraph)
- Forbidden-pattern ACs present for every known lint rule in active adapters
- No weasel words (fast, secure, robust — replace with measurable bounds)
- `spec_type` matches the pattern (bootstrap vs feature vs bugfix vs refactor)

## Output

One densified specification file at `.dark-factory/specs/<spec-name>.md`, ready for `dark-factory validate`. Report the before/after density counts and the major dimensions expanded. Suggest the validate command as the next step.
