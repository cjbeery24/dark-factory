# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date (UTC):** 2026-04-07 04:04
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Dark Factory Commit:** 0793711 (Phase 3.4-3.6)
**Run ID:** 8c3c18168c204e82

---

## Pre-Run State

- Previous run state: `interrupted` at iteration 1, completed_step 1
- Git status: modified validation review files, untracked architecture/ and precheck/ dirs
- Repository reset performed: Yes (git checkout + git clean + .dark-factory artifact removal)

---

## Run 1 — Killed After Blind Scenarios

**Outcome:** Manually interrupted after scenario generation completed and implementer started.
**Total wall time through scenarios:** ~16 minutes (04:04:48 → 04:20:39)

### Phase Timings

| Phase | Start (UTC) | Wall Seconds | Exit Code | Notes |
|-------|-------------|-------------|-----------|-------|
| Spec Validator | 04:04:48 | 183.8s (~3m) | 0 | PASS, score 95, divergence LOW |
| Architect | 04:07:52 | 188.1s (~3m) | 0 | Produced manifest with 8 files |
| Scenario Architect (generate) | 04:11:01 | 299.3s (~5m) | 0 | 6 scenario files, but coverage gaps |
| Coverage Gap Detection | 04:16:01 | — | — | Missing: EC-4, EC-5, EC-6, EC-7, EC-8, EC-9 |
| Scenario Architect (coverage_regen) | 04:16:01 | 142.3s (~2.4m) | 0 | Filled gaps, 23 anchors passed |
| Smoke Test | — | — | — | 0 crashes, **27 tautological** assertions |
| Scenario Architect (repair) | 04:18:37 | 121.3s (~2m) | 0 | Repair attempted |
| **Implementer** (killed) | 04:20:39 | — | — | Killed shortly after start |

### Pipeline Stdout (key lines)

```
WARNING: config.toml runtime keys are deprecated — synthesized bootstrap delta
WARNING: spec_type missing from frontmatter for 2026-03-28-api-bootstrap; defaulting to 'feature'
WARNING: repair reduced assertions in 06-edge-cases.test.ts (25 → 23) — reverting
WARNING: scenario repair failed validation, reverting to original scenarios
```

### Scenario Output

6 scenario files, 833 total lines:

| File | Lines | Covers |
|------|-------|--------|
| 01-server-setup.test.ts | 112 | FR-1, AC-1, AC-2, EC-1, EC-2, EC-3 |
| 02-logging.test.ts | 135 | FR-2 logging |
| 03-error-handling.test.ts | 140 | FR-3 error handling |
| 04-health-endpoint.test.ts | 148 | FR-4 health checks |
| 05-cross-cutting.test.ts | 150 | Cross-cutting concerns |
| 06-edge-cases.test.ts | 148 | EC-4 through EC-9 (after regen) |

### Coverage Analysis

- Initial generation missed **6 edge cases** (EC-4 through EC-9) — all caught by coverage gap detection
- Coverage regen pass successfully filled the gaps (23 anchors confirmed)
- This is a healthy sign that the coverage enforcement loop is working

### Architecture Manifest

The architect produced a clean manifest with:
- 8 file mappings (6 create, 2 modify)
- 1 API contract (GET /health)
- 6 dependencies (fastify, pino, pino-pretty, zod, @fastify/cors, @fastify/rate-limit)
- Runtime config: pnpm, node, port 3001

### environment.toml Issue

The architect wrote `environment.toml` but left most values empty:

```toml
[commands]
build = "pnpm install && pnpm run build"
install = ""
lint = "pnpm exec eslint ."
server_health = ""
server_start = ""
test = ""
typecheck = "pnpm -r typecheck"

[server]
boot_timeout_s = 45
health_path = ""
port = 0
```

**Observation:** The manifest YAML has `server_start: "cd apps/api && pnpm dev"`, `health_path: "/health"`, `port: 3001` — but these weren't propagated to `environment.toml`. The architect populated the manifest correctly but didn't transfer values to the environment config. This could cause the quality gate to fail on server startup if the gate reads `environment.toml` for server config.

---

## Key Findings

### Issue 1: 27 Tautological Assertions (HIGH)

The smoke test detected **27 tautological assertions** across the 6 scenario files. These are assertions that always pass regardless of implementation (e.g., checking that a constant equals itself, or that a truthy value is truthy). The SA repair phase attempted to fix them but **failed validation** — it reduced the assertion count in `06-edge-cases.test.ts` from 25 to 23, which triggered the repair-reduction safeguard. All scenarios were reverted to originals.

**Impact:** The implementer would face blind scenarios with 27 trivially-passing tests, reducing the effective test coverage. The information barrier is technically maintained, but the quality of the blind testing is degraded.

**Potential improvement:** The repair phase needs a strategy beyond simple fix-and-check. When repair reduces assertions, it should try to replace tautological assertions with meaningful ones rather than removing them.

### Issue 2: environment.toml Not Populated (MEDIUM)

The architect wrote a correct manifest YAML but left `environment.toml` mostly empty (server_start, health_path, port, test command, etc.). The environment.toml is what downstream agents read for runtime configuration. Empty `server_start` and `port=0` would likely cause quality gate failures.

**Root cause hypothesis:** The architect agent may be writing the manifest YAML first and not transferring the runtime section to environment.toml, or the environment.toml template defaults are overriding the manifest values.

### Issue 3: Deprecation Warnings (LOW)

Two deprecation warnings surfaced:
1. `config.toml runtime keys are deprecated` — a bootstrap delta was synthesized automatically
2. `spec_type missing from frontmatter` — defaulted to 'feature'

Neither is blocking, but they indicate the spec and config are using older formats.

### Issue 4: Scenario Coverage Gap Detection Works Well (POSITIVE)

The coverage gap detection correctly identified 6 missing edge cases (EC-4 through EC-9) and the regen pass successfully filled them, achieving 23 anchors. This is the coverage enforcement loop working as designed.

---

## Metrics Summary

| Metric | Value |
|--------|-------|
| Total pipeline wall time (through scenarios) | ~16 min |
| Spec validator wall time | 183.8s |
| Architect wall time | 188.1s |
| Scenario generation (total, all phases) | 562.9s |
| Scenario files generated | 6 |
| Total scenario lines | 833 |
| Coverage anchors | 23 |
| Tautological assertions | 27 |
| Repair outcome | Failed (reverted) |
| Model used | claude-sonnet-4-20250514 |
