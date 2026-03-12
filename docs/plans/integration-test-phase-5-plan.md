# Integration Tests — Phase 5: Deferred Suites Implementation Plan

## Context

The first 4 phases implemented 242 integration tests across 13 test files covering
subsystem-level concerns (config, state, events, scenarios, barriers, docker args, git,
plan parsing, cost/budget, trust/autonomy, notifications, cleanup/signals, CLI basics).

This plan covers everything that was deferred: full pipeline orchestration, iteration
loop, quality gate chain, deployment pipeline, Docker execution, and gaps in partially
implemented suites.

---

## What's Deferred (Inventory)

### Entire suites not yet implemented

| Suite | File | Tests planned | Core dependency |
|-------|------|--------------|-----------------|
| 2 | `test_pipeline_flow.py` | 14 | `mock_invoke_agent` fixture, state + events real |
| 3 | `test_iteration_loop.py` | 10 | `mock_invoke_agent` fixture, trajectory + strategy real |
| 7 | `test_docker_execution.py` | 9 | Real Docker daemon |
| 9 | `test_quality_gate_chain.py` | 11 | `mock_execute_container` fixture, real subprocess for pytest/ruff/mypy |
| 12 | `test_deployment_pipeline.py` | 10 | `mock_run_command` fixture, time mocking |

### Gaps in partially implemented suites

| Suite | File | Missing tests | Dependency |
|-------|------|--------------|------------|
| 1 | `test_cli_e2e.py` | ~10 commands (validate, run, gate, scenarios, new-spec, deploy, scale-test, integrate, calibrate-cost, setup validate) | Mock pipeline for `run`/`validate`/`gate`/`scenarios`/`deploy` |
| 6 | `test_barrier_enforcement.py` | 5 Docker-based barrier tests | Real Docker |
| 10 | `test_scenario_lifecycle.py` | 4 tests (run with real pytest, multi-run, regression, coverage) | Fixture project + real subprocess |
| 11 | `test_plan_execution.py` | 6 tests (concurrent dispatch, checkpoint, failure propagation, governor) | `mock_run_pipeline` fixture |
| 14 | `test_git_workflow.py` | 2 tests (Node/Go detection) | Minimal |
| 15 | `test_cost_budget.py` | 3 tests (accumulation across iterations, calibration, cost events) | Pipeline-level mocking |

### Infrastructure not yet created

- `tests/integration/Dockerfile.test` — minimal Alpine image for Docker tests
- `.github/workflows/integration-tests.yml` — CI workflow
- Dev dependencies: `pytest-xdist`, `responses`, `freezegun`

---

## Implementation Phases

### Phase 5A: Mock Infrastructure & Fixtures

**Goal:** Build the shared mock infrastructure that Suites 2, 3, 9, 11, 12, and CLI
gaps all depend on. This is the critical enabler for everything else.

**Why first:** Every deferred pipeline-level test needs `mock_invoke_agent` or
`mock_run_pipeline`. Building this once, correctly, unblocks 5 suites.

#### 5A.1: Core mock fixtures in conftest.py

Add to `tests/integration/conftest.py`:

```
mock_invoke_agent(responses: list[AgentResult])
```
- Patches `darkfactory.pipeline.agents.invoke_agent`
- Each call pops the next `AgentResult` from the sequence
- Raises if called more times than expected (catches orchestrator bugs)
- Records call args for assertion: `mock.calls` list of `(role, prompt, kwargs)`

```
mock_run_pipeline(exit_codes: list[int])
```
- Patches `darkfactory.pipeline.orchestrator.run_pipeline`
- Returns exit codes in sequence (for plan executor tests)
- Records call args

```
mock_execute_container(results: list[RunResult])
```
- Patches `darkfactory.docker.containers.execute_container`
- Returns RunResult objects in sequence
- For quality gate tests that need Docker-level mocking

