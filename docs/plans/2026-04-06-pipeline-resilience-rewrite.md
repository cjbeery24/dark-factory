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
- [ ] `load_environment(project_dir) -> EnvironmentContract` — single read path. Caches per process.
- [ ] Rename `patch_config_from_runtime` (orchestrator.py:957) → `refresh_config_from_environment(config, env) -> Config`. Relocate from architect step to a step-boundary helper.
- [ ] `step_begin(spec, config) -> Config`: calls `load_environment` + `refresh_config_from_environment`. Records `(spec_id, step_id)` in per-process registry. **Resets prior entry for the same `(spec_id, step_id)` first** so cycle 2 doesn't silently pass cycle 1's record.
- [ ] Wire `step_begin` as the first statement of all eight call sites:
  - [ ] Architect step
  - [ ] Scenario Architect step
  - [ ] Implementer step
  - [ ] Quality Gate step
  - [ ] Reviewer step
  - [ ] Oracle step
  - [ ] Evaluator step (enterprise)
  - [ ] Iteration loop per-cycle re-entry in `iteration.py`
- [ ] Migrate every reader catalogued in 1.3 (`config.server_setup_cmd`, `config.test_cmd`, etc.). Field-level reads stay unchanged — Config is just freshened at step boundary.
- [ ] Delete `docs/migration/server_setup_cmd-readers.md` once migration verified.

### 2.4 Backcompat shim
- [ ] Create `darkfactory/architecture/backcompat.py`.
- [ ] If `environment.toml` doesn't exist when pipeline starts AND `config.toml` has runtime keys: generate synthetic `0000-bootstrap-from-config-toml.toml` delta. Apply. Materialize.
- [ ] Mark `config.toml` runtime keys deprecated (CLI warning).
- [ ] After `environment.toml` exists, `config.toml` runtime keys become a HARD ERROR. One-shot migration, not permanent fallback.

### 2.5 Plan-state schema
- [ ] Create `darkfactory/plan/state.py` with `ShippedSpec` dataclass:
  ```python
  spec_id: str
  spec_type: Literal["bootstrap","feature","bugfix"]
  spec_origin: str
  shipped_at: datetime
  env_hash_at_ship: str        # hash of materialized env.toml at ship time
  delta_filename: str | None
  scenario_archive_path: str
  ```
- [ ] Persisted at `.dark-factory/plan/shipped.jsonl`. Append-only.

### 2.6 Revert `0e0a0be` (`fresh=True` bandaid)
- [ ] `git revert 0e0a0be` or equivalent surgical removal. Now safe because `load_environment` provides a fresh read path that doesn't depend on the bandaid.

**Phase 2 exit criteria:**
- [ ] Pipeline runs on a project with only `config.toml` and produces a synthetic delta + materialized `environment.toml`.
- [ ] All quality_gate readers use `load_environment`.
- [ ] Unit tests: fold determinism, conflict detection, forbidden-path rejection, canonical_dump byte-equality.
- [ ] **Step-begin enforcement test** `tests/test_step_begin_enforcement.py`:
  - [ ] Static grep: every read of `config.server_setup_cmd` (et al, generated from 1.3 audit) appears in a function whose first statement is `step_begin(...)` OR is preceded by one in the same function.
  - [ ] Dynamic registry: `Config.__getattribute__` wrapper for environment-sourced fields raises `StepBegunNotCalled` if registry miss.
  - [ ] **Two-cycle test:** runs two iteration cycles in sequence, asserts cycle 1's registry entry is cleared before cycle 2's `step_begin` records.

---

## Phase 3 — Architect emits amendments

**Goal:** Wire the Architect to actually produce environment deltas. Repurpose `manifest.runtime` as the amendment payload itself.

### 3.1 Architect prompt rewrite
- [ ] `agents/architect.md`: add "Environment contract" section. Bootstrap = full runtime block. Feature/bugfix = partial or empty runtime block.
- [ ] `darkfactory/pipeline/orchestrator.py:879-911` (Architect prompt construction): rewrite the runtime injection block to mount and reference `environment.toml` (if exists) and `precheck_advisory.json`. Same `runtime:` shape in the manifest output, semantics keyed on spec_type.
- [ ] **`spec_type=None` policy:** when frontmatter lacks `spec_type` (transitional state before Phase 4.0), treat as `feature` — strictest allowlist (rejects writes to `runtime.language` and `layout.source_dirs`). Log WARN: `"spec_type missing from frontmatter; defaulting to 'feature' until migration. Run dark-factory specs migrate-frontmatter to backfill."`

### 3.2 Architect mount table
- [ ] `darkfactory/pipeline/agents.py`: Architect container `:ro` mount of:
  - [ ] `.dark-factory/architecture/environment.toml`
  - [ ] `.dark-factory/architecture/environment.deltas/`
  - [ ] anchor manifest JSON
  - [ ] `precheck_advisory.json`
- [ ] **No write mount** — writes happen host-side from parsed manifest.runtime.

