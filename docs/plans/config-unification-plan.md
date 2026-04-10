# Unified Config System: Test-Gated Implementation Plan

**Date:** 2026-04-10
**Status:** Reviewed by Neo — issues integrated
**Motivation:** Eliminate the three-source-of-truth bug that causes ~40% of pipeline runs to waste money on infrastructure/config confusion.

---

## Problem Statement

Dark Factory currently has three sources of truth for operational configuration:

1. **`config.toml`** — Human-authored at `dark-factory init`, contains operational fields (`build_cmd`, `lint_cmd`, `test_cmd`) under `[gates]` and structural fields (`language`, `source_dirs`) under `[core]`.

2. **`environment.toml`** — Machine-authored via append-only delta log from the Architect agent's manifest `runtime:` blocks. Applied at every step boundary via `step_begin()` → `refresh_config_from_environment()`.

3. **`refresh_gate_commands()`** — Post-implementation hook in `detect.py` that re-detects gate commands from the filesystem and fills empty `PipelineConfig` slots. Also writes to `config.toml` on disk via `_patch_config_toml()` — provenance laundering: a machine-detected value persists as if human-authored.

### Why this is a problem

- **Provenance laundering:** `refresh_gate_commands` writes detections into `config.toml` permanently. On the next run, `load_config` reads them as human-authored values. A bad detection from one spec permanently alters config for all future specs.
- **Invisible masking:** When the gate runs `pnpm test`, you can't tell which layer set it without reading three files and mentally simulating the overlay order.
- **False safety net:** `refresh_gate_commands` silently fixes Architect failures instead of making them visible. Architect prompt bugs go unnoticed.
- **40% waste:** Approximately 40% of pipeline runs ($720+ of $1,800 total spend) are wasted on infrastructure/config confusion.

### Target end state

- `config.toml` owns **only pipeline tuning**: max_iterations, budgets, autonomy, timeouts, enterprise flags, model config.
- `environment.toml` (via delta log) is the **single source of truth** for all operational config: commands, services, package manager, source dirs, server config.
- `dark-factory init` seeds a bootstrap delta from filesystem detection.
- `refresh_gate_commands` is eliminated. Auto-detection runs through the delta log with visible provenance.
- A deterministic preflight check validates the environment before spending agent money.

---

## Current Data Flow

```
init_project() → detect_project() → _write_config() → config.toml [gates] section
                                                         |
orchestrator.run_pipeline()                              |
  ├── load_config(config.toml) → PipelineConfig          |
  ├── ensure_environment_bootstrap(config) → environment.toml (if missing)
  ├── refresh_config_from_environment(config, env) → overlays env onto config
  │
  └── run_iteration_loop():
        ├── step_begin() → re-loads environment.toml, overlays onto config
        ├── _step_implementation() → Implementer runs
        ├── _refresh_gate_commands_hook() → detect_project() → patches config.toml + PipelineConfig
        └── run_quality_gate() → uses config.build_cmd, etc.
```

### Target Data Flow

```
init_project() → detect_project() → _write_bootstrap_delta() → 0000-bootstrap-from-detection.toml
                                   → _write_config() → config.toml (tuning only)
                                                         |
orchestrator.run_pipeline()                              |
  ├── load_config(config.toml) → PipelineConfig (tuning only)
  ├── ensure_environment_bootstrap(config) → migrates legacy config.toml if needed
  ├── run_preflight_check() → abort at $0 if environment broken
  ├── refresh_config_from_environment(config, env) → overlays ALL operational config
  │
  └── run_iteration_loop():
        ├── step_begin() → re-loads environment.toml, overlays onto config
        ├── _step_implementation() → Implementer runs
        ├── write_autodetect_delta() → fills gaps via delta log (visible, traceable)
        └── run_quality_gate() → uses config from environment contract
```

---

## Dependency Graph

```
Phase 1 (Observability)
    │
Phase 2 (Reroute refresh_gate_commands)
    │
    ├── Phase 3 (Deep init + bootstrap delta)
    │       │
    │       ├── Phase 4 (Field coverage) ← can parallel with Phase 3
    │       │       │
    │       └───────┤
    │               │
    │         Phase 5 (Remove operational fields from config.toml)
    │               │
    └───────────────┤
                    │
              Phase 6 (Kill refresh_gate_commands)
                    │
              Phase 7 (Deterministic preflight)
```