```
mock_run_command(results: list[CompletedProcess])
```
- Patches `darkfactory.deployment.command.run_command`
- Returns CompletedProcess in sequence with configurable returncode/stdout
- For deployment tests (canary_shift_cmd, rollback_cmd, flag_cmd, etc.)

```
agent_result_factory(**overrides) -> AgentResult
```
- Convenience builder with sensible defaults for each role
- `agent_result_factory(role=AgentRole.ORACLE, output="SHIP\nAll good.")` etc.

```
spec_run_factory(**overrides) -> SpecRun
```
- Builds a SpecRun with sensible defaults
- Supports setting iteration count, status, iterations list

```
gate_result_factory(**overrides) -> GateResult
```
- Builds GateResult with configurable pass/fail/total counts

#### 5A.2: Realistic agent output strings

```
mock_agent_outputs() -> dict[str, str]
```
Returns a dict with keys for every agent role's typical output:
- `validator`: "VALIDATED — all sections present, score 100/100"
- `scenario_architect`: "Generated 10 scenarios across 4 categories"
- `implementer`: "Implemented FR-1, FR-2, FR-3. Files changed: src/calculator.py"
- `quality_gate_pass`: scenario-counts.env format with PASSED=10, FAILED=0, TOTAL=10
- `quality_gate_fail_build`: exit code 2 output
- `reviewer_approve`: "APPROVE — implementation meets spec requirements"
- `reviewer_reject`: "REJECT — missing FR-3 implementation"
- `oracle_ship`: "SHIP\nAll functional requirements satisfied."
- `oracle_no_ship`: "NO-SHIP\n\n## Feedback\n- FR-3: not implemented\n\n## Section References\n- FR-3"
- `escalation`: "Escalation analysis: 3 iterations exhausted"
- `evaluator`: "PASS — all health checks pass, response times within bounds"

**Tests for Phase 5A:**
- `test_mock_invoke_agent_pops_sequentially`
- `test_mock_invoke_agent_raises_on_overrun`
- `test_mock_invoke_agent_records_calls`
- `test_agent_result_factory_defaults`
- `test_spec_run_factory_defaults`

**Estimated test count:** 5 (meta-tests for fixtures)

---

### Phase 5B: Pipeline Flow (Suite 2)

**Goal:** Test the 7-step pipeline orchestrator (`run_pipeline`) with real state,
events, config, and audit — only agent invocations mocked.

**Mock boundary:** `invoke_agent` mocked via `mock_invoke_agent`. Everything else
(state writes, event emission, config resolution, trust scoring, cost tracking) is real.

**Key source:** `darkfactory/pipeline/orchestrator.py` — `run_pipeline()`

#### Tests

| # | Test | Mock setup | Validates |
|---|------|-----------|-----------|
| 1 | `test_full_pipeline_ship_first_iteration` | All agents succeed, oracle SHIP | Exit 0, state=SHIPPED, events contain SPEC_START→SPEC_SHIPPED, 7 agent invocations in order (validator, SA, implementer, gate, reviewer, oracle) |
| 2 | `test_pipeline_no_ship_then_ship` | Iter 1: oracle NO-SHIP. Iter 2: oracle SHIP | Exit 0, state shows 2 iterations, feedback file written after iter 1, implementer prompt in iter 2 contains feedback |
| 3 | `test_pipeline_max_iterations_escalation` | All oracles NO-SHIP (3 iterations) | Exit 2, state=ESCALATED, escalation agent invoked, `notify_escalated` called |
| 4 | `test_pipeline_build_failure_hard_abort` | Quality gate returns exit_code=2 (build fail) | Exit 1, state=FAILED, no reviewer/oracle invoked, only 3 agent calls (validator, SA, implementer) |
| 5 | `test_pipeline_resume_from_checkpoint` | Pre-write state with completed_step=2 (scenarios done). Mock iter agents only | Starts from step 3, validator and SA not invoked, implementer is first call |
| 6 | `test_pipeline_dry_run` | `dry_run=True` | Exit 0, no agents invoked, no state file written, events file empty |
| 7 | `test_pipeline_event_sequence` | Full ship run | Events file contains events in order: SPEC_START, AGENT_START(validator), AGENT_COMPLETED, ..., SPEC_SHIPPED. Timestamps monotonically increasing |
| 8 | `test_pipeline_audit_trail` | Full ship run | Audit directory has log entries for every agent invocation |
| 9 | `test_pipeline_cost_tracking` | Agents with turn counts | Cost events emitted, total_turns in state matches sum of agent turns |
| 10 | `test_pipeline_budget_exceeded` | `cost_budget_usd=0.001`, many turns | Raises BudgetExceeded or exit 3, state=ABORTED |
| 11 | `test_pipeline_autonomy_level_1_hold` | autonomy=1, oracle SHIP | Exit 4, state=HOLD, `.hold` file created, HumanHoldRequired raised |
| 12 | `test_pipeline_autonomy_level_3_auto` | autonomy=3 | Pipeline completes without pause |
| 13 | `test_pipeline_spec_change_raises` | Write state with spec_hash=X, modify spec | Raises DarkFactoryError with "spec has changed" |
| 14 | `test_pipeline_transient_retry` | First invoke_agent raises TransientFailure, second succeeds | Agent retried, pipeline succeeds, retries tracked |