### 3.3 Orchestrator transforms `manifest.runtime` → delta → fold → materialize
- [ ] After Architect produces output: parse `manifest.runtime`, validate via extended `validate_environment` (manifest.py:953) with strict allowlist.
- [ ] Transform validated payload into structured `[[ops]]` block.
- [ ] Write `.dark-factory/architecture/environment.deltas/NNNN-<spec>.toml`.
- [ ] Refold + materialize.
- [ ] On `EnvironmentConflict`: halt with explicit escalation.
- [ ] Branch on spec_type (default `feature` per 3.1 policy).
- [ ] Emit `ENVIRONMENT_AMENDED` event with the diff.

### 3.4 Lockfile-based hint pre-fill
- [ ] Before Architect runs, scan project for: `pnpm-lock.yaml`, `yarn.lock`, `package-lock.json`, `requirements.txt`, `Cargo.lock`, etc.
- [ ] Write detected hints to `.dark-factory/architecture/hints.json`. Mount `:ro` into Architect.
- [ ] Reduces chance Architect emits npm commands against a pnpm workspace.

### 3.5 Implementer prompt receives environment context
- [ ] Implementer prompt template: add section "Project environment: <materialized environment.toml contents>".
- [ ] Add instruction: "If the build/test/run commands in your environment do not match these, modify project files (package.json, tsconfig, etc.) to make these commands work — do not change the commands themselves."

### 3.6 Other agent mounts
- [ ] Implementer container: `:ro` mount of `environment.toml` only (not deltas).
- [ ] Scenario Architect container: `:ro` mount of `environment.toml`.
- [ ] Reviewer / Oracle: `:ro` mount of `environment.toml` and the delta produced by this spec.

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
- [ ] Implement `dark-factory specs migrate-frontmatter` command.
- [ ] Reads each spec under `.dark-factory/specs/`. Infers `spec_type` from heuristics:
  - Title contains "bootstrap" / "setup" / "scaffold" → `bootstrap`
  - Title contains "fix" / "bug" / "regression" → `bugfix`
  - Default → `feature`
- [ ] Asks human confirmation on ambiguous cases.
- [ ] Writes back updated frontmatter.

### 4.1 Frontmatter requirement
- [ ] `darkfactory/spec/spec_model.py`: add `spec_type: Literal[bootstrap,feature,bugfix]` and `environment_contract: Literal[write,amend,read]` (optional, default derived from spec_type).
- [ ] Add `depends_on: list[str] | None` for plan-level ordering hints.
- [ ] Validator hard-fails on missing or unknown `spec_type`.

### 4.2 Smoke probe / policy split
- [ ] `darkfactory/pipeline/quality_gate.py` smoke runner: always runs, always reports. New field `gate_policy: Literal[advisory, fatal]`.
- [ ] Policy resolver: `bootstrap + iter1 → advisory`, `bootstrap + iter≥2 → fatal`, `feature/bugfix all iters → fatal`.
- [ ] `darkfactory/pipeline/iteration.py`: consume `gate_policy` to decide whether smoke failure aborts iteration.

### 4.3 Coverage floor by spec_type
- [ ] `darkfactory/scenarios/coverage.py`: read `spec_type`. Apply tier:
  - `bootstrap`: 100% of FRs that touch `environment.toml`. Other FRs = 1 scenario each.
  - `feature`: current rules.
  - `bugfix`: current rules + 1 regression-reproducing scenario required.

### 4.4 Architect amendment validation by spec_type
- [ ] Bootstrap: amendment may write any allowed path.
- [ ] Feature/bugfix: amendment may not touch `runtime.language` or `layout.source_dirs`. Reject at validation time.

### 4.5 `/df-write-spec` skill update
- [ ] `skills/df-write-spec.md`: add `spec_type` as second prompt after spec name.
- [ ] Bootstrap branch: scaffold minimal FRs (server boots, health endpoint, env validation). Warn if user lists >5 FRs.
- [ ] Bugfix branch: REQUIRE a "Reproduction" section. Reference shipped-scenario entries when applicable.
- [ ] Feature branch: largely unchanged.

### 4.6 `dark-factory validate` final form
- [ ] Default exit 0 on advisory-only, exit 1 on fatal.
- [ ] `--strict` treats advisory as fatal. For human authoring + CI use only.
- [ ] Pipeline never invokes `--strict`.
- [ ] Validates `spec_type` present + valid (FATAL if not).
- [ ] Validates bugfix specs have a Reproduction section (FATAL).

**Phase 4 exit criteria:**
- [ ] Existing specs in budget-brain-v2 are tagged with `spec_type` via migration command.
- [ ] A bootstrap spec runs differently from a feature spec (smoke advisory on iter1 for former, fatal everywhere else).
- [ ] A bugfix spec with no Reproduction section is rejected.

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

- [ ] Pre-flight check in `orchestrator.py` before launching any analysis
      container: if effective `source_dirs` equals the framework default
      `["src"]` AND the project root contains `apps/`, `packages/`, `lib/`, or
      `cmd/`, refuse to launch and emit a fatal error telling the user to set
      `layout.source_dirs` explicitly. This is a hard stop, not a warning —
      silent barrier leaks are not acceptable.