Phases 3 and 4 can be developed in parallel. All others are sequential.

---

## Phase 1: Observability Foundation

**Goal:** Add provenance tracking and a `resolved_config_dump()` diagnostic so we can verify all subsequent changes don't break config resolution.

**Why first:** Every subsequent phase changes which layer sets config values. Without observability, we can't verify correctness.

### Step 1.1: Add provenance tracking to PipelineConfig

**Files:** `darkfactory/models/config.py`

**Changes:**
- Add `_provenance: dict[str, str] = field(default_factory=dict, repr=False, compare=False)` to PipelineConfig
- Add `set_provenance(self, field_name: str, source: str)` method
- Add `get_provenance(self, field_name: str) -> str` method (returns source or `"default"`)

**Test gate:**
- `tests/unit/test_config.py::test_provenance_default_is_empty`
- `tests/unit/test_config.py::test_provenance_set_and_get`
- All existing `test_config.py` tests pass unchanged

**Effort:** S

### Step 1.2: Annotate provenance in config loader

**Files:** `darkfactory/config/loader.py`

**Changes:**
- In `_build_config()`, after constructing PipelineConfig, iterate kwargs that were set from TOML and call `config.set_provenance(field_name, f"config.toml:[{section}]")`

**Test gate:**
- `tests/unit/test_config_loader.py::test_loaded_config_has_provenance_for_set_fields`
- `tests/unit/test_config_loader.py::test_unset_fields_have_default_provenance`
- All existing loader tests pass

**Effort:** S

### Step 1.3: Annotate provenance in refresh_config_from_environment

**Files:** `darkfactory/architecture/runtime.py`

**Changes:**
- In `refresh_config_from_environment()`, after each `setattr(config, config_field, value)`, call `config.set_provenance(config_field, "environment.toml")`

**Test gate:**
- `tests/unit/test_runtime.py::test_refresh_sets_provenance_on_overlaid_fields`
- `tests/unit/test_runtime.py::test_refresh_does_not_set_provenance_on_skipped_fields`
- All existing runtime tests pass

**Effort:** S

### Step 1.4: Annotate provenance in refresh_gate_commands

**Files:** `darkfactory/setup/detect.py`

**Changes:**
- In `refresh_gate_commands()`, after each `config.<field> = value`, call `config.set_provenance(field, "auto-detect")`

**Test gate:**
- `tests/unit/test_detect_refresh.py::test_refresh_sets_autodetect_provenance`
- All existing detect tests pass

**Effort:** S

### Step 1.5: Add resolved_config_dump function

**Files:** Create `darkfactory/config/diagnostics.py`

**Changes:**
- `resolved_config_dump(config: PipelineConfig) -> str` iterates operational fields and formats: `field = "value"  # source: provenance`
- Operational fields: `build_cmd`, `lint_cmd`, `typecheck_cmd`, `test_cmd`, `setup_cmd`, `test_server_cmd`, `server_setup_cmd`, `language`, `source_dirs`, `test_dir`, `package_manager`

**Test gate:**
- `tests/unit/test_config_diagnostics.py::test_resolved_config_dump_shows_provenance`
- `tests/unit/test_config_diagnostics.py::test_resolved_config_dump_default_fields_show_default`

**Effort:** S

### Step 1.6: Wire into orchestrator

**Files:** `darkfactory/pipeline/orchestrator.py`

**Changes:**
- After `refresh_config_from_environment` call, add `log.debug(resolved_config_dump(config))`

**Test gate:**
- All existing orchestrator tests pass
- Manual verification with DEBUG logging

**Effort:** S

### Phase 1 Exit Gate
All existing tests pass. Provenance system is purely additive — zero behavior changes.

---

## Phase 2: Reroute refresh_gate_commands Through Delta Log

**Goal:** Stop `refresh_gate_commands` from writing to `config.toml`. Write detection results as an `auto-detect` delta instead. Gap-filling survives but is visible and traceable.

### Step 2.1: Add auto-detect delta writer

**Files:** Create `darkfactory/setup/autodetect_delta.py`

