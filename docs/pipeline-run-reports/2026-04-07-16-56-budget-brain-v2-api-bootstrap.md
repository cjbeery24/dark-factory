# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date:** 2026-04-07 16:56 UTC
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap

---

## Pre-Run State

- Repository had existing `.dark-factory/` artifacts from prior runs (agents, config.toml, plans, reviews, shipped-scenarios, specs, templates)
- Full repo reset performed: `git checkout -- .`, `git clean -fd`, removed runtime artifacts (scenarios, reports, audit, state, failures, events.jsonl)
- `dark-factory init --update` refreshed agents and templates
- `pip install -e` reinstalled dark-factory editable package
- Docker images rebuilt (all cached — no changes to dependencies)
- No orphaned Docker containers or networks found

---

## Run 1

### Pipeline Execution

**Result: IMMEDIATE FAILURE — source_dirs barrier pre-flight check**

The pipeline exited before launching any containers.

### Error Output

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

### Root Cause Analysis

1. **Monorepo detection:** budget-brain-v2 is a pnpm monorepo with source code in `apps/api/src`, `apps/web/src`, and `packages/shared/src`. The Phase 4.5.1 pre-flight check (`darkfactory/docker/source_dirs_preflight.py`) correctly detected the `apps` and `packages` markers.

2. **Silent barrier leak prevention:** If the pipeline ran with the default `source_dirs = ['src']`, the Scenario Architect container would mask an empty/non-existent `/project/src` directory via tmpfs overlay, while the actual source directories (`apps/*/src`, `packages/*/src`) would remain fully readable inside the container. This would completely defeat the information barrier — the agent that generates blind test scenarios would be able to read all implementation code.

3. **Config state:** The `environment.toml` had `source_dirs = []` (empty list), which the pipeline resolved to the framework default `['src']`. Neither the config.toml nor the bootstrap environment delta included `layout.source_dirs`.

4. **Fix required (not applied — observe-only):** The `environment.toml` or a spec-level environment delta needs:
   ```toml
   [layout]
   source_dirs = ["apps/api/src", "apps/web/src", "packages/shared/src"]
   ```

### State File
No state file was generated — the pipeline exited before creating one.

### Events (events.jsonl)
No events file was generated.

### Container Logs
No containers were launched.

### Quality Gate Report
Not reached.

---

## Observations

### What worked well
- **Pre-flight check did its job:** The Phase 4.5.1 source_dirs barrier pre-flight correctly prevented a silent barrier leak. Without this check, the Scenario Architect would have had full access to implementation source code, completely defeating the information barrier architecture.
- **Actionable error message:** The error correctly detected the monorepo structure and suggested the exact `source_dirs` values needed.
- **Config migration:** The deprecated `config.toml` runtime keys were automatically synthesized into a bootstrap environment delta.

### Issues found
1. **`dark-factory init` doesn't auto-detect source_dirs for monorepos:** Running `dark-factory init` on a pnpm monorepo set `source_dirs = []` instead of detecting `apps/*/src` and `packages/*/src`. The pre-flight check has the detection logic (`_detect_candidate_source_dirs`), but `init` doesn't use it. This means every monorepo will fail on first pipeline run until the user manually sets `source_dirs`.

2. **No `sync-agents` CLI command:** There's no dedicated command to update agents/templates in a target project. Had to use `dark-factory init --update` which works but could overwrite config. A lighter-weight sync command would be safer for ongoing updates.

3. **Environment delta workflow unclear:** The error says to set `source_dirs` in "your bootstrap spec's environment delta" but doesn't explain what file to create or where. The auto-generated `0000-bootstrap-from-config-toml.toml` delta was created from `config.toml` migration but didn't include `source_dirs`. A user would need to know the delta file format and naming convention.

---

## Summary & Metrics

| Metric | Value |
|--------|-------|
| Pipeline outcome | BLOCKED — pre-flight failure |
| Phases completed | 0 / ~7 |
| Total wall time | <1 second |
| Containers launched | 0 |
| Iterations | 0 |
| Blocking issue | `source_dirs` not configured for monorepo |
| Fix complexity | Low — single config edit |

**Conclusion:** The pipeline cannot run on this monorepo project without first configuring `layout.source_dirs` in the environment. The pre-flight check is functioning correctly as a safety guard, but the `init` command should ideally auto-populate `source_dirs` for monorepos to prevent this first-run friction.
