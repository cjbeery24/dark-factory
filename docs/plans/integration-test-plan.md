# Comprehensive Integration Test Plan — Dark Factory

## Executive Summary

Dark Factory has 53 unit test files (~7,500 LOC) with heavy mocking. What's missing is **integration tests that exercise real interactions** between subsystems — Docker containers, filesystem barriers, subprocess pipelines, state persistence across steps, and end-to-end CLI flows. This plan fills that gap.

## Guiding Principles

1. **Integration tests complement, not replace, unit tests** — Unit tests stay mocked; integration tests exercise real interactions.
2. **Layered approach** — Start with cheap "medium" integration tests (real filesystem, real subprocess, mocked Docker), then add expensive "large" tests (real Docker containers).
3. **CI-friendly** — Tests must run in GitHub Actions. Docker-dependent tests are marked and can be skipped in environments without Docker.
4. **Deterministic** — No flaky tests. Use timeouts, retries only for known transient failures, and fixed test fixtures.
5. **Explicit mock boundaries** — Every suite documents whether Docker is mocked (at `execute_container`) or real. No ambiguity.

---

## Test Infrastructure

### Directory Structure

```
tests/
├── unit/                    # Existing 53 test files (migrated from tests/ — Phase 0)
├── integration/
│   ├── conftest.py          # Shared fixtures: tmp projects, config builders, event parsers
│   ├── fixtures/
│   │   ├── specs/           # Valid/invalid spec files for pipeline tests
│   │   │   ├── complete-spec.md     # Passes 100% completeness checklist
│   │   │   └── incomplete-spec.md   # Missing required sections
│   │   ├── projects/        # Minimal sample projects (Python, Node)
│   │   │   └── python-sample/
│   │   ├── scenarios/       # Pre-generated scenario YAML files
│   │   └── events/          # Pre-populated event sequences for cross-suite use
│   ├── test_cli_e2e.py              # CLI command integration tests
│   ├── test_pipeline_flow.py        # Multi-step pipeline orchestration
│   ├── test_iteration_loop.py       # Iteration loop internals (trajectory, strategy, oracle overrides)
│   ├── test_state_lifecycle.py      # State persistence, resume, registry
│   ├── test_config_resolution.py    # Config loading, defaults, migration
│   ├── test_barrier_enforcement.py  # Information barrier integrity (Docker + non-Docker)
│   ├── test_docker_execution.py     # Real Docker container tests
│   ├── test_docker_args.py          # Docker argument construction (mounts, tmpfs, labels)
│   ├── test_quality_gate_chain.py   # Quality gate subprocess chain (mocked Docker)
│   ├── test_scenario_lifecycle.py   # Scenario generation → detection → running → satisfaction
│   ├── test_plan_execution.py       # Multi-spec plan with topo sort + concurrency
│   ├── test_deployment_pipeline.py  # Progressive deployment stages
│   ├── test_event_flow.py           # Event emission → JSONL → dashboard → OTEL
│   ├── test_git_workflow.py         # Branch creation, worktrees, commit flow
│   ├── test_cost_budget.py          # Cost tracking and budget enforcement
│   ├── test_trust_autonomy.py       # Trust scoring → autonomy decisions
│   ├── test_notification_dispatch.py # Notification triggers across pipeline
│   └── test_cleanup_signals.py      # CleanupManager signal handling
│
├── helpers.py               # Existing (keep at root, importable by both unit/ and integration/)
└── conftest.py              # Root conftest — pytest markers, fixture registration
```

### Pytest Configuration Updates

```toml
# pyproject.toml additions
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "integration: integration tests (deselect with -m 'not integration')",
    "docker: requires Docker daemon (deselect with -m 'not docker')",
    "slow: tests that take >30s",
]
```

### Shared Fixtures (`tests/integration/conftest.py`)

```python
@pytest.fixture
def project_dir(tmp_path):
    """Create a minimal Dark Factory project with valid structure.

    Creates:
    - .dark-factory/{specs,scenarios,reports,state,audit,plans}
    - A minimal Python source file (src/calculator.py)
    - A pyproject.toml with project metadata
    - A valid .dark-factory/config.toml with language=python
    """
    # Returns Path to project root

@pytest.fixture
def project_dir_no_config(tmp_path):
    """Create project structure without config.toml (for config resolution tests)."""

@pytest.fixture
def pipeline_config(project_dir):
    """Return a PipelineConfig loaded from the test project's config.toml."""

@pytest.fixture
def sample_spec(project_dir):
    """Write a valid spec file that passes the completeness checklist and return its name."""

@pytest.fixture
def failing_spec(project_dir):
    """Write an incomplete spec file and return its name."""

@pytest.fixture
def events_file(project_dir):
    """Return path to events JSONL file, create parent dirs."""

@pytest.fixture
def populated_events(events_file):
    """Return events file pre-populated with a realistic event sequence:
    spec_started → agent_started → agent_completed → gate_completed →
    oracle_verdict → spec_shipped. All with valid timestamps and UUIDs."""

@pytest.fixture
def state_store(project_dir):
    """Return a configured StateStore for the test project."""

@pytest.fixture
def mock_execute_container():
    """Mock execute_container with sequenceable return values.

    Usage:
        mock = mock_execute_container([
            AgentResult(output="VALIDATED", exit_code=0, ...),  # spec validator
            AgentResult(output="scenarios generated", ...),      # scenario architect
            AgentResult(output="implemented", ...),              # implementer
            ...
        ])

    Each call to execute_container pops the next result from the sequence.
    Raises if called more times than expected.
    """

@pytest.fixture
def mock_agent_outputs():
    """Factory for realistic agent output strings.

    Returns dict with keys: 'validator', 'scenario_architect', 'implementer',
    'quality_gate_pass', 'quality_gate_fail_build', 'reviewer_approve',
    'oracle_ship', 'oracle_no_ship'.

    Oracle outputs include proper SHIP/NO-SHIP first-line format.
    Quality gate outputs include scenario-counts.env format.

    Used by mock_execute_container to populate return values:
        outputs = mock_agent_outputs()
        container = mock_execute_container([
            AgentResult(output=outputs['validator'], ...),
            AgentResult(output=outputs['oracle_ship'], ...),
        ])
    """
```