**Implementation notes:**
- Mock `invoke_agent` not `execute_container` — this tests the orchestrator, not Docker
- Mock `require_docker()` to no-op (skip Docker daemon check)
- Mock `setup_networks()` and `setup_proxies()` to no-op
- Mock `configure_barriers()` to return minimal barrier config
- Real: state file I/O, events emission, config loading, trust calculation, cost estimation
- Each test creates a `project_dir` with spec file, asserts on state file and events after run

**Estimated test count:** 14

---

### Phase 5C: Iteration Loop (Suite 3)

**Goal:** Test iteration loop internals — trajectory analysis, strategy switching,
oracle overrides, evaluator invocation, feedback validation.

**Mock boundary:** `invoke_agent` mocked. Trajectory classification, strategy actions,
composite scoring, satisfaction scoring are all real.

**Key source:** `darkfactory/pipeline/iteration.py` — `run_iteration_loop()`

#### Tests

| # | Test | Setup | Validates |
|---|------|-------|-----------|
| 1 | `test_trajectory_degrading_triggers_switch` | 3 iterations: scores 80→60→40 | `classify_trajectory` returns DEGRADING, `strategy_action` returns SWITCH_MODEL, model_override set in state |
| 2 | `test_trajectory_plateau_triggers_fresh_start` | 3 iterations: scores 50→52→51 | Trajectory=PLATEAU, strategy=FRESH_START, iteration counter and history reset |
| 3 | `test_trajectory_oscillating_triggers_switch` | 4 iterations: scores 60→40→70→30 | Trajectory=OSCILLATING, strategy=SWITCH_MODEL |
| 4 | `test_trajectory_improving_continues` | 3 iterations: scores 40→60→80 | Trajectory=IMPROVING, strategy=CONTINUE, no model switch |
| 5 | `test_composite_anomaly_blocks_ship` | Oracle SHIP, but anomaly_score > threshold | SHIP overridden, returns False (NO-SHIP), event emitted |
| 6 | `test_oracle_satisfaction_floor` | Oracle SHIP, satisfaction=30, floor=50 | SHIP overridden, state records override reason |
| 7 | `test_oracle_pass_rate_floor` | Oracle SHIP, pass_rate=60, floor=80 | SHIP overridden |
| 8 | `test_evaluator_invoked_when_deploy_cmd` | config.deploy_cmd set | EVALUATOR agent invoked after quality gate |
| 9 | `test_evaluator_skipped_no_deploy_cmd` | No deploy_cmd | EVALUATOR not invoked, agent calls skip evaluator role |
| 10 | `test_feedback_validation_blocks_leakage` | Oracle NO-SHIP with feedback containing scenario names | `validate_oracle_feedback` detects leakage, warning logged |

