# B9e — Adapter layering audit (mechanism-tokens, substrate-rules, agent addenda)

**Date:** 2026-04-25
**Phase / item:** ROADMAP.md Phase 3 B9e
**Branch:** `experiment/phase-4-product-owner`
**Author:** Claude (assistant)
**Reviewers requested:** Linus (kernel-quality) — initial round complete,
classification corrections folded in (§1, §3a)
**Reference:** B9a v(N) classification framing —
`templates/contracts/postgres/project-starters/README.md`

## Purpose

Phase 3 B9d landed the project-side staging path. B9a v(N) migrated
three postgres convention helpers to that path as starter templates.
B9e completes the Phase-3 layering audit by classifying the remaining
adapter content surfaces:

1. `darkfactory/adapters/postgres/mechanism-tokens.toml`
2. `darkfactory/adapters/postgres/substrate-rules.toml`
3. Per-adapter agent addenda:
   - `darkfactory/adapters/postgres/architect_addendum.md`
   - `darkfactory/adapters/postgres/scenario_architect_addendum.md`
   - `darkfactory/adapters/postgres/spec_validator_addendum.md`
   - `darkfactory/adapters/typescript/architect_addendum.md`
   - `darkfactory/adapters/python_fastapi/architect_addendum.md`

## Classification framing (per B9a)

- **UNIVERSAL** — encodes library-wire-level truth or pure mechanism
  that holds for any project using this ecosystem. Stays in framework.
- **CONVENTION** — references project-specific names (column names,
  GUC namespaces, app-callback shapes, etc.). Migration target:
  framework live copy gets `DEPRECATION NOTICE`; v(N+1) deletes; the
  project-side staging path becomes the authoritative source.

A regex / heuristic that hardcodes a *de-facto community convention*
(e.g., the `app.*` Postgres GUC namespace) but still produces correct
behavior for the canonical case — and has defense-in-depth elsewhere
when the convention misses — is **UNIVERSAL with accepted false-
negative**, not CONVENTION. The distinction matters because CONVENTION
implies "framework removes this" at v(N+1); UNIVERSAL-with-accepted-
false-negative implies "framework keeps this with a documented
limitation, fixable in a follow-up if a real failure case shows up."

## Audience analysis

Per AUTHORIZATION-MODEL.md trigger #1 (audience escalation): every
content surface in this audit is *already* consumed by the same
agent(s) it would be in any post-migration form. B9e changes
*who authors* the content (framework maintainer → project owner for
the convention slice), not *who reads* it. No promotion from a
humans/Reviewer/Oracle audience to a barrier-restricted-agent
audience.

Trigger #2 (changed-surfaces declaration): B9e does not touch
`darkfactory/docker/barriers.py`, `scripts/quality-gate.sh` report
rows, `scripts/lib/scenario_lint.py`, or any new artifact under
`.dark-factory/`. Trigger #2 does not fire. Per-surface audience is
recorded below for completeness and reproduced in the commit body.

| Surface | Consumer agent(s) | Container barrier |
|---------|-------------------|-------------------|
| `mechanism-tokens.toml` | Spec Validator (host-side regex precheck before LLM call) | Loaded host-side; findings + spec text are what the agent prompt sees, not the TOML itself |
| `substrate-rules.toml` | Spec Validator (rendered into prompt as taxonomy table); host-side `triggered_rules` consumer; gate-time `verify_substrate` | Spec Validator: `:ro` project mount, masks SOURCE_DIRS+`.git` |
| `postgres/architect_addendum.md` | Architect | `:ro` project mount; masks scenarios/audit/state/failures/`.git` |
| `postgres/scenario_architect_addendum.md` | Test Engineer (Scenario Architect) | `:ro` project mount; masks SOURCE_DIRS+`.git` |
| `postgres/spec_validator_addendum.md` | Spec Validator | as above |
| `typescript/architect_addendum.md` | Architect | as Architect above |
| `python_fastapi/architect_addendum.md` | Architect | as Architect above |

---

## File-by-file classification

### 1. `mechanism-tokens.toml` (14 entries)

This file is a pre-LLM regex blocklist that catches mechanism-leaked
spec text (`SET LOCAL`, `current_setting(`, `pg_policies`, etc.).
Loaded host-side; feeds findings to the spec validator.