---

## Test Suites

### Suite 1: CLI End-to-End (`test_cli_e2e.py`)

**What it tests:** CLI commands invoked via `darkfactory.cli.main(argv=[...])` with captured stdout/stderr and sys.exit. This avoids requiring installation and is faster than subprocess. A small subset of smoke tests use `subprocess.run(["dark-factory", ...])` to verify the entry point works.

**Mock boundary:** No Docker mocking. Tests only exercise CLI parsing, init, validation, and read-only commands. Pipeline-triggering commands are tested in Suite 2.

| Test | Description | Validates |
|------|-------------|-----------|
| `test_init_creates_project_structure` | `main(["init"])` on empty dir | All dirs created, config.toml generated, .gitignore updated |
| `test_init_detects_python_project` | `init` on dir with pyproject.toml | Language=python in generated config |
| `test_init_detects_node_project` | `init` on dir with package.json | Language=node in generated config |
| `test_init_idempotent` | `init` twice on same dir | No errors, no duplicates |
| `test_new_spec_generates_valid_file` | `main(["new-spec", "Add auth"])` | Spec file created with all required sections |
| `test_validate_passes_valid_spec` | `validate` on complete spec | Exit code 0, validation report generated |
| `test_validate_fails_incomplete_spec` | `validate` on spec missing sections | Exit code != 0, specific missing sections reported |
| `test_setup_validate_checks_env` | `setup validate` | Reports Docker status, git status, claude availability |
| `test_migrate_config_sh_to_toml` | `migrate-config` with legacy config.sh | config.toml created with equivalent values |
| `test_metrics_command_with_events` | `metrics` with pre-populated events.jsonl | Summary output with correct aggregations |
| `test_dashboard_last_n` | `dashboard --last 3` with events data | Shows exactly 3 most recent runs |
| `test_events_rotate` | `events rotate` with large events file | File rotated, old events archived |
| `test_scenarios_command` | `scenarios <spec>` with fixture project | Invokes scenario runner with correct args |
| `test_gate_command` | `gate <spec>` with fixture project | Invokes quality gate with correct args |
| `test_scale_test_command` | `scale-test` with fixture project | Scale validation executes across specs |
| `test_integrate_command` | `integrate <path>` with target project | Integration files copied/symlinked correctly |
| `test_confirm_command` | `confirm <spec>` with .hold file | Hold file removed, pipeline resumes |
| `test_confirm_no_hold_file` | `confirm <spec>` without .hold file | Clear error message |
| `test_deploy_command` | `deploy <spec>` with fixture project | Deployment invoked with correct args |
| `test_calibrate_cost_command` | `calibrate-cost` with historical data | Calibration output generated |
| `test_cli_exit_codes` | Various error conditions | Correct exit codes (0, 1, 2, 4) per scenario |
| `test_global_flags` | `--dry-run`, `--debug`, `--verbose`, `--ci` | Flags respected in output/behavior |
| `test_unknown_command` | `main(["nonexistent"])` | Helpful error message, exit code 2 |
| `test_subprocess_entry_point_smoke` | `subprocess.run(["dark-factory", "--help"])` | Entry point works, help text displayed |

### Suite 2: Pipeline Flow (`test_pipeline_flow.py`)

**What it tests:** The 7-step pipeline orchestration with agent invocations mocked at the Docker boundary (mock `execute_container` via shared fixture), but everything else real — state transitions, event emission, config resolution, satisfaction scoring.

**Mock boundary:** `execute_container` mocked via `mock_execute_container` fixture. All other modules (state, events, config, audit) are real.

