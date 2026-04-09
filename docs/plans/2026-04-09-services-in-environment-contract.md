# Phase 4 — Services & test_base_url in Environment Contract

**Status:** Not started
**Created:** 2026-04-09
**Consensus:** Neo + Liotta signed off after 8-scenario stress test
**Triggered by:** Run 14 ESCALATED — `database-foundation` spec needs PostgreSQL sidecar; legacy bootstrap-only gate blocks non-bootstrap specs from declaring services
**Goal:** Move services and test_base_url out of the legacy bridge path and into the environment contract with proper delta ops, dependency tracking, and topological startup ordering

---

## Architectural decisions (locked)

1. **Open adds.** Any spec type (bootstrap, feature, bugfix, refactor) can add new services. The bootstrap-only restriction was stress-tested against 8 common infrastructure scenarios and broke on 5 of them (Redis cache layer, search service, queue worker, test fixtures, monitoring sidecars).
2. **Guarded modifications.** Any spec type can modify an existing service, but the delta system emits a dependency warning listing shipped specs that reference the service (via `services_at_ship` in shipped.jsonl).
3. **Warned removals.** Any spec type can remove a service, with a dependency warning listing all shipped specs that included it. The warning is non-blocking — the human wrote the spec, so the intent is presumed correct — but it's logged and included in the Oracle's context.
4. **Delta ops are explicit.** `set` (create or overwrite), `modify` (overwrite only if non-default), `remove` (reset to default). Same ops already defined in `deltas.py:_VALID_OPS`.
5. **3-level dotted paths.** Services use `services.<name>.<field>` (e.g., `services.postgres.image`). Existing 2-level paths are unaffected.
6. **test_base_url migrates alongside services.** Same phase, same pattern — it becomes `server.test_base_url` in the environment contract.
7. **Delete all legacy code.** `bridge_unmodeled_runtime_fields` and the legacy services gate in orchestrator.py are deleted entirely, not deprecated.
8. **Hash-stable serialization.** `canonical_dump` MUST skip the `[services]` section entirely when `services` is empty, and MUST NOT emit `test_base_url` for existing contracts that predate this change. A `SCHEMA_VERSION` bump (1 → 2) gates the new fields.

---

## Critical constraint: hash stability across schema change

Adding `test_base_url` to `ServerConfig` or `services` to `EnvironmentContract` changes `canonical_dump` output, which breaks every existing delta chain — the first delta's `parent_hash` was computed against the old empty-contract hash.

**Resolution: SCHEMA_VERSION bump (1 → 2) with conditional serialization.**

- `canonical_dump` version 1 (existing): emits the 4 known sections, no `test_base_url`, no `services`
- `canonical_dump` version 2 (new): adds `test_base_url` under `[server]`, adds `[services.<name>]` sub-tables
- Empty `services: {}` is NEVER serialized — hash of `EnvironmentContract()` is identical in v1 and v2
- `test_base_url = ""` is NEVER serialized (only non-default fields in new sections get emitted)
- Existing delta files (schema_version=1) continue to fold with v1 hashing
- New deltas use schema_version=2 with v2 hashing
- The transition delta (first Phase 4 delta in any project) computes parent_hash with v1, applies with v2
- `fold_deltas` detects the version transition and switches hash computation mid-fold

This is the single most important correctness constraint in the entire plan.

---

## Priority ordering (from 8-scenario analysis)

| Priority | Task | Rationale |
|----------|------|-----------|
| P0 | Remove bootstrap-only gate for services | Currently blocks 5 of 8 common infrastructure scenarios |
| P1 | Services in EnvironmentContract with delta ops | Core schema extension — everything else depends on this |
| P1.5 | Hash stability migration | Must land with or before P1; breaks all existing projects otherwise |
| P2 | `services_at_ship` tracking in shipped.jsonl | Powers dependency warnings for modify/remove |
| P3 | `depends_on` + topological sort in gate startup | Required for services with ordering deps (app->db) |
| P3.5 | Services for non-HTTP/Journey gates | Quality gate currently skips services for unit-test-only scenarios |
| P4 | `test_only` flag for test fixtures | Distinguishes prod services from test-only sidecars |
| P5 | Spec-validator early detection of missing service deps | Catches missing deps before architect burns tokens |
| P6 | Delete legacy bridge code | Final cleanup after all paths verified |
| Future | `build` alternative to `image` (project-local services) | Deferred until a real use case appears |
| Future | Resource hints (`memory_limit`, `shm_size`) | Deferred — current Docker defaults work for dev/CI |

