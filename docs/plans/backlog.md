# Dark Factory Backlog

Running list of known issues and improvements not yet scheduled into an active plan. Add new entries at the top of the relevant section. When an item is picked up, move it into a dated plan file under `docs/plans/` and link back.

---

## Scenario Architect quality

### Bug #8 — SA writes tests to detached `/tmp/` dirs
**Status:** Promoted to Phase 4.5 of `2026-04-08-pipeline-install-gate-and-untestable-tightening.md`.

Scenario-architect emits scenarios that write test scripts to `/tmp/bootstrap-edge-XXXX/` and shell out to run them. Those temp dirs have no `node_modules`, so the script crashes with `Cannot find module 'pino'` before exercising the behavior under test. Oracle correctly classifies as `ENV_ISSUE` but the scenario is just broken.
**First seen:** 2026-04-08 01:09 run (docs/pipeline-run-reports/2026-04-08-01-09-budget-brain-v2-2026-03-28-api-bootstrap.md)
**Fix direction:** SA prompt — tests must run from project root with project module resolution, not detached tmp dirs.

### Unknown HTTP assertion forms
**Status:** Promoted to Phase 4.5 of `2026-04-08-pipeline-install-gate-and-untestable-tightening.md`.

Scenario-architect emits assertions like `- Header "Access-Control-Allow-Origin" contains ""` that the HTTP scenario runner doesn't parse and silently skips. Either the runner's grammar is too narrow or the SA is hallucinating assertion syntax.
**First seen:** 2026-04-08 03:07 run
**Fix direction:** audit SA prompt's assertion grammar against runner's parser; add assertion-form validation at scenario-generation time.

---

## Pipeline ergonomics / reset

### `dark-factory init --update` clobbers customized config.toml
Every run of `dark-factory init --update` overwrites `.dark-factory/config.toml` with a fresh default, killing user customizations (notifications, autonomy level, enterprise flags, test_server settings, scenarios multi_run tuning). Reset procedure has to `git restore .dark-factory/config.toml` after every init.
**First seen:** 2026-04-08 03:07 run
**Fix direction:** `init --update` should only refresh `agents/` and `templates/` when a config already exists. Require explicit `--force-config` to overwrite.

### Reset prompt gaps — missing runtime artifacts
The standard reset prompt wipes `scenarios/ reports/ audit/ state/ failures/ events.jsonl` + `architecture/environment.toml` + `architecture/environment.deltas/`, but doesn't explicitly list:
- `reviews/` — prior-run reviewer outputs
- `metrics.jsonl` — prior-run metrics
- `precheck/` — regenerated per run
- `architecture/environment.toml.lock` — stale lock
- `architecture/*-manifest.yaml` — cached manifests (intentional to exercise Phase 3.6 cached-resume path, but should be documented)

Works today only because `.dark-factory/` is gitignored and `git clean -fdx` catches them. Fragile if any subpath ever becomes tracked.

### `dark-factory reset` helper
User-deferred. Idea: fold the reset procedure into a single CLI subcommand so the reset prompt simplifies to `dark-factory reset && dark-factory run <spec>`. Would obsolete most of the reset-prompt gaps above.
**Fix direction:** implement as `dark-factory reset [--keep-manifests] [--keep-config]`, then update the standard reset prompt to call it.

---

## Structural (from 2026-04-08 plan Phase 6)

### manifest-consumer-grep-lint
Static grep for architect-emitted manifest field names (`commands.*`, `server.*`, `layout.*`, `services`, `test_base_url`) in `darkfactory/` source. Warn on zero hits per field. Would have caught Bug #6 (and Bug #7 via adjacency). Narrow grep version only — no per-spec-type consumer registry.

### manifest-command-dispatcher
Unify gate / smoke / implementer setup on a single `run_manifest_command(name, manifest)` helper that honors architect-declared CWD. Today three call sites reinvent it inconsistently. Dispatcher + consumer-lint together prevent the next "contract without a consumer" class of bug.
