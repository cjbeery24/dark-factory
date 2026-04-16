# Pipeline Run Report — 2026-03-28-database-foundation

**Target project:** `budget-brain-v2`
**Spec:** `2026-03-28-database-foundation`
**Run ID:** `977e570083114f7a`
**Spec hash:** `1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662`
**Launched:** 2026-04-16 18:00:13 UTC
**Terminated:** 2026-04-16 18:05:16 UTC
**Wall time:** ~5 min 3 s

## Outcome: INTERRUPTED at `_step_architecture_lock`

The pipeline aborted before any implementer, scenario-architect, gate, or oracle activity. Root cause: the architect emitted a manifest whose `commands.setup` used `drizzle-kit push` (auto-apply schema, no journal), but the spec §11 mandates `db:migrate` (CLI-driven migrations with migration journal). The `migrate-mode-vs-push-mode` consistency rule detected the drift during `_apply_manifest_to_environment` and raised `DarkFactoryError`.

State file:

```
spec=2026-03-28-database-foundation
run_id=977e570083114f7a
spec_hash=1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662
status=interrupted
iteration=1
completed_step=1
```

## Iteration summary

Only `iteration=1` started. Agents that ran:

| Agent | Wall seconds | Exit code | Notes |
|-------|-------------:|----------:|-------|
| spec-validator | 147.9 | 0 | Re-validation inside pipeline (standalone pre-flight validation by operator scored 92/100, divergence LOW) |
| architect | 154.1 | 0 | Produced manifest but introduced a migration-mode contradiction that the consistency rule rejected |

No scenarios generated. No implementer ever invoked. No gate ever ran.

## Consistency violation (verbatim)

From `/tmp/df-run-2026-03-28-database-foundation.log`:

```
WARNING: event=consistency_violation rule=migrate-mode-vs-push-mode env_path=commands.setup env_match='drizzle-kit push' spec_match='`db:migrate`'
ERROR: error: Architecture manifest contradicts spec constraints:
  - spec↔env contradiction [migrate-mode-vs-push-mode]: spec contains '`db:migrate`' but manifest sets commands.setup='cd apps/api && npx drizzle-kit push' which matches forbidden pattern 'drizzle-kit push'. Spec mandates CLI-driven migrations (db:migrate); env would run push-mode (drizzle-kit push / db:push) which auto-applies schema changes and bypasses the migration journal.
```

Manifest evidence (uncommitted; left on disk for post-mortem):

```yaml
# .dark-factory/architecture/2026-03-28-database-foundation-manifest.yaml
commands:
    setup: "cd apps/api && npx drizzle-kit push"
```

Architect's own summary confirms the intent:

> **Runtime:** Added setup command `cd apps/api && npx drizzle-kit push`

— from `.dark-factory/reports/2026-03-28-database-foundation-architecture.md`, FR-1 section.

## REMEDIATION_TARGET payload

Emitted by the orchestrator at the moment of failure (C1 remediation loop):

```json
{
  "event": "REMEDIATION_TARGET",
  "data": {
    "schema_version": 1,
    "failure_class": "consistency_violation",
    "rule_or_check": "migrate-mode-vs-push-mode",
    "remediation_type": "edit_env_contract",
    "remediation_target_path": ".dark-factory/architecture/2026-03-28-database-foundation-manifest.yaml",
    "auto_applicable": false,
    "suggested_diff": "[[policy.consistency_rules]] name=\"migrate-mode-vs-push-mode\" description=\"Spec mandates CLI-driven migrations ..."
  }
}
```

`auto_applicable: false` — operator confirmation required; the next step is `dark-factory remediate 977e570083114f7a` or `/df-write-hints from-failure 977e570083114f7a`.

## Move 3b verification — real-run evidence

This run is the first full-pipeline exercise of Move 3b (architect cache gate, three-axis stamp).

**1. Per-spec adapter activation (G1 backstop).** At t=0 s, before any agent started:

```json
{"event":"SPEC_ADAPTER_ACTIVATED","data":{"adapter":"postgres","reason":"spec_triggers"}}
```