---

## Phase 4.1 — Schema extension + hash migration (P0 + P1 + P1.5)

**Goal:** Services and test_base_url become first-class environment contract fields with full delta support. Existing delta chains remain valid.

### 4.1a ServiceEntry dataclass + EnvironmentContract.services

**File:** `darkfactory/architecture/environment.py`

Add `ServiceEntry` dataclass:
```python
@dataclass
class ServiceEntry:
    image: str = ""
    env: dict[str, str] = field(default_factory=dict)
    health_cmd: str = ""
    health_timeout: int = 30
    inject_env: dict[str, str] = field(default_factory=dict)
    test_only: bool = False          # P4: test fixtures (Mailhog, Localstack)
    depends_on: list[str] = field(default_factory=list)  # P3: ordering deps
```

Add to `EnvironmentContract`:
```python
@dataclass
class EnvironmentContract:
    runtime: Runtime = field(default_factory=Runtime)
    commands: Commands = field(default_factory=Commands)
    server: ServerConfig = field(default_factory=ServerConfig)
    layout: Layout = field(default_factory=Layout)
    services: dict[str, ServiceEntry] = field(default_factory=dict)
```

Add `test_base_url` to `ServerConfig`:
```python
@dataclass
class ServerConfig:
    port: int = 0
    health_path: str = ""
    boot_timeout_s: int = 0
    test_base_url: str = ""
```

### 4.1b PATH_TYPES + ALLOWED_PATHS for services

**File:** `darkfactory/architecture/environment.py`

Services use a dynamic allowlist pattern since service names aren't known statically. Add a regex-based allowlist entry alongside the static paths:

```python
# Static paths (existing + test_base_url)
ALLOWED_PATHS: frozenset[str] = frozenset({...existing..., "server.test_base_url"})

# Dynamic path pattern for services (new)
import re
_SERVICE_PATH_RE = re.compile(
    r"^services\.[a-z0-9]([a-z0-9-]*[a-z0-9])?\."
    r"(image|env|health_cmd|health_timeout|inject_env|test_only|depends_on)$"
)

def is_allowed_path(path: str) -> bool:
    """Check if a dotted path is in the static or dynamic allowlist."""
    return path in ALLOWED_PATHS or _SERVICE_PATH_RE.match(path) is not None
```

Update `PATH_TYPES` with a lookup function for dynamic paths:
```python
_SERVICE_FIELD_TYPES: dict[str, type] = {
    "image": str,
    "env": dict,
    "health_cmd": str,
    "health_timeout": int,
    "inject_env": dict,
    "test_only": bool,
    "depends_on": list,
}
```

### 4.1c Hash-stable canonical_dump (CRITICAL)

**File:** `darkfactory/architecture/environment.py`

`canonical_dump` must produce byte-identical output for contracts that predate Phase 4:

1. **Skip `services` when empty.** If `contract.services` is `{}`, do NOT emit a `[services]` header. This ensures `hash_view(EnvironmentContract())` is unchanged.
2. **Skip `test_base_url` when default.** The `[server]` section must NOT emit `test_base_url = ""`. Only emit `test_base_url` when it has a non-empty value.
3. **Services serialization (when non-empty):** For each service name (sorted): `[services.<name>]` sub-header, then sorted key=value pairs from `ServiceEntry` fields. `dict` fields (`env`, `inject_env`) use nested TOML tables (`[services.postgres.env]`). Empty dicts and default-value fields are skipped.

The general principle: new fields are invisible in serialization until they carry non-default values. This guarantees `hash_view` stability for all existing contracts.

