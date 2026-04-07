# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date (UTC):** 2026-04-07 14:41
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Pipeline version:** dark-factory 1.0.0 (editable install, commit 37a691f)

---

## Pre-Run Setup

### Repository Reset
- Restored tracked files via `git checkout -- .`
- Cleaned untracked files via `git clean -fd` (removed `src/`, `.dark-factory/architecture/`, `.dark-factory/failures/`, `.dark-factory/precheck/`)
- Removed runtime artifacts: scenarios, reports, audit, state, events.jsonl
- Updated agents and templates from dark-factory source
- Reinstalled dark-factory editable package
- Rebuilt Docker images (`dark-factory build node`) — all layers cached
- Cleaned orphaned Docker containers and networks

---

## Run 1

### Pipeline Execution
- **Start time (UTC):** 14:42:06
- **End time (UTC):** ~14:49:12 (interrupted immediately after architect completed)
- **Outcome:** INTERRUPTED — architecture manifest YAML parse failure
- **Run ID:** fa4bc166cb184735
- **Total wall time:** ~7 minutes

### Phase Progression

| Phase | Status | Wall Time | Notes |
|-------|--------|-----------|-------|
| Spec Validation | PASS | 252.8s (~4.2min) | Score 89/100, risk LOW |
| Architecture | COMPLETED (output invalid) | 171.9s (~2.9min) | Manifest generated but failed YAML parse |
| Scenarios | NOT REACHED | — | Blocked by manifest parse failure |
| Implementation | NOT REACHED | — | — |
| Quality Gate | NOT REACHED | — | — |

### Root Cause: Architecture Manifest YAML Parse Error

The architect agent generated a manifest file that contains bare TypeScript generic syntax in a YAML value. Specifically, line 64 of the manifest:

```yaml
- { name: check, type: function, constraints: [returns Promise<{status: ok|error, message?: string}>] }
```

The `>` character at column 106 is a YAML block scalar indicator. When the YAML parser encounters it mid-value like this, it fails:

```
ERROR: error: Architecture manifest parse errors:
  - Malformed YAML: while scanning for the next token
found character '>' that cannot start any token
  in "<unicode string>", line 64, column 106
```

The manifest parser (`darkfactory/spec/manifest.py:183`) catches the `yaml.YAMLError` and reports it, then the pipeline correctly interrupts.

### Analysis

**Why did the architect produce invalid YAML?**

The architect agent correctly modeled the `HealthCheck` data model but included TypeScript-style type annotations (`Promise<{status: ok|error, message?: string}>`) as a bare string in a YAML flow mapping. This is invalid because:
1. `>` is a reserved YAML character (block scalar indicator)
2. `|` is also reserved (literal block scalar)
3. The value needed to be quoted to be valid YAML

A correct version would be:
```yaml
- { name: check, type: function, constraints: ["returns Promise<{status: ok|error, message?: string}>"] }
```

**This is a recurring risk**: any time the architect describes TypeScript generics, Go channels, C++ templates, or other languages with `<>` syntax in unquoted YAML values, the manifest will fail to parse.

### Diagnostic Data

#### State File (final)
```
spec=2026-03-28-api-bootstrap
run_id=fa4bc166cb184735
spec_hash=ff42f734228fdcb4ba029324ca695c00e4b16bb412dc06b89fa16181ce1ed293
status=interrupted
iteration=1
completed_step=1
```

#### Events JSONL
```jsonl
{"ts":"2026-04-07T14:42:06Z","event":"SPEC_START","spec":"2026-03-28-api-bootstrap","run_id":"fa4bc166cb184735"}
{"ts":"2026-04-07T14:42:07Z","event":"AGENT_START","data":{"role":"spec-validator","attempt":1}}
{"ts":"2026-04-07T14:46:19Z","event":"AGENT_COMPLETED","data":{"role":"spec-validator","turns":200,"exit_code":0,"model_id":"claude-sonnet-4-20250514","est_input_tokens":800000,"est_output_tokens":400000,"wall_seconds":252.82}}
{"ts":"2026-04-07T14:46:20Z","event":"AGENT_START","data":{"role":"architect","attempt":1}}
{"ts":"2026-04-07T14:49:12Z","event":"AGENT_COMPLETED","data":{"role":"architect","turns":200,"exit_code":0,"model_id":"claude-sonnet-4-20250514","est_input_tokens":800000,"est_output_tokens":400000,"wall_seconds":171.91}}
```

Note: No INTERRUPTED or error event was emitted to events.jsonl — the pipeline printed the error to stderr only.

#### Pipeline stderr
```
WARNING: config.toml runtime keys are deprecated — synthesized bootstrap delta at .dark-factory/architecture/environment.deltas/0000-bootstrap-from-config-toml.toml
ERROR: error: Architecture manifest parse errors:
  - Malformed YAML: while scanning for the next token
found character '>' that cannot start any token
  in "<unicode string>", line 64, column 106
```

### Artifacts Produced
- `.dark-factory/reviews/2026-03-28-api-bootstrap-validation.md` — Spec validation report (PASS, 89/100)
- `.dark-factory/reports/2026-03-28-api-bootstrap-architecture.md` — Architecture generation report
- `.dark-factory/architecture/2026-03-28-api-bootstrap-manifest.yaml` — The malformed manifest (128 lines)
- `.dark-factory/architecture/environment.toml` — Environment config
- `.dark-factory/architecture/environment.toml.lock` — Lock file
- `.dark-factory/architecture/environment.deltas/0000-bootstrap-from-config-toml.toml` — Bootstrap delta from deprecated config.toml
- `.dark-factory/architecture/hints.json` — Architect hints

---

## Improvement Opportunities

### P0 — YAML Safety in Manifest Generation
The architect agent's system prompt should instruct it to **always quote string values** in YAML flow mappings that may contain reserved characters (`>`, `|`, `<`, `{`, `}`, `[`, `]`, `:`, `#`, `&`, `*`, `!`, `%`, `@`, `` ` ``). Alternatively, the manifest parser could attempt to sanitize or auto-quote problematic values before parsing. A third option: validate the manifest YAML inside the architect container before exiting, giving the agent a chance to self-correct.

### P1 — Missing INTERRUPTED Event in events.jsonl
When the pipeline interrupts due to a manifest parse error, it should emit a structured event to `events.jsonl` (e.g., `PIPELINE_ERROR` or `MANIFEST_PARSE_FAILED`) so that automated analysis tools can detect failure modes without parsing stderr.

### P2 — Spec Validator Wall Time
The spec validator took 252s (~4.2 min) with 200 turns — this seems high for a validation pass that produced a relatively short report. May be worth investigating whether the agent is doing unnecessary work or if the turn count is inflated.

### P3 — Config.toml Deprecation Warning
The pipeline still emits a deprecation warning about `config.toml` runtime keys. The budget-brain-v2 project should migrate to `environment.toml` to eliminate this warning, or the pipeline should handle the migration silently.