Project-level `[detected].active_adapters` in `project-hints.toml` was `["typescript-node"]` at pipeline-start (postgres had been dropped by `dark-factory init --update` — see task #19). Postgres was nevertheless added for this run because the adapter's `spec_triggers(spec_text)` matched on the spec's RLS / `current_setting('app.tenant_id')` density. Exactly the scenario Move 3b's backstop event was designed to make auditable.

**2. Stamp-after-validate ordering.** The fresh architect manifest on disk has **no** `active_adapters` or `stamp_integrity` fields:

```bash
$ grep -E "active_adapters|stamp_integrity" .dark-factory/architecture/2026-03-28-database-foundation-manifest.yaml
# (no output)
```

This is correct behavior — `_stamp_active_adapters` is invoked *after* `_apply_manifest_to_environment` succeeds, and apply raised on the consistency violation. Matches the invariant pinned by `test_manifest_not_stamped_when_apply_fails`. A stamped manifest here would have meant a contradictory manifest was cached as "good" — that class of silent corruption is what Move 3b prevents.

**3. Next-run recovery.** Because the manifest carries no stamp, the next pipeline run on this spec will:
- Read the manifest, find no `active_adapters` key.
- Compare stamped adapters `[]` against current activated adapters `["postgres", "typescript-node"]`.
- Classify as `adapter_set_drift` (prior stamp treated as absent), emit `ARCHITECT_CACHE_INVALIDATED`, force a fresh architect regenerate.

No manual cleanup needed on the manifest — the cache gate does the right thing on the next attempt.

## Environment state unchanged (fail-closed apply)

`.dark-factory/architecture/environment.toml` shows NO migration command — matches baseline:

```toml
[commands]
build = "cd apps/api && pnpm build"
install = "cd apps/api && pnpm install"
lint = "cd apps/api && pnpm lint"
server_start = "cd apps/api && pnpm start"
test = "cd packages/shared && pnpm test"
typecheck = "cd apps/api && pnpm typecheck"
```

`_apply_manifest_to_environment` raised before mutating `environment.toml`. Fail-closed. The apply layer's atomicity is working as intended.

## Audit spot-check — spec-validator barrier config

Verbatim docker run argv for the spec-validator container (from `.dark-factory/audit/2026-03-28-database-foundation-977e570083114f7a.log`):

```
docker run --rm ... -v ~/sites/budget-brain-v2:/project:ro \
  -v ~/sites/budget-brain-v2/.dark-factory/reviews:/project/.dark-factory/reviews:rw \
  --tmpfs /project/apps/api/src:ro,size=0 --tmpfs /project/.git:ro,size=0 \
  ... dark-factory-node claude -p --dangerously-skip-permissions ...
  --disallowedTools WebSearch --disallowedTools WebFetch
  --append-system-prompt "INFORMATION BARRIER: You MUST NOT access source code directories or .git/ ..."
```

Confirms the validator-barrier changes from the 2026-04-16 determinism investigation are in effect:
- `apps/api/src` masked (source dir under tmpfs overlay)
- `.git` masked
- `WebSearch` / `WebFetch` denied
- Barrier prompt present ("INFORMATION BARRIER: source code directories ... do not exist")

Note: only `apps/api/src` is listed in `--tmpfs` — `apps/web/src` and `packages/shared/src` from `project-hints.toml`'s `source_dirs` are not. This is defensible if `source_dirs` in effect at mount time only included `apps/api/src` (inspect `[layout].source_dirs` in `environment.toml`: `[ "apps/api/src" ]`, confirming only one was in effect — but see footnote). Worth a follow-up if Move 3b or a later move extends to multi-dir masking.

## Pre-flight warnings (non-fatal, preserved from log for audit completeness)

```
WARNING: spec '2026-03-28-database-foundation' declares ID-form depends-on entries that cannot be resolved outside a plan context: F-1b — ignoring for standalone run
WARNING: test_server_cmd is set but test_base_url is empty; server will default to port 8000
WARNING: library_symbols: 3 undeclared import(s) in spec §5
WARNING: library_symbols: line 140 — spec §5 imports 'drizzle-orm/postgres-js' but 'drizzle-orm' is not declared as a dependency in any package.json under ~/sites/budget-brain-v2 [dependency]
WARNING: library_symbols: line 140 — spec §5 imports 'postgres' but 'postgres' is not declared as a dependency in any package.json under ~/sites/budget-brain-v2 [dependency]
WARNING: library_symbols: line 195 — spec §5 imports 'drizzle-kit' but 'drizzle-kit' is not declared as a dependency in any package.json under ~/sites/budget-brain-v2 [dependency]
```

The `library_symbols` warnings are expected — this is a bootstrap spec that *introduces* the drizzle/postgres stack. Not related to the failure.

## Next steps

1. **Run `dark-factory remediate 977e570083114f7a`** to interactively accept/reject the suggested consistency-rule edit, or use `/df-write-hints from-failure 977e570083114f7a` to iterate on the spec or project hints so the next architect run stops choosing push mode.
2. **Re-run the pipeline.** Move 3b's cache gate will correctly regenerate (the broken manifest is missing its stamp).
3. **Task #19 (`Fix _write_project_hints to preserve committed detection`)** is still queued. This run did not hit a failure mode caused by task #19 — postgres activation came from `spec_triggers`, and `test_runner = ""` was unused. Task #19 remains a standalone follow-up.

## Observations on the architect's choice (orthogonal to framework bugs)

The spec is explicit about CLI-driven migrations in §11 (migration safety: commands, pre-flight checks, journal). The architect nevertheless picked `drizzle-kit push`. This is not a framework bug — the consistency rule caught it — but it is a signal that the architect prompt may under-weight "migration mode" compared to "scaffold expediency." Worth considering in a future adapter-prompt update: raise the signal that `push` mode is a forbidden-pattern when any `db:migrate` token appears in the spec.

The framework handled this correctly: refuse to advance, surface the specific rule + file + suggested remediation, leave the environment un-mutated, withhold the cache stamp. That is exactly what the architecture-lock step is meant to do.
