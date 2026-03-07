# Phase 6: Progressive Deployment — Implementation Plan

**Date:** 2026-03-06
**Scope:** Transform the single-shot deploy mechanism into a progressive deployment pipeline with ephemeral environments, canary analysis, feature flags, metric-based rollback, and expand-contract migration enforcement.

**Prerequisite reading:** `docs/reviews/2026-03-04-production-readiness-analysis.md` Section 4.5–4.6, 4.12

---

## Implementation Progress

| Tier | Status | Notes |
|------|--------|-------|
| Tier 1: Command Safety + Ephemeral Environments | **COMPLETE** | Reviewed by Linus (2 rounds) |
| Tier 2: Migration Safety | **COMPLETE** | Reviewed by Linus (2 rounds) |
| Tier 3: Feature Flags | **COMPLETE** | Reviewed by Linus (2 rounds) |
| Tier 4: Canary Deployment + Metrics Gate | **COMPLETE** | Reviewed by Linus (2 rounds) |
| Tier 5: Multi-Layer Rollback | **COMPLETE** | Reviewed by Linus (2 rounds) |
| Tier 6: Post-Ship Orchestration + Full Wiring | **COMPLETE** | Reviewed by Linus (2 rounds) |

**Test count after all 6 tiers:** 491 tests passing (346 original + 145 new), zero regressions.

### What was built (Tiers 1-5)

**New files created:**
- `darkfactory/deployment/__init__.py` — package init
- `darkfactory/deployment/models.py` — DeploymentStatus, RollbackLayer, CanaryStage, MetricThreshold, CanaryResult, DeploymentResult, MigrationCheckResult
- `darkfactory/deployment/command.py` — safe command execution (validate_command + run_command, metachar rejection, symlink-safe path containment)
- `darkfactory/deployment/environment.py` — ephemeral env provisioning (compose-first, health check with exponential backoff, teardown with orphan cleanup)
- `darkfactory/deployment/migration_check.py` — expand-contract enforcement (SQL, Python ORM, JS ORM patterns, user-extensible)
- `darkfactory/deployment/feature_flags.py` — CRUD flag operations via user commands, graceful degradation
- `darkfactory/deployment/metrics_gate.py` — metric collection + threshold evaluation (max/min bounds, fail-safe float('inf'))
- `darkfactory/deployment/progressive.py` — canary orchestration (staged traffic shifting, FileLock, soak with abort, Abortable protocol)
- `darkfactory/deployment/rollback.py` — 4-layer cascade (flag kill → traffic revert → container rollback → git revert PR)

**New test files created:**
- `tests/test_command.py` — 16 tests
- `tests/test_environment.py` — 16 tests
- `tests/test_migration_check.py` — 25 tests
- `tests/test_feature_flags.py` — 12 tests
- `tests/test_metrics_gate.py` — 19 tests
- `tests/test_progressive.py` — 13 tests
- `tests/test_rollback.py` — 14 tests

**Modified files:**
- `darkfactory/models/config.py` — ~37 new config fields, validate() method, canary validation in __post_init__
- `darkfactory/config/defaults.py` — CANARY_FIELDS, FLAGS_FIELDS, MIGRATION_FIELDS added to SECTION_MAP
- `darkfactory/config/loader.py` — canary_stages → CanaryStage and canary_metrics → MetricThreshold deserialization
- `darkfactory/models/events.py` — 20 new EventType enum values
- `darkfactory/events/types.py` — 20 new emit_* helper functions
- `darkfactory/pipeline/cleanup.py` — abort_requested field, deploy_cmd_allow_shell/project_dir fields, safe teardown
- `darkfactory/pipeline/evaluator.py` — converted to thin deprecation shim delegating to deployment.environment

### Key Linus review findings (fixed)

**Round 1 (6 blocking issues):**
- run_command was not calling validate_command internally — fixed (safety is now mandatory, not opt-in)
- "." project_dir in teardown — fixed (explicit project_dir parameter)
- Dead orphan cleanup code — fixed (wired into teardown flow)
- deploy_cmd_allow_shell bypassing all validation — fixed (scoped to _LEGACY_SHELL_FIELDS only)
- cleanup.py using raw bash -c — fixed (uses run_command when project_dir available)
- Competing deployment subsystems — fixed (evaluator.py is now a thin shim)

**Round 2 (3 critical + 7 quality issues):**
- Shell injection in create_flag — fixed (all flag ops use run_command uniformly)
- git revert without -m 1 — fixed (handles merge commits correctly)
- No HEAD save/restore in L4 rollback — fixed (_cleanup_revert_branch helper)
- DELETE FROM regex multi-line bug — fixed (same-statement scoping with [^;]*)
- Lock timeout returning PROMOTED — fixed (returns FAILED)
- Loose grep in git log — fixed (anchored ^dark-factory: {spec_name}$)
- Plus: stale docstring, inconsistent execution paths, object typing issues — all fixed

### Tier 6 implementation details

Tier 6 wired all standalone modules into the pipeline orchestrator:

- **`darkfactory/pipeline/orchestrator.py`** — new public `post_ship_pipeline()` method; modified `_handle_ship()` to run canary AFTER PR creation but BEFORE merge; on canary failure: close PR with branch name; compose_project persisted to state for resume; rollback notifications wired
- **`darkfactory/cli.py`** — added `deploy` subcommand for manual deployment trigger
- **`darkfactory/models/pipeline.py`** — extended PipelineStatus with DEPLOYING/DEPLOYED/ROLLED_BACK; extended SpecRun with deployment_result and deployment_step
- **`darkfactory/notify/dispatcher.py`** — added 5 new notification functions (canary_promoted, canary_failed, rollback, rollback_failed, flag_killed)
- **`darkfactory/git/ops.py`** — added `close_pr()` with branch parameter
- **`tests/test_post_ship_pipeline.py`** — 28 integration tests

### Phase 6 — COMPLETE

All 6 tiers implemented, Linus-reviewed (2 rounds each), 491 tests passing, zero regressions.

---

## Table of Contents