| Test | Description | Validates |
|------|-------------|-----------|
| `test_full_pipeline_ship_first_iteration` | Mock agents return success → SHIP | All 7 steps execute in order, state file shows SHIPPED |
| `test_pipeline_no_ship_then_ship` | First oracle NO-SHIP, second SHIP | 2 iterations, state tracks both, feedback passed to implementer |
| `test_pipeline_max_iterations_escalation` | All oracles NO-SHIP | Stops at max_iterations, state=ESCALATED |
| `test_pipeline_build_failure_hard_abort` | Quality gate returns build failure (exit 2) | Stops immediately, state=FAILED, no review/oracle |
| `test_pipeline_resume_from_checkpoint` | Write state file with completed_step=3, call `run_pipeline(resume=True)` | Starts from step 4, doesn't re-run steps 1-3 |
| `test_pipeline_spec_change_raises_error` | Change spec content between runs (different hash) | Raises `DarkFactoryError` with "spec has changed" message, suggests `--fresh` |
| `test_pipeline_dry_run` | `--dry-run` flag | No agents invoked, no state written, plan logged |
| `test_pipeline_event_sequence` | Full pipeline run | Events emitted in correct order with correct types |
| `test_pipeline_audit_trail` | Full pipeline run | Audit log has entries for all agent invocations |
| `test_pipeline_cost_tracking` | Mock agents with token counts | Cost accumulated correctly, budget check at each step |
| `test_pipeline_budget_exceeded` | Set low budget | Pipeline aborts when budget exceeded |
| `test_pipeline_anomaly_detection` | Mock implementer with excessive turns | Anomaly detected, pipeline escalates |
| `test_pipeline_autonomy_level_1_hold` | autonomy_level=1, oracle SHIP | Raises `HumanHoldRequired`, state=HOLD, .hold file created with metadata, exit code 4 |
| `test_pipeline_autonomy_level_3` | autonomy_level=3 | Pipeline proceeds without pause |
| `test_pipeline_transient_failure_retry` | Mock agent OOM on first try, success on second | Retries with backoff, succeeds on retry |

### Suite 3: Iteration Loop (`test_iteration_loop.py`) — NEW

**What it tests:** The iteration loop internals (`run_iteration_loop` in `darkfactory/pipeline/iteration.py`) — trajectory analysis, strategy switching, anomaly scoring, oracle overrides, evaluator invocation, and feedback validation.

**Mock boundary:** `execute_container` mocked. All scoring, trajectory, and strategy logic is real.

| Test | Description | Validates |
|------|-------------|-----------|
| `test_trajectory_degrading_triggers_strategy_switch` | 3 iterations with declining pass rate | Trajectory=DEGRADING, `_apply_strategy` called |
| `test_strategy_fresh_start_resets_state` | FRESH_START strategy applied | Iteration state reset, implementer gets clean-slate prompt |
| `test_composite_anomaly_blocks_ship` | Oracle says SHIP but composite anomaly score high | SHIP overridden, pipeline continues iterating |
| `test_oracle_satisfaction_floor` | Oracle SHIP but satisfaction < floor | SHIP overridden due to satisfaction floor |
| `test_oracle_pass_rate_floor` | Oracle SHIP but pass rate < floor | SHIP overridden due to pass rate floor |
| `test_evaluator_invoked_when_deploy_cmd_set` | config has deploy_cmd | Evaluator agent runs after quality gate |
| `test_evaluator_skipped_when_no_deploy_cmd` | No deploy_cmd in config | Evaluator step skipped |
| `test_feedback_file_validation` | Oracle NO-SHIP with feedback | `validate_oracle_feedback` passes, feedback file created |
| `test_trajectory_oscillating` | Pass rates alternate up/down | Trajectory=OSCILLATING |
| `test_trajectory_plateau` | Pass rates stable over 3+ iterations | Trajectory=PLATEAU |

### Suite 4: State Lifecycle (`test_state_lifecycle.py`)

**What it tests:** State persistence, recovery, and registry interactions with real filesystem operations.

| Test | Description | Validates |
|------|-------------|-----------|
| `test_state_write_read_roundtrip` | Write SpecRun, read back | All fields preserved including nested iterations |
| `test_state_concurrent_threads` | Two threads write simultaneously via FileLock | Lock prevents corruption, both writes succeed sequentially |
| `test_state_partial_write_recovery` | Simulate interrupted write | Previous valid state preserved or clear error |
| `test_registry_records_completed_runs` | Complete a pipeline run | Registry contains run with correct metadata |
| `test_registry_query_by_spec` | Multiple runs, query one spec | Returns only matching runs, ordered by time |
| `test_plan_checkpoint_save_restore` | Multi-spec plan, save checkpoint after 2 | Checkpoint shows which specs completed |
| `test_state_file_format_stability` | Write with current version | State file is valid JSON, schema documented |
| `test_lock_timeout` | Hold lock in one thread, attempt second acquire | Second caller times out with clear error |
| `test_lock_cleanup_on_crash` | Create stale lock file | Next run detects stale lock, acquires successfully |

### Suite 5: Config Resolution (`test_config_resolution.py`)

**What it tests:** Config loading precedence, defaults, validation, and migration.

