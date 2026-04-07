# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date (UTC):** 2026-04-07 03:49
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Dark Factory commit:** 0793711

## Pre-Run State

- Previous run existed with `status=interrupted`, `iteration=2`, `last_decision=NO-SHIP`, `pass_rate=50`
- Repository had modified tracked files and untracked .dark-factory artifacts from prior run
- Full reset performed before this run

---

## Run 1

### Setup
- Repository reset (tracked files restored, untracked files removed, .dark-factory artifacts cleaned)
- Python bytecache cleared
- Agents/templates synced to budget-brain-v2
- dark-factory editable install refreshed
- Docker images rebuilt (all layers cached)
- Orphaned containers/networks cleaned

### Timeline

| Time (UTC) | Event |
|---|---|
| 03:50:01 | `SPEC_START` — run_id `870a5ed9985d48c5` |
| 03:50:02 | `AGENT_START` — spec-validator |
| 03:53:17 | `AGENT_COMPLETED` — spec-validator (195.7s wall, ~800K input / ~400K output tokens) |
| 03:53:18 | `AGENT_START` — architect |
| 03:55:42 | `AGENT_COMPLETED` — architect (144.4s wall, ~800K input / ~400K output tokens) |
| ~03:55:43 | **Pipeline aborted** — environment contract allowlist violation |

**Total wall time:** ~5m42s (pipeline never reached implementation or scenario phases)

### Phase Progression

Final state file:
```
spec=2026-03-28-api-bootstrap
run_id=870a5ed9985d48c5
status=interrupted
iteration=1
completed_step=1
```

Pipeline completed steps 0 (spec validation) and 1 (architect), then failed during post-architect environment contract validation.

### Events (full JSONL)

```jsonl
{"ts":"2026-04-07T03:50:01Z","event":"SPEC_START","spec":"2026-03-28-api-bootstrap","run_id":"870a5ed9985d48c5"}
{"ts":"2026-04-07T03:50:02Z","event":"AGENT_START","data":{"role":"spec-validator","attempt":1}}
{"ts":"2026-04-07T03:53:17Z","event":"AGENT_COMPLETED","data":{"role":"spec-validator","turns":200,"exit_code":0,"model_id":"claude-sonnet-4-20250514","wall_seconds":195.71}}
{"ts":"2026-04-07T03:53:18Z","event":"AGENT_START","data":{"role":"architect","attempt":1}}
{"ts":"2026-04-07T03:55:42Z","event":"AGENT_COMPLETED","data":{"role":"architect","turns":200,"exit_code":0,"model_id":"claude-sonnet-4-20250514","wall_seconds":144.42}}
```

No gate, scenario, implementation, review, or oracle events were emitted — the pipeline never reached those phases.

### Root Cause: Environment Contract Allowlist Violation

**Error message (verbatim):**
```
ERROR: error: Architecture manifest violates environment contract allowlist:
spec_type='feature' may not declare ['test_base_url'].
Only spec_type='bootstrap' may set service or test_base_url.
(These fields bypass the env contract schema in Phase 3 and are gated here pending Phase 4 schema extension.)
```

**Chain of events:**

1. The spec `2026-03-28-api-bootstrap` has no `spec_type` in its frontmatter metadata block. The frontmatter only contains `feature-id`, `depends-on`, and `plan`.

2. The orchestrator (`orchestrator.py:946-953`) warns and defaults missing `spec_type` to `"feature"`:
   ```
   WARNING: spec_type missing from frontmatter for 2026-03-28-api-bootstrap;
   defaulting to 'feature' until migration. Run `dark-factory specs migrate-frontmatter` to backfill.
   ```

3. The architect agent correctly identifies this as a server bootstrap spec and emits a manifest containing `test_base_url: "http://localhost:3001"` (line 138 of the manifest YAML). This is correct behavior — the spec IS bootstrapping an API server that needs health checks against a live URL.

4. The orchestrator's post-architect allowlist check (`orchestrator.py:1082-1092`) rejects the manifest because the effective `spec_type` is `"feature"` (not `"bootstrap"`), and only bootstrap specs may declare `test_base_url`.

**This is a spec metadata issue, not a pipeline logic bug.** The allowlist is working as designed. The spec simply lacks the `spec_type: bootstrap` frontmatter that would authorize the architect to set `test_base_url`.

### Additional Warning

```
WARNING: config.toml runtime keys are deprecated — synthesized bootstrap delta at
.dark-factory/architecture/environment.deltas/0000-bootstrap-from-config-toml.toml.
Future spec runs will read environment.toml.
```

The config.toml → environment.toml migration is in progress. A bootstrap delta was auto-synthesized, which correctly set `commands.build`, `commands.lint`, `commands.typecheck`, and `server.boot_timeout_s`. This worked as expected.

### Artifacts Produced

- `architecture/2026-03-28-api-bootstrap-manifest.yaml` — Complete architecture manifest (file_map, data_models, runtime section with test_base_url)
- `architecture/environment.toml` — Environment config (commands, layout, runtime, server sections)
- `architecture/environment.toml.lock` — Lock file
- `architecture/environment.deltas/0000-bootstrap-from-config-toml.toml` — Bootstrap delta from config.toml migration
- `architecture/hints.json` — Lockfile hints for implementer

### Quality Gate Results
N/A — pipeline never reached quality gate phase.

### Container/Server Logs
N/A — no implementation or test containers were launched.

### Observations

1. **Blocking issue: `spec_type` frontmatter missing from pre-Phase-4 specs.** All specs created by `/df-write-plan` before Phase 4 landed lack `spec_type` in frontmatter. The `dark-factory specs migrate-frontmatter` command is referenced in the warning but may not exist yet.

2. **The architect correctly identified this as a bootstrap spec** and emitted `test_base_url`, but the orchestrator's frontmatter-based allowlist overrode the architect's judgment. This is the intended safety behavior, but it means old specs cannot proceed without frontmatter updates.

3. **Token usage is high for early phases.** Both spec-validator and architect show ~800K input / ~400K output token estimates. For a spec that ultimately fails a metadata check, this represents wasted cost. Consider: could the `spec_type` allowlist check run _before_ the architect agent, since it only needs the spec frontmatter and could fail fast?

4. **The pipeline correctly set `status=interrupted`** rather than `status=failed`, which is accurate — the run was interrupted by a validation error, not by an agent failure.

### Outcome

**Pipeline run failed before reaching implementation or scenario phases.** Root cause is a missing `spec_type: bootstrap` in the spec's frontmatter metadata. The fix would be to add `spec_type: bootstrap` to the spec's dark-factory-metadata block, but per instructions, no fixes are being applied in this test run.

### Recommendations for Pipeline Improvement

1. **Fail-fast on spec_type allowlist:** Move the `test_base_url` / service allowlist check to run before the architect agent starts (or at least before it completes), since the check only depends on spec frontmatter, not architect output. This would save ~2.5 minutes and ~1.2M tokens.

2. **Frontmatter migration tooling:** Implement `dark-factory specs migrate-frontmatter` to backfill `spec_type` on existing specs, or detect it automatically from spec title/content (e.g., specs with "bootstrap" in the name are likely `spec_type: bootstrap`).

3. **Architect awareness of spec_type constraints:** The architect's system prompt could include the allowlist rules so it avoids emitting fields that will be rejected. Currently the architect does the right thing architecturally but gets overruled by a metadata gap.