**Implementation notes:**
- Build `SpecRun` with `spec_run_factory`, pass to `run_iteration_loop` directly
- Mock `invoke_agent` to return role-appropriate `AgentResult` objects
- Mock `run_quality_gate` to return `GateResult` with controlled pass/fail counts
- For trajectory tests: run multiple iterations by providing enough mock responses
- For evaluator tests: mock deployment functions (`deploy`, `health_check`, `teardown`)
- Real: trajectory classification, strategy actions, composite scoring, satisfaction scoring

**Estimated test count:** 10

---

### Phase 5D: Quality Gate Chain (Suite 9)

**Goal:** Test the quality gate module with a real Python fixture project. The gate
runs `pytest`, `ruff`, `mypy` as real subprocesses against the fixture project.
Docker execution is mocked.

**Mock boundary:** `execute_container` mocked (the gate script runs inside Docker in
production). Instead, we call `run_quality_gate()` which internally builds Docker
args — we intercept at `execute_container` and instead run the underlying commands
directly against the fixture project.

**Alternative approach (simpler):** Since `run_quality_gate` calls `execute_container`
which runs a bash script inside Docker, and we can't run Docker in tests, we test at
a higher level. Mock `execute_container` to return a `RunResult` with realistic gate
output (scenario-counts.env contents, build/lint/type check results). This tests the
result parsing, satisfaction scoring, and gate chaining logic.

**Key source:** `darkfactory/pipeline/quality_gate.py` — `run_quality_gate()`

#### Tests

| # | Test | Mock setup | Validates |
|---|------|-----------|-----------|
| 1 | `test_full_gate_pass` | Container returns exit 0 + counts PASSED=10,FAILED=0,TOTAL=10 | GateResult.exit_code=0, pass_rate=100, satisfaction > 0 |
| 2 | `test_build_failure_exit_2` | Container returns exit 2 (build fail) | GateResult.exit_code=2, scenario counts all 0 |
| 3 | `test_lint_failure_exit_1` | Container returns exit 1 + counts | GateResult.exit_code=1, scenario counts parsed correctly |
| 4 | `test_scenario_counts_parsing` | Container output contains scenario-counts.env format | Correct passed/failed/total extracted |
| 5 | `test_zero_scenarios_handled` | Container returns exit 0 but TOTAL=0 | GateResult has 0 scenarios, pass_rate=0 |
| 6 | `test_gate_timeout` | Container returns timed_out=True | GateResult.exit_code=124 (EXIT_TIMEOUT) |
| 7 | `test_dry_run_skips_container` | dry_run=True | No container invoked, GateResult has exit_code=0 |
| 8 | `test_mutation_gate_runs_when_configured` | Gate passes + mutation_cmd set | Second container invocation for mutation testing |
| 9 | `test_mutation_gate_skipped_when_unconfigured` | No mutation_cmd | Only one container invocation |
| 10 | `test_mutation_score_below_threshold` | mutation_min_score=80, mutation returns 60 | Gate failure due to mutation |
| 11 | `test_scenario_counts_file_written` | Full gate pass | `{spec}-scenario-counts.env` file exists with correct content |

**Implementation notes:**
- All tests mock `execute_container` with controlled `RunResult` objects
- Write scenario-counts.env to project_dir before returning from mock (simulating container side-effect)
- Test satisfaction scoring integration with real `compute_satisfaction` where applicable

**Estimated test count:** 11

---

### Phase 5E: Deployment Pipeline (Suite 12)

**Goal:** Test canary deployment, rollback cascade, health checks, and feature flags.

**Mock boundary:** `run_command` mocked (all deployment commands are subprocess calls).
`time.sleep` mocked (soak periods). Event emission is real.

**Key sources:**
- `darkfactory/deployment/progressive.py` — `run_canary()`
- `darkfactory/deployment/rollback.py` — `rollback()`
- `darkfactory/deployment/health.py` — health check strategies
- `darkfactory/deployment/feature_flags.py` — flag management