| Test | Description | Validates |
|------|-------------|-----------|
| `test_defaults_when_no_config` | No config.toml present | PipelineConfig has sensible defaults for all fields |
| `test_toml_overrides_defaults` | config.toml with partial values | Specified values override defaults, unspecified use defaults |
| `test_invalid_toml_fails_clearly` | Malformed config.toml | Clear error message with line number |
| `test_unknown_keys_warned` | config.toml with typo key | Warning logged, key ignored, pipeline continues |
| `test_config_sh_migration` | Legacy config.sh file | All bash variables correctly mapped to TOML keys |
| `test_source_dirs_validation` | Non-existent source_dirs | Error before pipeline starts |
| `test_language_detection_fallback` | No language in config | Auto-detected from project files |
| `test_env_var_interpolation` | Config referencing $ENV_VAR | Environment variables resolved |

### Suite 6: Information Barrier Enforcement (`test_barrier_enforcement.py`)

**What it tests:** Two layers: (a) Docker mount configurations and tmpfs overlays (requires Docker, `@pytest.mark.docker`), and (b) audit barrier detection (pure string matching, no Docker needed).

**Mock boundary:** Tests marked `@pytest.mark.docker` use real containers. Non-Docker tests exercise `check_output_for_barriers` with real file I/O only.

| Test | Description | Docker? | Validates |
|------|-------------|---------|-----------|
| `test_implementer_cannot_read_scenarios` | Run container with implementer barriers | Yes | `ls .dark-factory/scenarios/` returns empty |
| `test_implementer_cannot_read_reports` | Run container with implementer barriers | Yes | `ls .dark-factory/reports/` returns empty |
| `test_implementer_cannot_read_git` | Run container with implementer barriers | Yes | `.git/` is empty, `git log` fails |
| `test_scenario_architect_cannot_read_source` | Run container with SA barriers | Yes | SOURCE_DIRS are empty/missing |
| `test_reviewer_has_read_only_mount` | Run container with reviewer barriers | Yes | `touch /project/test` fails with permission error |
| `test_symlink_escape_blocked` | Create symlink from visible → masked dir | No | Pre-flight check catches it, returns error |
| `test_barrier_audit_detection_flagged` | Audit checker with output containing masked paths | No | Checker flags potential violation |
| `test_barrier_audit_detection_clean` | Audit checker with clean output | No | Checker reports no violations |
| `test_implementer_can_write_source` | Run container with implementer mounts | Yes | Writing to SOURCE_DIR succeeds |
| `test_all_agent_roles_barrier_config` | Enumerate all 7 roles | No | Each role has correct mount mode and mask list per spec |

### Suite 7: Docker Execution (`test_docker_execution.py`)

**What it tests:** Real Docker container lifecycle — build, run, output capture, timeout, cleanup. All tests require Docker (`@pytest.mark.docker`).

**Note:** These tests use a minimal Alpine-based test image (not the full `dark-factory-python` image) to avoid the `claude-code` npm install dependency. The test image is built in CI from a simple Dockerfile that includes only `bash` and basic tools.

| Test | Description | Validates |
|------|-------------|-----------|
| `test_container_runs_and_returns_output` | Simple echo command in container | Output captured correctly |
| `test_container_timeout_kills` | Sleep longer than timeout | Container killed, timeout error returned |
| `test_container_resource_limits` | Set CPU/memory limits | Container respects limits (check via docker inspect) |
| `test_container_network_isolation` | Container on df-pipeline network | Can reach proxy, cannot reach arbitrary hosts |
| `test_container_cleanup_on_success` | Run container | Container removed after completion |
| `test_container_cleanup_on_failure` | Container exits non-zero | Container removed, error captured |
| `test_docker_image_exists` | Check for test image | Image present or clear build instruction |
| `test_egress_proxy_blocks_disallowed` | Attempt curl to non-allowlisted host | Request blocked by tinyproxy |
| `test_egress_proxy_allows_permitted` | Attempt curl to allowlisted host | Request succeeds |

### Suite 8: Docker Argument Construction (`test_docker_args.py`) — NEW

**What it tests:** The argument-building layer (`darkfactory/docker/args.py`) that produces `docker run` arguments including bind mounts, tmpfs overlays, resource limits, labels, network assignment, and environment variables.

**Mock boundary:** No Docker. Tests call the arg-builder functions directly and assert on the produced argument lists.

| Test | Description | Validates |
|------|-------------|-----------|
| `test_implementer_args_include_rw_mount` | Build args for IMPLEMENTER role | Project mounted :rw |
| `test_reviewer_args_include_ro_mount` | Build args for REVIEWER role | Project mounted :ro |
| `test_tmpfs_overlays_for_masked_dirs` | Build args with barrier masks | `--tmpfs` args for each masked directory |
| `test_resource_limits_set` | Build args with CPU/memory config | `--cpus` and `--memory` flags present |
| `test_network_assignment` | Build args with network config | `--network` flag matches role's network |
| `test_labels_include_run_metadata` | Build args with run_id, spec_name | `--label` flags include metadata |
| `test_auth_env_oauth_preferred` | Both OAUTH_TOKEN and API_KEY set | Only OAUTH_TOKEN passed to container |
| `test_auth_env_api_key_fallback` | Only API_KEY set | API_KEY passed to container |
| `test_auth_env_missing_warns` | Neither token set | Warning logged |
| `test_disallowed_tools_flags` | Build args with barrier config | `--disallowedTools` flags present for defense-in-depth |

