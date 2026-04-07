# Pipeline Resilience Rewrite

**Status:** Not started
**Created:** 2026-04-06
**Consensus:** Liotta + Neo signed off on v2.1
**Goal:** Make the pipeline able to ship a plan of mixed specs (bootstrap, feature, bugfix) end-to-end. Later specs track and modify environment architecture set up by foundational specs as the project matures.

---

## Architectural decisions (locked)

1. **`environment.toml` is a materialized view** over an append-only delta log of structured environment amendments at `.dark-factory/architecture/environment.deltas/NNNN-<spec>.toml`. The Architect's existing `manifest.runtime` block is repurposed as the per-spec amendment payload — no new file format, no translation layer.
2. **`Config` re-hydration at step boundaries** via `step_begin(spec, config)` called as the first statement of all eight known steps, enforced by static grep + dynamic registry guard. No EnvironmentView abstraction.
3. **Plan = ordered list, not DAG.** Plan runner is `for spec in specs: run_single_spec; refold_deltas; union_smoke_probe`. Single-spec runs remain primary entry point. DAG deferred until 10 clean sequential plan runs prove it's worth the complexity.
4. **Information barriers untouched throughout.** Amendment schema rejects `test.*`, `scenario.*`, `oracle.*` paths so the env contract cannot become a leak channel.

---

## Phase 0 — Tourniquet + verification gate

**Goal:** Stop bleeding on pre-check. Confirm the run-20:34 failure is *information starvation*, not *capability starvation*, before committing to env.toml as the fix.

### 0.1 Demote precheck to advisory
- [x] Add `fatal: bool = False` to `PreCheckResult` (`darkfactory/spec/precheck.py:123`); set `fatal=True` on the three structural early returns (oversize, parser error, empty file).
- [x] `darkfactory/pipeline/orchestrator.py` precheck block: gate abort on `fatal`, not `passed`. Record-and-continue on advisory findings.
- [x] CLI `_cmd_precheck` and `_cmd_validate` (`darkfactory/cli.py`): exit 0 on advisory-only, exit 1 on `fatal`.
- [x] Add `--strict` flag to both `precheck` and `validate` subparsers; pipeline never invokes `--strict`.

### 0.2 Dual call site for advisory JSON
- [x] Create `darkfactory/spec/advisory.py` with `ensure_precheck_dir`, `precheck_advisory_path` (pure), `build_advisory_payload`, `write_precheck_advisory`. Deterministic JSON: `sort_keys=True, indent=2, ensure_ascii=True`.
- [x] Location: `.dark-factory/precheck/<spec>.json` — NEW top-level dir, deliberately outside `.dark-factory/state/` because state is masked from the Architect (see `darkfactory/docker/barriers.py:96`).
- [x] Orchestrator and both CLI handlers all write through `write_precheck_advisory`. Exception handling tightened to `(OSError, TypeError)`.
- [x] Update `agents/architect.md`: add `.dark-factory/precheck/<spec>.json` to CAN READ list and add Pre-Check Advisory section.
- [x] No mount changes needed: project is already mounted into Architect container; precheck/ is not in any mask list.

**Phase 0 exit criteria:**
- [x] Verification gate run (see 0.3) reached the Implementer phase and beyond.
- [x] Byte-identity test pinned: `tests/unit/test_advisory.py` covers empty AND populated finding sets across all 7 finding categories (11 tests passing).

### 0.3 Verification gate (BLOCKING — do not proceed without passing)
- [x] Reset `budget-brain-v2`. Ran `dark-factory run 2026-03-28-api-bootstrap` (run `6c41c4ae0c7b4bc6`).
- [x] Pipeline reached Implementer, smoke, gate, reviewer, AND oracle. Did not abort at pre-check.
- [x] Smoke=passed, pass_rate=70%, server_error=false, crashes=0. Oracle issued correctly-reasoned NO-SHIP at 70% (target 95%).
- [x] **Pass condition met:** failures are "wrong implementation" type (FR-3 error middleware, CORS dev-conditional, ZodError extraction), not "command not found" or "mount missing." Diagnosis confirmed: info-starvation, not capability-starvation. → proceed to Phase 1.
- [x] Documented in `docs/pipeline-run-reports/2026-04-06-23-57-budget-brain-v2-api-bootstrap.md`.