#### Tests

| # | Test | Mock setup | Validates |
|---|------|-----------|-----------|
| 1 | `test_canary_full_promotion` | All shift_traffic succeed, no threshold breaches | DeploymentResult.status=PROMOTED, stages_completed=4 (default stages), events contain CANARY_STAGE_START/PASSED for each + CANARY_PROMOTED |
| 2 | `test_canary_rollback_on_metrics_breach` | Stage 2 metric threshold breached | status=ROLLED_BACK, stages_completed=1, rollback_reason contains threshold name |
| 3 | `test_canary_traffic_shift_failure` | shift_traffic returns False at stage 1 | status=FAILED, error mentions traffic shift |
| 4 | `test_canary_abort_during_soak` | cleanup_manager.abort_requested=True during soak | status=ROLLED_BACK, reason="aborted during soak" |
| 5 | `test_canary_lock_timeout` | Pre-acquire canary lock file | status=FAILED, error="canary lock timeout", CANARY_LOCK_TIMEOUT event |
| 6 | `test_canary_custom_stages` | stages=[1%, 50%, 100%] | Exactly 3 stages executed, percents correct |
| 7 | `test_canary_invalid_stages_raises` | stages=[50%, 10%] (not increasing) | ValueError raised |
| 8 | `test_rollback_cascade_l1_succeeds` | flag_disable_cmd succeeds | Returns FLAG_KILL, only L1 attempted |
| 9 | `test_rollback_cascade_l1_fails_l2_succeeds` | L1 fails, L2 succeeds | Returns TRAFFIC_REVERT, both attempted, events show L1 failed + L2 success |
| 10 | `test_rollback_cascade_all_fail` | All layers fail | Returns None, ROLLBACK_ALL_FAILED event |
| 11 | `test_rollback_git_revert_skipped_without_post_merge` | post_merge=False | GIT_REVERT layer skipped entirely |
| 12 | `test_rollback_git_revert_with_post_merge` | post_merge=True, git commands succeed | GIT_REVERT attempted, PR creation command called |
| 13 | `test_health_check_http` | Mock HTTP response | Health check passes/fails based on status code |
| 14 | `test_health_check_command` | Mock command returns 0/1 | Health check passes/fails based on exit code |
| 15 | `test_feature_flag_creation` | flag_cmd configured | Flag creation command called with correct env vars |

**Implementation notes:**
- Mock `run_command` for all deployment subprocess calls
- Mock `time.sleep` to instant (no real soak waits)
- Mock `collect_and_evaluate` for metric thresholds — return controlled (metrics, breached) tuples
- For rollback tests: mock `run_command` per-call with success/failure sequences
- For git revert: mock `darkfactory.utils.subprocess_runner.run` for git commands
- Real: event emission (assert events in events.jsonl), FileLock, DeploymentResult construction
- Use `project_dir` fixture for lock files and events

**Estimated test count:** 15

---

### Phase 5F: CLI Command Gaps (Suite 1 expansion)

**Goal:** Complete CLI coverage for commands not yet tested.

**Mock boundary:** Pipeline-triggering commands (`run`, `validate`, `gate`, `scenarios`,
`deploy`) mock their underlying functions. Read-only commands are real.

**Key source:** `darkfactory/cli.py` — `_COMMANDS` dict, `_dispatch()`

#### Tests to add

