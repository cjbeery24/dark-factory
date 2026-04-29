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
tenant-scoped database access. **Three of the four regexes below are
byte-identical to** the canonical patterns at
`darkfactory.contracts.foot_gun_patterns.PATTERNS` (B5 single source
of truth). The fourth (`owner-bypass-missing-force`) uses a
gate-friendly negative-lookahead form because the spec's regex runs
under `scenario_lint` at gate time without any post-match consumer
logic — the B5-library variant matches `ENABLE ROW LEVEL SECURITY`
and relies on a consumer-side absence-of-FORCE check that
scenario_lint doesn't perform. Names in `reason:` strings use
kebab-case to match the library's join keys + scenario_lint emit-rule
names.

**`canonical_violation:` is REQUIRED on every entry — Phase 4 B4
fatal-on-mismatch.** B4's round-trip validator runs each pattern
against its declared `canonical_violation` snippet at precheck time;
if the regex doesn't match its own canonical violation (the H2
failure mode: spec author writes a regex against the wrong dialect),
the spec is rejected before scenarios run. `canonical_clean:` is
optional but recommended — pins the negative direction (regex must
NOT match the clean snippet, false-positive defense).

The block below is self-pinned: any drift between the regex and its
canonical snippets fails CI. Spec authors who copy this block
verbatim get B4-valid §4a entries by default.

<!-- B6.5-FORBIDDEN-TEST-SHAPES-EXAMPLE -->