**Linus review (post-Phase-0 fixes applied):**
- [x] Tightened `except Exception` → `except (OSError, TypeError)` in 3 sites.
- [x] Renamed/expanded `_count_advisory_findings` to include ambiguities; removed redundant `elif` branch.
- [x] Made `precheck_advisory_path` pure (no side effect); `write_precheck_advisory` calls `ensure_precheck_dir` directly.
- [x] Added byte-identity test for non-empty findings (`test_advisory_byte_identical_with_populated_findings`).
- [ ] **Deferred to Phase 1:** Replace `MagicMock` precheck mocks with real `PreCheckResult`, then drop the three `getattr(..., "fatal", False)` defensive coercions (tracked).
- [ ] **Deferred to Phase 1:** Decide Implementer container access to `.dark-factory/precheck/` — explicit mask or explicit allow (tracked).
- [ ] **Deferred to Phase 1:** Decide whether `summary` belongs in advisory payload (tracked).

### 0.4 Manual probe (proof-of-concept for env.toml)
- [ ] As a one-time probe, before Phase 2 lands: manually inject the string `"This project uses pnpm; use \`pnpm --filter @budget-brain/api dev\` to start the server."` into the implementer prompt.
- [ ] Re-run the spec.
- [ ] Confirm: implementer uses pnpm and smoke passes.
- [ ] Document in `docs/migration/phase-0-verification.md`: this is the manual proof that giving the implementer environment information unblocks the loop.
- [ ] Revert the prompt injection.

---

## Phase 1 — Telemetry, runner correctness, dead-weight removal

**Goal:** Produce the diagnostic signal needed to operate the rest of the rollout. Fix the runner bugs distorting that signal. Remove code firing on noise.

### 1.1 Persist smoke container logs (success + failure)
- [x] `_persist_smoke_logs` helper added to `darkfactory/pipeline/quality_gate.py`. Captures container logs (tail=500) on every invocation. Header includes resolved `server_setup_cmd`, `test_server_cmd`, container name.
- [x] Symlinks `latest-smoke.log` to most recent (best-effort; logs at debug on filesystems without symlink support).
- [x] `run_server_smoke` pre-creates `.dark-factory/reports/<spec>/<run_id>/` BEFORE invoking `_start_server_stack`, eliminating the run-20:34 regression.
- [x] Persist call wired in BOTH success and failure paths of `run_server_smoke`. Failure header documents the error even when the container has already been torn down.

### 1.2 Cap implementer re-entry at 1
- [x] `models/config.py`: `max_pre_gate_retries` and `max_smoke_retries` defaults lowered from 2 → 1. Comment cites the "400 turns won't progress" rationale.
- [x] `pipeline/agents.py`: added `attempt_offset` parameter to `invoke_agent`; AGENT_START event uses `attempt + attempt_offset` so outer retries are visible.
- [x] `pipeline/iteration.py`: `_step_implementation` accepts and forwards `attempt_offset`. Pre-gate retry loop passes `pre_gate_attempt + 1`; smoke retry loop passes `max_pre_gate_retries + smoke_attempt + 1`.
- [x] No restructure of the retry loops themselves — the cap-at-1 behavior is achieved by lowering the default config bounds, not by removing the loop.

### 1.3 Audit `server_setup_cmd` readers (BLOCKER for Phase 2.3)
- [x] Audited all 14 modules touching the relevant fields. Tier 1 (pipeline runtime), Tier 2 (scenarios), and Tier 3 (setup/init) classified.
- [x] `docs/migration/server_setup_cmd-readers.md` committed with exhaustive `(file, line)` table.
- [x] Phase 2.3 / Phase 2.6 hand-off documented inline in the audit file.

### 1.4 Strip trajectory + plateau classifier — DEFERRED to Phase 7
**Status:** Scope re-assessed during Phase 1 implementation. The "strip" is not
a surgical deletion — it touches `iteration.py:599-690` (entire trajectory +
early-escalation block), `iteration.py:768-809` (`_apply_strategy`), 6 fields
on `SpecRun`, 4 fields on `PipelineConfig`, 2 event types, 3 dedicated test
files, and references in `loader.py`/`defaults.py`/`init.py`/`pipeline.py`/
`errors.py`/`dispatcher.py`/`dashboard.py`.

The `_ESCALATION_TRAJECTORIES` short-circuit at iteration ≥ 3 is currently the
only mechanism that stops a degrading run before it burns the full iteration
budget; `ship_with_gaps` depends on `stable_failure_ratio`. Removing these
changes pipeline behavior in ways Phase 0's verification gate does not exercise.

Deferred to **Phase 7 (surgical strip)**, where the env-contract rewrite has
already shifted the iteration loop's center of gravity and the classifier code
is provably dead in observed runs.