**Changes:**
- `write_autodetect_delta(project_dir: Path, config: PipelineConfig) -> list[tuple[str, str]]`:
  1. Runs `detect_project(project_dir)`
  2. For each gate command field where `config.<field>` is empty and `detected.<field>` is non-empty, adds an op to a delta
  3. Writes delta named `NNNN-auto-detect.toml` with `spec_id = "auto-detect"`
  4. Materializes the environment contract
  5. Logs WARNING for each gap filled: `"auto-detect: filled {field} = {value} — Architect should declare this in runtime: block"`
  6. Overlays new values onto config via `refresh_config_from_environment()`
  7. Sets provenance to `"auto-detect"` on each overlaid field
  8. Returns list of `(field, value)` pairs written

**Constraints:**
- Must NOT set bootstrap-only paths (`runtime.language`, `layout.source_dirs`)
- Must respect `_next_delta_number()` for delta chain integrity
- Must be idempotent: if detection results match materialized view, skip delta write
- Must implement snapshot+rollback pattern (same as `amend_environment_from_manifest` in amend.py lines 244-270) — if `materialize()` fails after delta file is written, clean up the delta file
- **Concurrent writes:** Two pipeline runs for different specs could race on `_next_delta_number()`. Document as accepted limitation — the second run will fail with `EnvironmentConflict` on next `materialize`, which is safe (retry recovers). Future: add file locking if this becomes frequent.

**Test gate:**
- `tests/unit/test_autodetect_delta.py::test_fills_empty_commands_via_delta`
- `tests/unit/test_autodetect_delta.py::test_noop_when_all_commands_set`
- `tests/unit/test_autodetect_delta.py::test_delta_chains_with_prior_deltas`
- `tests/unit/test_autodetect_delta.py::test_warns_for_each_gap_filled`
- `tests/unit/test_autodetect_delta.py::test_detection_failure_returns_empty`
- `tests/unit/test_autodetect_delta.py::test_idempotent_second_call`
- `tests/unit/test_autodetect_delta.py::test_does_not_set_bootstrap_only_paths`
- `tests/unit/test_autodetect_delta.py::test_rollback_on_materialize_failure`

**Effort:** M

### Step 2.2: Replace _refresh_gate_commands_hook in iteration loop

**Files:** `darkfactory/pipeline/iteration.py`

**Changes:**
- Remove import of `refresh_gate_commands` from `darkfactory.setup.detect`
- Remove `_refresh_gate_commands_hook()` function
- At the same call site (after implementation, before pre-gate), call `write_autodetect_delta(project_dir, config)`
- Update progress messages

**Test gate:**
- Update `tests/unit/test_detect_refresh.py::TestHookCallSitePlacement` to verify new call site
- `tests/unit/test_iteration.py::test_autodetect_delta_called_after_implementation`
- All existing iteration tests pass

**Effort:** M

### Phase 2 Exit Gate
`config.toml` is never modified during pipeline runs. Auto-detected commands appear in the delta log with provenance. `refresh_gate_commands()` still exists as dead code (removed in Phase 6).

---

## Phase 3: Deep Init Detection + Bootstrap Delta

**Goal:** Expand detection capabilities. Change `dark-factory init` to write detection results into a bootstrap delta instead of `config.toml`'s `[gates]` section.

### Step 3.1: Expand detect.py detection capabilities

**Files:** `darkfactory/setup/detect.py`

**Changes to `DetectionResult`:**
- Add `package_manager: str = ""` (from lockfiles: pnpm-lock.yaml → pnpm, yarn.lock → yarn, etc.)
- Add `workspace_layout: str = ""` (`"monorepo"` or `"single"`)
- Add `service_dependencies: list[dict] = field(default_factory=list)` (from docker-compose.yml)
- Add `orm_tool: str = ""` (prisma/drizzle/alembic/typeorm from markers)
- Add `available_scripts: dict[str, str] = field(default_factory=dict)` (from package.json)
- Add `setup_cmd: str = ""` (inferred from ORM/migration tools)
- Add `server_cmd: str = ""` (from package.json scripts: dev, start, serve)
- Add `server_health_path: str = ""` (left empty — too many conventions: `/health`, `/healthz`, `/api/health`, `/_health`. Let the Architect set it explicitly.)