| `#` | `name` | Pattern (verbatim) | Class | One-line reason |
|----:|--------|--------------------|-------|------------------|
| 1 | `set_local` | `\bSET\s+LOCAL\b` | UNIVERSAL | Postgres SQL keyword. |
| 2 | `set_session` | `\bSET\s+SESSION\b` | UNIVERSAL | Postgres SQL keyword. |
| 3 | `current_setting_call` | `\bcurrent_setting\s*\(` | UNIVERSAL | Postgres built-in function name. |
| 4 | `set_config_call` | `\bset_config\s*\(` | UNIVERSAL | Postgres built-in function name. |
| 5 | `row_level_security_statement` | `\b(?:ENABLE\|DISABLE\|FORCE\|NO\s+FORCE)\s+ROW\s+LEVEL\s+SECURITY\b` | UNIVERSAL | Postgres SQL keyword(s). |
| 6 | `create_policy_stmt` | `\bCREATE\s+POLICY\b` | UNIVERSAL | Postgres SQL keyword. |
| 7 | `alter_system_stmt` | `\bALTER\s+SYSTEM\b` | UNIVERSAL | Postgres SQL keyword. |
| 8 | `app_guc_identifier` | `\bapp\.[a-z_][a-z0-9_]*_[a-z0-9_]+\b` | **UNIVERSAL with accepted false-negative** | The rule's purpose (block GUC-namespace mechanism leakage) is universal; the regex hardcodes the canonical Postgres community namespace (`app.*`). Non-canonical namespaces (`myapp.*`, `tenancy.*`) miss this rule but still hit defense-in-depth at scenario_lint and the substrate-rule taxonomy. |
| 9 | `guc_term` | `\bGUC\b` | UNIVERSAL | Postgres-internal terminology. |
| 10 | `pg_catalog_reference` | `\bpg_(?:policies\|class\|roles\|user\|tables\|attribute\|indexes\|namespace\|database\|stat_activity)\b` | UNIVERSAL | Postgres internal catalog/system view names. |
| 11 | `postgres_js_library` | `\bpostgres\.js\b\|\bpostgres-js\b` | UNIVERSAL | Library-wire knowledge for the postgres-stack adapter. |
| 12 | `drizzle_library` | `\bdrizzle(?:-orm\|-kit)?\b` | UNIVERSAL | Library-wire knowledge for the postgres-stack adapter. |
| 13 | `tx_local_scope` | `\btx[-_]local\b\|\btransaction[-_]local\b` | UNIVERSAL | Generic Postgres transaction-scoping terminology. |
| 14 | `bypassrls_privilege` | `\bBYPASSRLS\b` | UNIVERSAL | Postgres role-attribute keyword. |

**Result:** 14/14 UNIVERSAL.

**Action:** add an inline `# B9e:` annotation above the
`app_guc_identifier` `[[token]]` declaring the `app.*` namespace
hardcoding as an *accepted false-negative* (a documented design
limit, not a deferred TODO). Cross-reference this audit doc.

### 2. `substrate-rules.toml` (3 rules)

Substrate-primitive taxonomy: triggers (regex patterns scanned against
spec text) + required-primitives (declarations the spec author must
include in a `## Substrate Primitives` block when the trigger fires).
Renders into the spec validator addendum at composition time.

| `trigger` | `match_patterns` (verbatim) | Class | One-line reason |
|-----------|-----------------------------|-------|------------------|
| `custom_guc` | `SET LOCAL app\.[a-z_]+`, `current_setting\('app\.` | **UNIVERSAL with accepted false-negative** | The rule's purpose (require structured GUC declarations when the spec depends on a custom GUC) is universal; the match_patterns hardcode the `app.*` canonical namespace, same accepted-false-negative as #8 above. |
| `rls_policy` | `ENABLE ROW LEVEL SECURITY`, `CREATE POLICY` | UNIVERSAL | Pure Postgres SQL keywords. |
| `tenant_isolation` | `tenant.*isolat`, `multi[- ]tenant` | UNIVERSAL | Concept-level terms describing the access-control invariant. |

**Result:** 3/3 UNIVERSAL.

**Action:** add an inline `# B9e:` annotation above the `custom_guc`
rule's `match_patterns` mirroring the mechanism-tokens annotation.

### 3. Per-adapter agent addenda

#### 3a. `darkfactory/adapters/postgres/architect_addendum.md`