### 1.5 Strip mutation testing + token placeholders — DEFERRED to Phase 7
**Status:** Same reasoning as 1.4. `MUTATION_CMD` and the `est_*_tokens`
placeholders are noise, not blockers. Deletion deferred to Phase 7 alongside
the trajectory strip.

**Note:** `0e0a0be` (`fresh=True` bandaid) revert is moved to Phase 2.6 — reverting before `load_environment` exists reintroduces the staleness bug Phase 0's verification depends on.

**Phase 1 exit criteria:**
- [x] Smoke logs persist on both outcomes (1.1).
- [x] A run that hits the pre-gate / smoke retry loop shows `attempt:N` reflecting outer retry, not `attempt:1` again (1.2 — `attempt_offset` threaded through `invoke_agent`).
- [ ] ~~Anomaly detector and trajectory classifier are gone from the codebase.~~ → Phase 7
- [x] `server_setup_cmd` reader audit document committed (1.3).

---

## Phase 2 — Environment contract plumbing

**Goal:** Introduce `environment.toml` as a materialized view over an append-only delta log. Migrate readers. Add backcompat shim. **No agent writes deltas yet** — that's Phase 3.

### 2.1 Schema + canonical_dump
- [x] Create `darkfactory/architecture/environment.py` with `EnvironmentContract` dataclass:
  ```python
  runtime: Runtime          # language, package_manager, language_version
  commands: Commands        # install, server_start, server_health, build, lint, typecheck, test
  server: ServerConfig      # port, health_path, boot_timeout_s
  layout: Layout            # source_dirs, test_dirs
  ```
- [x] Strict allowlist `ALLOWED_PATHS` of dotted paths permitted in amendments. Reject anything under `test.*`, `scenario.*`, `oracle.*`.
- [x] `BOOTSTRAP_ONLY_PATHS = {"runtime.language", "layout.source_dirs"}`.
- [x] `validate_environment(env, spec_type) -> list[Error]` enforcing allowlist + bootstrap-only paths.
- [x] Implement `canonical_dump(contract) -> bytes` as a **hand-rolled deterministic TOML writer**, NOT a wrapper around `tomli_w` / `tomlkit`. Rules:
  - Lexicographically-sorted keys at every level
  - ASCII-only output
  - Double-quoted strings with `\\` and `\"` escapes only
  - Integers as decimal, booleans as lowercase
  - Lists as `[ "a", "b" ]` with one space after each comma
  - No inline tables, no comments
  - LF line endings, trailing newline
- [x] Golden-file test `tests/unit/test_canonical_dump.py` against `tests/fixtures/environment_canonical.toml`. Byte-equality assertion. (14 tests passing.)

### 2.2 Delta log + fold
- [x] `darkfactory/architecture/deltas.py` + `tests/unit/test_deltas.py` (12 tests passing).
- [x] Filesystem layout under `.dark-factory/architecture/`:
  ```
  environment.toml                # materialized view (regenerated, never hand-edited)
  environment.toml.lock           # hash of last successful fold
  environment.deltas/
    0001-<spec>.toml
    0002-<spec>.toml
    ...
  ```
- [x] Delta format: `schema_version`, `spec_id`, `parent_hash` (sha256 of `canonical_dump` at Architect read time), `applied_at`, `[[ops]]` blocks with `op` (set/modify/remove), `path`, `value`.
- [x] `fold_deltas(deltas_dir) -> EnvironmentContract` — deterministic, total. Lexicographic order.
- [x] `materialize(deltas_dir, out_path)` — fold + write `environment.toml` + `.lock` sidecar.
- [x] `EnvironmentConflict` raised on parent_hash mismatch, forbidden-path write, schema mismatch, malformed op, or modify-on-default.
- [x] Unit tests: fold determinism, conflict detection, forbidden path rejection, modify-on-default, lex ordering, materialize+lock.

### 2.3 Reader migration via `step_begin`
- [x] `load_environment(project_dir) -> EnvironmentContract` — single read path. Caches per process. (commit 8d435bc, `darkfactory/architecture/runtime.py`)
- [x] Rename `patch_config_from_runtime` → `refresh_config_from_environment`. **Partial:** `refresh_config_from_environment` is the canonical path and runs at every step boundary via `step_begin`. `patch_config_from_runtime` has been slimmed down to only the two fields that are not yet in `EnvironmentContract` (`services`, `test_base_url`) — it survives as a bridge until Phase 4 extends the env contract schema, at which point it can be deleted entirely along with its call site in `_step_architecture_lock`.
- [x] `step_begin(spec, config) -> Config`: calls `load_environment` + `refresh_config_from_environment`. Records `(spec_id, step_id)` in per-process registry, resets prior entry first. (commit 8d435bc)
- [x] Wire `step_begin` as the first statement of all eight call sites:
  - [x] Architect step (orchestrator.py:944)
  - [x] Scenario Architect step (orchestrator.py:1243, :1498, :1885)
  - [x] Implementer step (iteration.py:932)
  - [x] Quality Gate step (iteration.py:1095)
  - [x] Reviewer step (iteration.py:1225)
  - [x] Oracle step (iteration.py:1333)
  - [x] Evaluator step (iteration.py:1177)
  - [x] Iteration loop per-cycle re-entry in `iteration.py` (iteration.py:421)