**New detection functions:**
- `_detect_package_manager()` — import from `darkfactory.architecture.hints` (already does lockfile-based detection at hints.py lines 39-55) to avoid code duplication. Wrap its `EnvironmentHints.package_manager` result into `DetectionResult.package_manager`. Also detect `bun.lockb` for Bun projects.
- `_detect_workspace_layout()` — pnpm-workspace.yaml, lerna.json, nx.json, turbo.json, workspace entries in package.json
- `_detect_services()` — parse docker-compose.yml/yaml if present via `yaml.safe_load` with try/except. Extract only top-level service names and images — do NOT try to resolve extends/overrides/variable interpolation.
- `_detect_orm_and_migrations()` — prisma/schema.prisma, drizzle.config.ts, alembic.ini. When detected, infer `setup_cmd` (e.g., prisma → `npx prisma db push`, drizzle → `pnpm drizzle-kit push`, alembic → `alembic upgrade head`). Prefix with detected package manager.
- `_detect_server_cmd()` — package.json scripts for dev/start/serve

**Design principle:** Conservative detection. False positives are worse than false negatives — better to leave a field empty and let the Architect fill it than bake incorrect values into the delta log.

**Test gate:**
- `tests/unit/test_detect.py::test_detects_pnpm_package_manager`
- `tests/unit/test_detect.py::test_detects_npm_package_manager`
- `tests/unit/test_detect.py::test_detects_yarn_package_manager`
- `tests/unit/test_detect.py::test_detects_monorepo_workspace_layout`
- `tests/unit/test_detect.py::test_detects_docker_compose_services`
- `tests/unit/test_detect.py::test_detects_drizzle_orm`
- `tests/unit/test_detect.py::test_detects_prisma_orm`
- `tests/unit/test_detect.py::test_detects_server_cmd_from_scripts`
- All existing `test_detect.py` tests pass unchanged

**Effort:** L

### Step 3.2: Write bootstrap delta from init

**Files:** `darkfactory/setup/init.py`

**Changes:**

New function `_write_bootstrap_delta(project_dir, detection)`:
- Creates `.dark-factory/architecture/environment.deltas/` directory
- Builds ops from DetectionResult → env contract paths: `runtime.language`, `runtime.package_manager`, `commands.build`, `commands.lint`, `commands.typecheck`, `commands.test`, `commands.setup`, `commands.server_start`, `layout.source_dirs`, `layout.test_dirs`, `server.health_path`
- Only includes ops for non-empty detected values
- Uses `hash_view(EnvironmentContract())` as parent hash (first delta)
- Materializes to `environment.toml`

Modify `_write_config()`:
- Remove `[gates]` section entirely
- Remove `source_dirs` and `test_dir` from `[core]`
- Keep `language` in `[core]` (needed for Docker image selection before env contract loads)

**Test gate:**
- Update `tests/unit/test_init.py::test_writes_basic_config` — assert no `[gates]`, no `source_dirs`
- `tests/unit/test_init.py::test_init_creates_bootstrap_delta`
- `tests/unit/test_init.py::test_bootstrap_delta_contains_detected_commands`
- `tests/unit/test_init.py::test_bootstrap_delta_materializes_environment_toml`
- `tests/unit/test_init.py::test_config_toml_has_no_gates_section`
- All existing init tests pass (with updated assertions)

**Risks:**
- Existing projects with `[gates]` in config.toml are handled by `backcompat.py` in Phase 5
- `language` lives in both config.toml and env contract; env overlay wins at runtime

**Effort:** M

### Phase 3 Exit Gate
`dark-factory init` on a new project creates a bootstrap delta. `config.toml` has no operational fields. Legacy projects still work via backcompat.

---

## Phase 4: Field Coverage Fixes

**Goal:** Wire missing fields so the Architect's manifest can fully control them. Can be developed in parallel with Phase 3.

### Step 4.1: Add package_manager to PipelineConfig

**Files:**
- `darkfactory/models/config.py` — Add `package_manager: str = ""`
- `darkfactory/architecture/runtime.py` — Add `("runtime", "package_manager", "package_manager")` to `_ENV_TO_CONFIG`

**NOTE:** Do NOT add `package_manager` to `CORE_FIELDS` in defaults.py. It should only flow through the environment contract (`_ENV_TO_CONFIG`), not be loadable from config.toml — that would contradict the goal of removing operational fields from config.toml.

**Test gate:**
- `tests/unit/test_runtime.py::test_refresh_overlays_package_manager`
- `tests/unit/test_config.py::test_package_manager_default_empty`
- All existing tests pass

**Effort:** S

### Step 4.2: Expand ALLOWED_PATHS