| # | Test | Mock | Validates |
|---|------|------|-----------|
| 1 | `test_new_spec_creates_file` | None (real) | Spec file created in `.dark-factory/specs/`, has required sections |
| 2 | `test_validate_passes_valid_spec` | Mock `run_pipeline` → exit 0 | Exit code 0 |
| 3 | `test_validate_fails_incomplete_spec` | Mock `run_pipeline` → exit 1 | Exit code 1 |
| 4 | `test_run_invokes_pipeline` | Mock `run_pipeline` → exit 0 | `run_pipeline` called with correct spec_name and config |
| 5 | `test_run_dry_run` | Mock `run_pipeline` | `dry_run=True` passed to `run_pipeline` |
| 6 | `test_gate_invokes_quality_gate` | Mock `run_quality_gate` → GateResult(exit_code=0) | Quality gate called, exit 0 |
| 7 | `test_scenarios_invokes_runner` | Mock `run_scenarios` | Scenario runner called with correct args |
| 8 | `test_deploy_invokes_deployment` | Mock `post_ship_pipeline` | Deployment called |
| 9 | `test_scale_test_invokes_validation` | Mock `run_scale_validation` | Scale validation called |
| 10 | `test_integrate_copies_files` | None (real) | Integration files copied to target |
| 11 | `test_calibrate_cost` | Mock `run_calibration` | Calibration called |
| 12 | `test_setup_validate` | None (real-ish) | Reports Docker/git/claude status |
| 13 | `test_regression_invokes_runner` | Mock `run_regression` | Regression runner called |
| 14 | `test_unknown_command_error` | None | Exit 2, helpful error message |

**Estimated test count:** 14

---

### Phase 5G: Plan Execution Gaps (Suite 11 expansion)

**Goal:** Test concurrent plan dispatch, checkpoint/resume, failure propagation,
and governor concurrency limits.

**Mock boundary:** `run_pipeline` mocked (each spec dispatch calls `run_pipeline`).
Plan parsing, topo sort, conflict detection are real (already tested).

**Key source:** `darkfactory/plan/executor.py` — `execute_plan()`

#### Tests to add

| # | Test | Mock setup | Validates |
|---|------|-----------|-----------|
| 1 | `test_concurrent_independent_specs` | 3 specs no deps, mock records call times | All 3 dispatched (futures submitted), PlanResult.shipped=3 |
| 2 | `test_dependency_blocks_execution` | A→B dependency | B dispatched only after A completes (verify via mock call order) |
| 3 | `test_plan_checkpoint_and_resume` | Run 5 specs, first 2 succeed, third fails. Resume | Resume skips first 2, retries from 3rd |
| 4 | `test_plan_failure_blocks_dependents` | Spec A fails (exit 1) | Dependent specs marked BLOCKED/skipped, PlanResult.skipped > 0 |
| 5 | `test_governor_budget_enforcement` | Plan budget nearly exhausted | Later specs blocked by `check_budget_pre_dispatch` |
| 6 | `test_plan_wall_time_limit` | `plan_max_wall_time_s=1`, specs take longer | Plan aborted with timeout, PLAN_TIMEOUT event |

**Implementation notes:**
- Mock `run_pipeline` with controlled exit codes and optional sleep (for concurrency tests)
- Write plan markdown files and spec files in `project_dir`
- Assert `PlanResult` fields (shipped, failed, skipped)
- For checkpoint tests: verify checkpoint file written after each spec, read back on resume

**Estimated test count:** 6

---

### Phase 5H: Scenario Lifecycle Gaps (Suite 10 expansion)

**Goal:** Fill remaining scenario tests — running scenarios with real pytest against
the fixture project, multi-run satisfaction, regression filtering.

**Mock boundary:** No Docker mocking. Tests run real `pytest` subprocess against the
`tests/integration/fixtures/projects/python-sample/` fixture project.

**Key source:** `darkfactory/scenarios/runner.py` — `run_scenarios()`

#### Pre-requisite: Enhance fixture project

The `python-sample` fixture project needs scenario test files in its scenarios dir:

```
fixtures/projects/python-sample/
├── .dark-factory/
│   ├── scenarios/add-multiply/
│   │   ├── 01-contract_test_multiply.py    # Tests multiply(3,4)==12, etc.
│   │   ├── 02-happy_path_test_floats.py    # Tests multiply(2.5, 4.0)==10.0
│   │   └── 03-edge_cases_test_zero.py      # Tests multiply(0, x)==0
│   └── specs/add-multiply.md
├── src/calculator.py  (already has multiply)
└── tests/check_calculator.py (existing)
```