### Suite 9: Quality Gate Chain (`test_quality_gate_chain.py`)

**What it tests:** The quality gate Python module (`darkfactory/pipeline/quality_gate.py`) with a real Python test project fixture. Docker is mocked — the gate logic invokes subprocesses for build/lint/type/test, which run directly on the host against the fixture project.

**Mock boundary:** `execute_container` mocked. Subprocess calls for `pytest`, `ruff`, `mypy` are real (running against the fixture project). Deployment subprocess commands (provision, health check, etc.) are mocked via `unittest.mock.patch` on `subprocess.run`.

| Test | Description | Validates |
|------|-------------|-----------|
| `test_full_gate_pass` | Valid Python project, all tests pass | Gate returns pass, report generated |
| `test_build_failure_stops_chain` | Syntax error in source | Build gate fails, subsequent gates skipped |
| `test_lint_failure_continues` | Lint violation present | Lint fails, type check still runs |
| `test_type_check_failure` | Type error in source | Type check fails, report includes details |
| `test_self_test_failure` | Failing unit test | Self-test gate fails, pass rate < 100% |
| `test_scenario_pass_rate_extraction` | Mix of passing/failing scenarios | Correct pass/fail counts in report |
| `test_gate_timeout_handling` | Test with `time.sleep(999)` + short gate timeout (5s) | Gate times out, reports timeout. Test itself has `@pytest.mark.timeout(30)` as safety net |
| `test_custom_build_cmd` | Custom build_cmd in config | Custom command used instead of default |
| `test_mutation_gate_optional` | No MUTATION_CMD configured | Mutation gate skipped, not failed |
| `test_report_file_generation` | Full gate run | Both detailed and summary reports created |
| `test_scenario_counts_file` | Gate with scenarios | scenario-counts.env has correct PASSED/FAILED/TOTAL |

### Suite 10: Scenario Lifecycle (`test_scenario_lifecycle.py`)

**What it tests:** Scenario YAML validation, detection, `run_scenarios` invocation with real subprocess against fixture project, and satisfaction scoring with real files.

**Mock boundary:** No Docker. `run_scenarios` invokes `pytest` via subprocess against the fixture project directly.

| Test | Description | Validates |
|------|-------------|-----------|
| `test_scenario_yaml_validation` | Valid/invalid scenario files | Validator accepts valid, rejects invalid with messages |
| `test_scenario_detection_finds_all` | Multiple scenario files in dir | All discovered with correct metadata |
| `test_run_scenarios_with_real_pytest` | Real pytest scenarios in fixture project | `run_scenarios` executes, captures pass/fail counts |
| `test_run_scenarios_toolchain_detection` | Fixture project with pyproject.toml | Correct test command selected (pytest) |
| `test_satisfaction_scoring` | Mix of pass/fail across categories | Weighted score calculated correctly |
| `test_satisfaction_threshold` | Score below/above threshold | Correct pass/fail determination |
| `test_multi_run_satisfaction` | Run scenarios N times | Aggregate scoring handles flaky detection |
| `test_regression_selective` | Changed files, subset of scenarios | Only affected scenarios run |
| `test_coverage_analysis` | Scenarios vs spec sections | Coverage report shows uncovered sections |

### Suite 11: Plan Execution (`test_plan_execution.py`)

**What it tests:** Multi-spec plan parsing, topological ordering, concurrent execution, and checkpoint recovery.

**Mock boundary:** `execute_container` mocked. Plan executor records call start/end times via mock; concurrency assertions verify ordering constraints rather than real timing.

| Test | Description | Validates |
|------|-------------|-----------|
| `test_plan_parse_valid_yaml` | Valid plan YAML | All specs parsed with dependencies and phases |
| `test_topo_sort_ordering` | Specs with dependency graph | Execution order respects dependencies |
| `test_topo_sort_cycle_detection` | Circular dependency | Clear error, no infinite loop |
| `test_concurrent_independent_specs` | 3 specs with no deps, mock records start times | All 3 started before any completes (ordering assertion) |
| `test_dependency_blocks_execution` | A depends on B, mock records timestamps | A's start time > B's end time |
| `test_plan_checkpoint_and_resume` | Save checkpoint after 2 of 5 specs, resume | Resume runs only remaining 3 |
| `test_plan_failure_propagation` | Dependency fails | Dependent specs marked BLOCKED |
| `test_conflict_detection` | Specs modifying same files | Conflict detected, execution serialized |
| `test_governor_concurrency_limit` | 5 specs, max_concurrent=2, mock records active count | Active count never exceeds 2 |
| `test_optimizer_reorders` | Historical data available | Specs reordered for faster feedback |

### Suite 12: Deployment Pipeline (`test_deployment_pipeline.py`)

**What it tests:** Progressive deployment stages, health checks, feature flags, and rollback.