Three sections: **Database schema setup** (universal — declares
`commands.setup` for Drizzle/Prisma/Knex/Alembic/Django migrations),
**Migrate-mode vs push-mode** (universal — meta-guidance on
project-policy arbitration via `project-hints.toml`), **Substrate
manifest emission**.

The substrate-manifest section's *prose* is universal (TOML-to-TOML
transform, copy-verbatim discipline, mandatory-fields list). The
*worked example payload* (lines 92-105 of the current file) hardcodes
three concrete project-convention names:

```toml
spec_id = "2026-03-28-database-foundation"
[[guc]] name = "app.tenant_id"
[[rls_table]] name = "users"
expected_policies = ["users_tenant_isolation"]
```

`2026-03-28-database-foundation` is the budget-brain-v2 spec id;
`app.tenant_id`, `users`, and `users_tenant_isolation` are
budget-brain-v2's specific GUC namespace, tenant table, and policy
name. The Architect agent (LLM) reads this prompt fragment when
emitting a manifest. LLMs pattern-match concrete names in worked
examples — a spec for `myapp.org_id` against a `workspaces` table
will, on the margin, drift toward the example's names. This is
the same LLM-bias failure mode B9a's contract-helper migration
exists to prevent.

**Result:** structurally UNIVERSAL; example payload **CONVENTION**.

**Action:** rewrite the example payload's project-specific values as
explicit angle-bracket placeholders (`<spec-filename-stem>`,
`<your_guc_name>`, `<your_table>`, `<your_policy_name>`). Line-count-
neutral so the 80/80 governance cap holds. The structural fields
(`name`, `default`, `registration_check`, etc.) and the
`postgres_version = "15"` field stay concrete because they're
universal across postgres-stack projects. (Initial audit pass
classified this file UNIVERSAL outright — Linus review correctly
flagged the example payload. Corrected here.)

#### 3b. `darkfactory/adapters/postgres/scenario_architect_addendum.md`