#### Tests to add

| # | Test | Setup | Validates |
|---|------|-------|-----------|
| 1 | `test_run_scenarios_real_pytest` | Fixture project with passing scenarios | RunResult.exit_code=0, stdout contains pass count |
| 2 | `test_run_scenarios_with_failures` | Add a deliberately failing scenario | RunResult.exit_code=1, stdout shows failure |
| 3 | `test_satisfaction_from_real_run` | Parse real pytest output into satisfaction score | Score matches expected weighted calculation |
| 4 | `test_toolchain_detection_python` | Fixture project with pyproject.toml | Pytest command auto-selected |

**Estimated test count:** 4

---

### Phase 5I: Docker Execution (Suite 7) + Docker Barriers (Suite 6 gaps)

**Goal:** Test real Docker container lifecycle and barrier enforcement with actual
containers. All tests marked `@pytest.mark.docker`.

**Mock boundary:** None — these are real Docker tests.

**Pre-requisites:**
- Docker daemon running
- `tests/integration/Dockerfile.test` built
- Network `df-pipeline-test` created

#### Suite 7 tests

| # | Test | Setup | Validates |
|---|------|-------|-----------|
| 1 | `test_container_runs_returns_output` | `echo hello` in container | Output="hello", exit_code=0 |
| 2 | `test_container_timeout_kills` | `sleep 999` with timeout=2 | timed_out=True, exit_code=124 |
| 3 | `test_container_nonzero_exit` | `exit 42` | exit_code=42, container cleaned up |
| 4 | `test_container_cleanup_on_success` | Run container | Container removed (docker ps shows nothing) |
| 5 | `test_container_cleanup_on_failure` | Container exits 1 | Container removed |
| 6 | `test_container_resource_limits` | CPU=0.5, memory=128m | docker inspect confirms limits |
| 7 | `test_container_label_filtering` | Run with label `df-run-id=test` | `cleanup_containers("test")` finds and removes it |

#### Suite 6 Docker barrier tests

| # | Test | Setup | Validates |
|---|------|-------|-----------|
| 8 | `test_implementer_cannot_read_scenarios` | Implementer barriers + tmpfs | `ls .dark-factory/scenarios/` returns empty |
| 9 | `test_implementer_cannot_read_reports` | Implementer barriers | `ls .dark-factory/reports/` returns empty |
| 10 | `test_scenario_architect_cannot_read_source` | SA barriers | SOURCE_DIRS masked |
| 11 | `test_reviewer_read_only_mount` | Reviewer barriers | `touch /project/test` fails |
| 12 | `test_implementer_can_write_source` | Implementer rw mount | Write to source succeeds |

**Implementation notes:**
- Build `tests/integration/Dockerfile.test` from `alpine:3.19` with bash/curl/git
- Use `execute_container` from `darkfactory.docker.containers` with real Docker
- `build_base_args`, `build_agent_args` from `darkfactory.docker.args` for arg construction
- `configure_barriers` from `darkfactory.docker.barriers` for barrier configs
- Skip all these tests if Docker not available: `pytest.importorskip` or `pytest.mark.skipif`
- CI: separate job with Docker service

**Estimated test count:** 12

---

### Phase 5J: Remaining Gaps

#### Git workflow gaps (Suite 14)

| Test | Validates |
|------|-----------|
| `test_project_detection_node` | package.json → node |
| `test_project_detection_go` | go.mod → go |

#### Cost/budget gaps (Suite 15)

| Test | Mock setup | Validates |
|------|-----------|-----------|
| `test_cost_events_emitted` | Mock invoke_agent with turn counts | ITER_COST events in events.jsonl |
| `test_calibration_from_history` | Pre-populated events with actual costs | Calibration output adjusts estimates |

**Estimated test count:** 4

---

### Phase 5K: CI & Infrastructure

#### Dockerfile.test