### 4.1d SCHEMA_VERSION bump in deltas.py

**File:** `darkfactory/architecture/deltas.py`

- Bump `SCHEMA_VERSION` from 1 to 2
- `load_delta` accepts both schema_version 1 and 2
- Schema version 1 deltas: validated against `ALLOWED_PATHS` only (no service paths)
- Schema version 2 deltas: validated against `is_allowed_path()` (includes service regex)
- `fold_deltas` handles mixed-version chains: v1 deltas use v1 hash computation, v2 deltas use v2 hash computation
- The transition is seamless because 4.1c guarantees `canonical_dump` is backward-compatible for contracts without services

### 4.1e Delta ops for 3-level paths

**File:** `darkfactory/architecture/deltas.py`

`_split_path` currently returns `(section, key)` for 2-level paths. Extend to handle `services.<name>.<field>`:

- New `_split_path` returns `(section, sub_key, field)` for 3-level paths, `(section, key, None)` for 2-level
- `_apply_op` routes based on section:
  - 2-level (non-services): existing behavior (setattr on dataclass)
  - 3-level (services): resolve `contract.services[name].field`
- `set` on a service field auto-creates the `ServiceEntry` if missing from the dict
- `remove` on a service field resets it to default; `remove` on `services.<name>` (2-level) removes the entire entry from the dict
- `load_delta` validates service paths against `_SERVICE_PATH_RE`

**Hard dependency:** 4.1a must be complete (ServiceEntry exists) before this task.

### 4.1f validate_environment for service paths

**File:** `darkfactory/architecture/environment.py`

Extend `validate_environment` to handle service paths:
- Use `is_allowed_path()` instead of `path in ALLOWED_PATHS`
- Type-check service field values against `_SERVICE_FIELD_TYPES`
- Services are NOT in `BOOTSTRAP_ONLY_PATHS` — any spec type can set them

### 4.1g _contract_from_toml for services (CRITICAL — Neo review finding)

**File:** `darkfactory/architecture/runtime.py` lines 120-169

`_contract_from_toml` currently hardcodes 4 sections and uses `_section()` helper that assumes flat dataclass sections. After Phase 4.1, `environment.toml` will contain `[services.postgres]` sub-tables. Without updating this function, **services written to disk are silently discarded on read-back**.

Add service parsing:
```python
services: dict[str, ServiceEntry] = {}
raw_services = raw.get("services", {})
if isinstance(raw_services, dict):
    for svc_name, svc_raw in raw_services.items():
        if isinstance(svc_raw, dict):
            services[svc_name] = _section(ServiceEntry, svc_raw)

return EnvironmentContract(
    runtime=_section(Runtime, raw.get("runtime", {})),
    commands=_section(Commands, raw.get("commands", {})),
    server=_section(ServerConfig, raw.get("server", {})),
    layout=_section(Layout, raw.get("layout", {})),
    services=services,
)
```

Note: `_section()` helper works for `ServiceEntry` because it's a flat dataclass with scalar + list + dict fields. However, the `dict[str, str]` fields (`env`, `inject_env`) need additional handling since `_section` currently only knows about `bool`, `int`, `str`, and `list` types. Either extend `_section` to handle `dict` fields, or parse them separately.

### 4.1h Remove bootstrap-only gate for services (P0)

**File:** `darkfactory/pipeline/orchestrator.py` lines 1194-1210

Delete the legacy fallback gate entirely. Services flow through the environment contract delta system, which has no bootstrap-only restriction for service paths.

### 4.1i test_base_url in environment contract

**Files:** `environment.py`, `amend.py`, `runtime.py`

- Add `server.test_base_url` to `ALLOWED_PATHS` and `PATH_TYPES`
- `_build_ops` in `amend.py` maps `rt.test_base_url` -> `"server.test_base_url"` amendment op
- Add `("server", "test_base_url", "test_base_url")` to `_ENV_TO_CONFIG` in `runtime.py:176` (NOT orchestrator.py — Neo review finding: `refresh_config_from_environment` lives in `runtime.py`)