Five sections. The first four (substrate-aware overview, how to write
substrate scenarios, tenant-isolation positive/negative pair, "runs
against gate sidecar not a mock") are UNIVERSAL — substrate-scenario
discipline at the SQL/wire layer.

The fifth section, **Wire-contract assertion helpers**, has a
bulleted helper-list that names six helpers without flagging the
B9a v(N) split. Three of them (`assertAppSetsTxGuc`,
`assertTenantSeesOwnRows`, `assertTenantCannotSeeOtherRows`) are
project-side conventions; the other three are framework-side
universal helpers.

The example import block stays unified at v(N) because framework's
`darkfactory/adapters/postgres/contracts/ts/index.ts` re-exports
all six. At v(N+1), framework deletes the convention re-exports and
the import block must split into framework + project paths.

**Result:** prose UNIVERSAL; helper-list bullets **MIGRATE in v(N)**
by annotating the three convention helpers. Import block stays
unified in v(N) (no path change yet); split lands at v(N+1) together
with framework deletion.

**Action:** annotate the three convention helpers with `*(project)*`
markers in the existing bulleted list and append a one-line note
pointing at the B9a starter dir. Stays under the 80-line governance
cap (current rendered count: 78/80; budget: 2 lines).

#### 3c. `darkfactory/adapters/postgres/spec_validator_addendum.md`

Three sections: **Postgres substrate-primitive completeness**,
**Wire-accurate predicate language for library-mediated surfaces**,
**Substrate rule taxonomy** (rendered from `substrate-rules.toml`).

The taxonomy section's content depends on `substrate-rules.toml` —
the corrections in §2 propagate here automatically (the rule's
inline `# B9e:` comment is rendered into the addendum's taxonomy
table; comments are stripped by the loader, so the visible prompt
text doesn't change, but the inline rationale lives in the source
file for maintainers).

**Result:** UNIVERSAL prose. No edit needed.

#### 3d. `darkfactory/adapters/typescript/architect_addendum.md`

TypeScript/Node ecosystem mechanics — pnpm/npm/yarn package managers,
tsconfig.json, ESM/CJS module systems, monorepo detection signals.
Examples reference common monorepo paths (`apps/api/src/server.ts`).

**Result:** UNIVERSAL. No edit.

#### 3e. `darkfactory/adapters/python_fastapi/architect_addendum.md`

Generic Python/FastAPI scaffolding — uv/pip/pyproject/pytest/mypy/
pyright, dotted-module import paths.

**Result:** UNIVERSAL. No edit. (Stub adapter; revisit when a real
Python FastAPI spec runs end-to-end.)

---

## Migration plan (this commit)

1. `darkfactory/adapters/postgres/mechanism-tokens.toml` — `# B9e:`
   inline annotation above the `app_guc_identifier` `[[token]]`,
   naming the `app.*` namespace hardcoding as an accepted
   false-negative (UNIVERSAL, not deferred CONVENTION).
2. `darkfactory/adapters/postgres/substrate-rules.toml` — same on
   the `custom_guc` rule's `match_patterns`.
3. `darkfactory/adapters/postgres/architect_addendum.md` — replace
   project-convention values in the worked example with angle-bracket
   placeholders. Line-count-neutral.
4. `darkfactory/adapters/postgres/scenario_architect_addendum.md` —
   `*(project)*` annotation on three convention helpers + one-line
   note pointing at the B9a starter dir. Within the 80-line cap.
5. **No edits** to: `postgres/spec_validator_addendum.md`,
   `typescript/architect_addendum.md`,
   `python_fastapi/architect_addendum.md` — UNIVERSAL.

## Tests added (this commit)

- `tests/unit/test_b9e_layering_audit.py`:
  - `test_mechanism_tokens_app_guc_documents_accepted_false_negative` —
    pin the inline `B9e:` annotation on the `app_guc_identifier`
    `[[token]]` block; if a future maintainer "tidies" the comment
    away, the test fails before merge.
  - `test_substrate_rules_custom_guc_documents_accepted_false_negative` —
    same on the `custom_guc` rule.
  - `test_scenario_addendum_marks_project_helpers` — pin the
    `*(project)*` markers on the three convention helpers in the
    rendered addendum text.
  - `test_architect_addendum_example_uses_placeholders` — pin that
    the substrate-manifest worked example uses placeholder
    angle-bracket names rather than `app.tenant_id` / `users` /
    `users_tenant_isolation` / `2026-03-28-database-foundation`.

The contract harness's existing
`test_scenario_architect_addendum_within_governance_line_cap` and
`test_architect_addendum_within_governance_line_cap` tests cover the
governance-cap regression backstop already; B9e adds no new cap test.

## Follow-up work (real, not deferred-as-shelf)

- **v(N+1) `scenario_architect_addendum.md` import-block split.**
  When B9a v(N+1) ships (framework deletes the three convention
  helpers + their re-export from `contracts/ts/index.ts`), update
  the addendum's import-example block from unified
  (`'./_contracts/postgres'`) to split (`'./_contracts/postgres'` +
  `'./_contracts/postgres/project'`). Gates on B9a v(N+1).

(No mechanism-tokens / substrate-rules overlay items here — those
were classified UNIVERSAL with accepted false-negative, not
deferred CONVENTION. If a project using a non-canonical GUC
namespace (`myapp.*`, `tenancy.*`) hits a real failure case driven
by the false-negative, that's the prompt to build an overlay
mechanism — driven by failure signal, not audit cosmetics.)

## Exit criteria — B9e

- [x] Audit doc lists every entry in each surveyed file with
      classification + one-line reason.
- [x] Each CONVENTION entry has a v(N) action shipped in this commit
      (architect example placeholders, scenario addendum annotation).
- [x] Each UNIVERSAL-with-accepted-false-negative entry has an inline
      `# B9e:` source annotation.
- [x] Tests pin the markers.
- [x] All existing tests still pass + new B9e tests.
- [x] ROADMAP/PLAN updated with B9e DONE status.

## What this commit does NOT do

- Does **not** touch `darkfactory/docker/barriers.py`,
  `scripts/quality-gate.sh` report rows,
  `scripts/lib/scenario_lint.py`, or any new artifact under
  `.dark-factory/` (AUTHORIZATION-MODEL.md trigger #2 inert).
- Does **not** add or remove any audit row in iteration feedback,
  scenario lint output, or gate report (no Implementer-readable
  surface change).
- Does **not** change framework live-helper file paths or contents
  (B9a v(N) already shipped the live copies + DEPRECATION NOTICE
  comments; no double-edit).
- Does **not** ship a project-side TOML overlay mechanism for
  mechanism-tokens / substrate-rules (those are UNIVERSAL with
  accepted false-negative, not deferred CONVENTION; overlay work
  becomes appropriate only when a real non-canonical-namespace
  failure case shows up).