- [ ] Same check: if `source_dirs` contains a path that does not exist under
      the project root, refuse. An empty or missing masked dir is a leak.

### 4.5.2 Monorepo auto-detection in hints

- [ ] `darkfactory/architecture/hints.py`: extend `EnvironmentHints` with a
      `source_dirs_candidates: list[str]` field populated from layout signals:
      - `apps/*/src` for pnpm/yarn/npm monorepos
      - `packages/*/src` similarly
      - `cmd/` for Go
      - Whatever the equivalent convention is per detected language
- [ ] The pre-flight in 4.5.1 uses these candidates to produce an actionable
      error message ("your monorepo appears to use apps/*/src — set
      `layout.source_dirs = [\"apps/api/src\", \"apps/web/src\"]` in your bootstrap spec").

### 4.5.3 Contract re-resolution at step boundaries

- [ ] `step_begin(spec_id, step_id, config, project_dir)` — already exists from
      Phase 2.3 — must re-read `environment.toml` and update `config.source_dirs`
      (and anything else the mount builder reads) before each step's container
      launch. Currently it only mutates command strings.
- [ ] `darkfactory/docker/mounts.py` mount-builder: source the masked-dirs list
      from the live PipelineConfig, not from a value cached at orchestrator
      start. Add a test that simulates an architect amendment landing mid-run
      and verifies the next agent's mount config reflects the new source_dirs.

### 4.5.4 Defense-in-depth audit

- [ ] After every analysis agent run, grep the agent's transcript/audit log
      for string matches against files under the effective `source_dirs`. Emit
      a BARRIER_LEAK_SUSPECTED event on any hit. This is defense-in-depth and
      imperfect (misses semantic leakage), but it catches the naive case of
      "agent actually read the file".

**Phase 4.5 exit criteria:**
- [ ] budget-brain-v2 scenario architect run with no `source_dirs` in
      `config.toml` refuses to launch with a clear error message naming the
      monorepo layout.
- [ ] With `layout.source_dirs` set in the bootstrap amendment, every
      downstream agent's mount config reflects the architect's value — verified
      by inspecting the actual `docker run` command in the audit log.
- [ ] No BARRIER_LEAK_SUSPECTED events on a clean run.

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
- [ ] **Validation gate (user-driven).** Two-spec sequence on a real project (budget-brain-v2): bootstrap ships → scenarios land in `.dark-factory/shipped-scenarios/<bootstrap-id>/` with spec_origin headers → feature spec runs and `dark-factory scenarios <feature>` includes the bootstrap regression scenarios → feature ships → its scenarios join the archive → `shipped.jsonl` has both rows. Iteration-loop gate integration is the deferred 5.4 followup and is **not** part of this exit criteria.

---

## Phase 6 — Plan runner

**Goal:** Thin outer loop that threads state between specs. Single-spec runs remain primary.

### 6.1 Command + plan format
- [ ] `dark-factory plan run <plan>` reads `.dark-factory/plans/<plan>.md` containing an ordered list of spec IDs.
- [ ] Plan parser produces `OrderedSpec[]`.

### 6.2 Outer loop
- [ ] Implement in `darkfactory/plan/runner.py`:
  ```python
  for spec in plan.specs:
      result = run_single_spec(spec)
      if result.shipped:
          archive_scenarios(spec)
          write_shipped_row(spec)
          refold_environment_from_deltas()
          run_union_smoke_probe()
      else:
          halt_with_escalation(spec, result)
  ```

### 6.3 Union smoke probe between specs
- [ ] Each archived scenario runs against its `env_hash_at_ship`, NOT current HEAD.
- [ ] Plan runner refolds deltas to that hash, runs prior smoke probe, then refolds forward.
- [ ] If fold succeeds at hash but the surface vanished (later delta removed it): record `stranded` status, escalate to human, no auto-pass/fail.
- [ ] If any prior spec's smoke now fails: halt with explicit escalation. Spec N silently broke spec N-3.

### 6.4 Single-spec run inheritance
- [ ] User running `dark-factory run <spec>` standalone gets same on-disk state.
- [ ] Soft check: if spec frontmatter declares `depends_on: <other-spec>` and that spec isn't in `shipped.jsonl`, warn and require `--force` to proceed.

### 6.5 Resume + rollback
- [ ] `dark-factory plan resume <plan>`: restart from first un-shipped spec.
- [ ] `dark-factory plan rollback <plan> --to <spec>`: drop all delta files after named spec, refold, mark later shipped-rows as rolled-back.

**Phase 6 exit criteria:**
- [ ] 3-spec plan (bootstrap → feature → bugfix) runs end-to-end.
- [ ] Single-spec resume of any spec in the plan also works.
- [ ] Union smoke probe catches a deliberately-introduced regression in a test fixture.
- [ ] `stranded` status fires correctly when a delta removes a surface a prior probe depended on.

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