- [x] Migrate every reader catalogued in 1.3 (`config.server_setup_cmd`, `config.test_cmd`, etc.). Verified 2026-04-07: every Tier 1/Tier 2 reader lives inside a function whose Config has been freshened by `step_begin` (quality_gate.py reads → `_step_quality_gate`; iteration.py reads → `_step_implementation`; orchestrator.py scenario-arch context block → `_step_scenario_architect`; workspace check → post-`_step_spec_validator`). Pre-flight port/base_url sanity checks in `orchestrator.py:105,120` intentionally read raw config.toml values *before* any step runs — per the "field-level reads stay unchanged" clause, they don't need freshening because the Architect hasn't amended anything yet at that point.
- [x] Delete `docs/migration/server_setup_cmd-readers.md` once migration verified. Deleted.

### 2.4 Backcompat shim
- [x] Create `darkfactory/architecture/backcompat.py`. (commit bf6a0ba; follow-up 8c2ea7c for monorepo source_dirs detection)
- [x] If `environment.toml` doesn't exist when pipeline starts AND `config.toml` has runtime keys: generate synthetic `0000-bootstrap-from-config-toml.toml` delta. Apply. Materialize.
- [x] Mark `config.toml` runtime keys deprecated (CLI warning).
- [x] After `environment.toml` exists, `config.toml` runtime keys become a HARD ERROR. One-shot migration, not permanent fallback.

### 2.5 Plan-state schema
- [x] Create `darkfactory/plan/state.py` with `ShippedSpec` dataclass. (commit 609bc8f — fields match spec exactly)
- [x] Persisted at `.dark-factory/plan/shipped.jsonl`. Append-only. (fsynced, atomic append via single-write POSIX O_APPEND)

### 2.6 Revert `0e0a0be` (`fresh=True` bandaid)
- [x] The `fresh=True` bandaid at the `dark-factory validate` call site was reverted in commit f136632 ("Revert 'Pass fresh=True from validate...'"). **However**, that revert alone was unsafe: it re-introduced the original bug where `validate` routed through resume detection and hard-errored with "spec has changed since last run — use --fresh" whenever the user edited a spec after a prior `run`. The plan's stated rationale ("load_environment provides a fresh read path") didn't actually apply — the bandaid concerned the *spec-state resume path*, not environment.toml loading; they're two different freshness concerns. The structural fix: new `allow_resume: bool = True` kwarg on `run_pipeline`; `_cmd_validate` passes `allow_resume=False` because validate is read-only and has no resume state worth protecting. The auto-resume gate at `orchestrator.py:272` now reads `if allow_resume and not fresh and state_file.exists()`. Two regression tests pin the behavior: `test_allow_resume_false_skips_stale_state_check` (validate bypasses stale hash) and `test_allow_resume_true_still_catches_stale_state` (the guard still fires for `dark-factory run`). `fresh: bool = False` on `run_pipeline` is kept — it's a legitimate `--fresh` flag for `dark-factory run`, not part of the bandaid.

