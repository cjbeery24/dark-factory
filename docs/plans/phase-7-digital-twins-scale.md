# Phase 7: Digital Twins & Scale — Implementation Plan

## Context

Phase 7 is the final phase of the Dark Factory production readiness roadmap. Phases 1-6 are complete, delivering: hardened foundations, CI/CD closed-loop, probabilistic satisfaction scoring, concurrent execution, observability/intelligence, and progressive deployment. Phase 7 closes the remaining gaps to achieve true Level 5 (zero human intervention from issue to production).

Phase 7 contains 12 items spanning diverse concerns. This plan breaks them into **4 sub-phases** ordered by dependency and grouped by module affinity. Sub-phases 7A, 7B, and 7C are independent and can be implemented in any order. Sub-phase 7D depends on the stability of 7A-7C.

---

## Sub-Phase 7A: Pipeline Resilience & Iteration Intelligence

**Items:** Strategy switching (3), Transient failure retry (4), Multi-model agent selection (10)

**Rationale:** All three modify how agents are invoked and how the iteration loop responds to failure. They share blast radius in `pipeline/iteration.py`, `pipeline/agents.py`, `docker/args.py`. Multi-model is a prerequisite for strategy switching (model change is one strategy). Transient retry is prerequisite for strategy switching (don't switch strategies when the real problem was a network timeout).

### Deliverables

1. **Multi-model agent selection** — Per-role model IDs via `[model.ids]` TOML section, fallback to global `model_id`
2. **Transient failure retry** — Classify infrastructure failures (OOM, network timeout, Docker daemon errors) separately from implementation failures; retry with exponential backoff + jitter
3. **Strategy switching** — On bad trajectory at iteration >= 3, auto-switch strategy (model change, fresh start) before escalating; max 2 strategy switches per spec

### Files to modify

| File | Change |
|------|--------|
| `darkfactory/models/config.py` | Add `model_ids: dict[str, str]`, `transient_retry_max: int = 2`, `transient_retry_base_s: float = 30.0`, `strategy_switch_enabled: bool = False`, `strategy_max_switches: int = 2` |
| `darkfactory/config/defaults.py` | Add `MODEL_IDS_FIELDS` section, transient retry + strategy fields to existing sections |
| `darkfactory/config/loader.py` | Handle `[model.ids]` sub-table |
| `darkfactory/docker/args.py` | `build_claude_args` reads `config.model_ids.get(role.value, config.model_id)` — line 115 |
| `darkfactory/pipeline/agents.py` | Add `_is_transient(exit_code, stderr) -> bool` classifier; separate transient retry loop (backoff+jitter) from existing `agent_retries`; pass resolved model_id to events |
| `darkfactory/pipeline/iteration.py` | When `strategy_switch_enabled` and trajectory in `_ESCALATION_TRAJECTORIES` at iter >= 3: call `_apply_strategy()` instead of escalating. Strategies: SWITCH_MODEL (use fallback model), FRESH_START (reset to iter 1 with clean state). Only escalate after `strategy_max_switches` exhausted |
| `darkfactory/scenarios/trajectory.py` | Add `StrategyAction` dataclass with `action` enum: `CONTINUE, SWITCH_MODEL, FRESH_START, ESCALATE`. Extend `strategy_recommendation()` to return `StrategyAction` (keep `__str__` for backward compat) |
| `darkfactory/models/pipeline.py` | Add `model_override: str = ""`, `strategy_switches: int = 0` to `SpecRun` |
| `darkfactory/plan/executor.py` | Wrap `run_pipeline` call with transient-failure retry at plan level |
| `darkfactory/errors.py` | Add `TransientFailure(DarkFactoryError)` |

### New test files

| File | Tests |
|------|-------|
| `tests/test_strategy_switch.py` | Trajectory -> action mapping, model override flow, fresh-start reset, max-switches guard, escalation after exhaustion |
| `tests/test_transient_retry.py` | Transient classification (OOM, network, Docker), backoff timing, jitter, plan-level retry |
| `tests/test_multi_model.py` | Per-role model selection, fallback to global, config parsing of `[model.ids]` |

**Complexity:** Large

---

## Sub-Phase 7B: Selective Regression & Quality Gate Enhancements

**Items:** Selective regression (5), Spec sanitization (9), Mutation testing integration (12)

**Rationale:** All three enhance the quality/validation pipeline. Zero dependency on 7A. Selective regression and mutation testing both touch the quality gate and regression paths. Spec sanitization is a pre-validation step. These share module affinity in `scenarios/`, `pipeline/quality_gate.py`, and `audit/`.

### Deliverables

1. **Selective regression** — `--only-affected` flag runs only scenarios whose specs' `target-files` overlap with changed files; critical for projects with 50+ shipped specs
2. **Mutation testing** — Run `mutation_cmd` as quality gate step after scenario tests; enforce `mutation_min_score` threshold
3. **Spec sanitization** — Regex-based prompt injection detection before pipeline entry; abort on high-severity injections

### Files to modify

| File | Change |
|------|--------|
| `darkfactory/state/registry.py` | Store `target_files` in `.registry-meta` during `register()`; add `read_target_files(project_dir, spec_name) -> set[str]` |
| `darkfactory/scenarios/regression.py` | Add `only_affected: list[str] | None` param; filter specs by target-file overlap with changed files |
| `darkfactory/pipeline/quality_gate.py` | Add mutation testing step: run `config.mutation_cmd`, parse exit code, add `mutation_score` to `GateResult` |
| `darkfactory/models/config.py` | Add `mutation_min_score: int = 0`, `spec_sanitization_enabled: bool = True` |
| `darkfactory/config/defaults.py` | Add mutation and sanitization fields |
| `darkfactory/pipeline/orchestrator.py` | Call `sanitize_spec()` before spec validator; abort on high-severity injection |
| `darkfactory/cli.py` | Add `--only-affected` flag to `regression` subcommand; compute changed files via `git diff --name-only` |

### New files

| File | Purpose |
|------|---------|
| `darkfactory/scenarios/coverage.py` | `affected_specs(project_dir, changed_files) -> list[str]` — cross-references changed files against registry target_files |
| `darkfactory/audit/sanitizer.py` | `sanitize_spec(text) -> SanitizeResult` — detect prompt injection patterns (system prompt overrides, tool-calling injection, barrier circumvention, encoded payloads) |
| `tests/test_selective_regression.py` | Target-file metadata storage/retrieval, affected-spec detection, `--only-affected` filtering |
| `tests/test_mutation_gate.py` | mutation_cmd execution, score parsing, gate fail on low score |
| `tests/test_sanitizer.py` | Prompt injection patterns, severity levels, false positive resistance |

**Complexity:** Medium

---

## Sub-Phase 7C: Post-Merge Monitoring & Deployment Tooling

**Items:** Post-merge rollback monitor (6), Reference deployment scripts (7), E2E deployment integration test (8)

**Rationale:** All live in `deployment/` and concern post-ship behavior. The rollback monitor consumes already-defined-but-unwired config fields (`rollback_monitor_cmd`, `rollback_window_seconds`, `rollback_poll_interval`) and the unused `emit_rollback_window_expired` event. Reference scripts provide templates that the monitor would call. The integration test exercises the full flow. Zero dependency on 7A or 7B.

### Deliverables

1. **Post-merge rollback monitor** — After merge, poll `rollback_monitor_cmd` every `rollback_poll_interval` seconds for `rollback_window_seconds` duration; trigger L1-L4 rollback cascade on breach; emit `ROLLBACK_WINDOW_EXPIRED` when window passes clean
2. **Reference deployment scripts** — Copy-paste templates for K8s/Argo Rollouts, docker-compose, and Unleash feature flags
3. **E2E deployment integration test** — Validate full post-ship flow: provision -> health -> canary -> metric -> teardown using mock scripts

### Files to modify

| File | Change |
|------|--------|
| `darkfactory/pipeline/orchestrator.py` | After successful merge in `_handle_ship()`, call `monitor_rollback_window()` if `config.rollback_monitor_cmd` is set |

### New files

| File | Purpose |
|------|---------|
| `darkfactory/deployment/monitor.py` | `monitor_rollback_window(config, spec_name, run_id, events_file, project_dir) -> bool` — polling loop with cascade trigger on breach |
| `scripts/deploy/k8s-argo/canary-shift.sh` | Template: `kubectl`/Argo Rollouts canary traffic shift with `$CANARY_PERCENT` |
| `scripts/deploy/k8s-argo/rollback.sh` | Template: Argo Rollouts abort + rollback |
| `scripts/deploy/k8s-argo/health-check.sh` | Template: Kubernetes readiness probe |
| `scripts/deploy/docker-compose/canary-shift.sh` | Template: nginx upstream weighting |
| `scripts/deploy/docker-compose/rollback.sh` | Template: docker-compose rollback |
| `scripts/deploy/docker-compose/docker-compose.deploy.yml` | Template: ephemeral environment with app + db |
| `scripts/deploy/unleash/flag-create.sh` | Template: Unleash API flag creation |
| `scripts/deploy/unleash/flag-enable.sh` | Template: Unleash API flag enable |
| `scripts/deploy/unleash/flag-disable.sh` | Template: Unleash API flag kill switch |
| `docs/DEPLOYMENT-SCRIPTS.md` | Usage guide, env var reference, config.toml examples |
| `tests/test_monitor.py` | Polling loop, breach detection, cascade trigger, clean window expiration |
| `tests/test_deploy_integration.py` | Full post_ship_pipeline flow with mock scripts |

**Complexity:** Medium

---

## Sub-Phase 7D: Digital Twins, Scale & Plan Optimization

**Items:** Digital twin framework (1), DTU behavioral validation at scale (2), Plan-level optimization (11)

**Rationale:** Items 1 and 2 are tightly coupled — the twin framework defines service mocks, and scale validation runs thousands of scenarios against them. Item 11 (plan optimization) benefits from all prior sub-phases being stable and generates its best results when it has historical performance data from 7A-7C improvements. This is the largest new subsystem.

### Deliverables

1. **Digital twin framework** — Record/replay HTTP proxy using stdlib `http.server` + `urllib.request`; deterministic replay by method+path+body-hash matching
2. **Scale validation** — Parallel scenario runner with `ThreadPoolExecutor`; throughput tracking (scenarios/hour), latency percentiles
3. **Plan optimization** — Reorder specs for minimum wall time: critical-path scheduling (longest-first for parallelizable), failure-likelihood ordering (historically-failing specs first for fast failure)

### New package

```
darkfactory/twins/
├── __init__.py
├── models.py        — TwinSpec, TwinConfig dataclasses
├── recorder.py      — HTTP proxy recorder (request/response -> JSONL)
├── replayer.py      — Deterministic replay server (JSONL -> responses)
└── manager.py       — TwinManager lifecycle (start, stop, health)
```

### Files to modify

| File | Change |
|------|--------|
| `darkfactory/models/config.py` | Add `twins: list` (TwinSpec list), `scale_concurrency: int = 4`, `scale_target_rate: int = 0` |
| `darkfactory/config/defaults.py` | Add `TWINS_FIELDS`, `SCALE_FIELDS` |
| `darkfactory/config/loader.py` | Handle `[[twins]]` TOML array-of-tables |
| `darkfactory/docker/networks.py` | Add twin containers to Docker network topology |
| `darkfactory/plan/topo_sort.py` | Accept `optimize: bool` param; call optimizer before sort |
| `darkfactory/plan/executor.py` | Wire `--optimize` flag |
| `darkfactory/cli.py` | Add `scale-test` subcommand, `--optimize` flag to `plan` |
| `darkfactory/models/events.py` | Add `SCALE_VALIDATION_START`, `SCALE_VALIDATION_COMPLETE` |

### New files

| File | Purpose |
|------|---------|
| `darkfactory/scenarios/scale.py` | `run_scale_validation()` — parallel scenario runner with throughput metrics |
| `darkfactory/plan/optimizer.py` | `optimize_plan()` — reorder specs based on historical metrics |
| `tests/test_twins.py` | Recorder/replayer round-trip, request matching, unmatched fallback |
| `tests/test_scale.py` | Parallel execution, throughput measurement, failure handling |
| `tests/test_plan_optimizer.py` | Critical-path scheduling, history-based ordering |

**Complexity:** Large

---

## Summary

| Sub-Phase | Items | Complexity | Independent? | Key Modules |
|-----------|-------|------------|-------------|-------------|
| **7A** Pipeline Resilience | 3, 4, 10 | Large | Yes | iteration.py, agents.py, docker/args.py, trajectory.py |
| **7B** Quality Gate | 5, 9, 12 | Medium | Yes | regression.py, quality_gate.py, registry.py, new sanitizer |
| **7C** Deploy Monitoring | 6, 7, 8 | Medium | Yes | deployment/monitor.py, orchestrator.py, scripts/deploy/ |
| **7D** Digital Twins | 1, 2, 11 | Large | After 7A-7C | New twins/ package, scale.py, plan/optimizer.py |

**Recommended order:** 7A -> 7B -> 7C -> 7D (though 7A/7B/7C can be parallelized)

## Verification

After each sub-phase:
1. Run full test suite: `python3 -m unittest discover -s tests`
2. Verify no regressions in existing 273+ tests
3. Run `dark-factory setup validate` to confirm environment health
4. For 7A: verify `[model.ids]` config parsing and strategy switch logs in dry-run
5. For 7B: verify `dark-factory regression --only-affected` filters correctly
6. For 7C: verify monitor polling with a mock `rollback_monitor_cmd` script
7. For 7D: verify twin recorder/replayer round-trip with a simple HTTP endpoint