**Mock boundary:** HTTP endpoints mocked with `responses` library. Deployment subprocess commands (`provision_cmd`, `deploy_cmd`, `rollback_cmd`, `flag_cmd`, `health_url`) mocked with `unittest.mock.patch` on `subprocess.run` — each mock records invocations for assertion.

| Test | Description | Validates |
|------|-------------|-----------|
| `test_canary_promotion_full` | All stages healthy | Progresses through all canary stages |
| `test_canary_rollback_on_unhealthy` | Stage 2 fails health check | Rollback triggered, previous stage restored |
| `test_health_check_polling` | Mock HTTP health endpoint | Polls at configured interval, respects timeout |
| `test_feature_flag_creation` | Flag command configured | Flag subprocess called before deployment |
| `test_feature_flag_rollout` | Progressive rollout percentages | Flag subprocess called at each stage with correct % |
| `test_migration_pre_check` | Migration command configured | Migration subprocess runs before deploy, blocks on failure |
| `test_metrics_gate_evaluation` | Mock metrics HTTP endpoint | Passes/fails based on threshold |
| `test_rollback_layers` | Multi-stage deployment | Rollback subprocess called for each stage in reverse order |
| `test_environment_provision_teardown` | Provision command configured | Provision subprocess before deploy, teardown subprocess after |
| `test_deployment_event_emission` | Full deployment | Events emitted for each stage transition |

### Suite 13: Event Flow (`test_event_flow.py`)

**What it tests:** End-to-end event lifecycle — emission, persistence, querying, dashboard rendering, OTEL export.

| Test | Description | Validates |
|------|-------------|-----------|
| `test_event_emission_to_jsonl` | Emit various event types | All written to file, valid JSONL |
| `test_event_ordering` | Rapid event emission | Events ordered by timestamp |
| `test_dashboard_renders_events` | Pre-populated events via `populated_events` fixture | Dashboard output includes all runs |
| `test_otel_export_spans` | Events with timing data | OTEL spans created with correct attributes |
| `test_event_rotation` | Large events file | Rotation preserves recent, archives old |
| `test_event_types_complete` | All 24+ event types | Each type serializes/deserializes correctly |
| `test_cost_events_aggregate` | Multiple cost events | Total cost aggregation matches sum (exact equality) |

### Suite 14: Git Workflow (`test_git_workflow.py`)

**What it tests:** Real git operations in a temporary repository (initialized with `git init` in tmp_path).

| Test | Description | Validates |
|------|-------------|-----------|
| `test_branch_create_and_switch` | Create feature branch | Branch exists, HEAD on new branch |
| `test_commit_changes` | Stage and commit files | Commit in log with correct message |
| `test_worktree_create_and_cleanup` | Create worktree, do work, cleanup | Worktree exists during work, cleaned up after |
| `test_worktree_isolation` | Changes in worktree | Don't appear in main working tree |
| `test_project_detection_python` | pyproject.toml present | Detected as Python project |
| `test_project_detection_node` | package.json present | Detected as Node project |
| `test_project_detection_go` | go.mod present | Detected as Go project |

### Suite 15: Cost & Budget (`test_cost_budget.py`)

**What it tests:** Cost estimation, accumulation, and budget enforcement across pipeline steps.

| Test | Description | Validates |
|------|-------------|-----------|
| `test_cost_estimation_exact` | Known token counts and pricing | Estimated cost matches exact formula output |
| `test_budget_enforcement_stops_pipeline` | Budget = $1, mock agents exceed $1 | Pipeline stops with budget exceeded |
| `test_cost_accumulation_across_iterations` | Multi-iteration run via mock | Total cost = sum of all agent costs |
| `test_calibration_adjusts_estimates` | Historical data available | Calibrated estimates closer to actuals |
| `test_cost_event_emission` | Agent invocations with costs | Cost events emitted with correct amounts |

### Suite 16: Trust & Autonomy (`test_trust_autonomy.py`)

**What it tests:** Trust score calculation and autonomy level decisions based on historical performance.

| Test | Description | Validates |
|------|-------------|-----------|
| `test_trust_score_new_spec` | No history | Default trust score, conservative autonomy |
| `test_trust_score_improves_with_success` | Multiple successful runs | Trust score increases |
| `test_trust_score_degrades_on_failure` | Failed runs | Trust score decreases |
| `test_autonomy_prediction` | Various trust scores | Correct autonomy level recommended |
| `test_anomaly_baseline_violation` | Turns/files exceed baseline | Anomaly flagged with details |

### Suite 17: Notification Dispatch (`test_notification_dispatch.py`)

**What it tests:** Notification triggers at pipeline milestones with real event data.

**Mock boundary:** `_send` mocked to capture payloads without making real HTTP/email calls.

| Test | Description | Validates |
|------|-------------|-----------|
| `test_ship_notification_content` | SHIP decision | Notification contains spec name, run ID, pass rate |
| `test_escalation_notification` | Pipeline escalation | Notification contains failure reason, iteration count |
| `test_webhook_payload_format` | Webhook notification | Valid JSON payload with expected fields |
| `test_notification_on_budget_exceeded` | Budget exceeded | Notification sent with cost details |
| `test_no_notification_on_dry_run` | Dry run pipeline | No notifications sent |

