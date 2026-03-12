# Integration Test Coverage Expansion

**Status:** Planning Complete
**Total New Tests:** ~69 (61 integration + 8 unit)
**Reviewed by:** Neo (2 iterations, all concerns addressed)

---

## De-duplication Audit

The following were removed from the original scope to avoid overlap with existing tests:

- `TestSatisfactionScoring` (5 tests) — duplicates `tests/unit/test_satisfaction.py`
- `TestTrajectoryClassification` (5 tests) — duplicates `tests/unit/test_trajectory.py`
- `TestOracleOverrides` (3 tests) — duplicates `tests/integration/test_iteration_loop.py::TestOracleOverrides` and `TestCompositeAnomalyBlocksShip`
- `TestParseOracleDecision` — moved to unit tests (pure function, no I/O)
- `TestShellMetacharRejection` — covered by `tests/unit/test_command.py::TestValidateCommand`
- `TestAffectedSpecs` (4 tests) — duplicates `tests/unit/test_selective_regression.py::TestAffectedSpecs`
- Basic barrier violation detection — covered by `test_barrier_enforcement.py::TestAuditBarrierDetection`

### Key Corrections

- Lowercase "ship" parses as **SHIP** (not NO-SHIP) — `_parse_oracle_decision` calls `.upper()` before comparison
- `_MIN_OUTPUT_LEN = 20` confirmed in `darkfactory/pipeline/agents.py:29`
- `@pytest.mark.integration` required on all new integration test classes
- Threading tests need `@pytest.mark.slow`

---

## Phase 0: Conftest & Unit Test Additions

- [x] **0a. Conftest fixtures** — `tests/integration/conftest.py` (~60 lines)
- [x] **0b. Unit tests** — `tests/unit/test_oracle_parsing.py` (8 tests)

### 0a. Conftest Fixtures

Add to `tests/integration/conftest.py`:

1. **`registry_fixture(project_dir)`** — Pre-populates `.dark-factory/shipped-scenarios/` with 3 specs (add-auth, add-api, add-ui), each with scenario files and `.registry-meta` containing `target_files`. Returns dict mapping spec_name to target_files list. Used by `test_selective_regression.py`.

2. **`multi_spec_plan_fixture(project_dir)`** — Writes a plan markdown with 3 specs in a dependency chain (S1 independent, S2 depends on S1, S3 depends on S2) plus corresponding spec files. Returns `(plan_name, spec_names)`. Used by `test_plan_failure_recovery.py`.

3. **`mock_run_scenarios()`** — Patches `darkfactory.scenarios.runner.run_scenarios` with a SequenceMock. Used by `test_selective_regression.py`.

### 0b. `tests/unit/test_oracle_parsing.py` (8 tests)

Oracle decision parsing — pure function, no I/O. Relocated from integration scope.

| Test | What It Validates |
|---|---|
| `test_ship_exact` | "SHIP" -> "SHIP" |
| `test_ship_with_suffix` | "SHIP: approved" -> "SHIP" |
| `test_ship_whitespace` | "  SHIP  " -> "SHIP" (strip+upper) |
| `test_ship_lowercase` | "ship" -> "SHIP" (`.upper()` makes it match) |
| `test_no_ship` | "NO-SHIP" -> "NO-SHIP" |
| `test_empty_output` | "" -> "NO-SHIP" (default) |
| `test_multiline_ship_on_later_line` | "Summary\n\nSHIP" -> "NO-SHIP" (first non-empty line is "Summary") |
| `test_arbitrary_first_line` | "Looking good" -> "NO-SHIP" |

---

## Phase 1: Independent Test Files

These 5 files have no cross-dependencies. Can be implemented in any order.

- [x] **1a.** `tests/integration/test_command_security.py` (12 tests)
- [x] **1b.** `tests/integration/test_audit_integration.py` (8 tests)
- [x] **1c.** `tests/integration/test_output_parsing_integration.py` (7 tests)
- [x] **1d.** `tests/integration/test_framework_integration.py` (12 tests)
- [x] **1e.** `tests/integration/test_negative_paths.py` (13 tests)

### 1a. `test_command_security.py` (12 tests)

