# Plan: Runtime Environment in Architecture Manifest

## Problem

The pipeline config (`config.toml`) defines runtime commands (`server_setup_cmd`, `test_server_cmd`) and services (`[[services]]`), but no agent ever sees them. The Implementer places files without knowing the runtime CWD. The Architect has no runtime context when deciding file paths. This has caused persistent server startup failures across every pipeline run for specs with services.

The deeper issue: `config.toml` is human-authored and static. As new specs ship (adding Redis, changing server entrypoints, updating migrations), the human must manually update config between specs. This breaks the lights-out goal.

## Design

### Core Idea

The Architect manifest gets a `runtime` section that declares what the spec needs. The orchestrator reads it as fallback when config.toml doesn't specify a value. The schema is designed for future cumulative merge across specs, but the initial implementation reads only the current spec's manifest.

### Schema

```yaml
runtime:
  # Additive — merged by name across specs (future)
  services:
    - name: postgres
      image: "postgres:16-alpine"
      env:
        POSTGRES_USER: postgres
        POSTGRES_PASSWORD: postgres
        POSTGRES_DB: testdb
      health_cmd: "pg_isready -U postgres"
      health_timeout: 30
      inject_env:
        DATABASE_URL: "postgres://postgres:postgres@postgres:5432/testdb"

  # Scalars — last-writer-wins across specs (future)
  # Only set if this spec establishes or changes them
  server_setup_cmd: "cd apps/api && npx drizzle-kit push:pg"
  test_server_cmd: "cd apps/api && node dist/server.js"
  test_base_url: "http://localhost:3000"
  # Parsed in Phase 1 but not wired to quality gate until Phase 2
  server_health_path: "/health"
  server_startup_timeout: 30
```

### Information Flow

```
config.toml (human escape hatch, always overrides)
  + current spec's manifest runtime section (fallback for unset fields)
    = effective PipelineConfig used by quality gate
```

The Architect receives existing runtime context (from config.toml or previous manifests) in its prompt so it can make informed decisions about file placement relative to CWDs.

### Who Does What

| Actor | Runtime Role | Writes To |
|-------|-------------|-----------|
| Plan | Seeds initial runtime expectations (advisory) | Plan doc |
| Spec | Declares requirements ("needs Redis") | Spec doc |
| Architect | Translates to concrete runtime config | Manifest `runtime:` |
| Orchestrator | Reads manifest, patches PipelineConfig | In-memory |
| Quality Gate | Validates (boots services, health checks) | Gate report |

The Implementer does NOT write runtime config. It writes application code following the manifest's `file_map`. The Orchestrator materializes runtime config mechanically.

## Implementation

### Phase 1: Manifest Runtime as Config Fallback

- [x] Add `RuntimeDecl` dataclass to `darkfactory/spec/manifest.py`
- [x] Add `runtime` field to `ArchitectureManifest`
- [x] Parse `runtime:` YAML block in `parse_manifest()`
- [x] Validate runtime fields (non-empty commands, well-formed URLs, DNS-safe service names)
- [x] Inject existing runtime context into Architect prompt in `_step_architecture_lock()`
- [x] Update `agents/architect.md` with `runtime:` schema docs and guidance
- [x] Orchestrator: after manifest validation in `_step_architecture_lock()`, read manifest `runtime` and patch `PipelineConfig` for unset fields (before returning, so patched config is available to scenario generation)
- [x] config.toml explicit values always take precedence
- [x] Update `format_manifest_for_implementer()` to emit runtime context (CWD, server commands) so Implementer places files correctly
- [x] Update `format_manifest_for_sa()` and `_load_manifest_context_for_sa()` to emit runtime service availability for scenario design
- [x] Unit tests for parsing, validation, and config patching
- [x] Update docs (WORKFLOW.md, CONFIGURATION.md)

### Phase 2: Cumulative Runtime Across Specs (Future)

These are deferred until a real multi-spec project needs them. The Phase 1 schema already supports them — no schema changes needed, only orchestrator logic.

- [ ] Ancestor manifest discovery: walk `PlanSpec.deps` to find shipped ancestor manifests
- [ ] `_merge_runtime_manifests()` function with defined merge semantics:
  - Services: merge by name (additive). Same-name collision = error (unless `override: true`)
  - Scalars (`server_setup_cmd`, etc.): last-writer-wins
  - `inject_env`: additive merge, error on key collision
  - config.toml overrides all
- [ ] Deep-copy PipelineConfig per spec dispatch to prevent concurrent mutation
- [ ] Resume gate: check manifest hash (not just spec hash) for runtime staleness
- [ ] `server_setup_cmd` as ordered list (chain Spec 1 migrations + Spec 2 migrations)
- [ ] Add `server_health_path` and `server_startup_timeout` as manifest scalars (tightly coupled to `test_server_cmd`)
- [ ] Integration tests with multi-spec dependency chains

## Design Decisions

**Why the Architect declares runtime (not the Implementer):** The Architect already owns file placement via `file_map`. Runtime CWD is a file placement constraint. Having the Implementer write docker-compose or config.toml adds blast radius — it already failed at placing drizzle.config.ts correctly.

**Why the Orchestrator materializes (not the Architect):** The Architect has a read-only project mount. It can't write config.toml. It writes runtime decisions into its manifest (which it already writes to `.dark-factory/architecture/`), and the orchestrator reads them mechanically.

**Why config.toml overrides all:** Human escape hatch. If the Architect gets it wrong, the human can always force correct values. This also makes migration seamless — existing projects with config.toml work unchanged.

**Why the schema is designed for accumulation now:** Services are lists (additive by name), scalars are optional (set only if changed). When Phase 2 adds merge logic, no schema changes needed. Neo's insight: runtime needs evolve across specs, so the schema must support layering even if we don't implement it yet.

## References

- Liotta review: Architect is the right structural authority. Don't build merge until you need it. "Solve the post-it-note problem first."
- Neo review: Cumulative manifests are the long-term answer. Schema should support it from day one. Watch for resume staleness and concurrent config mutation.
- Conversation context: 2026-04-01, persistent server startup failures on budget-brain-v2 auth-schemas-middleware spec across 4+ pipeline runs.