1. [Current State](#1-current-state)
2. [Target State](#2-target-state)
3. [Architecture](#3-architecture)
4. [Module Design](#4-module-design)
5. [Implementation Tiers](#5-implementation-tiers)
6. [Config Schema Changes](#6-config-schema-changes)
7. [Integration Points](#7-integration-points)
8. [Testing Strategy](#8-testing-strategy)
9. [Risk Register](#9-risk-register)
10. [Acceptance Criteria](#10-acceptance-criteria)

---

## 1. Current State

The pipeline today has a single deploy mechanism wired into the iteration loop as step 4.5:

```
Quality Gate (step 4) → [Deploy → Health Check → Evaluator → Teardown] → Review (step 5) → Oracle (step 6)
```

**What exists:**
- `darkfactory/pipeline/evaluator.py`: `deploy(config)`, `health_check(config, timeout)`, `teardown(config)` — wraps `config.deploy_cmd` / `config.deploy_teardown_cmd` via subprocess
- `darkfactory/pipeline/cleanup.py`: `CleanupManager` calls `deploy_teardown_cmd` on SIGTERM/SIGINT/atexit
- Config fields: `deploy_cmd`, `deploy_base_url`, `deploy_health_endpoint`, `deploy_health_timeout`, `deploy_teardown_cmd` (all strings, all optional)
- The evaluator agent runs in a Docker container with source code masked, exercises the deployed application, writes reports

**What's missing:**
- No automated environment provisioning (user must provide `DEPLOY_CMD` manually)
- No staged rollout (quality gate pass → immediately "shipped")
- No canary analysis or traffic shifting
- No feature flag integration
- No automated rollback triggers
- No migration safety enforcement
- No deployment-specific metrics or health monitoring

---

## 2. Target State

### 2.1 Deployment Model: Pre-Merge Canary

**Critical architectural decision:** The post-ship deployment pipeline runs AFTER PR creation but BEFORE merge/auto-merge. This means:

```
Oracle SHIP
  ↓
PR Creation (branch pushed, PR opened — NOT merged yet)
  ↓
Ephemeral Environment Provisioning (from the PR branch)
  ↓
Behavioral Evaluation (evaluator agent, mandatory when deploy configured)
  ↓
Migration Safety Check (expand-contract enforcement)
  ↓
Feature Flag Registration (flag created, OFF by default)
  ↓
Canary Deployment (1% → 10% → 50% → 100%, configurable stages)
  ↓ (at each stage)
Metric Collection → Threshold Evaluation → Proceed / Rollback
  ↓
Full Promotion
  ↓ (on success)
Add to Merge Queue / Auto-Merge          ← merge happens AFTER canary passes
  ↓
Rollback Window (configurable, default 1h)
  ↓
Deployment Complete (cleanup ephemeral, archive metrics)
```

**Why pre-merge:** If canary fails, the PR is simply closed — no revert commit needed, no merge queue race, no direct push to main. L4 (git revert) is reserved for the post-merge rollback window, where a regression is detected after the code has already been merged. This eliminates the race conditions between merge queue and rollback identified in the architectural review.

**On canary failure:** Close the PR, emit `CANARY_FAILED` event, notify. The spec is marked `ROLLED_BACK` (not `SHIPPED`). A follow-up spec can be created to address the failure.

**On post-merge rollback window failure:** L4 (git revert) creates a *new PR* for the revert — never pushes directly to main. This preserves the PR review workflow even for emergency rollbacks.

### 2.2 Key Design Constraints

- **Zero external dependencies.** Stdlib only, consistent with the rest of Dark Factory. External tools (Unleash, Argo, Flagger) are invoked via user-provided commands, not imported as libraries.
- **Command-based extensibility.** Feature flag operations, canary traffic shifting, metric collection, and rollback all delegate to user-configured commands. Dark Factory orchestrates the workflow; the user's infrastructure executes it.
- **Fail-safe defaults.** If no deployment config is provided, the pipeline behaves exactly as today (SHIP → PR → merge → done). Progressive deployment is opt-in.
- **Layered rollback.** Four rollback layers (flag kill → traffic revert → container rollback → git revert PR), each faster than the next, each independently configurable.
- **Command safety.** All `*_cmd` config fields are validated as script paths (not arbitrary shell strings), per Section 4.12 of the production readiness analysis.

---

## 3. Architecture

### 3.1 New Modules

```
darkfactory/deployment/
├── __init__.py
├── environment.py      # Ephemeral environment lifecycle
├── progressive.py      # Canary orchestration + traffic shifting
├── rollback.py         # Multi-layer rollback engine
├── feature_flags.py    # Feature flag create/enable/disable/check
├── metrics_gate.py     # Metric collection + threshold evaluation
├── migration_check.py  # Expand-contract migration enforcement
├── command.py          # Safe command execution + validation
└── models.py           # Deployment-specific dataclasses

darkfactory/config/
└── validation.py       # Command path validation (new file, or extend existing)
```

### 3.2 Post-Ship Pipeline Flow

```
                    ┌──────────────────────────────────────────────────┐
                    │          Post-Ship Deployment Pipeline            │
                    │                                                    │
                    │  ┌─────────────────┐                              │
                    │  │  Environment     │  docker-compose up           │
                    │  │  Provisioning    │  health check w/ backoff     │
                    │  └────────┬────────┘                              │
                    │           ↓                                        │
                    │  ┌─────────────────┐                              │
                    │  │  Behavioral     │  evaluator agent              │
                    │  │  Evaluation     │  mandatory when deploy        │
                    │  │                 │  configured via compose_file  │
                    │  └────────┬────────┘                              │
                    │           ↓                                        │
                    │  ┌─────────────────┐                              │
                    │  │  Migration      │  expand-contract check        │
                    │  │  Safety Check   │  block destructive+additive   │
                    │  └────────┬────────┘                              │
                    │           ↓                                        │
                    │  ┌─────────────────┐                              │
                    │  │  Feature Flag   │  create flag, default OFF     │
                    │  │  Registration   │                               │
                    │  └────────┬────────┘                              │
                    │           ↓                                        │
                    │  ┌─────────────────────────────────────────┐      │
                    │  │  Canary Stages (configurable)            │      │
                    │  │                                           │      │
                    │  │  for each stage in [1%, 10%, 50%, 100%]: │      │
                    │  │    1. shift_traffic(stage.percent)        │      │
                    │  │    2. wait(stage.soak_seconds)            │      │
                    │  │    3. collect_metrics()                   │      │
                    │  │    4. evaluate_thresholds()               │      │
                    │  │       → PASS: continue to next stage      │      │
                    │  │       → FAIL: rollback(layer), close PR   │      │
                    │  │                                           │      │
                    │  └────────┬──────────────────────────────────┘      │
                    │           ↓                                        │
                    │  ┌─────────────────┐                              │
                    │  │  Merge to Main  │  merge queue / auto-merge     │
                    │  └────────┬────────┘                              │
                    │           ↓                                        │
                    │  ┌─────────────────┐                              │
                    │  │  Rollback Window │  post-merge monitoring        │
                    │  │  (configurable)  │  L4 revert creates new PR    │
                    │  └─────────────────┘                              │
                    └──────────────────────────────────────────────────┘
```

### 3.3 Rollback Layers

| Layer | Speed | Mechanism | Config Key | When Used |
|-------|-------|-----------|------------|-----------|
| L1: Flag kill | Seconds | Disable feature flag | `flag_disable_cmd` | First response to any anomaly |
| L2: Traffic revert | Seconds-minutes | Shift traffic back to stable | `canary_rollback_cmd` | Canary metric threshold breach |
| L3: Container rollback | Minutes | Redeploy previous version | `deploy_rollback_cmd` | Post-canary failure |
| L4: Git revert | Minutes-hours | `git revert` → new PR → CI | git ops (built-in) | Post-merge rollback window only |

Rollback cascades: if L1 fails, try L2; if L2 fails, try L3; etc. Each layer emits events and notifications.

**Pre-merge failures (canary):** L1 → L2 → L3 → close PR. L4 is not used because nothing has been merged.

**Post-merge failures (rollback window):** L1 → L2 → L3 → L4 (git revert as new PR).

### 3.4 Post-Merge Rollback Window

After canary passes and the code is merged, the pipeline enters a rollback window (default 1 hour). Two monitoring modes are supported:

**Mode A: Pipeline-resident monitoring (default)**
The pipeline process stays alive and polls canary metrics every `rollback_poll_interval` seconds (default: 60s). If any metric breaches a threshold, the cascade rollback fires (L1→L2→L3→L4). The pipeline emits `ROLLBACK_WINDOW_EXPIRED` when the window closes without incident, then exits.

**Tradeoff:** The pipeline process is alive for the full rollback window. For a 1-hour window, this means ~1 hour of CI runner time. This is acceptable for self-hosted runners but expensive for cloud runners. The `rollback_window_seconds = 0` setting disables the rollback window entirely (canary promotion is the final step).

**Mode B: External monitoring (opt-in)**
Set `rollback_monitor_cmd` to a script that handles post-merge monitoring externally (e.g., a cron job, a Prometheus alerting rule, a PagerDuty integration). When set, the pipeline calls the command with `DF_SPEC_NAME`, `DF_RUN_ID`, `DF_ROLLBACK_CMD` env vars and exits immediately after merge. The external monitor is responsible for triggering rollback if needed.

**When `rollback_monitor_cmd` is set:** Pipeline emits `DEPLOY_COMPLETE`, calls the monitor command, and exits. No polling loop. The `rollback_window_seconds` value is passed to the monitor as `DF_ROLLBACK_WINDOW_S` for informational use.

**When `rollback_monitor_cmd` is not set and `rollback_window_seconds > 0`:** Mode A (pipeline-resident polling).

**When `rollback_window_seconds = 0`:** No rollback window. Pipeline emits `DEPLOY_COMPLETE` immediately after merge.

### 3.5 Concurrent Canary Locking

When multiple specs ship concurrently (plan-level parallelism from Phase 4), canary deployment must be serialized:

**Lock mechanism:** File-based lock using the existing `darkfactory/state/lock.py` `FileLock` class. Lock path: `{project_dir}/.dark-factory/state/canary.lock`.

**Behavior:**
- Lock acquisition timeout: `canary_lock_timeout` config (default: 600s / 10 minutes).
- On timeout: skip canary, merge directly (degrade to pre-Phase-6 behavior), emit `CANARY_LOCK_TIMEOUT` event + notification.
- Stale detection: Lock file contains PID. On acquisition failure, check if PID is alive via `os.kill(pid, 0)`. If not alive, break stale lock and log warning.
- Each spec deploys to the same canary infrastructure (not separate environments). This is by design — the canary validates the spec against the real deployment target.

---

## 4. Module Design

### 4.1 `deployment/command.py` — Safe Command Execution

```python
"""Safe command execution with path validation.

Addresses Section 4.12 requirement: 'Replace eval with safe execution.
Require DEPLOY_CMD to be a path to a script, not an arbitrary shell string.
Validate the path exists and is a regular file before execution.'
"""

import os
import re
import subprocess
from pathlib import Path

# Shell metacharacters that indicate arbitrary shell, not a script path
SHELL_METACHAR_RE = re.compile(r'[;|&`$()]')


def validate_command(cmd: str, project_dir: str, field_name: str) -> Path:
    """
    Validate that a command config value is a safe script path.

    1. Extract script path (first whitespace-delimited token).
    2. Resolve relative to project_dir.
    3. Verify it is a regular file (not symlink to outside project, not dir).
    4. Verify it is executable.
    5. Reject if cmd contains shell metacharacters (;, |, &, `, $, parens).

    Raises ValueError with descriptive message on validation failure.
    Called from PipelineConfig.__post_init__() for all *_cmd fields.
    """

def run_command(cmd: str, project_dir: str, env: dict | None = None,
                timeout: int = 120, capture: bool = False) -> subprocess.CompletedProcess:
    """
    Execute a validated command safely.

    Uses subprocess.run([validated_path, *args], ...) — NOT bash -c.
    Inherits env from os.environ, overlays with provided env dict.
    On timeout: raises subprocess.TimeoutExpired.
    On non-zero exit: returns CompletedProcess (caller decides severity).
    """
```

**Design decisions:**
- All existing `*_cmd` fields (`deploy_cmd`, `deploy_teardown_cmd`) are also migrated to this validation. This is a breaking change for users who put inline shell in `deploy_cmd`, but it closes the Section 4.12 requirement. A `deploy_cmd_allow_shell = false` escape hatch is provided for migration (default: false, meaning safe execution; true reverts to `bash -c` with a deprecation warning).
- Validation runs via a `PipelineConfig.validate(project_dir)` method called by the orchestrator after config loading (not in `__post_init__`). This is necessary because `PipelineConfig` is a pure data container that does not receive `project_dir` at construction time — the orchestrator passes it separately to `run_pipeline()`. The `validate()` method is called once at pipeline start, before any commands are executed. This preserves the fail-fast property while respecting the existing config architecture.
- Symlink resolution uses `Path.resolve()` and checks the resolved path is within `project_dir` or is an absolute path to a known system location (`/usr/bin/`, `/usr/local/bin/`).

### 4.2 `deployment/models.py` — Dataclasses

```python
"""Deployment-specific data models."""

import enum
from dataclasses import dataclass, field


class DeploymentStatus(enum.Enum):
    PENDING = "pending"
    PROVISIONING = "provisioning"
    EVALUATING = "evaluating"
    CANARY = "canary"
    PROMOTED = "promoted"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class RollbackLayer(enum.Enum):
    FLAG_KILL = "flag_kill"         # L1
    TRAFFIC_REVERT = "traffic_revert"  # L2
    CONTAINER_ROLLBACK = "container_rollback"  # L3
    GIT_REVERT = "git_revert"      # L4


@dataclass
class CanaryStage:
    """One stage in the canary rollout."""
    percent: int          # Traffic percentage (1-100)
    soak_seconds: int     # How long to observe at this percentage
    name: str = ""        # Human-readable label (auto-generated if empty)

    def __post_init__(self):
        if not 1 <= self.percent <= 100:
            raise ValueError(f"CanaryStage percent must be 1-100, got {self.percent}")
        if self.soak_seconds < 0:
            raise ValueError(f"CanaryStage soak_seconds must be >= 0, got {self.soak_seconds}")
        if not self.name:
            self.name = f"canary-{self.percent}pct"


@dataclass
class MetricThreshold:
    """A single metric threshold for canary evaluation."""
    name: str              # e.g., "error_rate", "latency_p99"
    max_value: float | None = None   # Upper bound — breach if observed > max_value
    min_value: float | None = None   # Lower bound — breach if observed < min_value
    metric_cmd: str = ""             # Command that outputs the current value (stdout, plain float)

    def __post_init__(self):
        if self.max_value is None and self.min_value is None:
            raise ValueError(f"MetricThreshold '{self.name}' must have max_value, min_value, or both")


@dataclass
class CanaryResult:
    """Result of one canary stage evaluation."""
    stage: CanaryStage
    passed: bool
    metrics: dict[str, float]       # name → observed value
    breached: list[str] = field(default_factory=list)  # names of breached thresholds
    wall_seconds: float = 0.0


@dataclass
class DeploymentResult:
    """Overall deployment result."""
    status: DeploymentStatus
    stages_completed: int
    stages_total: int
    rollback_layer: RollbackLayer | None = None
    rollback_reason: str = ""
    wall_seconds: float = 0.0
    error: str = ""


@dataclass
class MigrationCheckResult:
    """Result of migration safety analysis."""
    safe: bool
    destructive_ops: list[str] = field(default_factory=list)  # e.g., ["DROP TABLE users"]
    additive_ops: list[str] = field(default_factory=list)     # e.g., ["ADD COLUMN email"]
    mixed: bool = False            # True if destructive + additive in same file
    files_checked: list[str] = field(default_factory=list)
```

### 4.3 `deployment/environment.py` — Ephemeral Environment Lifecycle

```python
"""Ephemeral environment provisioning, health verification, and teardown."""

# Responsibilities:
# 1. Provision ephemeral environment via docker-compose or user command
# 2. Health check with exponential backoff (wall-clock deadline)
# 3. Deterministic teardown — even on pipeline failure (via CleanupManager)
# 4. TTL enforcement — auto-teardown after configurable max lifetime

# Key functions:
def provision(config, project_dir, spec_name, run_id) -> EnvironmentInfo:
    """
    Provision an ephemeral environment.

    Strategy (in priority order):
    1. If config.deploy_compose_file is set: docker-compose -f {file} up -d
    2. If config.deploy_cmd is set: run the command (backward-compatible)
    3. Else: skip (no deployment configured), return None

    Returns EnvironmentInfo(base_url, container_ids, compose_project).
    Registers teardown with CleanupManager.
    """

def health_check(base_url, endpoint, timeout, max_retries=10) -> bool:
    """
    Poll health endpoint with exponential backoff.

    Uses a wall-clock deadline (time.monotonic), matching existing evaluator.py.
    Backoff: 1s, 2s, 4s, 8s, 16s... capped at 30s per inter-attempt delay.
    Per-attempt: curl --connect-timeout 5 --max-time 10.
    Total budget: 'timeout' seconds from first attempt start.

    With default timeout=60s, expect ~5 attempts:
      Attempt 1 @ t=0   (up to 10s curl + 1s sleep)
      Attempt 2 @ t=11  (up to 10s curl + 2s sleep)
      Attempt 3 @ t=23  (up to 10s curl + 4s sleep)
      Attempt 4 @ t=37  (up to 10s curl + 8s sleep)
      Attempt 5 @ t=55  (up to 10s curl) → deadline @ t=60

    Returns True if 2xx received within timeout.
    """

def teardown(config, compose_project=None) -> None:
    """
    Deterministic teardown.

    1. If compose_project: docker-compose -p {project} down --volumes --remove-orphans
    2. If config.deploy_teardown_cmd: run command via deployment.command.run_command()
    3. Remove any orphaned containers by label df-deploy-{run_id}
    """
```

**Design decisions:**
- `docker-compose` is invoked via subprocess, not imported as a library (zero-dependency constraint).
- Environment variables passed to compose: `DF_SPEC_NAME`, `DF_RUN_ID`, `DF_PROJECT_DIR`, `DF_BASE_BRANCH`, plus any from `config.deploy_env` dict.
- Compose project name: `df-{spec_name}-{run_id[:8]}` for uniqueness.
- Health check uses `subprocess.run(["curl", ...])` consistent with existing `evaluator.py`.
- "Deployment is configured" (triggers mandatory evaluator) means `deploy_compose_file != ""`. Legacy `deploy_cmd` users keep existing optional-evaluator behavior.

### 4.4 `deployment/progressive.py` — Canary Orchestration

```python
"""Progressive canary deployment with staged traffic shifting."""

# Responsibilities:
# 1. Execute canary stages sequentially
# 2. At each stage: shift traffic, soak, collect metrics, evaluate
# 3. On threshold breach: trigger rollback at appropriate layer
# 4. Emit events and notifications at each stage transition
# 5. Acquire canary lock for concurrent spec serialization

# Key functions:
def run_canary(config, spec_name, run_id, stages, thresholds, events_file,
               project_dir, cleanup_manager) -> DeploymentResult:
    """
    Execute the full canary rollout.

    1. Validate stages are monotonically increasing by percent.
    2. Acquire canary lock (FileLock, timeout=config.canary_lock_timeout).
       On lock timeout: skip canary, return PROMOTED (degrade gracefully).
    3. For each stage:
       a. Call config.canary_shift_cmd with CANARY_PERCENT={stage.percent}
       b. Soak for stage.soak_seconds (interruptible via abort flag)
       c. For each threshold: run threshold.metric_cmd, parse stdout as float
       d. If any metric breaches threshold: initiate rollback, return ROLLED_BACK
       e. Else: emit CANARY_STAGE_PASSED event, continue
    4. After all stages pass: return PROMOTED
    """

def _soak(seconds, cleanup_manager) -> bool:
    """
    Sleep for the soak period, checking for abort every second.

    Uses time.sleep(1) in a loop. Checks cleanup_manager.abort_requested
    each iteration. No threading needed — signal handlers in CleanupManager
    set abort_requested=True, which naturally interrupts the soak loop.

    Returns True if soak completed. Returns False if aborted.
    """

def _shift_traffic(config, percent, project_dir) -> bool:
    """
    Call canary_shift_cmd with CANARY_PERCENT env var.

    Uses deployment.command.run_command() for safe execution.
    Returns True if command exits 0.
    """

def _collect_metrics(thresholds, project_dir, timeout=30) -> dict[str, float]:
    """
    Run each threshold's metric_cmd and collect values.

    Each command must output a single plain float on stdout (e.g., "0.05").
    Commands run sequentially (not parallel — metric endpoints may rate-limit).
    On command failure or unparseable output: value = float('inf')
    (triggers max_value breach) or float('-inf') (triggers min_value breach).
    """

def _evaluate_thresholds(metrics, thresholds) -> list[str]:
    """
    Compare collected metrics against thresholds.
    Checks both max_value (breach if observed > max) and min_value (breach if observed < min).
    Returns list of breached threshold names (empty = all clear).
    """
```

**Design decisions:**
- Traffic shifting is delegated to user commands, not built-in. Dark Factory doesn't know or care whether the user uses Istio, Nginx, HAProxy, AWS ALB, or feature flags for traffic control.
- Soak period uses main-thread `time.sleep(1)` loop with `cleanup_manager.abort_requested` check — no threading needed. The existing `CleanupManager` signal handlers (SIGTERM, SIGINT) already run in the main thread and can set this flag.
- Metric commands must output a single plain float on stdout. No multi-format parsing — the user's script handles format conversion. This eliminates the ambiguity between "5%" and "0.05".
- Default canary stages if none configured: `[{1%, 60s}, {10%, 120s}, {50%, 300s}, {100%, 0s}]`.
- Canary stages are validated for monotonically increasing percent values at the start of `run_canary()`.

### 4.5 `deployment/rollback.py` — Multi-Layer Rollback

```python
"""Cascading multi-layer rollback engine."""

# Responsibilities:
# 1. Attempt rollback at the specified layer
# 2. If that layer fails, cascade to the next layer
# 3. Emit events and notifications at each attempt
# 4. Final fallback: git revert (always available, post-merge only)

# Key functions:
def rollback(config, spec_name, run_id, reason, start_layer, project_dir,
             events_file, post_merge=False) -> RollbackLayer | None:
    """
    Execute cascading rollback starting at start_layer.

    Cascade order: FLAG_KILL → TRAFFIC_REVERT → CONTAINER_ROLLBACK → GIT_REVERT

    For each layer:
      1. Check if the layer's command is configured (skip if not)
      2. Run the command via deployment.command.run_command() with timeout
      3. If exit 0: emit ROLLBACK_SUCCESS event, return layer
      4. If failure: emit ROLLBACK_LAYER_FAILED event, cascade to next layer

    GIT_REVERT (L4) is only attempted if post_merge=True. Pre-merge failures
    close the PR instead (handled by the caller, not by rollback()).

    If all attempted layers fail: emit ROLLBACK_ALL_FAILED, return None.
    """

def _rollback_flag_kill(config, spec_name, project_dir) -> bool:
    """Run config.flag_disable_cmd with FLAG_NAME=df-{spec_name}."""

def _rollback_traffic(config, project_dir) -> bool:
    """Run config.canary_rollback_cmd (shifts all traffic to stable)."""

def _rollback_container(config, project_dir) -> bool:
    """Run config.deploy_rollback_cmd (redeploys previous version)."""

def _rollback_git_revert(spec_name, project_dir, reason, config) -> bool:
    """
    Create a revert commit and open a NEW PR for it.

    Uses git revert (not git reset --hard) for safety on shared branches.
    Finds the merge commit by searching for 'dark-factory: {spec_name}' in git log.
    Creates revert branch: 'dark-factory/revert-{spec_name}-{timestamp}'.
    Creates revert commit with message 'Revert dark-factory: {spec_name} — {reason}'.
    Opens a PR for the revert (does NOT push directly to main).
    """
```

**Design decisions:**
- `post_merge` flag controls whether L4 (git revert) is available. Pre-merge canary failures never use L4.
- Git revert creates a new PR, never pushes directly to main. This preserves the PR workflow for all code changes.
- Cascade skips unconfigured layers. But: `canary_rollback_cmd` is validated as required when `canary_enabled=true` (see config validation in Section 6). A canary without a traffic rollback mechanism is a foot-gun.
- Each layer has an independent timeout (configurable, default 120s). A hung rollback command doesn't block the cascade forever.
- Rollback emits both events (for observability) and notifications (for human awareness).

### 4.6 `deployment/feature_flags.py` — Feature Flag Operations

```python
"""Feature flag lifecycle management via user-provided commands."""

# Responsibilities:
# 1. Create feature flag for a spec (default OFF)
# 2. Enable/disable flag at specified percentage
# 3. Check flag status
# 4. Kill switch (immediate disable)

# Key functions:
def create_flag(config, spec_name, project_dir) -> bool:
    """
    Create a feature flag for the spec.

    Runs config.flag_create_cmd via deployment.command.run_command() with:
      FLAG_NAME=df-{spec_name}
      FLAG_DEFAULT=off

    If no flag_create_cmd configured: skip (feature flags are optional).
    Returns True if created or skipped.
    """

def enable_flag(config, spec_name, project_dir, percent=100) -> bool:
    """
    Enable the flag at the specified percentage.

    Runs config.flag_enable_cmd with:
      FLAG_NAME=df-{spec_name}
      FLAG_PERCENT={percent}
    """

def disable_flag(config, spec_name, project_dir) -> bool:
    """
    Kill switch — immediately disable the flag.

    Runs config.flag_disable_cmd with:
      FLAG_NAME=df-{spec_name}
    """

def check_flag(config, spec_name, project_dir) -> str | None:
    """
    Check current flag status.

    Runs config.flag_check_cmd with FLAG_NAME=df-{spec_name}.
    Parses stdout: expects 'on', 'off', or a percentage.
    Returns None if no flag_check_cmd configured.
    """
```

**Design decisions:**
- Flag naming convention: `df-{spec_name}` — simple, greppable, collision-unlikely.
- All operations are optional. If no flag commands are configured, the pipeline skips flag operations entirely. This keeps Dark Factory usable for teams without feature flag infrastructure.
- Flag operations are idempotent — creating an already-existing flag or disabling an already-disabled flag must not error. This is the user's responsibility in their command scripts.
- All commands execute via `deployment.command.run_command()` (validated script paths).

### 4.7 `deployment/metrics_gate.py` — Metric Collection and Threshold Evaluation

```python
"""Metric collection and threshold-based deployment gating."""

# Responsibilities:
# 1. Collect metrics from user-provided commands
# 2. Evaluate against configured thresholds (max_value and/or min_value)
# 3. Provide structured verdict (pass/fail with details)

# Key functions:
def collect_and_evaluate(thresholds, project_dir, timeout=30) -> tuple[dict[str, float], list[str]]:
    """
    Collect metrics and evaluate against thresholds.

    For each MetricThreshold:
      1. Run metric_cmd via deployment.command.run_command() with timeout
      2. Parse stdout as plain float (e.g., "0.05")
      3. Check: if max_value set and observed > max_value → breach
      4. Check: if min_value set and observed < min_value → breach

    Returns (metrics_dict, breached_names).
    """

def parse_metric_output(stdout: str) -> float:
    """
    Parse a metric command's stdout into a float.

    Accepts ONLY plain float format: "0.05", "123.4", "-1.0"
    Strips whitespace. Rejects empty, multi-line, non-numeric.

    On parse failure: returns float('inf') (fail-safe — triggers max_value breach).
    Logs warning with the unparseable output for debugging.
    """
```

**Design decisions:**
- Single format only (plain float). The user's metric script is responsible for any format conversion. This eliminates the ambiguity between "5%" (percentage) and "0.05" (decimal) — the user decides their convention and configures `max_value` accordingly.
- `min_value` support enables thresholds like "success rate must stay above 99.9%" or "throughput must not drop below 1000 req/s".

### 4.8 `deployment/migration_check.py` — Expand-Contract Enforcement

```python
"""Enforce expand-contract migration safety."""

# Responsibilities:
# 1. Scan migration files for destructive operations
# 2. Detect mixed destructive+additive in same migration
# 3. Block unsafe migrations before deployment
# 4. Support SQL, Python ORM, and JavaScript ORM patterns

# Key functions:
def check_migrations(project_dir, migration_dirs, spec_name,
                     extra_patterns=None) -> MigrationCheckResult:
    """
    Scan migration files for safety violations.

    Searches migration_dirs for files modified in the current spec's changes
    (via git diff --name-only against base branch).

    Pattern sets (case-insensitive regex):

    SQL destructive:
      - DROP TABLE, DROP COLUMN, DROP INDEX, DROP CONSTRAINT, DROP DATABASE
      - DELETE FROM (without WHERE — bulk delete)
      - TRUNCATE TABLE
      - ALTER TABLE ... DROP ...

    SQL additive:
      - CREATE TABLE, CREATE INDEX
      - ADD COLUMN, ADD CONSTRAINT
      - ALTER TABLE ... ADD ...

    Python ORM destructive (Django/Alembic/SQLAlchemy):
      - RemoveField, DeleteModel, drop_column, drop_table, drop_index
      - op.drop_column, op.drop_table, op.drop_index, op.drop_constraint

    Python ORM additive:
      - AddField, CreateModel, add_column, create_table, create_index
      - op.add_column, op.create_table, op.create_index

    JavaScript ORM destructive (Sequelize/Knex/TypeORM):
      - dropTable, removeColumn, dropIndex, dropColumn

    JavaScript ORM additive:
      - createTable, addColumn, addIndex, createIndex

    Extra patterns: config.migration_extra_patterns allows users to define
    additional {destructive: [...], additive: [...]} regex patterns for
    frameworks not covered by the built-in sets.

    A migration is UNSAFE if it contains both destructive AND additive operations
    in the same file — this violates expand-contract.

    Returns MigrationCheckResult with safe=False if any file is mixed.
    """

def suggest_split(migration_file, destructive_ops, additive_ops) -> str:
    """
    Generate a human-readable suggestion for splitting a mixed migration.

    Returns a string like:
      'Split {file} into two migrations:
       1. Expand: ADD COLUMN email (backward-compatible)
       2. Contract: DROP COLUMN old_email (after rollback window)'
    """
```

**Design decisions:**
- Three built-in pattern sets: SQL, Python ORM, JavaScript ORM. Covers Django, Alembic, SQLAlchemy, Sequelize, Knex, TypeORM, Prisma (uses SQL files), and raw SQL.
- `migration_extra_patterns` config field allows users to add patterns for unlisted frameworks.
- `migration_check_policy` config: `"warn"` (default) logs warning but does not block; `"block"` fails the quality gate. False positives (e.g., "DROP" in a comment) are tolerable in warn mode.
- Only checks files modified by the current spec (via `git diff --name-only`).

---

## 5. Implementation Tiers

### Tier 1: Command Safety + Ephemeral Environments (Week 19) ✅ COMPLETE

**Files created:**
- `darkfactory/deployment/__init__.py`
- `darkfactory/deployment/models.py`
- `darkfactory/deployment/command.py`
- `darkfactory/deployment/environment.py`

**Files modified:**
- `darkfactory/models/config.py` — add `deploy_compose_file`, `deploy_env`, `deploy_ttl_seconds`, `deploy_cmd_allow_shell` fields; add `validate(project_dir)` method for command validation
- `darkfactory/config/defaults.py` — add defaults for new fields
- `darkfactory/pipeline/evaluator.py` — refactor to use `environment.provision()` / `environment.teardown()`
- `darkfactory/pipeline/cleanup.py` — add `abort_requested: bool` flag to `CleanupManager`; modify `_handle_signal` to set `abort_requested = True` and defer `sys.exit()` (currently calls `sys.exit()` immediately — the soak loop needs the signal handler to return so the loop can check `abort_requested` and perform graceful teardown before exiting); register compose teardown
- `darkfactory/events/types.py` — add `DEPLOY_PROVISION`, `DEPLOY_HEALTH_OK`, `DEPLOY_HEALTH_FAIL`, `DEPLOY_TEARDOWN` event types

**Tests:**
- `tests/test_command.py` — path validation, metacharacter rejection, symlink safety, run_command timeout
- `tests/test_environment.py` — provision, health check with backoff (wall-clock deadline), teardown, TTL enforcement, compose env vars

**Deliverable:** Safe command execution for all `*_cmd` fields. Automated ephemeral environment provisioning when `deploy_compose_file` is set.

### Tier 2: Migration Safety (Week 19-20) ✅ COMPLETE

**Files created:**
- `darkfactory/deployment/migration_check.py`

**Files modified:**
- `darkfactory/models/config.py` — add `migration_dirs`, `migration_check_enabled`, `migration_check_policy`, `migration_extra_patterns` fields
- `darkfactory/pipeline/quality_gate.py` — call `check_migrations()` as a gate stage
- `darkfactory/events/types.py` — add `MIGRATION_CHECK_PASS`, `MIGRATION_CHECK_FAIL` event types

**Tests:**
- `tests/test_migration_check.py` — SQL destructive/additive detection, Python ORM patterns, JS ORM patterns, mixed detection, suggest_split, extra_patterns, warn vs block policy, comments false positive handling

**Deliverable:** Quality gate blocks (or warns on) migrations that mix destructive and additive operations in the same file.

### Tier 3: Feature Flags (Week 20) ✅ COMPLETE

**Files created:**
- `darkfactory/deployment/feature_flags.py`

**Files modified:**
- `darkfactory/models/config.py` — add `flag_create_cmd`, `flag_enable_cmd`, `flag_disable_cmd`, `flag_check_cmd` fields (validated via command.py)
- `darkfactory/events/types.py` — add `FLAG_CREATED`, `FLAG_ENABLED`, `FLAG_DISABLED` event types

**Tests:**
- `tests/test_feature_flags.py` — create, enable, disable, check, skip-when-unconfigured, idempotency, command validation

**Deliverable:** Standalone feature flag module with full test coverage. No orchestrator wiring yet (deferred to Tier 6).

### Tier 4: Canary Deployment + Metrics Gate (Week 20-21) ✅ COMPLETE

**Files created:**
- `darkfactory/deployment/progressive.py`
- `darkfactory/deployment/metrics_gate.py`

**Files modified:**
- `darkfactory/models/config.py` — add `canary_stages`, `canary_shift_cmd`, `canary_rollback_cmd`, `canary_metrics`, `canary_enabled`, `canary_lock_timeout` fields; validate `canary_rollback_cmd` required when `canary_enabled=true`
- `darkfactory/events/types.py` — add `CANARY_STAGE_START`, `CANARY_STAGE_PASSED`, `CANARY_STAGE_FAILED`, `CANARY_PROMOTED`, `CANARY_LOCK_TIMEOUT` event types

**Tests:**
- `tests/test_progressive.py` — full canary pass, threshold breach at each stage, soak interruption via abort flag, metric collection failure, default stages, monotonic percent validation, lock acquisition + timeout
- `tests/test_metrics_gate.py` — plain float parsing, empty output, multi-line output, non-numeric output, min_value breach, max_value breach, both bounds

**Deliverable:** Standalone canary + metrics modules with full test coverage. No orchestrator wiring yet (deferred to Tier 6).

### Tier 5: Multi-Layer Rollback (Week 21-22) ✅ COMPLETE

**Files created:**
- `darkfactory/deployment/rollback.py`

**Files modified:**
- `darkfactory/models/config.py` — add `deploy_rollback_cmd`, `rollback_timeout`, `rollback_window_seconds` fields
- `darkfactory/git/ops.py` — add `revert_commit(spec_name, project_dir, reason, config)` function (creates new PR for revert); add `close_pr(project_dir)` function (`gh pr close` for the current branch's open PR — used on pre-merge canary failure)
- `darkfactory/events/types.py` — add `ROLLBACK_STARTED`, `ROLLBACK_SUCCESS`, `ROLLBACK_LAYER_FAILED`, `ROLLBACK_ALL_FAILED` event types

**Tests:**
- `tests/test_rollback.py` — each layer individually, cascade on failure, cascade when multiple layers unconfigured, git revert only when post_merge=True, git revert creates PR (not direct push), timeout enforcement, event emission, `test_rollback_cascade_all_fail` (ROLLBACK_ALL_FAILED event verified)

**Deliverable:** Standalone rollback module with full test coverage. No orchestrator wiring yet (deferred to Tier 6).

### Tier 6: Post-Ship Orchestration + Full Wiring (Week 22) ✅ COMPLETE

This tier wires all Tiers 1-5 into the pipeline orchestrator. No new standalone modules — only integration.

**Files modified:**
- `darkfactory/pipeline/orchestrator.py` — new `_post_ship_pipeline()` method; modify `_handle_ship()` to call it AFTER PR creation but BEFORE merge; on canary failure: close PR; on canary success: proceed to merge queue / auto-merge
- `darkfactory/cli.py` — add `deploy` subcommand for manual deployment trigger
- `darkfactory/models/pipeline.py` — extend `PipelineStatus` with `DEPLOYING`, `DEPLOYED`, `ROLLED_BACK` states; extend `SpecRun` with `deployment_result: DeploymentResult | None`; add `deployment_step` field for state persistence
- `darkfactory/state/store.py` — add deployment step tracking (steps 100-105 for post-ship stages)
- `darkfactory/notify/dispatcher.py` — add `notify_canary_promoted()`, `notify_canary_failed()`, `notify_rollback()`, `notify_rollback_failed()`, `notify_flag_killed()` notification functions
- `darkfactory/events/types.py` — add `DEPLOY_COMPLETE`, `ROLLBACK_WINDOW_EXPIRED` event types

**Tests:**
- `tests/test_post_ship_pipeline.py` — full happy path, failure at each stage, skip-when-unconfigured, resume after crash at each deployment step, canary failure closes PR (not merge+revert), post-merge rollback creates revert PR, cleanup after rollback

**Deliverable:** Complete post-ship pipeline. All modules wired. Manual `dark-factory deploy` command. State persistence for deployment steps enables crash recovery.

---

## 6. Config Schema Changes

New fields in `.dark-factory/config.toml`:

```toml
[deploy]
# Existing fields (unchanged)
deploy_cmd = ""                       # Legacy: raw deploy command
deploy_base_url = ""                  # Base URL for health checks
deploy_health_endpoint = "/health"    # Health check path
deploy_health_timeout = 60            # Health check timeout (seconds)
deploy_teardown_cmd = ""              # Legacy: teardown command

# New: Command safety
deploy_cmd_allow_shell = false        # Escape hatch: true = allow bash -c (deprecated)

# New: Ephemeral environments
deploy_compose_file = ""              # Path to docker-compose.test.yml (enables automated provisioning + mandatory evaluator)
deploy_env = {}                       # Extra env vars passed to compose
deploy_ttl_seconds = 3600             # Max lifetime for ephemeral environment (default: 1h)

# New: Rollback
deploy_rollback_cmd = ""              # Command to redeploy previous version (L3)
rollback_timeout = 120                # Timeout per rollback layer (seconds)
rollback_window_seconds = 3600        # Post-promotion monitoring window (default: 1h, 0=disabled)
rollback_poll_interval = 60           # Seconds between metric polls during rollback window (Mode A)
rollback_monitor_cmd = ""             # External monitoring command (Mode B — pipeline exits after calling)

[canary]
canary_enabled = false                # Enable progressive canary deployment
canary_shift_cmd = ""                 # Command to shift traffic (receives CANARY_PERCENT env var)
canary_rollback_cmd = ""              # REQUIRED when canary_enabled=true — shifts all traffic to stable (L2)
canary_lock_timeout = 600             # Seconds to wait for canary lock (concurrent spec serialization)

# Canary stages — list of {percent, soak_seconds}
# Validated: percent must be 1-100, stages must be monotonically increasing
# Default: [{percent=1, soak_seconds=60}, {percent=10, soak_seconds=120}, {percent=50, soak_seconds=300}, {percent=100, soak_seconds=0}]
canary_stages = [
    {percent = 1,   soak_seconds = 60},
    {percent = 10,  soak_seconds = 120},
    {percent = 50,  soak_seconds = 300},
    {percent = 100, soak_seconds = 0},
]

# Canary metrics — list of {name, max_value, min_value, metric_cmd}
# metric_cmd must output a single plain float on stdout (e.g., "0.05")
# At least one of max_value or min_value must be set per metric
canary_metrics = [
    # Example:
    # {name = "error_rate",   max_value = 0.05, metric_cmd = "scripts/check-error-rate.sh"},
    # {name = "success_rate", min_value = 99.0, metric_cmd = "scripts/check-success-rate.sh"},
    # {name = "latency_p99",  max_value = 500,  metric_cmd = "scripts/check-latency.sh"},
]

[flags]
flag_create_cmd = ""                  # Create flag (receives FLAG_NAME, FLAG_DEFAULT)
flag_enable_cmd = ""                  # Enable flag (receives FLAG_NAME, FLAG_PERCENT)
flag_disable_cmd = ""                 # Kill switch (receives FLAG_NAME)
flag_check_cmd = ""                   # Check flag status (receives FLAG_NAME, outputs status)

[migration]
migration_check_enabled = false       # Enable expand-contract migration enforcement
migration_check_policy = "warn"       # "warn" (log only) or "block" (fail quality gate)
migration_dirs = ["migrations/", "db/migrate/", "alembic/versions/"]
migration_extra_patterns = {}         # User-defined {destructive = [...], additive = [...]} regex patterns
```

### Config Loader Notes

TOML sections (`[deploy]`, `[canary]`, `[flags]`, `[migration]`) are cosmetic for organization. The existing config loader flattens them into `PipelineConfig` fields (e.g., `[canary].canary_enabled` → `PipelineConfig.canary_enabled`).

**New deserialization requirement:** `canary_stages` and `canary_metrics` are TOML arrays of inline tables. The config loader must deserialize these into `list[CanaryStage]` and `list[MetricThreshold]` respectively. Implementation in `darkfactory/config/loader.py`:

```python
# In load_config(), after reading TOML:
if "canary" in raw and "canary_stages" in raw["canary"]:
    config.canary_stages = [
        CanaryStage(**stage) for stage in raw["canary"]["canary_stages"]
    ]
if "canary" in raw and "canary_metrics" in raw["canary"]:
    config.canary_metrics = [
        MetricThreshold(**m) for m in raw["canary"]["canary_metrics"]
    ]
```

### Config Validation

Validation is split between `__post_init__()` (data integrity, no project_dir needed) and a new `validate(project_dir)` method (command safety, needs project_dir).

**In `__post_init__()` (existing pattern):**

```python

# canary_rollback_cmd required when canary_enabled
if self.canary_enabled and not self.canary_rollback_cmd:
    raise ValueError("canary_rollback_cmd is required when canary_enabled=true")

# canary_stages monotonically increasing
if self.canary_stages:
    percents = [s.percent for s in self.canary_stages]
    if percents != sorted(percents):
        raise ValueError(f"canary_stages percent must be monotonically increasing, got {percents}")

# migration_check_policy must be "warn" or "block"
if self.migration_check_policy not in ("warn", "block"):
    raise ValueError(f"migration_check_policy must be 'warn' or 'block', got '{self.migration_check_policy}'")
```

**In `validate(project_dir)` (new method, called by orchestrator at pipeline start):**

```python
def validate(self, project_dir: str) -> None:
    """Validate command paths and other project-dir-dependent config."""
    # All *_cmd fields validated as safe script paths (unless allow_shell=true)
    for field_name in CMD_FIELDS:
        value = getattr(self, field_name)
        if value and not self.deploy_cmd_allow_shell:
            validate_command(value, project_dir, field_name)
```

**Backward compatibility:** All new fields have empty/false defaults. The pipeline behaves identically to today when no deployment config is added. The `deploy_cmd_allow_shell=true` escape hatch preserves legacy `deploy_cmd` behavior for users with inline shell commands (with deprecation warning).

---

## 7. Integration Points

### 7.1 Pipeline Orchestrator (`orchestrator.py`)

The post-ship pipeline is a new method `_post_ship_pipeline()` called from `_handle_ship()`:

```python
# Current _handle_ship() flow:
#   1. Register scenarios in shipped registry
#   2. Run regression suite (if configured)
#   3. Create branch, commit, push, PR
#   4. Auto-merge (if high trust)
#   5. Emit SHIPPED event
#   6. Notify

# New _handle_ship() flow:
#   1. Register scenarios in shipped registry
#   2. Run regression suite (if configured)
#   3. Create branch, commit, push, PR
#   4. → _post_ship_pipeline()             ← NEW (runs BEFORE merge)
#      a. Provision ephemeral environment
#      b. Behavioral evaluation (mandatory when compose_file set)
#      c. Migration safety check
#      d. Create feature flag
#      e. Run canary stages
#      f. On FAILURE: rollback L1-L3, close PR, return ROLLED_BACK
#   5. On SUCCESS: merge queue / auto-merge  ← merge AFTER canary
#   6. Post-merge rollback window monitoring
#   7. Emit SHIPPED / DEPLOYED / ROLLED_BACK event
#   8. Notify (include deployment status)
```

### 7.2 State Persistence for Deployment Steps

New `deployment_step` values in state file for crash recovery:

| Step | Value | Description |
|------|-------|-------------|
| 100 | `deploy_provision` | Ephemeral environment provisioning |
| 101 | `deploy_evaluate` | Behavioral evaluation |
| 102 | `deploy_migration` | Migration safety check |
| 103 | `deploy_flag` | Feature flag creation |
| 104 | `deploy_canary` | Canary stages (+ sub-step: stage index) |
| 105 | `deploy_promoted` | Full promotion complete |
| 106 | `deploy_merged` | Merged to main, rollback window active |
| 107 | `deploy_complete` | Rollback window expired, deployment done |

On resume: read `deployment_step` from state file, skip completed steps. Canary resume also reads the last completed stage index to avoid re-running passed stages.

### 7.3 Quality Gate (`quality_gate.py`)

Migration check is added as an optional gate stage:

```python
# Current gate stages: build → lint → typecheck → test → scenarios
# New:                 build → lint → typecheck → migration check → test → scenarios
```

Migration check runs before tests because a migration failure is a structural issue that shouldn't wait for test execution.

### 7.4 CLI (`cli.py`)

New `deploy` subcommand:

```
dark-factory deploy <spec-name> [--run-id RUN_ID] [--stage canary|promote|rollback]
```

- `--stage canary`: Run canary deployment only
- `--stage promote`: Skip canary, promote directly (for hotfixes)
- `--stage rollback`: Trigger manual rollback

### 7.5 Events (`events/types.py`)

New event types (20 total):

```python
# Environment
DEPLOY_PROVISION = "deploy_provision"
DEPLOY_HEALTH_OK = "deploy_health_ok"
DEPLOY_HEALTH_FAIL = "deploy_health_fail"
DEPLOY_TEARDOWN = "deploy_teardown"

# Migration
MIGRATION_CHECK_PASS = "migration_check_pass"
MIGRATION_CHECK_FAIL = "migration_check_fail"

# Feature flags
FLAG_CREATED = "flag_created"
FLAG_ENABLED = "flag_enabled"
FLAG_DISABLED = "flag_disabled"

# Canary
CANARY_STAGE_START = "canary_stage_start"
CANARY_STAGE_PASSED = "canary_stage_passed"
CANARY_STAGE_FAILED = "canary_stage_failed"
CANARY_PROMOTED = "canary_promoted"
CANARY_LOCK_TIMEOUT = "canary_lock_timeout"

# Rollback
ROLLBACK_STARTED = "rollback_started"
ROLLBACK_SUCCESS = "rollback_success"
ROLLBACK_LAYER_FAILED = "rollback_layer_failed"
ROLLBACK_ALL_FAILED = "rollback_all_failed"

# Lifecycle
DEPLOY_COMPLETE = "deploy_complete"
ROLLBACK_WINDOW_EXPIRED = "rollback_window_expired"
```

### 7.6 Notifications (`notify/dispatcher.py`)

New notification functions (5):

```python
notify_canary_promoted(config, spec_name, run_id, stages_completed)
notify_canary_failed(config, spec_name, run_id, stage, breached_metrics)
notify_rollback(config, spec_name, run_id, layer, reason)
notify_rollback_failed(config, spec_name, run_id, reason)
notify_flag_killed(config, spec_name, reason)
```

### 7.7 Grafana Dashboards

Extend existing `dashboards/` with deployment panels:

- `dashboards/deployment-health.json` — canary stage progression, rollback frequency, deployment success rate, mean time to promotion, mean time to rollback

### 7.8 GitHub Actions

New workflow `.github/workflows/dark-factory-deploy.yml`:
- Triggered by: `workflow_dispatch` (manual) or called from `dark-factory.yml` after SHIP
- Runs: provision → evaluate → canary → promote/rollback
- Requires: self-hosted runner with Docker support

---

## 8. Testing Strategy

### Unit Tests (stdlib `unittest`)

| Test File | Module Under Test | Test Count (est.) |
|---|---|---|
| `test_command.py` | `deployment/command.py` | 12 |
| `test_environment.py` | `deployment/environment.py` | 12 |
| `test_migration_check.py` | `deployment/migration_check.py` | 14 |
| `test_feature_flags.py` | `deployment/feature_flags.py` | 8 |
| `test_progressive.py` | `deployment/progressive.py` | 18 |
| `test_metrics_gate.py` | `deployment/metrics_gate.py` | 10 |
| `test_rollback.py` | `deployment/rollback.py` | 15 |
| `test_post_ship_pipeline.py` | Orchestrator integration | 14 |
| **Total** | | **~103** |

### Mocking Strategy

All external commands are invoked via `deployment.command.run_command()` → `subprocess.run()`. Tests mock `subprocess.run` to simulate:
- Successful commands (exit 0, expected stdout)
- Failed commands (exit 1)
- Timed-out commands (subprocess.TimeoutExpired)
- Metric commands returning various formats

Docker-compose operations are also mocked — no real containers in unit tests.

### Required Test Scenarios (from architectural review)

| Scenario | Test File | Description |
|---|---|---|
| `test_rollback_cascade_all_fail` | `test_rollback.py` | All L1-L4 fail → ROLLBACK_ALL_FAILED event emitted |
| `test_canary_crash_resume` | `test_post_ship_pipeline.py` | Simulate crash at each canary stage, verify resume from last completed stage |
| `test_cleanup_after_rollback` | `test_post_ship_pipeline.py` | Verify ephemeral environment teardown runs after rollback |
| `test_metric_parse_adversarial` | `test_metrics_gate.py` | Empty string, very large numbers, binary data, multi-line output → all return float('inf') |
| `test_canary_failure_closes_pr` | `test_post_ship_pipeline.py` | Canary failure → PR closed, NOT merged then reverted |
| `test_post_merge_revert_creates_pr` | `test_rollback.py` | Post-merge L4 → new PR created, not direct push to main |
| `test_canary_lock_contention` | `test_progressive.py` | Second spec times out on lock → degrades to direct merge |
| `test_unconfigured_canary_rollback` | `test_progressive.py` | `canary_enabled=true` without `canary_rollback_cmd` → config validation error |

### Integration Tests

One integration test script `tests/test-deploy-integration.sh` that:
1. Starts a real HTTP server (Python `http.server`)
2. Provisions via docker-compose (if Docker available, else skip)
3. Health checks the server
4. Runs a mock canary with bash metric scripts
5. Tears down

This test runs in CI only on self-hosted runners (Docker required). A stub mode for GitHub-hosted runners skips Docker-dependent steps and tests only command validation + metric parsing.

---

## 9. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| User's canary_shift_cmd is slow or hangs | Medium | Medium | Per-command timeout (config.rollback_timeout). Soak period is interruptible via abort flag. |
| Metric commands return unparseable output | Medium | Low | Fail-safe: unparseable = infinity = threshold breach = rollback. Log warning with raw output. |
| Docker-compose not available on CI runner | Medium | Low | Graceful skip with warning when compose unavailable. Fall back to legacy deploy_cmd. |
| Rollback cascade exhausts all layers | Low | High | L4 (git revert PR) is always available post-merge. Pre-merge: PR closure is always available. ROLLBACK_ALL_FAILED event + notification ensures human is alerted. |
| Migration check false positives (e.g., "DROP" in a comment) | Medium | Low | Default policy is "warn" (not "block"). User can override to "block" after tuning patterns. |
| Feature flag command fails silently | Low | Medium | Check exit code. On failure: log warning, continue without flag (degrade gracefully, don't block pipeline). |
| Post-ship pipeline adds significant wall time | High | Medium | Canary soak times are configurable. Quick mode: single stage at 100% with 0s soak. Document expected wall time per configuration. |
| Concurrent specs both attempt canary deployment | Low | High | Canary lock with FileLock. Timeout → degrade to direct merge. Stale PID detection prevents orphaned locks. |
| Command injection via config values | Low | Critical | All `*_cmd` fields validated as script paths (no shell metacharacters). `deploy_cmd_allow_shell` escape hatch with deprecation warning. |
| Pre-merge canary passes but post-merge breaks | Low | Medium | Rollback window with L1-L4 cascade. Post-merge L4 creates revert PR. Notification ensures human awareness. |

---

## 10. Acceptance Criteria

### Must Have (Phase 6 complete)

1. **Command safety**: All `*_cmd` config fields validated as safe script paths per Section 4.12. Shell metacharacter rejection. Escape hatch for migration.
2. **Ephemeral environments**: `deploy_compose_file` config triggers automated provisioning with health check (exponential backoff, wall-clock deadline) and deterministic teardown.
3. **Behavioral evaluation**: Evaluator agent is mandatory when `deploy_compose_file` is set. Legacy `deploy_cmd` users keep optional evaluator behavior.
4. **Migration safety**: Quality gate warns/blocks on migrations mixing destructive and additive operations. SQL, Python ORM, and JS ORM patterns supported. User-extensible patterns.
5. **Feature flags**: Spec ships behind a flag (OFF) when flag commands are configured. Kill switch disables flag in seconds.
6. **Canary deployment**: Traffic shifts through configurable stages with monotonic percent validation. Metric thresholds (max_value and min_value) gate each stage. Threshold breach triggers rollback. `canary_rollback_cmd` required when `canary_enabled=true`.
7. **Multi-layer rollback**: Four rollback layers cascade from fastest (flag kill) to slowest (git revert). Pre-merge failures close the PR. Post-merge L4 creates a revert PR (never direct push to main).
8. **Concurrent canary locking**: FileLock with PID-based stale detection. Timeout degrades gracefully to direct merge.
9. **Pre-merge canary model**: Canary runs AFTER PR creation but BEFORE merge. Eliminates merge-queue/rollback race conditions.
10. **State persistence**: Deployment steps 100-107 tracked in state file. Crash recovery resumes from last completed deployment step.
11. **Backward compatibility**: Pipeline behaves identically to Phase 5 when no deployment config is added.
12. **100+ unit tests**: All modules tested with mocked subprocess calls, including adversarial inputs and edge cases from architectural review.
13. **Events and notifications**: 20 new event types, 5 new notification functions, all wired.
14. **CLI deploy command**: Manual trigger for deployment/rollback of shipped specs.

### Nice to Have (Phase 6 stretch)

- Grafana deployment dashboard template
- GitHub Actions deployment workflow
- Sample docker-compose.test.yml template
- Sample metric collection scripts for common providers (Prometheus, Datadog, CloudWatch)
- Expand-contract migration template generator

### Explicitly Out of Scope (Phase 7+)

- Digital twin framework
- Automated strategy switching (model change, fresh start)
- Selective regression
- Spec sanitization / prompt injection detection
- Real token counting (vs. turn-based estimation)

---

## Appendix A: Full File Manifest

### New Files (17)

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `darkfactory/deployment/__init__.py` | 5 | Package init |
| `darkfactory/deployment/models.py` | 100 | Dataclasses with validation |
| `darkfactory/deployment/command.py` | 90 | Safe command validation + execution |
| `darkfactory/deployment/environment.py` | 130 | Ephemeral env lifecycle |
| `darkfactory/deployment/progressive.py` | 170 | Canary orchestration + locking |
| `darkfactory/deployment/rollback.py` | 140 | Multi-layer rollback |
| `darkfactory/deployment/feature_flags.py` | 80 | Flag operations |
| `darkfactory/deployment/metrics_gate.py` | 70 | Metric collection/eval |
| `darkfactory/deployment/migration_check.py` | 130 | Expand-contract check (3 pattern sets) |
| `tests/test_command.py` | 120 | Command safety tests |
| `tests/test_environment.py` | 150 | Environment tests |
| `tests/test_migration_check.py` | 140 | Migration tests (SQL + ORM patterns) |
| `tests/test_feature_flags.py` | 100 | Flag tests |
| `tests/test_progressive.py` | 200 | Canary + lock tests |
| `tests/test_metrics_gate.py` | 120 | Metrics tests (incl. adversarial) |
| `tests/test_rollback.py` | 170 | Rollback tests (incl. cascade-all-fail) |
| `tests/test_post_ship_pipeline.py` | 160 | Integration tests (incl. crash resume) |
| **Total** | **~2,075** | |

### Modified Files (12)

| File | Changes |
|------|---------|
| `darkfactory/models/config.py` | Add ~37 new config fields, `validate(project_dir)` method, canary validation |
| `darkfactory/config/defaults.py` | Add defaults for new fields |
| `darkfactory/config/loader.py` | Add list-of-dict deserialization for canary_stages, canary_metrics |
| `darkfactory/pipeline/orchestrator.py` | Add `_post_ship_pipeline()`, modify `_handle_ship()` (pre-merge canary) |
| `darkfactory/pipeline/quality_gate.py` | Add migration check stage |
| `darkfactory/pipeline/evaluator.py` | Refactor to use `environment.py` |
| `darkfactory/pipeline/cleanup.py` | Add `abort_requested` flag, register compose teardown |
| `darkfactory/git/ops.py` | Add `revert_commit()` (creates new PR), `close_pr()` |
| `darkfactory/events/types.py` | Add 19 event types |
| `darkfactory/notify/dispatcher.py` | Add 5 notification functions |
| `darkfactory/cli.py` | Add `deploy` subcommand |
| `darkfactory/models/pipeline.py` | Extend PipelineStatus, SpecRun (deployment_result, deployment_step) |

---

## Appendix B: Example Config

```toml
# .dark-factory/config.toml — Phase 6 deployment configuration example

[deploy]
deploy_compose_file = "docker-compose.test.yml"
deploy_base_url = "http://localhost:3000"
deploy_health_endpoint = "/health"
deploy_health_timeout = 120
deploy_ttl_seconds = 3600
deploy_rollback_cmd = "scripts/deploy-rollback.sh"
rollback_timeout = 120
rollback_window_seconds = 3600

[canary]
canary_enabled = true
canary_shift_cmd = "scripts/canary-shift.sh"
canary_rollback_cmd = "scripts/canary-rollback.sh"   # REQUIRED when canary_enabled=true
canary_lock_timeout = 600
canary_stages = [
    {percent = 1,   soak_seconds = 60},
    {percent = 10,  soak_seconds = 120},
    {percent = 50,  soak_seconds = 300},
    {percent = 100, soak_seconds = 0},
]
canary_metrics = [
    {name = "error_rate",   max_value = 0.05,  metric_cmd = "scripts/check-error-rate.sh"},
    {name = "success_rate", min_value = 99.0,   metric_cmd = "scripts/check-success-rate.sh"},
    {name = "latency_p99",  max_value = 500.0,  metric_cmd = "scripts/check-latency.sh"},
]

[flags]
flag_create_cmd = "scripts/flag-create.sh"
flag_enable_cmd = "scripts/flag-enable.sh"
flag_disable_cmd = "scripts/flag-disable.sh"
flag_check_cmd = "scripts/flag-check.sh"

[migration]
migration_check_enabled = true
migration_check_policy = "warn"       # "warn" or "block"
migration_dirs = ["migrations/", "prisma/migrations/"]
# migration_extra_patterns = {destructive = ["custom_drop_pattern"], additive = ["custom_create_pattern"]}
```

---

## Appendix C: Neo Review Issues — Resolution Tracker

All issues from the architectural review have been addressed:

| ID | Severity | Issue | Resolution |
|---|---|---|---|
| CRITICAL-1 | CRITICAL | Command injection via config values | Added `deployment/command.py` with `validate_command()`. All `*_cmd` fields validated as script paths. Shell metacharacter rejection. `deploy_cmd_allow_shell` escape hatch. |
| HIGH-1 | HIGH | Post-ship pipeline timing vs git push/merge | Redesigned as pre-merge canary model. Canary runs AFTER PR creation, BEFORE merge. Failure closes PR. Post-merge L4 creates revert PR. |
| HIGH-2 | HIGH | Concurrent canary lock details missing | Added Section 3.4 with FileLock mechanism, PID-based stale detection, configurable timeout, graceful degradation. |
| HIGH-3 | HIGH | Event type count mismatch | Added `DEPLOY_COMPLETE`, `ROLLBACK_WINDOW_EXPIRED`, `CANARY_LOCK_TIMEOUT`. Updated count to 19. |
| MEDIUM-1 | MEDIUM | Soak uses threading.Event unnecessarily | Changed to main-thread `time.sleep(1)` loop with `cleanup_manager.abort_requested` flag. No threading. |
| MEDIUM-2 | MEDIUM | Health check backoff vs timeout budget | Clarified wall-clock deadline pattern with attempt budget example in Section 4.3. |
| MEDIUM-3 | MEDIUM | Migration check misses ORM patterns | Added Python ORM (Django/Alembic/SQLAlchemy) and JavaScript ORM (Sequelize/Knex/TypeORM) pattern sets. Added `migration_extra_patterns` for user extensibility. |
| MEDIUM-4 | MEDIUM | TOML sections vs flat PipelineConfig | Added Config Loader Notes in Section 6 clarifying flattening behavior and list-of-dict deserialization code. |
| MEDIUM-5 | MEDIUM | Canary without traffic rollback cmd | Added config validation: `canary_rollback_cmd` required when `canary_enabled=true`. |
| MEDIUM-6 | MEDIUM | No state persistence for post-ship pipeline | Added deployment_step tracking (steps 100-107) in Section 7.2. Resume skips completed steps. |
| LOW-1 | LOW | CanaryStage.percent not validated | Added `__post_init__` range check (1-100) + monotonic increase validation in config. |
| LOW-2 | LOW | Metric format ambiguity | Simplified to plain float only. User's script handles format conversion. |
| LOW-3 | LOW | No min_value on MetricThreshold | Added optional `min_value` field. At least one of max/min required. |
| LOW-4 | LOW | Tier 3 wires orchestrator prematurely | Deferred all orchestrator wiring to Tier 6. Tiers 1-5 are standalone modules with tests. |

### Second Review Issues (all resolved in-plan)

| ID | Severity | Issue | Resolution |
|---|---|---|---|
| NEW-1 | MEDIUM | `PipelineConfig` lacks `_project_dir` for command validation | Changed to `validate(project_dir)` method called by orchestrator, not `__post_init__()`. Preserves existing config-as-pure-data pattern. |
| NEW-2 | LOW | `abort_requested` flag vs `_handle_signal` calling `sys.exit()` | Tier 1 modifies `_handle_signal` to set `abort_requested=True` and defer exit, allowing soak loop graceful shutdown. |
| NEW-3 | LOW | File manifest header says 12, table lists 17 | Fixed header to 17. |
| NEW-4 | LOW | `close_pr()` referenced but not specified | Added to `git/ops.py` modifications in Tier 5. Uses `gh pr close` for current branch's open PR. |
| NEW-5 | MEDIUM | No rollback window monitoring mechanism | Added Section 3.4 with two modes: Mode A (pipeline-resident polling, default) and Mode B (external monitoring via `rollback_monitor_cmd`). Added `rollback_poll_interval` and `rollback_monitor_cmd` config fields. |