**Files:**
- `darkfactory/architecture/environment.py`:
  - Add to `ALLOWED_PATHS`: `server.env`, `commands.scenario_test`, `server.browser_base_url`, `server.browser_server_cmd`, `server.browser_startup_timeout`
  - Add fields to `ServerConfig`: `env`, `browser_base_url`, `browser_server_cmd`, `browser_startup_timeout`
  - Add `scenario_test` to `Commands`
  - Register new fields in `_PHASE4_NEW_FIELDS` for hash stability

- `darkfactory/architecture/runtime.py` — Add to `_ENV_TO_CONFIG`:
  - `("commands", "scenario_test", "scenario_test_cmd")`
  - `("server", "browser_base_url", "browser_base_url")`
  - `("server", "browser_server_cmd", "browser_server_cmd")`
  - `("server", "browser_startup_timeout", "browser_startup_timeout")`
  - Special handling for `server.env` → `config.server_env`

**Test gate:**
- `tests/unit/test_runtime.py::test_refresh_overlays_scenario_test_cmd`
- `tests/unit/test_runtime.py::test_refresh_overlays_browser_fields`
- `tests/unit/test_runtime.py::test_refresh_overlays_server_env`
- `tests/unit/test_canonical_dump.py::test_new_fields_at_default_do_not_change_hash`
- All existing canonical_dump tests pass

**Risk:** Hash stability is critical. New fields MUST be in `_PHASE4_NEW_FIELDS` and omitted from `canonical_dump` at default values, or existing delta chains break.

**Effort:** M

### Step 4.3: Remove source_dirs from BOOTSTRAP_ONLY_PATHS

**Files:** `darkfactory/architecture/environment.py`

**Changes:**
- Remove `"layout.source_dirs"` from `BOOTSTRAP_ONLY_PATHS`
- This allows feature specs to amend source_dirs (add workspaces)

**Test gate:**
- Update `tests/unit/test_amend.py::test_architect_yaml_feature_rejects_bootstrap_only_paths` — test with `runtime.language` only
- `tests/unit/test_amend.py::test_feature_spec_can_amend_source_dirs`
- All existing amend tests pass

**Effort:** S

### Phase 4 Exit Gate
All new fields wired end-to-end: Architect manifest → delta log → environment.toml → PipelineConfig. Hash stability preserved. Feature specs can amend source_dirs.

---

## Phase 5: Remove Operational Fields from config.toml

**Goal:** config.toml owns only pipeline tuning. backcompat handles legacy migration.

### Step 5.1: Update backcompat.py for full migration

**Files:** `darkfactory/architecture/backcompat.py`

**Changes:**
- Enhance `ensure_environment_bootstrap()`: after synthesizing the delta and materializing, strip operational fields from config.toml on disk
- Add `_strip_operational_fields(config_path: Path, env_path: Path)`:
  - **Safety guard:** Before stripping any field from config.toml, verify its value exists (non-empty) in the materialized environment.toml. This prevents data loss if the delta chain is incomplete (e.g., `materialize()` crashed on a prior run).
  - Remove from `[gates]`: `build_cmd`, `lint_cmd`, `typecheck_cmd`, `test_cmd`
  - Remove from `[core]`: `source_dirs`, `test_dir`, `test_cmd`, `scenario_test_cmd`
  - Remove from `[test_server]`: ALL operational fields — `server_cmd`, `setup_cmd`, `health_path`, `env`, `browser_base_url`, `browser_server_cmd`. Keep `startup_timeout` and `browser_startup_timeout` (these are tuning parameters, not operational config).
  - Preserve all tuning fields (`timeout`, `min_ship_pass_rate`, etc.)
  - Rewrite config.toml atomically (tempfile + replace, same pattern as `_patch_config_toml`)
- Log WARNING: `"Migrated operational fields from config.toml to environment.toml"`
- Add to `_CONFIG_TO_ENV`: `setup_cmd`, `scenario_test_cmd`, `source_dirs`, `test_dir`

**Test gate:**
- `tests/unit/test_backcompat.py::test_strips_operational_fields_from_config_toml`
- `tests/unit/test_backcompat.py::test_preserves_tuning_fields_in_config_toml`
- `tests/unit/test_backcompat.py::test_migration_includes_setup_cmd`
- `tests/unit/test_backcompat.py::test_migration_includes_source_dirs`
- All existing backcompat tests pass

**Risk:** Must handle `test_cmd` appearing in both `[core]` and `[gates]`. Strip from both.

**Effort:** M

### Step 5.2: Verify config.toml template has no operational fields