**Target:** `darkfactory/deployment/command.py` — `validate_command()`, `run_command()`
**Mock boundary:** Minimal. Real filesystem with tmp_path, real subprocess execution of small test scripts.
**Why integration:** Tests real filesystem interactions (symlinks, permissions, subprocess exec) that unit tests mock away.

| Test Class | Tests | What It Validates |
|---|---|---|
| `TestPathTraversalRejection` | 3 | `../../etc/passwd`, absolute path outside project, symlink chain escaping project |
| `TestValidCommandExecution` | 3 | Script in project executes successfully, with args, correct working directory |
| `TestTimeoutEnforcement` | 1 | Script sleeping 10s killed after 2s timeout |
| `TestEnvironmentIsolation` | 2 | Env vars passed to script, don't leak between sequential calls |
| `TestSymlinkAttack` | 2 | Symlink to outside project rejected, chained symlinks resolved and rejected |
| `TestConcurrentExecution` | 1 | Two `run_command` calls in threads, env isolation maintained |

**Notes:**
- Metacharacter rejection already covered by `tests/unit/test_command.py` (6 tests)
- Non-executable/directory rejection also covered there
- Use `.resolve()` on both sides for symlink assertions (macOS `/tmp` -> `/private/tmp`)
- Timeout test uses `sleep 10` script with timeout=2

**Pattern:** Create executable scripts in tmp_path via `write_text()` + `os.chmod(0o755)`.

### 1b. `test_audit_integration.py` (8 tests)

**Target:** `darkfactory/audit/checker.py` — `check_output_for_barriers()`, `validate_oracle_feedback()`
**Mock boundary:** Minimal. Real filesystem for audit log and events file.
**Why integration:** Tests event emission + audit log persistence — the side effects that unit tests don't cover.

| Test Class | Tests | What It Validates |
|---|---|---|
| `TestBarrierViolationEvents` | 3 | Single violation -> event in events.jsonl + audit.log entry; multiple violations -> all logged; clean output -> no events |
| `TestOracleFeedbackLeakageEvents` | 3 | Leakage detected -> audit.log entry; mixed patterns (scenario filename + assertion DSL) -> both flagged; clean feedback -> nothing logged |
| `TestAuditCompositeIntegration` | 2 | Barrier violation feeds into composite anomaly scoring -> score >= 70 blocks SHIP; no violations -> score 0 |

**Notes:**
- Basic violation detection and pattern matching already covered by `test_barrier_enforcement.py`
- This file focuses on **event/audit persistence integration** only

### 1c. `test_output_parsing_integration.py` (7 tests)

**Target:** `darkfactory/pipeline/quality_gate.py` (`_parse_scenario_counts`), pipeline output flow
**Mock boundary:** Mock `execute_container`. Real: env file parsing, gate result construction, floor enforcement.
**Why integration:** Tests the full gate -> parse -> floor enforcement -> iteration state flow.

| Test Class | Tests | What It Validates |
|---|---|---|
| `TestScenarioCountsParsing` | 4 | Normal env file, missing keys default to 0, malformed values default to 0, empty file |
| `TestGateFloorEnforcement` | 3 | Gate passes but satisfaction < floor -> forced failure; gate passes but pass_rate < floor -> forced failure; both above floor -> gate passes through |

**Notes:**
- Oracle decision parsing moved to `tests/unit/test_oracle_parsing.py`
- Satisfaction scoring and trajectory classification have comprehensive unit test coverage
- This file tests only integration seams: env file parsing and floor enforcement

### 1d. `test_framework_integration.py` (12 tests)

**Target:** `darkfactory/integration/integrator.py` — `integrate()`
**Mock boundary:** Mock subprocess for Docker build. Real: filesystem operations, config generation, CLAUDE.md updates.

| Test Class | Tests | What It Validates |
|---|---|---|
| `TestFullIntegration` | 2 | All required dirs created; config.toml + CLAUDE.md section generated |
| `TestLanguageDetection` | 3 | Python (pyproject.toml), Node (package.json), Go (go.mod) -> correct config defaults |
| `TestIdempotent` | 2 | integrate() twice -> no errors, no duplicate CLAUDE.md sections |
| `TestForceFlag` | 1 | force=True overwrites existing framework files |
| `TestSymlinkMode` | 1 | symlink=True creates symlinks not copies |
| `TestDryRun` | 1 | No files created, returns True |
| `TestDockerUnavailable` | 1 | Docker build fails -> integration still succeeds with warning |
| `TestMissingFrameworkDir` | 1 | Clear error when framework_dir doesn't exist |