**Phase 2 exit criteria:**
- [x] Pipeline runs on a project with only `config.toml` and produces a synthetic delta + materialized `environment.toml`. Covered by `tests/unit/test_backcompat.py::test_synthesizes_when_missing` + `::test_round_trips_through_load_environment` (11 backcompat tests total, all passing).
- [x] All quality_gate readers use `load_environment`. Implemented as "readers use PipelineConfig fields; Config is freshened in place at step boundary by `step_begin` which calls `load_environment` then `refresh_config_from_environment`". Verified per-reader in task 16's Phase 2.3 reader-migration verification (see commit fbf449c for the audit) and statically enforced by `tests/unit/test_step_begin_enforcement.py` (14 tests).
- [x] Unit tests: fold determinism, conflict detection, forbidden-path rejection, canonical_dump byte-equality. `tests/unit/test_deltas.py` (fold + conflict + forbidden-path), `tests/unit/test_canonical_dump.py` (byte-equality golden), `tests/unit/test_environment.py` (schema + allowlist + bootstrap-only paths). 72 unit tests across Phase 2 modules, all passing.
- [x] **Step-begin enforcement test** `tests/unit/test_step_begin_enforcement.py` (14 tests, all passing). Implemented as AST-based static analysis rather than grep-based, which is stricter:
  - [x] Pinned set of 11 agent-invoking step functions; each must call `step_begin(...)` as its first executable statement (parametrized test, 11 cases).
  - [x] Off-list check: any function in `orchestrator.py` / `iteration.py` that calls `invoke_agent` and is not in the pinned set (or on the loop-exempt list) fails the test. Catches "someone added a new step function and forgot step_begin".
  - [x] Loop closure: `run_iteration_loop` is exempt from the first-statement rule (it calls `step_begin` inside the while loop), but an explicit test asserts it contains zero direct `invoke_agent` calls — so the exemption can't be abused.
  - [x] **Two-cycle test:** `step_begin` called twice with the same `(spec_id, step_id)` asserts registry size stays 1 and the counter resets to 1.
  - [x] **Deliberately omitted:** the `Config.__getattribute__` wrapper raising `StepBegunNotCalled`. Phase 2.3 freshens config *in place*; readers don't (and shouldn't) know whether a step is active. A dynamic wrapper would re-introduce exactly the coupling Phase 2.3 removed. The AST-based approach gives us a stronger guarantee (static, catches additions at import time via test collection) without the coupling. Rationale is in the test file's module docstring.

---

## Phase 3 — Architect emits amendments

**Goal:** Wire the Architect to actually produce environment deltas. Repurpose `manifest.runtime` as the amendment payload itself.

### 3.1 Architect prompt rewrite
- [x] `agents/architect.md`: "Environment Contract" section added (lines 37-53, commits 5bbc79d + adfe041). Covers all three branches (bootstrap = full runtime; feature/bugfix = partial/empty; empty runtime = no-op delta).
- [x] `darkfactory/pipeline/orchestrator.py` Architect prompt construction rewritten to inject `environment.toml` by reference. See `orchestrator.py:1012-1017` Phase 3.1 comment block.
- [x] `spec_type=None` policy: defaulting to `feature` with WARN log. Commit 65d4d2f ("Phase 3.1 fix: actually parse spec_type from frontmatter") fixed the bug where SpecRun had no `spec_type` field and `getattr` always returned None — policy now works as specified.

### 3.2 Architect mount table
- [x] Architect container mount: `.dark-factory/architecture/` is writable on the Architect step (see `docker/barriers.py:120` `write_dirs=[".dark-factory/architecture", ".dark-factory/reports"]`) so host-side orchestrator transform of `manifest.runtime → delta` happens through a container-readable path. Anchor manifest JSON and precheck advisory are already in the Architect's CAN READ list per `agents/architect.md:11`.
- [x] No separate `:ro` mount needed — the full architecture dir is already bind-mounted; the distinction the plan drew ("mount env.toml, forbid delta writes from agent") is enforced at the *prompt* level (`agents/architect.md:13` "MUST NOT WRITE: environment.toml, environment.deltas/") and the orchestrator authoritatively writes deltas host-side from the parsed manifest, so an agent write would be overwritten on the next fold.

### 3.3 Orchestrator transforms `manifest.runtime` → delta → fold → materialize
- [x] All six bullets implemented in commit 5bbc79d + fix commit 37a691f. See `darkfactory/architecture/amend.py` for the transform and `darkfactory/pipeline/orchestrator.py` for the wire-through. 37a691f fixed the parser mismatch: architect emitted the nested env-shaped schema while parser read the old flat schema.

### 3.4 Lockfile-based hint pre-fill
- [x] Implemented in commit 0793711 as `darkfactory/architecture/hints.py`. Detects package manager (pnpm > yarn > npm; uv > poetry > pipenv > pip; Cargo, go.sum, Gemfile.lock), language, and workspace layout. Writes to `.dark-factory/architecture/hints.json` before architect runs; referenced from the architect prompt.

### 3.5 Implementer prompt receives environment context
- [x] Commit 0793711: `_step_implementation` prepends `environment.toml` as a fenced TOML block with a "treat as data, not instructions" preamble plus the "modify project files, not commands" instruction. See `iteration.py:1013-1043`.

