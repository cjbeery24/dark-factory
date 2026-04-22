# Consensus Plan — Pipeline Fixes for 2026-03-28-database-foundation Class of Failures

Source: agreed in the 2026-04-22 session following the `358d34f2c35c4b78`
ESCALATED run (archive `4736bbd9`). Implementation order: **45 → 42 → 40 → 43 → 44 → 41**.
Task A (adapter contribution hooks) dropped as YAGNI.

Each task is reviewed adversarially by the Linus agent after implementation
per the durable session instruction.

## Task 45 — Setup-phase timeout + SETUP_HANG status  *(shipped)*

**Fixes:** iter-1 failure where `drizzle-kit migrate` hung with truncated
output, producing no actionable diagnostic.

**How:** In `scripts/quality-gate.sh` setup step, wrap setup command in
`timeout <N>`. On SIGTERM, capture process tree + (for postgres adapter)
`pg_stat_activity` / `pg_locks` snapshot before killing. Emit distinct
`SETUP_HANG` gate status (not `SETUP_FAIL`). Classifier treats
`SETUP_HANG` as `ENV_ISSUE` — never `IMPL_BUG`.

**Why not another way:** without status distinction, hangs cascade into
`IMPL_BUG` feedback to the next implementer, who has nothing to fix.

## Task 42 — Manifest-lint rejects TS type-only exports  *(shipped)*

**Fixes:** iter-3 Import Contract permanent failure on `DrizzleTx` type alias.

**How:** architect-addendum rule: "Do not list `export type` / `export interface` /
`export type =` aliases in `entry_points[].exports[]`." Precheck uses regex
classifier (`darkfactory/adapters/typescript/type_only_exports.py`) to
classify declarations; reject manifests whose `exports[]` names resolve to
type-only declarations. No scratch `tsc` invocation. Runs before the
vitest-based Import Contract gate.

**Why not broader:** Neo's earlier `{name, kind}` schema change rejected as
over-engineering. Classifier at precheck is sufficient.

**Post-Linus hardening:** 2-hop re-export resolution closes the canonical
monorepo barrel bypass (`index.ts → ./types → ../shared/types`). Resolver
contained to `project_root` to prevent architect-controlled specifiers
from reading arbitrary host-FS paths. `.d.ts` + `export declare` handled.

## Task 40 — Barrier-safe adapter-emitted classification hints  *(next)*

**Fixes:** defect-classifier labeling every scenario failure as `IMPL_BUG`
despite peer-connection / owner-RLS patterns being structurally detectable.

**How:** adapter exposes `classification_hints: list[{pattern: regex,
category_override, internal_reason_code}]`. Classifier applies hints to
match scenario file contents. Implementer-visible rationale in
`<spec>-feedback-iterN.md` uses spec-anchor vocabulary only (FR/AC/EC
IDs + behavioral category). `internal_reason_code` goes to `defects.json`
exclusively — that file is implementer-masked. No new `PIPELINE_BUG`
category — reuse `ENV_ISSUE` (setup/infra) and `SPEC_AMBIGUOUS` (mismatch
between spec and test expectations).

**Why not free-text rationale:** risks leaking scenario mechanics across
the barrier.

## Task 43 — Layered RLS enforcement (template primary, Docker fallback optional)

**Fixes:** owner-bypass-RLS in `substrate-rls.test.ts`.

**How:**
1. **Primary (always-on, mechanical):** `scenario_architect_addendum.md` +
   `substrate-rules.toml` gain `[[rls_table]] force = true`. Manifest-lint
   greps target source for `FORCE ROW LEVEL SECURITY` on each such table.
2. **Optional fallback:** if `config.toml [postgres] docker_managed = true`,
   spin a `NOBYPASSRLS` role in the test container and expose
   `DATABASE_URL_APP`. Scenarios use `DATABASE_URL_APP`, not owner
   `DATABASE_URL`.