**Files:** `darkfactory/setup/init.py`, `darkfactory/config/defaults.py`

**Changes:**
- Verify `_write_config()` already excludes `[gates]` (done in Phase 3.2)
- Add deprecation comments to `GATES_FIELDS` in defaults.py (kept for backward compat parsing)

**Test gate:**
- `tests/unit/test_init.py::test_new_project_config_toml_has_no_operational_fields`
- All existing tests pass

**Effort:** S

### Phase 5 Exit Gate
Legacy projects migrated on first run. New projects have clean config.toml. backcompat is the bridge.

---

## Phase 6: Kill refresh_gate_commands

**Goal:** Remove the dead code. The auto-detect delta mechanism from Phase 2 is the permanent replacement.

### Step 6.1: Remove refresh_gate_commands and _patch_config_toml

**Files:** `darkfactory/setup/detect.py`

**Changes:**
- Remove `_GATE_CMD_FIELDS` constant
- Remove `_patch_config_toml()` function
- Remove `refresh_gate_commands()` function

**Verification:**
- `grep -r "refresh_gate_commands" darkfactory/` returns no hits
- `grep -r "_patch_config_toml" darkfactory/` returns no hits

**Test gate:**
- Remove or repurpose `tests/unit/test_detect_refresh.py`
- All remaining tests pass

**Effort:** S

### Step 6.2: Add "command not found" gate diagnostics

**Files:** `darkfactory/pipeline/quality_gate.py` (or the gate entry point)

**Changes:**
- Add `_check_gate_commands(config: PipelineConfig) -> list[str]` that returns warnings for empty command fields
- Call at top of `run_quality_gate()` and `run_pre_gate()`
- Log each warning at WARNING level:
  ```
  WARNING: build_cmd is empty. The Architect should declare it under runtime.commands.build.
  ```

**Test gate:**
- `tests/unit/test_quality_gate.py::test_warns_on_empty_build_cmd`
- `tests/unit/test_quality_gate.py::test_no_warning_when_commands_set`
- All existing gate tests pass

**Effort:** S

### Phase 6 Exit Gate
`refresh_gate_commands` is gone. No code writes to config.toml during pipeline runs. Missing commands produce clear diagnostics.

---

## Phase 7: Deterministic Preflight

**Goal:** Before any agent work, validate the environment contract's commands at $0 cost. Abort with clear diagnostics if they're broken.

### Step 7.1: Add preflight check function

**Files:** Create `darkfactory/pipeline/preflight.py`

**Changes:**
- `run_preflight_check(config: PipelineConfig, project_dir: Path) -> list[str]`:
  1. Load environment contract
  2. For each non-empty command, extract the binary name (split on whitespace, take first token — handles compound commands like `pnpm run build`), then validate via `which <binary>`
  3. Special handling for `cd <dir> && <cmd>` patterns: skip the `cd` prefix, extract binary from the actual command
  4. If services declared, validate Docker is running (`docker info`)
  5. Return list of error strings (empty = pass)

**Design constraints:**
- Zero cost: no container startup, no npm install, no builds — just binary existence checks
- Short timeouts (5s per check)
- Gated behind `preflight_enabled: bool = True` config flag for escape hatch

**Test gate:**
- `tests/unit/test_preflight.py::test_passes_when_all_commands_valid`
- `tests/unit/test_preflight.py::test_fails_when_binary_missing`
- `tests/unit/test_preflight.py::test_empty_commands_are_not_checked`
- `tests/unit/test_preflight.py::test_docker_daemon_check`

**Effort:** M

### Step 7.2: Wire preflight into orchestrator

**Files:** `darkfactory/pipeline/orchestrator.py`

**Changes:**
- After `refresh_config_from_environment` and before creating SpecRun, call `run_preflight_check()`
- If errors returned, raise `RuntimeError` with clear diagnostic pointing to environment contract

**Test gate:**
- `tests/unit/test_orchestrator.py::test_preflight_failure_aborts_before_agent_work`
- `tests/unit/test_orchestrator.py::test_preflight_success_continues`
- All existing orchestrator tests pass

**Risk:** Preflight must not be flaky. The `preflight_enabled` config flag is the escape hatch.

**Effort:** M

### Phase 7 Exit Gate
Stale config caught before burning agent money. Pipeline aborts with clear diagnostic. All tests pass.

---

## File Change Summary

