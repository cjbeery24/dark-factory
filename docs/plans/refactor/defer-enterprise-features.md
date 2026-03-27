# Plan: Defer Enterprise Features to Focus on Core Pipeline

**Status:** COMPLETE (2026-03-27)
**Priority:** 2 (High — import failures block new users from running the core pipeline)
**Scope:** Import restructuring, single config flag, notification guard pattern
**Estimated files changed:** 6-8 (actual: 13)
**Reviewed by:** Liotta, Neo

## Implementation Summary

Completed in commit `56de389`. Key changes:
- Added `enterprise_enabled: bool = False` to PipelineConfig and TOML loader
- Notification `_send()` returns early when enterprise disabled (shell hooks unaffected)
- Lazy imports for trust, deployment, anomaly, evaluator, regression modules
- Enterprise CLI commands show clear message when disabled
- Import isolation test verifies no enterprise modules at import time

## Problem Statement

Lines 29-54 of `orchestrator.py` unconditionally import deployment, feature flags, canary, rollback, notification, trust, and anomaly modules at the top level. If ANY of these modules has a broken import (missing dependency, syntax error, version mismatch), the **entire pipeline fails to start** — even for a user who just wants `dark-factory run <spec>` on a simple Python project.

The original Dark Factory achieved 12x velocity with none of these features. They add maintenance burden and cognitive load without contributing to pipeline correctness. The core implement → test → iterate loop must work independently of enterprise infrastructure.

**This is NOT a deletion plan.** The features are well-built and will be valuable when enterprise users need them. The goal is to make them clearly optional and unable to cause regressions in the core pipeline.

## Architecture Decision

### Single flag, not eight

One config flag controls all enterprise features:

```toml
[enterprise]
enabled = false   # Default for new configs
```

When you actually have users who need canary but not OTEL, split the flag then. Premature flag granularity is premature abstraction.

### Notification guard pattern: internal to dispatcher

Instead of guarding every `notify_*()` call site (there are 15+ across orchestrator.py and iteration.py), make the dispatcher itself check the flag. When `enterprise=false`, all `notify_*()` functions are no-ops. This is cleaner and prevents missed guard sites.

### Feature tiers

| Tier | Features | Status |
|------|----------|--------|
| **Core** (always active) | Spec validation, scenario generation, implementation, quality gate, review, oracle, iteration loop, barrier enforcement | Must work flawlessly |
| **Standard** (always on, lightweight) | Cost tracking, satisfaction scoring, trajectory classification, stable failure detection | Low overhead, directly useful for decisions |
| **Enterprise** (off by default, opt-in) | Deployment/canary, feature flags, trust scoring, dynamic autonomy, anomaly detection, OTEL export, notifications, regression, plan DAG execution | Gated behind `enterprise.enabled` |

## Implementation Steps

### Step 1: Add enterprise config flag

**File:** `darkfactory/models/config.py`

Add a single flag:

```python
# Enterprise features (off by default for new configs)
enterprise_enabled: bool = False
```

### Step 2: Make notification dispatcher self-guarding

**File:** `darkfactory/notify/dispatcher.py`

Instead of guarding 15+ call sites, make the dispatcher check the flag internally:

```python
# At module level or in each public function:
def notify_gate_complete(config, spec_name, run_id, iteration, pass_rate, *, passed):
    if not config.enterprise_enabled:
        return
    # ... existing implementation ...
```

Apply this pattern to every `notify_*` function. This way, existing call sites in orchestrator.py and iteration.py don't need changes — they just become no-ops when enterprise is disabled.

### Step 3: Lazy-import enterprise modules in orchestrator

**File:** `darkfactory/pipeline/orchestrator.py`

Move enterprise imports from top-level to inside the functions that use them:

```python
# REMOVE from top-level:
# from darkfactory.deployment.environment import health_check, provision, teardown
# from darkfactory.deployment.feature_flags import create_flag
# from darkfactory.deployment.progressive import run_canary
# from darkfactory.deployment.rollback import rollback
# from darkfactory.intelligence.trust import calculate_trust, predict_iterations, trust_to_autonomy

# KEEP at top-level (core + standard):
from darkfactory.pipeline.agents import invoke_agent
from darkfactory.pipeline.iteration import run_iteration_loop
from darkfactory.pipeline.quality_gate import run_smoke_test
from darkfactory.scenarios.detection import ...
# ... etc.

# MOVE to lazy imports inside functions:
def _step_deploy(spec_run, config, ...):
    if not config.enterprise_enabled:
        return
    from darkfactory.deployment.environment import provision, health_check, teardown
    from darkfactory.deployment.progressive import run_canary
    ...
```

### Step 4: Lazy-import enterprise modules in iteration loop

**File:** `darkfactory/pipeline/iteration.py`

Move enterprise imports from top-level (lines 24-30, 39-44) to inside guarded code paths:

```python
# REMOVE from top-level:
# from darkfactory.intelligence.anomaly import (
#     COMPOSITE_BLOCK, COMPOSITE_ESCALATE, check_baseline,
#     check_spec_limits, composite_score,
# )
# from darkfactory.notify.dispatcher import (
#     notify_anomaly, notify_gate_complete, notify_hold, notify_no_ship,
# )

# MOVE to lazy inside the anomaly check block:
if config.enterprise_enabled and events_file:
    from darkfactory.intelligence.anomaly import check_spec_limits, check_baseline
    check_spec_limits(...)
    check_baseline(...)
```

The `notify_*` calls don't need import guards because the dispatcher self-guards (Step 2). Keep the top-level import of `darkfactory.notify.dispatcher` — it will just be a no-op module when enterprise is off.

### Step 5: Guard trust scoring in orchestrator

**File:** `darkfactory/pipeline/orchestrator.py`

Trust scoring (`calculate_trust`, `predict_iterations`, `trust_to_autonomy`) should be guarded:

```python
if config.enterprise_enabled:
    from darkfactory.intelligence.trust import calculate_trust, trust_to_autonomy
    trust = calculate_trust(...)
    ...
else:
    # Use defaults — no dynamic autonomy adjustment
    pass
```

### Step 6: Guard enterprise CLI commands

**File:** `darkfactory/cli.py`

Enterprise-only CLI commands should show a clear message when enterprise is disabled:

```python
def cmd_deploy(args):
    config = load_config(...)
    if not config.enterprise_enabled:
        print("Deployment requires enterprise features. "
              "Set [enterprise] enabled = true in .dark-factory/config.toml")
        return 1
    ...
```

Commands to guard: `deploy`, `export-otel`, `regression`, `calibrate-cost`, `dashboard` (partially — metrics are Standard tier, but OTEL/notifications are Enterprise).

### Step 7: Update default config generation

**File:** `darkfactory/setup/` (init command), config templates

When `dark-factory init` generates a new config, enterprise features are off by default:

```toml
[core]
language = "python"
source_dirs = ["src"]
test_dir = "tests"

[gates]
# ... core gate config ...

# [enterprise]
# enabled = true   # Uncomment to enable: deployment, notifications, trust scoring, anomaly detection, OTEL
```

### Step 8: Add import isolation test

**File:** `tests/unit/test_core_imports.py` (new)

Verify the core pipeline doesn't require enterprise modules at import time:

```python
import sys

def test_orchestrator_import_no_enterprise():
    """Verify importing orchestrator doesn't pull in enterprise modules."""
    # Clear any cached imports
    enterprise_modules = [m for m in sys.modules if m.startswith("darkfactory.deployment")]
    for m in enterprise_modules:
        del sys.modules[m]

    # Import the orchestrator
    import importlib
    importlib.reload(darkfactory.pipeline.orchestrator)

    # Verify no enterprise modules loaded
    loaded = [m for m in sys.modules if any(
        m.startswith(prefix) for prefix in [
            "darkfactory.deployment",
            "darkfactory.intelligence.trust",
            "darkfactory.intelligence.anomaly",
        ]
    )]
    assert loaded == [], f"Enterprise modules imported at top level: {loaded}"
```

### Step 9: Update documentation

**Files:** `CLAUDE.md`, `docs/CONFIGURATION.md`

Add a "Feature Tiers" section:

```markdown
## Feature Tiers

### Core (always active)
- Spec validation, scenario generation, implementation, quality gate, review, oracle
- Information barriers (Docker tmpfs masking)
- Iteration loop (up to 3 iterations)

### Standard (always on, lightweight)
- Cost tracking and budget enforcement
- Satisfaction scoring (weighted category pass rates)
- Trajectory classification and strategy switching

### Enterprise (opt-in: `[enterprise] enabled = true`)
- Progressive deployment (canary, rollback)
- Feature flags (Unleash)
- Trust scoring and dynamic autonomy
- Anomaly detection
- OpenTelemetry export
- Notifications (Slack, webhook)
- Cross-spec regression testing
- Multi-spec plan execution (DAG)
```

## Verification Criteria

1. `dark-factory run <spec>` works with `enterprise_enabled=false` (default)
2. No enterprise module imported at orchestrator/iteration import time (test in Step 8)
3. `notify_*` functions are no-ops when enterprise is disabled
4. Enterprise features still work when `enterprise_enabled=true`
5. `dark-factory init` generates config with enterprise commented out
6. Enterprise CLI commands show helpful message when disabled
7. All existing tests pass

## Risks

- **Existing users with enterprise config** — existing `config.toml` files don't have `[enterprise] enabled = true`. Need migration: if `deploy_cmd` or similar enterprise config exists, auto-set `enterprise_enabled=true`. Add to `migrate-config` command.
- **Import guards may miss edge cases** — mitigated by the import isolation test
- **Single flag is too coarse** — accepted for now. Split when users actually need granularity.

## Non-Goals

- Do NOT delete enterprise code
- Do NOT refactor into a separate package (review suggestion noted but deferred — physical boundary is a larger refactor)
- Do NOT change enterprise feature behavior when enabled
- Do NOT make enterprise dependencies optional in pyproject.toml yet (separate future work)
