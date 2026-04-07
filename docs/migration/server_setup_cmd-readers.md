# `server_setup_cmd` and Sibling Field Readers — Audit

**Audit date:** 2026-04-07
**Phase:** 1.3 (BLOCKER for Phase 2.3 — environment contract migration)
**Method:** `grep -n` over `darkfactory/` for the field names listed below.
**Phase 2.6 enforcement test will read this file.** When Phase 2.3 deletes the
direct readers and routes everything through `load_environment()`, every entry
in this document must be reduced to "calls `load_environment()`."

## Fields audited

- `server_setup_cmd`
- `test_server_cmd`
- `test_base_url`
- `server_health_path`
- `server_startup_timeout`
- `test_cmd`
- `scenario_test_cmd`
- `build_cmd`
- `lint_cmd`
- `typecheck_cmd`

## Definition sites (NOT readers — schema only)

These declare the fields. They are not consumers and don't need to migrate.

| File | Lines | Notes |
|------|-------|-------|
| `darkfactory/models/config.py` | 46-47, 80-82, 192-196 | Dataclass field declarations on `PipelineConfig` (and dependent groups) |
| `darkfactory/spec/manifest.py` | 117-121, 437-447 | `ManifestRuntime` dataclass + parser |
| `darkfactory/setup/detect.py` | 42-45 | `DetectionResult` dataclass |
| `darkfactory/setup/workspace_check.py` | 28-30 | `WorkspaceCheckResult` parameters |
| `darkfactory/config/defaults.py` | 15-16, 42-51, 164-168 | TOML key → field name maps |
| `darkfactory/config/profiles.py` | 30-59 | Profile override key maps (kebab + snake) |
| `darkfactory/config/loader.py` | 131-151 | Env-var → field maps |

## Real readers (these MUST migrate in Phase 2.3)

### Tier 1 — pipeline runtime (highest priority)

| File | Line | Field(s) | Behavior |
|------|------|----------|----------|
| `darkfactory/pipeline/orchestrator.py` | 97-99 | `test_base_url`, `server_env.PORT` | Pre-flight PORT/test_base_url conflict check |
| `darkfactory/pipeline/orchestrator.py` | 113-115 | `test_server_cmd`, `test_base_url` | Pre-flight: warn if server cmd set without base url |
| `darkfactory/pipeline/orchestrator.py` | 383 | `scenario_test_cmd` | Error message text |
| `darkfactory/pipeline/orchestrator.py` | 394-396 | `lint_cmd`, `typecheck_cmd`, `test_cmd` | Passes to spec validator |
| `darkfactory/pipeline/orchestrator.py` | 817-831 | All five `server_*` and `test_*` fields | **Manifest runtime → config fallback merge** (Phase 2.3 replaces this entire block with delta application) |
| `darkfactory/pipeline/orchestrator.py` | 914-923 | `server_setup_cmd`, `test_server_cmd`, `test_base_url`, `server_health_path`, `server_startup_timeout` | Builds runtime context block fed to Architect prompt |
| `darkfactory/pipeline/orchestrator.py` | 1131-1133 | `test_server_cmd`, `scenario_test_cmd` | `HAS_*` boolean context flags for agents |
| `darkfactory/pipeline/orchestrator.py` | 1202 | `test_server_cmd` | `_require_behavioral` decision |
| `darkfactory/pipeline/quality_gate.py` | 217-246 | `test_cmd`, `build_cmd`, `lint_cmd`, `typecheck_cmd`, `scenario_test_cmd`, `test_base_url`, `test_server_cmd`, `server_startup_timeout`, `server_health_path` | Builds gate-runner CLI args |
| `darkfactory/pipeline/quality_gate.py` | 295-340 | `test_server_cmd`, `browser_server_cmd`, `test_base_url`, `server_health_path` | HTTP smoke server orchestration |
| `darkfactory/pipeline/quality_gate.py` | 406 | `server_health_path` | HTTP smoke health probe |
| `darkfactory/pipeline/quality_gate.py` | 506-563 | `build_cmd`, `lint_cmd`, `typecheck_cmd`, `test_server_cmd`, `test_base_url`, `server_health_path` | Browser smoke gate command |
| `darkfactory/pipeline/quality_gate.py` | 659-660 | `scenario_test_cmd` | Scenarios runner cmd |
| `darkfactory/pipeline/quality_gate.py` | 794 | `test_base_url` | HOST/PORT derivation comment |
| `darkfactory/pipeline/quality_gate.py` | 1023-1061 | `server_setup_cmd`, `server_startup_timeout`, `server_health_path` | **Smoke server boot — the bug from run 20:34 lives here** |
| `darkfactory/pipeline/iteration.py` | 341-353 | `server_setup_cmd` | Stable-failure detector for `server_setup_cmd failed` |
| `darkfactory/pipeline/iteration.py` | 480, 529 | `test_server_cmd` | Smoke retry decision |