```dockerfile
FROM alpine:3.19
RUN apk add --no-cache bash curl git python3
ENTRYPOINT ["bash"]
```

#### GitHub Actions workflow

```yaml
# .github/workflows/integration-tests.yml
jobs:
  integration-tests:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -e ".[dev]"
      - run: pytest tests/integration -v -m "not docker" --timeout=120

  docker-integration-tests:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    needs: [integration-tests]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -e ".[dev]"
      - run: docker build -t dark-factory-test -f tests/integration/Dockerfile.test .
      - run: pytest tests/integration -v -m "docker" --timeout=300
```

#### Dev dependencies

Add to `pyproject.toml`:
```toml
[project.optional-dependencies]
dev = [
    # existing...
    "pytest-xdist>=3.5",     # parallel execution
    "responses>=0.25",       # HTTP mocking (health checks)
    "freezegun>=1.4",        # time freezing (soak periods)
]
```

---

## Implementation Order & Dependencies

```
Phase 5A (mock infrastructure)
  ├── Phase 5B (pipeline flow)        ← needs mock_invoke_agent
  │     └── Phase 5C (iteration loop) ← needs 5B patterns + mock_invoke_agent
  ├── Phase 5D (quality gate)         ← needs mock_execute_container
  ├── Phase 5E (deployment)           ← needs mock_run_command
  ├── Phase 5F (CLI gaps)             ← needs mock_run_pipeline
  └── Phase 5G (plan execution gaps)  ← needs mock_run_pipeline
Phase 5H (scenario lifecycle)         ← independent, needs fixture project
Phase 5I (Docker tests)               ← independent, needs Docker + Dockerfile.test
Phase 5J (remaining gaps)             ← independent, minimal deps
Phase 5K (CI)                         ← after all tests written
```

Phases 5B–5G all depend on 5A but are independent of each other.
Phases 5H, 5I, 5J are independent of everything except existing fixtures.

**Recommended serial order:** 5A → 5B → 5C → 5D → 5E → 5F → 5G → 5H → 5J → 5I → 5K

Rationale: 5B and 5C are the highest-value tests (core pipeline logic). 5I (Docker)
is last because it requires external infrastructure and is the hardest to run locally.

---

## Test Count Summary

| Phase | Suite(s) | New tests |
|-------|----------|-----------|
| 5A | Fixture meta-tests | 5 |
| 5B | Pipeline Flow (Suite 2) | 14 |
| 5C | Iteration Loop (Suite 3) | 10 |
| 5D | Quality Gate Chain (Suite 9) | 11 |
| 5E | Deployment Pipeline (Suite 12) | 15 |
| 5F | CLI E2E gaps (Suite 1) | 14 |
| 5G | Plan Execution gaps (Suite 11) | 6 |
| 5H | Scenario Lifecycle gaps (Suite 10) | 4 |
| 5I | Docker Execution + Barriers (Suites 7, 6) | 12 |
| 5J | Git + Cost gaps (Suites 14, 15) | 4 |
| 5K | CI infrastructure | 0 |
| **Total** | | **95** |

Combined with existing 242 tests: **337 integration tests total**.

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Mock infrastructure is wrong | Phase 5A includes meta-tests that verify mock behavior |
| Pipeline orchestrator hard to test in isolation | Mock at `invoke_agent` level (high enough to test orchestration, low enough to be realistic) |
| Deployment tests need real time.sleep for soak | Mock `time.sleep` to instant; test soak abort via cleanup_manager flag |
| Docker tests flaky in CI | Separate job, longer timeout, skip if daemon unavailable |
| Fixture project drifts from production patterns | Minimal fixture, version-controlled, used by multiple test suites |
| Quality gate tests need real subprocess | Mock at `execute_container` level — test the parsing/scoring, not the bash script |
| Plan executor concurrency hard to test deterministically | Mock `run_pipeline` with controlled timing; assert call order, not real timing |
| Rollback git revert test needs real git history | Use `git_repo` fixture with commits; mock only `gh pr create` and `git push` |