**Pattern:** Create target project + framework_dir in tmp_path. For language detection, create minimal marker files.

### 1e. `test_negative_paths.py` (11 tests)

**Target:** `darkfactory/pipeline/orchestrator.py`, `darkfactory/pipeline/agents.py`
**Mock boundary:** Mock `invoke_agent` with specific failure modes. Use `pipeline_env` pattern from `test_pipeline_flow.py`. Real: state store, events, error handling.

| Test Class | Tests | What It Validates |
|---|---|---|
| `TestAgentEmptyOutput` | 2 | Output < 20 bytes with exit 0 -> forced exit 99; exactly 20 bytes -> ok |
| `TestAgentTimeout` | 2 | Exit 137 -> classified as transient, retried; exit 1 -> not transient |
| `TestTransientRetry` | 2 | Retry succeeds on 2nd attempt -> pipeline continues; all retries exhausted -> pipeline fails |
| `TestCorruptedStateResume` | 1 | Corrupted/truncated state file -> clear error on resume attempt |
| `TestMissingConfig` | 1 | No config.toml -> ConfigError at startup |
| `TestSpecDeletedMidRun` | 1 | Spec file removed between validation and implementation -> error |
| `TestEventsWriteFailure` | 1 | Read-only events file -> pipeline continues (graceful degradation) |
| `TestConcurrentPipelineRuns` | 1 | Two threads, same spec -> state locking prevents corruption; assert both complete without JSONDecodeError, final state file is valid JSON (`@pytest.mark.slow`) |

**Pattern:** Follow `test_pipeline_flow.py`. Use `pipeline_env` fixture. For transient failures, have SequenceMock raise `TransientFailure` on first call then return success.

---

## Phase 2: Tests with Conftest Dependencies

Depend on Phase 0 fixtures. Can be parallelized with each other.

- [x] **2a.** `tests/integration/test_plan_failure_recovery.py` (6 tests)
- [x] **2b.** `tests/integration/test_selective_regression.py` (8 tests)

### 2a. `test_plan_failure_recovery.py` (6 tests)

**Target:** `darkfactory/plan/executor.py` — `execute_plan()`, `_dispatch()`
**Mock boundary:** Mock `run_pipeline` and `worktree`. Real: plan parsing, topo sort, checkpoint, budget, events.
**Depends on:** `multi_spec_plan_fixture` from Phase 0.

**Do NOT duplicate** (already in `test_plan_dispatch.py`):
- Basic failure-blocks-dependents (TestPlanDispatch)
- Budget skipping (TestPlanBudget)
- Checkpoint/resume (TestPlanCheckpointResume)
- Wall-time limit (TestPlanWallTimeLimit)

| Test Class | Tests | What It Validates |
|---|---|---|
| `TestDependencyFailureCascade` | 2 | 3-spec chain: S1 ships, S2 fails -> S3 DEPENDENCY_FAILED (not queued); 3 independent specs: S1 ships, S2 fails, S3 still runs and ships |
| `TestPlanConcurrency` | 2 | max_concurrent=2, 3 independent specs -> verify at most 2 run simultaneously (use `time.monotonic` mock + `threading.Barrier`); verify dispatch events include concurrency metadata |
| `TestWorktreeCleanup` | 1 | Shipped spec's worktree cleaned up; failed spec's worktree preserved for debugging |
| `TestResumeWithChangedSpec` | 1 | Resume after spec hash changes -> spec re-run (not skipped). **Verify first:** check `_handle_resume()` in `executor.py` to confirm checkpoint stores spec hashes. If hashes aren't tracked, skip this test. |

**Pattern:** Follow `test_plan_dispatch.py` — use `_write_plan()`, `_write_spec()`, `_config()` helpers, patch `run_pipeline` and `worktree`.

### 2b. `test_selective_regression.py` (8 tests)