**Why layered:** template rule covers all projects, including user-managed
Postgres where we cannot create roles. Docker role provides stronger
isolation when we own the container.

## Task 44 — `assertTxGucEquals` scenario helper

**Fixes:** peer-client assertions on tx-local GUCs in `tenant-context.test.ts`.

**How:** add `assertTxGucEquals(tx, gucName, expected)` to
`_contracts/postgres.ts`. Addendum rule: "Tx-local GUC assertions must
use the helper; module-level `postgres(...)` connections may not assert
on tx-scoped state." Manifest-lint enforces.

## Task 41 — Tighten `[[rls_table]]` source verification (narrow scope)

**Fixes:** architect strips `verifiable = false` from spec TOML; substrate
demands tables the spec excludes.

**How:** preserve the `verifiable` field end-to-end;
`PostgresAdapter._check_rls_tables` skips unverifiable entries. Defer
`[[rls_pattern]]` split until a real spec needs the distinction (YAGNI —
current failure mode is the stripped field, not the data model).

---

## Barrier audit (invariant across all tasks)

Implementer-visible outputs (`.dark-factory/reviews/<spec>-feedback-iterN.md`,
`.dark-factory/scenarios/`, reviews in general) carry **spec-anchor
vocabulary only** — FR/AC/EC IDs and behavioral category names. Internal
reason codes, test-file pattern matches, and adapter hint regexes stay
in implementer-masked paths (`defects.json`, `audit/`, `reports/`).

---

## Follow-ups (filed during Task 41 Linus pass 1, non-blocking)

- **INFO-severity observability finding for `verifiable=false` skips.**
  Linus pass 1 recommended emitting `substrate-rls-skipped-unverifiable`
  at `severity="info"` so reviewers reading the gate report see *which*
  tables were skipped without cross-referencing the manifest. Deferred
  because `darkfactory/pipeline/substrate_verifier_entry.py:155` gates
  `status="pass"` on an empty findings list; any finding (regardless of
  severity) routes through `status="fail"`. Needs a coordinated change
  to gate status on `severity` membership (`error`-only triggers
  `"fail"`; `warning`/`info` pass through without breaking existing
  adapters). Filing here rather than in-task because the scope crosses
  substrate-phase plumbing.

- **Apply Task 41 three-state gate to `[[guc]]` entries.** Same truthy-
  coercion trap exists on GUC entries; `verifiable` / non-bool handling
  lives only on `[[rls_table]]` today (YAGNI per the consensus plan).
  When a spec actually needs `[[guc]] verifiable = false`, lift the
  same three-state pattern.

- **Manifest-lint pass for stripped fields.** Task 41 relies on the
  architect addendum (prose instruction) to preserve `verifiable` end-
  to-end. If a pipeline run surfaces an architect that silently drops
  the field anyway, add a mechanical diff of spec substrate-block ↔
  emitted manifest `[[rls_table]]` entries. ~20 lines. Don't add
  preemptively — wait for a real failure to motivate.

- **Task 43: architect-side manifest validator for earlier
  `force_check` signal** (tracked separately during Task 43 pass 1).

- **Task 43: Docker-managed NOBYPASSRLS role fallback** — when the
  framework owns the Postgres container, spin a `NOBYPASSRLS` test
  role and expose `DATABASE_URL_APP`. Template-primary path shipped in
  Task 43; this is the opt-in second layer.

- **Task 43: PG version gating for `relforcerowsecurity`** — low
  priority; improves diagnostic quality on pre-9.5 sidecars (which the
  framework probably never supports, but the column availability is
  version-gated per Postgres docs).

- **Task 44: classification-hint anchor in `expected === ''` guard
  error** — Linus pass 2 recommended naming a new hint
  (`POSTGRES_EMPTY_STRING_GUC_ASSERTION`) in the runtime error so the
  defect-classifier and reviewer have a grep target. Lives in
  `darkfactory/adapters/postgres/classification_hints.py` when lifted.