| Phase | Files Modified | Files Created | Files Removed |
|-------|---------------|---------------|---------------|
| 1 | config.py, loader.py, runtime.py, detect.py, orchestrator.py | diagnostics.py | — |
| 2 | iteration.py | autodetect_delta.py | — |
| 3 | detect.py, init.py | — | — |
| 4 | config.py, environment.py, runtime.py, defaults.py | — | — |
| 5 | backcompat.py, init.py, defaults.py | — | — |
| 6 | detect.py, quality_gate.py | — | test_detect_refresh.py (removed or repurposed) |
| 7 | orchestrator.py | preflight.py | — |

## Rollback Strategy

Each phase leaves the system in a working state. If a phase must be rolled back:

| Phase | Rollback Cost | Risk |
|-------|--------------|------|
| 1 | Trivial — remove provenance fields | Zero behavior change |
| 2 | Low — re-add `_refresh_gate_commands_hook` call | Auto-detect module becomes dead code |
| 3 | Low — revert `_write_config` to emit `[gates]` | backcompat still handles migration |
| 4 | **Medium** — revert field additions, but existing deltas with new paths become unreadable | Must check no deltas were written with new paths |
| 5 | Low — re-add operational fields to config template | Revert backcompat stripping |
| 6 | Low — re-add `refresh_gate_commands` | |
| 7 | Trivial — remove preflight call | |

**Phase 4 is the highest-risk rollback** due to environment contract schema changes. Test thoroughly before deploying.

## Estimated Total Effort

| Phase | Effort | Steps |
|-------|--------|-------|
| 1 — Observability | S | 6 steps, all small |
| 2 — Reroute refresh | M | 2 steps |
| 3 — Deep init + delta | L | 2 steps (detection expansion is bulk of work) |
| 4 — Field coverage | M | 3 steps (can parallel with Phase 3) |
| 5 — Remove from config | M | 2 steps |
| 6 — Kill refresh | S | 2 steps |
| 7 — Preflight | M | 2 steps |

---

## Cross-Phase Integration Test

After all phases are complete, add one end-to-end integration test that verifies the full overlay precedence chain:

```
PipelineConfig defaults → config.toml (tuning only) → bootstrap delta → Architect delta → auto-detect delta
```

**Test:** `tests/integration/test_config_precedence_chain.py::test_full_overlay_chain`
- Construct all layers with overlapping values
- Assert the final resolved value for each operational field comes from the correct layer
- Assert provenance annotations match expected sources
- Assert config.toml values are NOT used for operational fields (only tuning)

This test is the canary — if it fails, the layered resolution is broken.

---

## Review Notes (Neo, 2026-04-10)

Issues found and integrated:

| # | Severity | Issue | Resolution |
|---|----------|-------|------------|
| 1 | **Bug** | Phase 4.1 adds `package_manager` to `CORE_FIELDS`, contradicting config.toml cleanup goal | Removed — only wire through `_ENV_TO_CONFIG` |
| 2 | **Bug** | Phase 5.1 strips config.toml without checking env.toml has the values — data loss on incomplete delta chains | Added safety guard: verify value exists in materialized env before stripping |
| 3 | **Bug** | Phase 2.1 auto-detect delta writer lacks rollback on `materialize()` failure | Added snapshot+rollback requirement matching `amend_environment_from_manifest` pattern |
| 4 | Design | Phase 7.1 doesn't specify binary extraction from compound commands (`pnpm run build`) | Added: split on whitespace, first token; special handling for `cd X &&` patterns |
| 5 | Design | Phase 3.1 duplicates hints.py package manager detection | Changed to import from hints.py; added bun.lockb and turbo.json detection |
| 6 | Design | Phase 3.1 defaults `server_health_path` to `/health` — too many conventions | Changed to leave empty; let Architect set explicitly |
| 7 | Design | Concurrent delta writes can race on `_next_delta_number()` | Documented as accepted limitation; EnvironmentConflict on retry is safe recovery |
| 8 | Design | Phase 5.1 `[test_server]` stripping scope was incomplete | Expanded to all operational fields; kept timeouts as tuning parameters |
| 9 | Coverage | No integration test for full overlay precedence chain | Added cross-phase integration test |
| 10 | Note | Phase 2 auto-detect can diverge from Architect intent (fill lint when Architect deliberately omitted it) | Accepted for now — WARNING log provides visibility; future: explicit "no-op" declarations |