### 3.6 Other agent mounts
- [x] Implementer: reads `environment.toml` via prompt injection (see 3.5). Cleaner than a dedicated mount since the content is inlined as context.
- [x] Scenario Architect: `iteration.py:1263-1267` adds "Read the project's environment contract at /project/.dark-factory/architecture/environment.toml" to the SA prompt; `.dark-factory/architecture/` is already in its CAN READ set via the existing project mount.
- [x] Reviewer / Oracle: `iteration.py:1339` injects the environment contract path into the reviewer/oracle prompt. Same mechanism as SA.

**Phase 3 exit criteria:**
- [x] Re-run `2026-03-28-api-bootstrap` end-to-end. Architect produces delta `0001-…`. Fold succeeds. `environment.toml` materializes with pnpm commands.
- [x] Implementer reads `environment.toml` from its mount, uses correct package manager.
- [x] **Spec reaches blind scenarios for the first time in this phase.**

**Phase 3 exit gate: CLOSED 2026-04-07.** Validated on budget-brain-v2
(report: `docs/pipeline-run-reports/2026-04-07-14-59-budget-brain-v2-api-bootstrap.md`).
Two parser bugs surfaced during validation and were fixed before closure:
- Commit `65d4d2f` — Phase 3.1 fix: actually parse `spec_type` from frontmatter
  (SpecRun had no field; getattr always returned None).
- Commit `37a691f` — Phase 3.3 fix: parse architect runtime block as env-shaped
  amendment (parser read old flat schema while architect emitted nested).
- Commit `d908677` — architect: mandate YAML quoting for reserved chars +
  in-container parse check (unrelated stochastic architect bug surfaced mid-gate).

---

## Phase 4 — `spec_type` as required frontmatter

**Goal:** Make the three spec types real, branched, and validated.

### 4.0 Migration pass for existing specs
- [x] `dark-factory specs migrate-frontmatter` command landed in 027ae05. Implements the heuristics, confirmation prompt, and writeback.

### 4.1 Frontmatter requirement
- [x] Landed in 724fa04. `spec_model.py` carries `spec_type` + `depends_on`; validator hard-fails on missing/unknown spec_type (65d4d2f fix: actually parse it from frontmatter).

### 4.2 Smoke probe / policy split
- [x] Landed in 9fe858e. Smoke always runs, `gate_policy` resolver with bootstrap/iter1 advisory, fatal elsewhere; iteration loop consumes the policy.

### 4.3 Coverage floor by spec_type
- [x] Landed in a506537. Coverage reads spec_type; bootstrap/feature/bugfix tiers applied.

### 4.4 Architect amendment validation by spec_type
- [x] Landed in `darkfactory/architecture/amend.py` (`_filter_by_spec_type`). Bootstrap may write everything; feature/bugfix rejected at validation time if they touch `runtime.language` or `layout.source_dirs`.

### 4.5 `/df-write-spec` skill update
- [x] Landed in a5fe656. spec_type is required prompt; bootstrap/bugfix/feature branches carry their per-type rules.

### 4.6 `dark-factory validate` final form
- [x] Landed in ec8b7e1. Validate enforces spec_type + bugfix Reproduction section; advisory vs fatal split matches the plan.

**Phase 4 exit criteria:**
- [x] All three gates met via the commits above; bf6d699 exempts bootstrap specs from the workspace-script consistency check as a follow-up refinement.

---

## Phase 4.5 — source_dirs barrier leak (SECURITY)

**Goal:** Close a latent information-barrier leak where the Scenario Architect
container can read implementation source code on projects whose layout doesn't
match the default `source_dirs = ["src"]`.

**Background:** Surfaced during the Phase 3 exit-gate run on budget-brain-v2
(report: `2026-04-07-14-59-budget-brain-v2-api-bootstrap.md`). budget-brain-v2
is a pnpm monorepo with source in `apps/` and `packages/`; `src/` exists but is
empty. `config.toml` never set `source_dirs`, so the scenario container masked
`/project/src` (an empty dir) and the architect's code in `apps/`/`packages/`
remained readable. The primary Docker filesystem barrier — the whole point of
the framework — was non-functional for this project and every prior run.

Compounding: by the end of this same run, the architect's amendment had
correctly written `layout.source_dirs = ["apps/api/src"]` to `environment.toml`,
but that value landed *after* the Scenario Architect container was already
launched with the stale `source_dirs` from `config.toml`. Contract updates from
the architect step do not propagate to downstream mount configurations within
the same run — Phase 2's step-boundary primitive was supposed to enable this
but the mount config wiring was never done.