### 4.1 exit criteria
- [ ] `ServiceEntry` dataclass exists with all fields
- [ ] `EnvironmentContract.services` is `dict[str, ServiceEntry]`
- [ ] `canonical_dump` produces deterministic output for services
- [ ] **Hash stability test:** `hash_view(EnvironmentContract())` unchanged after code change
- [ ] **Round-trip test:** `canonical_dump` -> parse -> `canonical_dump` identical for contracts with services
- [ ] `validate_environment` accepts service paths, rejects invalid ones
- [ ] `_apply_op` handles 3-level paths (`services.<name>.<field>`)
- [ ] `_contract_from_toml` correctly parses `[services.<name>]` sub-tables
- [ ] `server.test_base_url` is a valid environment contract path
- [ ] `_ENV_TO_CONFIG` includes `test_base_url` mapping
- [ ] `SCHEMA_VERSION` = 2; `load_delta` accepts v1 and v2 deltas
- [ ] **Mixed delta chain test:** fold chain with v1 deltas followed by v2 service deltas
- [ ] Legacy services gate deleted from orchestrator.py
- [ ] All existing tests pass (no regressions)
- [ ] New unit tests for service delta ops (set, modify, remove at field and entry level)
- [ ] Empty-services serialization test: contract with `services={}` produces identical hash to no-services contract

---

## Phase 4.2 — Manifest-to-delta pipeline for services (P1 continued)

**Goal:** Architect's manifest services flow through the delta system instead of the legacy bridge.

### 4.2a amend.py: _build_ops emits service delta ops

**File:** `darkfactory/architecture/amend.py`

Currently `_build_ops` reads `rt.amendment_ops` (a flat dict). Extend to also emit service ops from `rt.services`:

For each `RuntimeServiceDecl` in `rt.services`, emit:
- `("services.<name>.image", value)` — always required
- `("services.<name>.env", value)` — if non-empty
- `("services.<name>.health_cmd", value)` — if non-empty
- `("services.<name>.health_timeout", value)` — if non-default (30)
- `("services.<name>.inject_env", value)` — if non-empty

**Op selection (Neo review finding):** `_render_delta` currently hardcodes `"op": "set"` for all ops. For Phase 4.2, `set` is correct for new services (create-or-overwrite). However, Phase 4.3b dependency warnings trigger on `modify`/`remove` ops. To make warnings work, `_build_ops` must detect when a service already exists in the current folded view and emit `"modify"` instead of `"set"`. This means `_build_ops` (or `_render_delta`) needs access to the current environment view. Options:
- a) Pass the current `EnvironmentContract` to `_build_ops` — it compares each service op against the current view
- b) Add a post-processing step in `amend_environment_from_manifest` that upgrades `set` -> `modify` based on current view
- c) Defer `modify` detection to Phase 4.3 when warnings are implemented

Recommended: (b) — keeps `_build_ops` pure, moves the view-aware logic to `amend_environment_from_manifest` where the view is already available.

### 4.2b refresh_config_from_environment reads services

**File:** `darkfactory/architecture/runtime.py` (NOT orchestrator.py — Neo review finding)

`refresh_config_from_environment` (runtime.py:189) currently maps env contract fields to PipelineConfig. Add service mapping after the existing `_ENV_TO_CONFIG` loop:

```python
if env.services:
    config.services = [
        ServiceDef(
            name=name,
            image=entry.image,
            env=dict(entry.env),
            health_cmd=entry.health_cmd,
            health_timeout=entry.health_timeout,
            inject_env=dict(entry.inject_env),
        )
        for name, entry in sorted(env.services.items())
        if entry.image  # skip entries with no image (removed services)
    ]
```

### 4.2c Delete bridge_unmodeled_runtime_fields

**File:** `darkfactory/pipeline/orchestrator.py` lines 904-940

Delete `bridge_unmodeled_runtime_fields` entirely. Remove the call site at `_step_architecture_lock` (line 1211). All fields now flow through the environment contract.