**Target:** `darkfactory/scenarios/coverage.py`, `darkfactory/scenarios/regression.py`, `darkfactory/state/registry.py`
**Mock boundary:** Mock `run_scenarios` (requires Docker). Real: registry, coverage, filesystem, events, FileLock.
**Depends on:** `registry_fixture`, `mock_run_scenarios` from Phase 0.

| Test Class | Tests | What It Validates |
|---|---|---|
| `TestRegressionLifecycle` | 4 | Full cycle (register -> change -> affected -> run), exclude list honored, empty registry -> (0,0,0), events emitted |
| `TestSymlinkLifecycle` | 2 | _link_scenarios creates symlink -> test runs -> _unlink_scenarios removes cleanly; no dangling symlinks after cleanup |
| `TestRegistryConcurrency` | 1 | Concurrent register/deregister with FileLock, 2 threads, 5s timeout (`@pytest.mark.slow`) |
| `TestRegistryMigration` | 1 | migrate_from_state populates registry from legacy state files |

**Note:** `TestAffectedSpecs` removed — exact duplicate of `tests/unit/test_selective_regression.py::TestAffectedSpecs`.

---

## Phase 3: Fixture Projects (Optional, Lower Priority)

- [x] **3.** `tests/integration/test_fixture_projects.py` (1 test — trimmed from 5 after review)

### Implementation Notes

Original plan called for 2 committed fixture directories and 5 tests. After code review (3 iterations),
most proposed tests duplicated existing coverage in `test_config_resolution.py`, `test_barrier_enforcement.py`,
or `test_scenario_lifecycle.py`. Committed fixture directories replaced with `tmp_path` runtime construction.

**Final scope:** 1 focused end-to-end integration test (`TestMultiSourceDirProject::test_config_to_barrier_end_to_end`)
that validates TOML config with path-separator source_dirs (`src/python`, `src/js`) flows through `load_config` →
`configure_barriers` → role mask verification → scenario detection. No other test covers this full path with
multi-component source dirs.

**Dropped (with rationale):**
- `TestNestedTestFixture` (2 tests) — tested `pathlib.rglob` (stdlib) and duplicated `test_config_resolution.py`
- `test_scenario_detection_finds_files` — trivially covered by existing scenario tests
- `test_spec_validation_with_multi_source` — `validate_spec_result` is unrelated to source dirs
- `test_scenario_fallback_detection` — exact duplicate of `test_scenario_lifecycle.py::test_fallback_any_file_present`
- Committed fixture directories (`multi-language/`, `nested-tests/`) — unnecessary; `tmp_path` is self-contained

---

## Critical Files Reference

| File | Why |
|---|---|
| `tests/integration/conftest.py` | Fixture patterns, SequenceMock, factory helpers |
| `tests/integration/test_pipeline_flow.py` | `pipeline_env` pattern (template for test_negative_paths.py) |
| `tests/integration/test_plan_dispatch.py` | Plan test patterns, existing coverage to avoid duplication |
| `tests/integration/test_barrier_enforcement.py` | Barrier test patterns (for test_audit_integration.py) |
| `tests/integration/test_iteration_loop.py` | `iter_env` pattern, existing oracle override tests |
| `darkfactory/pipeline/agents.py:29` | `_MIN_OUTPUT_LEN = 20` constant |
| `darkfactory/pipeline/iteration.py:520-529` | `_parse_oracle_decision` (uses `.upper()`) |
| `darkfactory/deployment/command.py` | `validate_command` / `run_command` signatures |
| `darkfactory/integration/integrator.py` | `integrate()` signature and steps |

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Overlap with existing tests | De-duplication audit completed; removed 40+ duplicate tests; explicit "do not duplicate" list for plan tests |
| macOS symlink resolution (`/tmp` -> `/private/tmp`) | Always call `.resolve()` on both sides in containment assertions |
| Threading test flakiness | 2 threads only, 5s timeout, `@pytest.mark.slow`, use `threading.Barrier` for deterministic synchronization |
| Private function imports | `_parse_oracle_decision`, `_parse_scenario_counts` are stable internal APIs |
| CI time increase | ~61 integration tests using tmp_path, no Docker/network; expected < 10s additional |

## Verification

After each phase, run:

```bash
# Run new tests for the phase
pytest <new-test-files> -v

# Verify no regressions
pytest tests/ -v --timeout=120
```