### Suite 18: Cleanup & Signal Handling (`test_cleanup_signals.py`) — NEW

**What it tests:** `CleanupManager` signal handling, atexit hooks, and Docker resource cleanup.

| Test | Description | Validates |
|------|-------------|-----------|
| `test_sigterm_triggers_cleanup` | Send SIGTERM to subprocess running cleanup manager | Docker containers stopped, networks removed |
| `test_sigint_triggers_cleanup` | Send SIGINT | Same cleanup behavior as SIGTERM |
| `test_atexit_cleanup` | Normal process exit | Registered cleanup functions called |
| `test_cleanup_idempotent` | Call cleanup twice | No errors on second call |
| `test_cleanup_with_no_resources` | No containers/networks registered | Cleanup completes without error |

---

## Implementation Priority

### Phase 0: Test Migration (Pre-requisite)
0. **Migrate existing tests from `tests/*.py` to `tests/unit/`**
   - Move all 53 test files to `tests/unit/`
   - Update any relative imports in test files
   - Update `tests/run-tests.sh` to reference new paths
   - Verify all existing tests pass from new location
   - Update CI workflow paths
   - This is a standalone PR before integration test work begins

### Phase 1: Foundation (Week 1)
1. Set up `tests/integration/` directory and `conftest.py` with all shared fixtures
2. Update `pyproject.toml` with markers and dev dependencies
3. Implement Suite 5 (Config Resolution) — dependency for all other suites
4. Implement Suite 1 (CLI E2E) — highest coverage-to-effort ratio
5. Implement Suite 2 (Pipeline Flow) — core business logic
6. Implement Suite 4 (State Lifecycle) — critical for reliability

### Phase 2: Core Subsystems (Week 2)
7. Implement Suite 3 (Iteration Loop) — complex orchestration internals
8. Implement Suite 10 (Scenario Lifecycle) — building block for quality gates
9. Implement Suite 9 (Quality Gate Chain) — depends on scenario lifecycle
10. Implement Suite 13 (Event Flow)

### Phase 3: Docker & Barriers (Week 3)
11. Implement Suite 8 (Docker Args) — no Docker needed, validates arg construction
12. Implement Suite 6 (Barrier Enforcement) — requires Docker for container tests
13. Implement Suite 7 (Docker Execution) — requires Docker
14. Implement Suite 14 (Git Workflow) — real git ops

### Phase 4: Advanced Features (Week 4)
15. Implement Suite 11 (Plan Execution)
16. Implement Suite 12 (Deployment Pipeline)
17. Implement Suite 15 (Cost & Budget)
18. Implement Suite 16 (Trust & Autonomy)
19. Implement Suite 17 (Notification Dispatch)
20. Implement Suite 18 (Cleanup & Signal Handling)

---

## CI Integration

### GitHub Actions Workflow

```yaml
# .github/workflows/integration-tests.yml
name: Integration Tests
on:
  push:
    branches: [main]
    paths:
      - 'darkfactory/**'
      - 'tests/**'
      - 'docker/**'
      - 'scripts/**'
      - 'pyproject.toml'
  pull_request:
    paths:
      - 'darkfactory/**'
      - 'tests/**'
      - 'docker/**'
      - 'scripts/**'
      - 'pyproject.toml'

jobs:
  unit-tests:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - uses: actions/cache@v4
        with:
          path: ~/.cache/pip
          key: ${{ runner.os }}-pip-${{ hashFiles('pyproject.toml') }}
      - run: pip install -e ".[dev]"
      - run: pytest tests/unit -v --timeout=60

  integration-tests:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - uses: actions/cache@v4
        with:
          path: ~/.cache/pip
          key: ${{ runner.os }}-pip-${{ hashFiles('pyproject.toml') }}
      - run: pip install -e ".[dev]"
      - run: pytest tests/integration -v -m "not docker" --timeout=120

  docker-integration-tests:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    needs: [unit-tests]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - uses: actions/cache@v4
        with:
          path: ~/.cache/pip
          key: ${{ runner.os }}-pip-${{ hashFiles('pyproject.toml') }}
      - run: pip install -e ".[dev]"
      # Build minimal test image (Alpine + bash, no claude-code)
      - run: docker build -t dark-factory-test -f tests/integration/Dockerfile.test .
      # Create test network and start proxy for egress tests
      - run: |
          docker network create df-pipeline-test || true
          docker run -d --name df-proxy-test --network df-pipeline-test \
            -v ${{ github.workspace }}/docker/tinyproxy-execution.conf:/etc/tinyproxy/tinyproxy.conf:ro \
            alpine:3.19 sh -c "apk add tinyproxy && tinyproxy -d"
      - run: pytest tests/integration -v -m "docker" --timeout=300
      - if: always()
        run: |
          docker rm -f df-proxy-test || true
          docker network rm df-pipeline-test || true
```

### Minimal Docker Test Image

```dockerfile
# tests/integration/Dockerfile.test
# Minimal image for barrier and container lifecycle tests.
# Does NOT include claude-code (avoids npm auth dependency in CI).
FROM alpine:3.19
RUN apk add --no-cache bash curl git
ENTRYPOINT ["bash"]
```