**Test migration (Neo review finding):** `tests/unit/test_manifest.py` lines 650-716 contain 4 tests that directly import and test `bridge_unmodeled_runtime_fields`. These tests must be:
- Deleted (the bridge tests) 
- Replaced with tests that verify `refresh_config_from_environment` populates `config.services` from the environment contract

**Ordering dependency:** 4.2c MUST happen AFTER 4.2b. Between 4.1h (delete legacy gate) and 4.2b, the bridge is still alive and handles service population. Deleting the bridge before `refresh_config_from_environment` reads services would leave `config.services` empty.

### 4.2 exit criteria
- [ ] Architect manifest with services produces correct environment delta
- [ ] `refresh_config_from_environment` populates `config.services` from contract
- [ ] `bridge_unmodeled_runtime_fields` deleted
- [ ] Call site at `_step_architecture_lock` removed
- [ ] 4 bridge tests in `test_manifest.py` deleted and replaced
- [ ] Pipeline run with services-in-manifest produces correct environment.toml
- [ ] Pipeline run without services still works (no regression)

---

## Phase 4.3 — Dependency tracking (P2 + P3 + P3.5)

**Goal:** Track service dependencies at ship time and use them for warnings and startup ordering.

### 4.3a services_at_ship in shipped.jsonl

**File:** `darkfactory/plan/state.py` (ShippedSpec lives here, not post_ship_archive.py)

Add `services_at_ship: list[str]` to `ShippedSpec`. Populated from the environment contract's `services` dict keys at ship time.

**Backward compat (Neo review finding):** `ShippedSpec.from_dict` (state.py:84) currently does `raw["env_hash_at_ship"]` — hard key access. The new field must use `.get("services_at_ship", [])` so existing JSONL rows (which predate Phase 4) don't crash on parse. `__post_init__` should accept empty list as valid.

**Call site:** `record_shipped_spec` in `post_ship_archive.py:159` must be updated to pass `services_at_ship` to the `ShippedSpec` constructor. The service names come from the environment contract at ship time.

### 4.3b Dependency warnings on modify/remove

**File:** `darkfactory/architecture/amend.py` or new `darkfactory/architecture/service_deps.py`

When a delta contains `modify` or `remove` on a service path:
1. Read `shipped.jsonl` via `iter_shipped` in `state.py`
2. Find all shipped specs where `services_at_ship` includes the affected service name
3. Emit a warning (logged + returned to caller) listing the affected specs
4. Warning is non-blocking — the spec author decided to make this change

The warning flows into the Oracle's context so the final ship decision accounts for potential downstream breakage.

**Prerequisite:** Phase 4.2a must implement `modify`/`remove` op detection (see 4.2a op selection discussion). If `_build_ops` only emits `set`, the warning system is inert.

### 4.3c depends_on + topological sort in quality gate

**File:** `darkfactory/pipeline/quality_gate.py` lines 980-1012

Currently `_start_service_containers` starts services sequentially in list order. Replace with topological sort based on `depends_on`:

1. Build dependency graph from `ServiceDef.depends_on` fields (need to add `depends_on` to `ServiceDef` in `config.py` and thread from `ServiceEntry`)
2. Detect cycles -> raise `ConfigError`
3. Start in topological order (deps first)
4. Existing health-check-per-service behavior unchanged

### 4.3d Services for non-HTTP/Journey quality gates (Neo review finding — CRITICAL for Run 14)

**File:** `darkfactory/pipeline/quality_gate.py` lines 379, 639

The quality gate currently only starts services when `formats.has_http or formats.has_journey`:
```python
_needs_services = bool(config.services) and (formats.has_http or formats.has_journey)
```

The Run 14 `database-foundation` spec may need PostgreSQL for unit tests (Format C/D native tests) that do NOT trigger HTTP/Journey format detection. If so, services won't start even after Phase 4, and the 37% plateau persists.

**Fix:** Start services for ALL gate runs when `config.services` is non-empty, not just HTTP/Journey. The `_needs_services` guard should be:
```python
_needs_services = bool(config.services)
```