### Tier 2 — scenarios subsystem

| File | Line | Field(s) | Behavior |
|------|------|----------|----------|
| `darkfactory/scenarios/compat.py` | 48-145 | `scenario_test_cmd`, `test_server_cmd`, `test_base_url`, `browser_server_cmd` | Format-compatibility decisions for scenario formats |
| `darkfactory/scenarios/runner.py` | 29, 40, 49-56 | `scenario_test_cmd` | Custom test runner override path |

### Tier 3 — setup / init / integration (out-of-band, runs before pipeline)

These run during `dark-factory init` or integrator scaffolding. They write
config files; they don't consume them at pipeline time. Phase 2.3 may leave
these alone OR have them write deltas instead of `[core]` keys, depending on
how the env contract handles bootstrap.

| File | Line | Notes |
|------|------|-------|
| `darkfactory/setup/detect.py` | 252-441 | Language-specific defaults + `_GATE_CMD_FIELDS` merge |
| `darkfactory/setup/init.py` | 205-281 | Prints detected commands; writes them to generated config |
| `darkfactory/setup/workspace_check.py` | 28-44 | Validates command shape |
| `darkfactory/integration/integrator.py` | 40-198 | Per-language template defaults |

## Migration target (Phase 2.3)

After Phase 2.3 lands, every entry in **Tier 1** and **Tier 2** will:

1. Call `load_environment(spec, project_dir)` once at the start of its
   relevant lifecycle stage (orchestrator: per `step_begin`; quality_gate:
   per gate invocation; iteration: per iteration boundary).
2. Read `env.commands.*` and `env.server.*` from the materialized
   `EnvironmentContract` instead of `config.test_server_cmd` etc.
3. Stop reading `config.<field>` directly. The `PipelineConfig` fields
   become **legacy bootstrap defaults**, written into the contract only
   when no delta exists for that field yet.

Tier 3 (setup/init/integrator) is deferred to Phase 4 (`spec_type`
migration). Bootstrap specs will write the initial environment contract;
init can keep emitting `[core]` defaults as the seed delta.

## Phase 2.6 enforcement

The Phase 2.6 enforcement test will:

1. Parse this file's "Tier 1" and "Tier 2" tables.
2. For each `(file, line)` pair, assert that the line no longer contains
   any of the audited field names as a `config.<field>` access.
3. Allow access via `env.commands.<field>` or `env.server.<field>`.

If the test fails, either Phase 2.3 missed a reader or someone added a new
direct reader after the audit.

## Notes / surprises

- `orchestrator.py:817-831` is the **manifest runtime → config fallback** block.
  This is the duality Phase 2 collapses: `manifest.runtime` IS the per-spec
  amendment payload, and the fallback-merge logic disappears entirely.
- `quality_gate.py:1023` is the smoke-boot block where the run-20:34 failure
  originated. This must be the first block migrated when Phase 2.3 starts.
- `iteration.py:341-353` reads `server_setup_cmd` only to recognize a
  `"server_setup_cmd failed"` substring in stable-failure detection. After
  Phase 2.3 the substring will need to come from `env.commands.server_start`
  or whatever the equivalent field is named, OR the detector will need to
  match on a structured event type instead of substring matching.
