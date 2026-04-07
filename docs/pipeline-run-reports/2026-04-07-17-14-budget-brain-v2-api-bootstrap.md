# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date:** 2026-04-07 17:14 UTC
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Dark Factory version:** editable install from ~/sites/dark-factory (commit 04baa02)

---

## Pre-Run Checklist

- [x] Repository state reset (tracked files restored, untracked removed)
- [x] Runtime artifacts cleaned (scenarios/, reports/, audit/, state/, failures/, events.jsonl)
- [x] Environment bootstrap files removed (environment.toml, bootstrap delta)
- [x] Python bytecode caches cleared
- [x] `dark-factory init --update` run on target project
- [x] Editable install updated (`pip install -e`)
- [x] Docker images rebuilt (`dark-factory build node`) — all cached
- [x] Orphaned Docker containers/networks cleaned

---

## Run 1

### Configuration
- Autonomy level: 3 (Autonomous, default)
- Start time: 2026-04-07 17:15:44 UTC
- End time: 2026-04-07 17:15:44 UTC (immediate failure)
- Wall-clock duration: <1 second

### Pipeline Output (verbatim)

```
WARNING: config.toml runtime keys are deprecated — synthesized bootstrap delta at
~/sites/budget-brain-v2/.dark-factory/architecture/environment.deltas/0000-bootstrap-from-config-toml.toml.
Future spec runs will read environment.toml.

ERROR: error: source_dirs barrier pre-flight FAILED: `layout.source_dirs` is the framework
default `['src']` but the project root contains monorepo markers ['apps', 'packages'], which
strongly suggests source lives elsewhere. The Scenario Architect container would mask an
empty/non-existent `src/` while real source code remained readable — silent barrier leak.

Detected likely source directories: ['apps/api/src', 'apps/web/src', 'packages/shared/src'].
Set `layout.source_dirs = ['apps/api/src', 'apps/web/src', 'packages/shared/src']` in your
bootstrap spec's environment delta.
```

### State File
No state file created — pipeline failed before state initialization.

### Quality Gate Report
N/A — pipeline never reached any agent phase.

### Container Logs
N/A — no containers launched.

### Audit Log
N/A — no audit log created.

### Events JSONL
N/A — no events emitted.

### Outcome
**BLOCKED** — Pre-flight refused to launch (early abort per monitoring rule).

### Root Cause Analysis

**Bug: `source_dirs` preflight reads stale default from PipelineConfig instead of freshly-synthesized environment.toml**

The orchestrator performs these steps in sequence:

1. **Line 202** (`orchestrator.py`): `config = resolve_profile(spec_file, config)` — loads PipelineConfig from `config.toml`. Since `config.toml` has no `source_dirs` key, `config.source_dirs` remains the dataclass default `["src"]`.

2. **Line 212**: `ensure_environment_bootstrap(config, project_dir)` — synthesizes `environment.toml` from `config.toml` + monorepo detection. The backcompat synthesizer correctly detects monorepo markers and writes:
   ```toml
   [layout]
   source_dirs = [ "apps/api/src", "apps/web/src", "packages/shared/src" ]
   ```
   This is confirmed in the generated delta file (`0000-bootstrap-from-config-toml.toml`, line 23-28).

3. **Line 255**: `check_source_dirs_preflight(project_dir, config.source_dirs)` — passes `["src"]` (the stale PipelineConfig default), NOT the freshly-synthesized `["apps/api/src", ...]` from environment.toml.

4. The preflight correctly identifies `["src"]` as suspect on a monorepo with `apps/` and `packages/` markers, and raises.

**The synthesizer did the right thing. The preflight did the right thing. But the orchestrator passes the wrong value to the preflight** — it should re-read `source_dirs` from the synthesized environment.toml (or update `config.source_dirs` after bootstrap) before running the preflight check.

### Affected Code Paths

| File | Line | Issue |
|------|------|-------|
| `darkfactory/pipeline/orchestrator.py` | 255 | Passes `config.source_dirs` (stale default) instead of environment.toml value |
| `darkfactory/architecture/backcompat.py` | 108-128 | Correctly synthesizes source_dirs in delta — not the bug |
| `darkfactory/docker/source_dirs_preflight.py` | 33-75 | Correctly rejects `["src"]` on monorepo — not the bug |

### Suggested Fix (not implemented per instructions)

After `ensure_environment_bootstrap()` returns (line 217), the orchestrator should re-load `source_dirs` from the synthesized `environment.toml` and update `config.source_dirs` before calling `check_source_dirs_preflight()`. Something like:

```python
# After ensure_environment_bootstrap:
from darkfactory.architecture.environment import load_environment
env = load_environment(project_dir / ".dark-factory" / "architecture" / "environment.toml")
config.source_dirs = env.get("layout.source_dirs", config.source_dirs)
```

### Regression Risk
- **Severity: HIGH** — This blocks ALL monorepo projects that rely on backcompat synthesis for `source_dirs`. Any project with `config.toml` (no explicit `source_dirs`) + monorepo markers will hit this on every run.
- **First appearance:** Likely introduced when the `source_dirs_preflight` (Phase 4.5.1, commit 0f7502e) was added without accounting for the backcompat bootstrap that runs just before it.
- **Workaround:** Manually add `source_dirs` to `config.toml` under a `[core]` section, bypassing the need for backcompat synthesis to set it. Not tested.