Services are cheap to start (a few seconds per sidecar) and harmless if unused. The current guard exists because services were historically only relevant to HTTP server testing, but database services change that assumption entirely.

**Verification:** This is a prerequisite for the Phase 4.5b verification run. If this isn't fixed, database-foundation will still fail at 37%.

### 4.3 exit criteria
- [ ] `ShippedSpec` has `services_at_ship` field with backward-compat `from_dict`
- [ ] `record_shipped_spec` passes `services_at_ship` to constructor
- [ ] Modify/remove delta on existing service emits dependency warning
- [ ] Warning includes list of affected shipped specs
- [ ] `ServiceDef` in `config.py` has `depends_on` field
- [ ] `_start_service_containers` uses topological sort
- [ ] Cycle detection raises clear error
- [ ] Services without `depends_on` start in name-sorted order (deterministic)
- [ ] Services start for ALL gate runs, not just HTTP/Journey
- [ ] **Backward compat test:** parse JSONL row without `services_at_ship`
- [ ] **Service startup for unit-test-only scenarios:** test that services start when only Format C/D scenarios exist

---

## Phase 4.4 — Test fixtures and early validation (P4 + P5)

### 4.4a test_only flag

**File:** `darkfactory/architecture/environment.py` (ServiceEntry already has the field from 4.1a)

Semantics:
- `test_only = true` services are started by the quality gate but excluded from deployment manifests (Phase 6)
- Examples: Mailhog (email capture), Localstack (AWS mock), Testcontainers patterns
- The flag is purely informational in Phase 4; Phase 6 deployment acts on it

### 4.4b Spec-validator early detection

**File:** `darkfactory/spec/precheck.py` or `darkfactory/spec/validator.py`

Before the architect runs, the spec validator can check:
1. Does the spec reference services (PostgreSQL, Redis, etc.) that aren't in the current environment contract?
2. If so, does the spec declare them in its runtime requirements?
3. Advisory finding: "Spec references PostgreSQL but no service declaration found. The architect will need to declare this service."

This is advisory, not blocking — the architect might add the service. But it catches the case where a feature spec assumes a service exists that was never bootstrapped.

### 4.4 exit criteria
- [ ] `test_only` flag serializes correctly in canonical_dump (only when true)
- [ ] Spec validator emits advisory finding for undeclared service references
- [ ] Advisory finding appears in precheck JSON

---

## Phase 4.5 — Cleanup and verification

### 4.5a Remove all Phase 4 TODO comments

**Files:**
- `amend.py:14-17` — "Fields without an environment-contract counterpart (services, test_base_url)..."
- `manifest.py:119-123` — "Services and test_base_url remain flat top-level keys..."
- `orchestrator.py:904-940` — already deleted in 4.2c
- `orchestrator.py:1194-1210` — already deleted in 4.1h

Grep for `Phase 4` and remove all "pending Phase 4" / "until Phase 4" comments.

### 4.5b Verification run

Re-run `2026-03-28-database-foundation` against budget-brain-v2. This spec requires PostgreSQL as a service. Expected outcome:
- Architect declares `services.postgres` in manifest runtime block
- Delta system records it in environment.toml
- Quality gate starts PostgreSQL sidecar from the contract (including for unit tests, per 4.3d)
- Tests that need PostgreSQL can connect
- Pass rate should exceed 37% (the Run 14 plateau)

### 4.5 exit criteria
- [ ] No `Phase 4` TODO/pending comments remain in codebase
- [ ] database-foundation spec progresses past 37% plateau
- [ ] Environment.toml contains `[services.postgres]` section after architect run

---

## Files changed (summary)