---

## Test Data & Fixtures

### Minimal Python Test Project

```
tests/integration/fixtures/projects/python-sample/
├── pyproject.toml           # name, pytest config, ruff config, mypy config
├── src/
│   └── calculator.py        # Simple module: add, subtract, multiply
│   └── __init__.py
└── tests/
    └── test_calculator.py   # 4-5 passing tests covering add/subtract/multiply
```

### Valid Spec File (passes completeness checklist)

```markdown
# tests/integration/fixtures/specs/complete-spec.md

# Spec: Add Multiply Function to Calculator

## Overview
Add a multiply function to the calculator module.

## Functional Requirements
- FR-1: `calculator.multiply(a, b)` returns `a * b` for integer inputs
- FR-2: `calculator.multiply(a, b)` returns `a * b` for float inputs
- FR-3: Handles negative numbers correctly

## Edge Cases
- EC-1: `multiply(0, x)` returns `0` for any `x`
- EC-2: `multiply(1, x)` returns `x` for any `x`
- EC-3: Very large numbers do not overflow (Python handles arbitrary precision)

## Error Conditions
- ERR-1: Non-numeric input raises `TypeError` with descriptive message
- ERR-2: `None` input raises `TypeError`

## Performance Requirements
- PERF-1: Completes in <1ms for standard inputs

## Security Considerations
- SEC-1: No code injection via string inputs (type checking prevents this)

## Source Files
- `src/calculator.py`

## Test Strategy
- Unit tests via pytest
- Scenarios cover all FR, EC, ERR categories
```

### Invalid Spec File (fails completeness checklist)

```markdown
# tests/integration/fixtures/specs/incomplete-spec.md

# Spec: Do Something

## Functional Requirements
- FR-1: It should work
```

### Pre-generated Scenario YAML

```yaml
# tests/integration/fixtures/scenarios/multiply-scenarios.yaml
scenarios:
  - name: basic_multiply
    category: happy_path
    spec_ref: FR-1
    assertions:
      - multiply(3, 4) == 12
  - name: float_multiply
    category: happy_path
    spec_ref: FR-2
    assertions:
      - multiply(2.5, 4.0) == 10.0
  - name: zero_multiply
    category: edge_case
    spec_ref: EC-1
    assertions:
      - multiply(0, 99) == 0
  - name: type_error
    category: error_condition
    spec_ref: ERR-1
    assertions:
      - multiply("a", 1) raises TypeError
```

### Pre-populated Events File

```jsonl
# tests/integration/fixtures/events/sample-run.jsonl
# Contains a complete event sequence for one pipeline run:
# spec_started, spec_validated, scenarios_generated,
# agent_started (implementer), agent_completed,
# gate_started, gate_completed,
# agent_started (reviewer), agent_completed,
# oracle_verdict (SHIP), spec_shipped
# Each line is valid JSON with uuid, timestamp, event_type, run_id, spec_name
```

---

## Success Criteria

| Metric | Target |
|--------|--------|
| Integration test count | 170+ tests across 18 suites |
| Line coverage (integration only) | >60% of darkfactory/ package |
| Combined coverage (unit + integration) | >85% |
| Non-Docker test runtime | <5 minutes |
| Docker test runtime | <15 minutes |
| Zero flaky tests | 100% deterministic |
| All CLI commands covered | 22/22 (all commands in _COMMANDS) |
| All pipeline steps covered | 7/7 |
| All agent roles barrier-tested | 7/7 |
| All iteration loop paths covered | trajectory, strategy, oracle overrides |

---

## Dependencies to Add

```toml
# pyproject.toml [project.optional-dependencies]
dev = [
    "pytest>=9.0",
    "pytest-timeout>=2.2",
    "pytest-xdist>=3.5",     # Parallel test execution
    "responses>=0.25",        # HTTP mocking for deployment tests
    "freezegun>=1.4",         # Time freezing for event/metric tests
]
```

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Docker tests slow in CI | Separate job with `timeout-minutes: 30`, runs only after unit tests pass |
| Docker images need claude-code auth | Use minimal Alpine test image (`Dockerfile.test`) for barrier/container tests |
| Flaky network tests | Mock HTTP with `responses` library, no real network calls |
| Flaky concurrency tests | Record start/end timestamps via mock, assert ordering constraints (not real timing) |
| Test project drift | Fixtures are minimal, pinned, version-controlled |
| Parallel test interference | Each test gets its own `tmp_path`, no shared state |
| Claude Code not available in CI | Agent invocations mocked at `execute_container` boundary |
| Git tests leave repo dirty | All git ops in `tmp_path` directories, never touch real repo |
| Test migration breaks existing CI | Phase 0 is a separate PR, verify before proceeding |
| Config fixtures diverge from production | `project_dir` fixture writes real `config.toml`, loaded via real `_load_config` |
| Deployment commands run for real | All deployment subprocess calls (`provision`, `deploy`, `rollback`, `flag`) mocked via `patch` |