### 4.5.1 Immediate detection / refuse-to-run
- [x] Landed in 0f7502e as `darkfactory/docker/source_dirs_preflight.py`. Hard-stops the run on default-`src`-plus-monorepo-markers or missing `source_dirs` paths. `lib/` intentionally excluded from the marker set (see commit message).

### 4.5.2 Monorepo auto-detection in hints
- [x] Satisfied by 0f7502e in the preflight itself — it auto-detects `apps/*/src`, `packages/*/src`, and `cmd/` to build the actionable error message. `EnvironmentHints` was intentionally NOT extended; the candidates live in the preflight because that is the only consumer and we avoid coupling the Architect hint contract to mount-builder concerns.

### 4.5.3 Contract re-resolution at step boundaries
- [x] Landed in e3eb38a. Mount config is rebuilt from the live PipelineConfig at every container launch; step_begin refreshes `source_dirs` from `environment.toml` before mounts are computed.

### 4.5.4 Defense-in-depth audit
- [x] Landed in 04baa02 as `darkfactory/audit/source_leak_scan.py`. Post-agent path-echo scan emits `BARRIER_PATH_ECHO_DETECTED` (renamed from the plan's `BARRIER_LEAK_SUSPECTED` — the commit message explains why: the event name should be honest about catching verbatim echoes, not semantic leaks).

**Phase 4.5 exit criteria:**
- [x] Preflight + mount rebuild + post-agent scanner are all in place. End-to-end validation on budget-brain-v2 is tracked implicitly via the successful Phase 3 exit-gate run that motivated this phase.

---

## Phase 5 — Shipped-scenarios as regression suite

**Goal:** Every successful ship contributes to a permanent regression suite that runs on subsequent specs.

### 5.1 Archive on ship
- [ ] Post-ship handler in `orchestrator.py`: copy `.dark-factory/scenarios/<spec>/*` → `.dark-factory/shipped-scenarios/<spec_id>/`.
- [ ] Tag each scenario file with header comments: `# spec_origin: <spec_id>` AND `# spec_type: <type>`.
- [ ] Append `ShippedSpec` row to `.dark-factory/plan/shipped.jsonl` with `env_hash_at_ship`.

### 5.2 Scenario runner consumes archive
- [x] `darkfactory/scenarios/runner.py`: when `spec_type ∈ {feature, bugfix}`, scenario set = union of (this spec's new scenarios) + (all shipped-scenarios). Includes shell-quoted multi-dir expansion for `{SCENARIOS_DIR}`.
- [x] Bootstrap: archive is empty / not run. Refactor: same.
- **Scope note:** this only affects `dark-factory scenarios` CLI and the Python regression caller. The pipeline iteration-loop blind-scenario gate runs through `quality-gate.sh` in Docker and is wired in 5.4 alongside the new/regression report split.

### 5.3 Budget + ordering + deferred surfaced in report
- [x] Wall-clock budget per regression run, configurable via `regression.budget_seconds` (default 600s) and `regression.avg_spec_seconds` (default 60s). Runtime kill-switch enforces the budget even if avg is wrong.
- [x] Deterministic ordering: newest-shipped first, then oldest tail. sha256(spec_id) tiebreaker.
- [x] Truncation reproducible: emitted to events.jsonl as `REGRESSION_SELECTION`. Per-spec durations also emitted for future avg calibration.
- [x] Selection-time deferred + runtime-killswitch deferred unioned in the returned deferred list.
- [ ] Gate report adds `regression: {ran: N, deferred: M, deferred_specs: [list]}` — lands in 5.4.
- [ ] Reviewer/Oracle prompt updates — lands in 5.4.

### 5.4 Gate distinguishes regression from new-scenario failures
- [x] CLI `dark-factory regression` exits 2 on regression failure (vs. exit 1 for new-scenario failure in `dark-factory scenarios`). Distinct exit codes are observable today.
- [x] `agents/reviewer.md` and `agents/oracle.md` updated **conditionally**: the new/regression severity guidance is gated on the gate report actually containing a regression section. Until the deferred bash attribution lands, the agents fall through to the existing single-aggregate path and do not hallucinate split data.
- [ ] **Deferred:** Docker-side `quality-gate.sh` does not yet attribute pass/fail per `spec_origin` header — the new/regression split lives at the Python layer and the orchestrator iteration loop still gates on a single aggregate pass rate. Tracked as a Phase 5.4-followup; needs scenario-result attribution by parsing the spec_origin headers Phase 5.1 writes.

**Phase 5 exit criteria:**
- [x] **Validation gate closed via hermetic two-ship integration test** at `tests/integration/test_phase5_archive_to_union.py`. Drives `registry_register` → `tag_archived_scenarios` → `capture_env_hash` → `record_shipped_spec` for two consecutive ships (bootstrap then feature), then asserts: archive dirs exist with header tags, `shipped.jsonl` has two rows in order with stable env_hash across re-read, append-not-overwrite preserved, and `run_scenarios(spec_type="feature")` builds a test command that unions the new dir with the bootstrap archive dir while excluding the current spec's own archive copy. Also asserts bootstrap-spec runs do NOT union an existing archive.
- [ ] **Opportunistic post-hoc validation:** the next time *any* spec ships through the real pipeline on a real project, verify the same four observables hold end-to-end with Docker mounts, real environment loading, and barrier masking. Not blocking — bonus.

---

## Phase 6 — Plan runner

**Goal:** Thin outer loop that threads state between specs. Single-spec runs remain primary.

### 6.1 Command + plan format
- [x] Landed in 7109320 alongside 6.2. `dark-factory plan run <plan>` reads the plan file and produces ordered specs.

### 6.2 Outer loop
- [x] Landed in 7109320. Sequential plan runner in `darkfactory/plan/runner.py`.

### 6.3 Union smoke probe between specs
- [x] 6.3a (fold_to_hash time-travel replay) landed in 60efd86; 6.3b (union smoke probe wired into sequential runner) landed in 716dd81. Includes stranded-status handling and halt-on-prior-spec-regression.

### 6.4 Single-spec run inheritance
- [x] Landed in 4245d41. `depends_on` soft check with `--force` escape. Phase 6.4 follow-up fix (the earlier ID-form regression) landed during this session's Phase 2 audit work.

### 6.5 Resume + rollback
- [x] Plan resume landed in eb2fc9c; plan rollback with two-phase commit landed in 6f484bc.

**Phase 6 exit criteria:**
- [x] All four observables covered by the commits above. Follow-up deployment gaps captured in 12e113f as Phase 7 items.

---

## Phase 7 — Surgical strip of dead spec/pipeline modules

**Goal:** Once Phases 0–6 are stable, finish the cleanup that was deferred.

- [ ] Delete `darkfactory/spec/shape_tokens.py`.
- [ ] Delete `darkfactory/spec/precheck_runs.py`.
- [ ] Delete `darkfactory/spec/validator_output.py`.
- [ ] Audit `darkfactory/spec/precheck.py`:
  - [ ] Keep `_detect_*` helpers (`_detect_shape_claims`, `_detect_acceptance_criteria_coverage`, `_detect_placeholders`).
  - [ ] Delete `PrecheckResult.passed` field.
  - [ ] Delete `run_precheck`'s `passed` consumers.
  - [ ] New shape: `run_precheck(spec) -> PrecheckAdvisory{findings: list[Finding]}`.
- [ ] Confirm `coverage.py` uses anchor manifest as a *data structure* only, not a *gate*. Strip any remaining gate semantics.

**Deferred (do not strip yet):**
- [ ] Vacuous-reason retry — strip after 10 clean pipelines confirm it never fires.
- [ ] DAG plan support — revisit after 10 clean sequential plan runs.

**Phase 7 exit criteria:**
- [ ] `darkfactory/spec/` shrinks substantially (~50% LOC reduction expected from ~3700 lines).
- [ ] All tests still pass.

---

## Cross-cutting policies (apply throughout)

- [ ] Information barriers untouched. No phase modifies tmpfs masking, `--disallowedTools`, or barrier audit code.
- [ ] Amendment schema strictly rejects `test.*`, `scenario.*`, `oracle.*` paths. Env contract cannot become a leak channel.
- [ ] Documentation updated at the end of each phase, not the end of the project.
- [ ] Each phase ships with unit tests for new modules + an integration test that re-runs `2026-03-28-api-bootstrap` end-to-end. The integration test is the regression bar.

---

## Verification milestones (the metric that matters)

After each phase, record the result of `dark-factory run 2026-03-28-api-bootstrap` in this table:

| Phase | Date | Reached implementer? | Reached scenarios? | Shipped? | Notes |
|---|---|---|---|---|---|
| 0 |  |  |  |  |  |
| 1 |  |  |  |  |  |
| 2 |  |  |  |  |  |
| 3 |  |  |  |  |  |
| 4 |  |  |  |  |  |
| 5 |  |  |  |  |  |
| 6 |  |  |  |  |  |
| 7 |  |  |  |  |  |

**Target:** by end of Phase 3, the spec reaches blind scenarios. By end of Phase 6, a 3-spec plan ships end-to-end.