| File | Changes |
|------|---------|
| `darkfactory/architecture/environment.py` | Add `ServiceEntry`, `services` dict to `EnvironmentContract`, `test_base_url` to `ServerConfig`, dynamic path allowlist (`is_allowed_path`, `_SERVICE_PATH_RE`), extended `validate_environment`, hash-stable `canonical_dump` |
| `darkfactory/architecture/deltas.py` | `SCHEMA_VERSION` 1->2, mixed-version chain support, 3-level path support in `_apply_op`/`_split_path`/`load_delta` |
| `darkfactory/architecture/amend.py` | `_build_ops` emits service delta ops, `modify` detection for existing services, remove Phase 4 TODO comments |
| `darkfactory/architecture/runtime.py` | `_contract_from_toml` parses `[services.<name>]`, add `test_base_url` to `_ENV_TO_CONFIG`, `refresh_config_from_environment` populates `config.services` |
| `darkfactory/pipeline/orchestrator.py` | Delete `bridge_unmodeled_runtime_fields` (lines 904-940), delete legacy services gate (lines 1194-1210), remove call site at line 1211 |
| `darkfactory/pipeline/quality_gate.py` | Topological sort in `_start_service_containers`, remove HTTP/Journey guard on `_needs_services` |
| `darkfactory/plan/state.py` | `services_at_ship` field in `ShippedSpec`, backward-compat `from_dict` |
| `darkfactory/pipeline/post_ship_archive.py` | `record_shipped_spec` passes `services_at_ship` |
| `darkfactory/spec/manifest.py` | Update comments (services now have env-contract counterpart) |
| `darkfactory/models/config.py` | Add `depends_on: list[str]` to `ServiceDef` |
| `darkfactory/spec/precheck.py` | Advisory finding for undeclared service references |
| `tests/unit/test_manifest.py` | Delete 4 `bridge_unmodeled_runtime_fields` tests (lines 650-716), add contract-based service tests |
| Tests (new) | Service delta ops, canonical_dump with services, hash stability regression, round-trip, empty-services serialization, topological sort, dependency warnings, service-path validation, mixed delta chain, backward-compat JSONL parse, unit-test-only service startup |

---

## Confirmed no-change files

These files were audited and require no changes (Neo review items 1g, 1h):

| File | Reason |
|------|--------|
| `darkfactory/spec/manifest.py` (`format_manifest_for_sa`) | Reads `rt.services` from `RuntimeDecl` — still populated by `_parse_runtime`, no change needed |
| `darkfactory/spec/manifest.py` (`validate_manifest_scope`) | Reads `manifest.runtime.services` — still populated by `_parse_runtime`, no change needed |
| `darkfactory/docker/args.py` | Reads `config.services` for `no_proxy` — works correctly after `refresh_config_from_environment` populates it, ordering guaranteed by `step_begin` |

---

## Smoke probe interaction (Neo review finding)

The plan runner's smoke probe (`probe_prior_ships`) reads `shipped.jsonl` and calls `fold_to_hash` to reconstruct prior contracts at their `env_hash_at_ship`. After Phase 4.1c changes `canonical_dump`, existing `env_hash_at_ship` values computed under v1 must still resolve correctly.

**Guaranteed by design:** Phase 4.1c ensures `canonical_dump` is byte-identical for contracts without services or non-default `test_base_url`. Since prior-shipped specs had no services in the contract (they used the bridge), their hashes are computed against contracts with `services={}` and `test_base_url=""`, which produce identical output in v1 and v2. No migration needed for existing `env_hash_at_ship` values.

**Test:** Add explicit regression test that `fold_to_hash` with a pre-Phase-4 `env_hash_at_ship` still succeeds after the code change.

---

## Design decisions deferred

| Decision | Reason | Revisit when |
|----------|--------|-------------|
| `build` field (project-local service images) | No use case yet — all current services use public images | A spec needs a custom sidecar built from project source |
| Resource hints (`memory_limit`, `shm_size`) | Docker defaults work for dev/CI scale | A service OOMs or needs shared memory (e.g., PostgreSQL) |
| Service networking beyond `--network-alias` | Current single-network model works | Multi-network isolation needed between services |
| Service volume mounts | No persistent data needed in CI/test | A service needs seed data or persistent state across gate runs |
| Warm service pool (reuse across iterations) | Sequential iteration model doesn't benefit | Pipeline supports parallel spec execution |