```yaml
forbidden_test_shapes:
  - pattern: 'postgres\s*\(\s*process\.env\.[A-Z_][A-Z0-9_]*[\s\S]*?current_setting\s*\(\s*[''"`]app\.'
    reason: "peer-client-guc-read: a second postgres() client outside withTenantDb cannot read app.* GUCs (transaction-scoped). Use assertAppSetsTxGuc helper instead."
    applies_to: [scenarios, holdouts]
    canonical_violation: |
      const peer = postgres(process.env.DATABASE_URL!);
      const rows = await peer`SELECT current_setting('app.tenant_id', true) AS v`;
      expect(rows[0]?.v).toBe('acme');
    canonical_clean: |
      await client.begin(async (tx) => {
        await tx`SELECT set_config('app.tenant_id', ${tenantId}, true)`;
        const rows = await tx`SELECT current_setting('app.tenant_id', true) AS v`;
        expect(rows[0]?.v).toBe(tenantId);
      });
  - pattern: '(?i)\btx\s*`[^`]*?\bSET\s+LOCAL\b[^`]*?\$\{'
    reason: "parameterized-set-local: Postgres SET LOCAL cannot bind parameters; the value placeholder becomes $1 and the server rejects with 'could not determine data type of parameter $1'. Use SELECT set_config('app.x', ${val}, true) instead."
    applies_to: [scenarios, holdouts]
    canonical_violation: |
      await client.begin(async (tx) => {
        await tx`SET LOCAL app.tenant_id = '${tenantId}'`;
      });
    canonical_clean: |
      await client.begin(async (tx) => {
        await tx`SELECT set_config('app.tenant_id', ${tenantId}, true)`;
      });
  - pattern: '\bCREATE\s+POLICY\b(?![\s\S]{0,200}\bFORCE\s+ROW\s+LEVEL\s+SECURITY\b)'
    reason: "owner-bypass-missing-force: declaring CREATE POLICY without ALTER TABLE ... FORCE ROW LEVEL SECURITY allows the table owner (and superuser) to bypass every policy. Tests authored as the owner role pass trivially without the schema's intended isolation."
    applies_to: [scenarios, holdouts]
    canonical_violation: |
      ALTER TABLE users ENABLE ROW LEVEL SECURITY;
      CREATE POLICY tenant_isolation ON users
        FOR SELECT USING (tenant_id = current_setting('app.tenant_id')::uuid);
    canonical_clean: |
      ALTER TABLE users ENABLE ROW LEVEL SECURITY;
      CREATE POLICY tenant_isolation ON users
        FOR SELECT USING (tenant_id = current_setting('app.tenant_id')::uuid);
      ALTER TABLE users FORCE ROW LEVEL SECURITY;
  - pattern: 'current_setting\s*\(\s*[''"`]app\.[a-z_]+[''"`][^)]*\)[\s\S]{0,200}?(===|!==|toBe\()\s*(null|''''|"")'
    reason: "strict-null-empty-guc-check: TS-side strict ===/!==/.toBe() checks against current_setting('app.*') compare against null or '' literals — the postgres.js wire surface returns '' for an unset GUC at the SQL layer's NULL, making both forms ambiguous. Use assertUnsetGucIsNullish helper from the postgres adapter."
    applies_to: [scenarios, holdouts]
    canonical_violation: |
      const v = await client`SELECT current_setting('app.tenant_id', true) AS v`;
      expect(v[0]?.v).toBe(null);
    canonical_clean: |
      await assertUnsetGucIsNullish(client, 'app.tenant_id');
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

### Step 4.5: Cross-section consistency check (hard-stop on contradiction)

Before writing the densified spec to disk, walk the new ACs against
their parent FRs to catch contradictions the densification pass may
have introduced or amplified. This step exists because Phase 5's
discriminating run (`docs/pipeline-run-reports/2026-04-29-07-57-budget-brain-v2-database-foundation-shipped.md`)
shipped a spec where FR-3 prose said "creates no tables" while AC-24
required `__drizzle_migrations` records (which presuppose the table).
Neither the validator nor the densifier caught it.

**Calibration: liberal recall.** This step runs at authoring time
with the user at the keyboard. Surface anything suspicious; the user
can dismiss with one keystroke. False negatives cost much more than
false positives here — the contradiction will travel into the
pipeline and waste 60+ minutes of compute.

#### Procedure

For each AC the densifier emitted in Step 3:

1. Identify its parent FR.
2. Re-read the parent FR's `Processing` / `Side effects` / `Output`
   prose, plus every sibling AC of the same FR.
3. Reduce each load-bearing clause (parent FR prose AND each AC) to
   a claim tuple: `(subject, predicate, polarity, object, anchor)`.
   Compound clauses joined by AND/OR/COMMA split into separate
   tuples. See the typology SSOT below for the predicate vocabulary
   and discriminating slots.
4. For each (claim_a, claim_b) pair where the AC participates,
   apply the **refinement filter** first. For surviving pairs, fit
   the pair to one of the five contradiction types T1–T5. If a pair
   fits, emit a typed contradiction entry.

#### Output schema (REQUIRED)

Emit a fenced JSON block with this exact shape:

```json
{
  "step": "4.5",
  "spec_name": "<spec-name>",
  "checks_performed": <integer>,
  "contradictions": [
    {
      "ac_id": "AC-N",
      "ac_text": "<the AC's THEN clause verbatim>",
      "parent_fr": "FR-N",
      "conflicting_anchor": "FR-N.<field>@line<L> | AC-M | EC-K",
      "conflicting_text": "<the conflicting clause verbatim>",
      "type": "T1 | T2 | T3 | T4 | T5",
      "type_name": "entity_creation_polarity_mismatch | quantitative_bound_mismatch | lifecycle_ordering_inversion | behavioral_exclusivity_violation | wire_contract_shape_mismatch",
      "discriminator": "<one sentence naming the slot the two claims disagree on>",
      "refinement_check": "<one sentence stating why this is NOT a refinement>"
    }
  ],
  "advisory_only": [
    {
      "ac_id": "AC-N",
      "compared_against": "FR-M | AC-K",
      "note": "<one sentence — predicate family overlap, polarity uncertainty, etc.>"
    }
    // MAX 3 entries in this array — see "advisory_only cap" below.
    // If you have more than 3 ambiguous pairs, promote the strongest
    // to typed contradictions or drop the weaker ones.
  ]
}
```

**Hard-stop trigger:** `len(contradictions) > 0`. Each
`contradictions[*]` MUST have a non-empty `type` from `{T1..T5}` and
a non-empty `discriminator`. Entries without a typology slot are
malformed — drop them rather than emit them. This defends against
the LLM emitting "this seems off" without structure.

**advisory_only cap:** ≤3 entries (the schema comment above is the
load-bearing constraint — observe it). If more than 3 ambiguous
pairs arise, either (a) promote the strongest into typed
contradictions (forcing the typology decision) or (b) drop the
weaker ones. The cap exists because an unbounded advisory bucket
trains the user to ignore the surface.

#### User interaction on hard-stop

When `len(contradictions) > 0`, present the JSON block to the user
along with this prompt:

```
Step 4.5 detected <N> contradiction(s):
  AC-24 vs FR-3.processing@line62 (T1 entity_creation_polarity_mismatch)
    Discriminator: __drizzle_migrations table existence

Action:
  REVISE       (default — open the conflicting section and rework)
  SHIP_ANYWAY  (acknowledge and proceed; you take responsibility)
  START_OVER   (discard the densified draft and re-interview)
```

Default action is REVISE. The user typing nothing or hitting enter
selects REVISE — the densifier re-opens the conflicting section and
iterates ONLY on that section before re-running Step 4.5. SHIP_ANYWAY
proceeds to Step 5 with the contradiction acknowledged in the run's
state file. START_OVER falls back to `/df-write-spec`.

#### Persistence

When the user accepts SHIP_ANYWAY or no contradictions are detected,
write the JSON block to
`.dark-factory/reviews/<spec-name>-densify-step-4-5.json` so the
record survives. Do NOT write this file before user resolution — a
file present with `contradictions: []` is the auditable "PASS" case.

#### Typology SSOT (inlined verbatim from `darkfactory/contracts/contradiction_typology.md`)

<!-- BEGIN-CONTRADICTION-TYPOLOGY -->
## Contradiction Typology (SSOT)

A contradiction between two spec clauses is reported only when both
clauses can be reduced to a structured tuple AND the pair fits one of
five named types AND the **refinement filter** below does not apply.
Open-vocabulary "this seems contradictory" is banned. If you cannot
name the type and cite the discriminating slot, you may not flag it.

### Claim tuple shape

Each load-bearing prose clause is reduced to:

```
(subject, predicate, polarity, object, anchor)
```

- **subject** — noun phrase referring to a system entity (table, file,
  function, env-var, command, role, request, event).
- **predicate** — one verb from the closed vocabulary below.
- **polarity** — `positive` or `negative`. Negation cues:
  `no`, `none`, `zero`, `never`, `not`, `MUST NOT`, `cannot`,
  `excludes`, `forbidden`, `without`.
- **object** — noun phrase the predicate acts on.
- **anchor** — byte-addressable pointer back to the spec:
  `FR-N.processing@line<L>`, `FR-N.side_effects@line<L>`,
  `FR-N.output@line<L>`, `AC-N`, `EC-N`, or `NFR-N`.

### Closed predicate vocabulary

Map free-text verbs to one of these by semantic class. Add a synonym
note when the prose verb differs from the canonical:

| Canonical predicate | Synonyms in prose |
|---------------------|-------------------|
| `creates` | creates, generates, emits, instantiates, spawns, makes |
| `produces_record_in` | records, tracks, writes-row-to, registers, logs (when persistent) |
| `requires` | requires, needs, depends_on, must-have, expects |
| `forbids` | forbids, MUST NOT, prohibits, excludes, never |
| `reads` | reads, fetches, loads, retrieves, queries |
| `writes` | writes, persists, saves, stores, mutates |
| `returns` | returns, yields, resolves-with, responds-with |
| `rejects` | rejects, errors, throws, fails-with, refuses, denies |
| `runs_before` | runs-before, precedes, prerequisite-for, must-occur-before |
| `runs_after` | runs-after, follows, succeeds, must-occur-after |

Verbs outside this list either map by closest semantic class (note the
synonym in the tuple's `raw` field) or reduce to `requires` /
`forbids` for declarative state.

**Compound clauses joined by AND/OR/COMMA split into separate claims.**
Example — the prose `"creates no tables and applies no policies"`
yields TWO tuples:

1. `(baseline migration, creates, negative, tables, FR-3.processing@line62)`
2. `(baseline migration, applies, negative, policies, FR-3.processing@line62)`

### Five contradiction types

| # | Name | Trigger rule | Discriminator slot |
|---|------|--------------|---------------------|
| **T1** | **entity_creation_polarity_mismatch** | Both claims share the same `subject`. Both predicates come from the `{creates, produces_record_in, generates, emits, writes}` family. Polarities differ (one positive, one negative). The negative claim's `object` is a superset of, or names an entity that includes, the positive claim's `object`. | `object` (the entity whose existence the two claims disagree on) |
| **T2** | **quantitative_bound_mismatch** | Both claims bound the same metric on the same `subject`. The bounds (counts, ranges, thresholds) are disjoint sets. | the metric / bound |
| **T3** | **lifecycle_ordering_inversion** | Both claims assert a temporal/causal ordering between the same two events using `runs_before` / `runs_after` predicates. The asserted order is opposite. | the (event_a, event_b) ordered pair |
| **T4** | **behavioral_exclusivity_violation** | One claim has `predicate=forbids` or carries an "ONLY through X" / "MUST NOT" exclusivity. A second claim asserts an action that fits the forbidden shape on a NEW external entry point, not on the internals of the asserted entry point. | the action surface |
| **T5** | **wire_contract_shape_mismatch** | Both claims describe the same observable surface (return value, error envelope, exit code, log line, JSON shape, header set, status code mapping). The asserted shapes differ. | the surface name |

### Refinement filter (mandatory before any flag)

Before flagging a pair as contradiction, ask: **"Is one clause a strict
subset / specialization / enumeration-of-cases of the other?"**

Discriminating signal: **the more specific clause's claim is true under
the more general clause's claim.** If yes, refinement, not
contradiction. Drop the pair without flagging.

Refinement examples (must NOT trigger):

| General | Specific | Why it is a refinement |
|---------|----------|-------------------------|
| `"function is idempotent"` | `"first call creates row, second call returns existing row"` | Specific behavior is one valid implementation of the general property. |
| `"ENABLE ROW LEVEL SECURITY on every tenant table"` | `"FORCE ROW LEVEL SECURITY on every tenant table"` | Specific is a strictly stronger restatement; both must hold. |
| `"all queries go through the ORM"` | `"withTenantDb internally issues SET LOCAL via the ORM"` | Specific describes the asserted entry point's internals, not a new external entry point. (See T4 anti-pattern below.) |

**T4 anti-pattern (must NOT fire):** "ONLY through X" + a clause
describing X's internal mechanism does NOT violate exclusivity. T4
fires only when the second clause describes a NEW external entry
point that bypasses X.

### Worked example — FR-3 baseline migration case

Prose at `FR-3.processing@line62`:

> "The baseline migration itself creates no tables and applies no policies."

Prose at `FR-3.side_effects@line70`:

> "The `__drizzle_migrations` tracking table is created in the database when the baseline migration is applied."

AC-24:

> "GIVEN baseline applies WHEN db:migrate runs THEN `__drizzle_migrations` records the baseline's hash."

Reduced claims:

1. `(baseline migration, creates, negative, tables, FR-3.processing@line62)`
2. `(baseline migration, applies, negative, policies, FR-3.processing@line62)`
3. `(baseline migration, creates, positive, __drizzle_migrations table, FR-3.side_effects@line70)`
4. `(baseline migration, produces_record_in, positive, __drizzle_migrations, AC-24)`

Pairwise walk:

- (1, 3): same subject, predicate family `{creates}`, polarities differ
  (negative vs positive), object overlap (claim 1's "tables" is the
  superset; claim 3's `__drizzle_migrations table` is a member of that
  set). **Refinement filter**: claim 3 is NOT a refinement of claim 1
  — claim 3 asserts presence; claim 1 asserts absence. Specific
  contradicts general. **T1 fires.**
- (1, 4): subject same, predicates differ (`creates` vs
  `produces_record_in`); but `produces_record_in` requires the table
  to exist as a side effect of the same subject. Closure of the
  predicate family forces (1) to also deny (4)'s precondition.
  **T1 fires** (same discriminator: `__drizzle_migrations` table
  existence).

The pair (1, 3) is the canonical T1 fixture. The
discriminator slot is the `object` — the `__drizzle_migrations` table.
<!-- END-CONTRADICTION-TYPOLOGY -->

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
