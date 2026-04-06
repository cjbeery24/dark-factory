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
- [ ] Locate `run_precheck` raise boundary in `darkfactory/spec/precheck.py`. Convert from `raise DarkFactoryError` to logged warning + return.
- [ ] `darkfactory/pipeline/orchestrator.py:282` wrapper: change `if not result.passed: raise …` to record-and-continue. Do NOT touch lines 279-295 (anchor manifest persistence).
- [ ] Locate CLI validate handler (likely under `darkfactory/cli/`). Default exit 0 on advisory-only findings, exit 1 on fatal (markdown won't parse, missing required frontmatter).
- [ ] Add `--strict` flag to validate handler that treats advisory as fatal. Pipeline never invokes `--strict`.

### 0.2 Dual call site for advisory JSON
- [ ] Create `darkfactory/spec/state.py:ensure_state_dir(spec_id, base_dir) -> Path`. Idempotent creation of `.dark-factory/state/<spec>/`.
- [ ] Both `orchestrator.py:282` AND CLI validate handler call `ensure_state_dir` before invoking precheck.
- [ ] Both call sites write `.dark-factory/state/<spec>/precheck_advisory.json` with the same structure: `{fatal: [], advisory: [], ambiguity_warnings: []}`.
- [ ] Update `agents/architect.md`: one paragraph noting `precheck_advisory.json` is mounted at `/work/.dark-factory/state/<spec>/precheck_advisory.json`.
- [ ] `darkfactory/pipeline/agents.py`: add `:ro` mount of `precheck_advisory.json` (and the existing anchor manifest JSON) into the Architect container.

**Phase 0 exit criteria:**
- [ ] All three previously-blocked archetypes (runs 18:58, 19:17, 19:56) reach the implementer phase on a clean re-run.
- [ ] Integration test asserts: after running `dark-factory validate <spec>` AND `dark-factory run <spec>` against the same spec+manifest, `precheck_advisory.json` exists in both cases AND is byte-identical.

### 0.3 Verification gate (BLOCKING — do not proceed without passing)
- [ ] Reset `budget-brain-v2`. Run `dark-factory run 2026-03-28-api-bootstrap`.
- [ ] Pipeline reaches the implementer phase (does not abort at pre-check).
- [ ] Inspect smoke failure logs from inside the container. Determine: did the implementer have the *information* it needed to invoke pnpm, or did the container *lack the binary / mount / permission*?
- [ ] **Pass condition:** logs show "wrong command" or "wrong arguments" failure mode. → proceed to Phase 1.
- [ ] **Fail condition:** logs show "command not found," "permission denied," or "mount missing." → STOP. The fix is in `docker/` (image contents, mount table, network policy), not in the orchestrator. Reassess the entire plan.
- [ ] Document the verification result in `docs/migration/phase-0-verification.md` with explicit logs.

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
- [ ] `darkfactory/pipeline/quality_gate.py` smoke runner: capture container stdout/stderr to `.dark-factory/reports/<spec>/<run_id>/smoke-iter<N>.log` (stdout) and `.smoke-iter<N>.err` (stderr) on every invocation, regardless of exit code.
- [ ] Symlink `latest-smoke.log` to most recent.
- [ ] Include resolved `server_setup_cmd` at top of log so failures are self-documenting.
- [ ] Verify report directory is created BEFORE the smoke step runs (regression noted in run 20:34).

### 1.2 Cap implementer re-entry at 1
- [ ] `darkfactory/pipeline/iteration.py`: when implementer hits 200-turn cap, do NOT re-launch from scratch. End the iteration, increment the real attempt counter, feed actual output to next iteration.
- [ ] Fix the `attempt:1` logging defect: every `AGENT_START` event reflects true attempt number.
- [ ] Add comment: "if 400 turns can't make progress, the next 200 won't either; iterate at the iteration boundary instead."

### 1.3 Audit `server_setup_cmd` readers (BLOCKER for Phase 2.3)
- [ ] Grep `darkfactory/` for every reader of `server_setup_cmd`, `test_cmd`, `build_cmd`, `lint_cmd`, `typecheck_cmd`, `server_health`, `config.runtime.*`.
- [ ] Document exhaustive list as `docs/migration/server_setup_cmd-readers.md`.
- [ ] Phase 2.3 deletes this file when done. Phase 2.6 enforcement test reads it.

### 1.4 Strip trajectory + plateau classifier
- [ ] Delete classifier modules (locate under `darkfactory/pipeline/`).
- [ ] Remove callsites in `iteration.py`.
- [ ] Remove reads from oracle/reviewer prompts.
- [ ] Remove `ANOMALY_DETECTED` and `TRAJECTORY_CLASSIFIED` event types from any consumers.
- [ ] Note in `CLAUDE.md`: stripped pending clean baseline runs.

### 1.5 Strip mutation testing + token placeholders
- [ ] Remove `MUTATION_CMD` paths in `quality_gate.py`.
- [ ] Remove gate report mutation row.
- [ ] Remove `MUTATION_CMD` config field from schema.
- [ ] Remove `est_input_tokens` / `est_output_tokens` placeholder fields. Either wire properly or delete — for now, delete.

**Note:** `0e0a0be` (`fresh=True` bandaid) revert is moved to Phase 2.6 — reverting before `load_environment` exists reintroduces the staleness bug Phase 0's verification depends on.

**Phase 1 exit criteria:**
- [ ] Smoke logs persist on both outcomes.
- [ ] A run that hits the 200-turn cap once shows `attempt:2` next, not `attempt:1` again.
- [ ] Anomaly detector and trajectory classifier are gone from the codebase.
- [ ] `server_setup_cmd` reader audit document committed.

---

## Phase 2 — Environment contract plumbing

**Goal:** Introduce `environment.toml` as a materialized view over an append-only delta log. Migrate readers. Add backcompat shim. **No agent writes deltas yet** — that's Phase 3.

### 2.1 Schema + canonical_dump
- [ ] Create `darkfactory/architecture/environment.py` with `EnvironmentContract` dataclass:
  ```python
  runtime: Runtime          # language, package_manager, language_version
  commands: Commands        # install, server_start, server_health, build, lint, typecheck, test
  server: ServerConfig      # port, health_path, boot_timeout_s
  layout: Layout            # source_dirs, test_dirs
  ```
- [ ] Strict allowlist `ALLOWED_PATHS` of dotted paths permitted in amendments. Reject anything under `test.*`, `scenario.*`, `oracle.*`.
- [ ] `BOOTSTRAP_ONLY_PATHS = {"runtime.language", "layout.source_dirs"}`.
- [ ] `validate_environment(env, spec_type) -> list[Error]` enforcing allowlist + bootstrap-only paths.
- [ ] Implement `canonical_dump(contract) -> bytes` as a **hand-rolled deterministic TOML writer**, NOT a wrapper around `tomli_w` / `tomlkit`. Rules:
  - Lexicographically-sorted keys at every level
  - ASCII-only output
  - Double-quoted strings with `\\` and `\"` escapes only
  - Integers as decimal, booleans as lowercase
  - Lists as `[ "a", "b" ]` with one space after each comma
  - No inline tables, no comments
  - LF line endings, trailing newline
- [ ] Golden-file test `tests/test_canonical_dump.py` against `tests/fixtures/environment_canonical.toml`. Byte-equality assertion.

### 2.2 Delta log + fold
- [ ] Filesystem layout under `.dark-factory/architecture/`:
  ```
  environment.toml                # materialized view (regenerated, never hand-edited)
  environment.toml.lock           # hash of last successful fold
  environment.deltas/
    0001-<spec>.toml
    0002-<spec>.toml
    ...
  ```
- [ ] Delta format: `schema_version`, `spec_id`, `parent_hash` (sha256 of `canonical_dump` at Architect read time), `applied_at`, `[[ops]]` blocks with `op` (set/modify/remove), `path`, `value`.
- [ ] `fold_deltas(deltas_dir) -> EnvironmentContract` — deterministic, total. Lexicographic order.
- [ ] `materialize(deltas_dir, out_path)` — fold + write `environment.toml`.
- [ ] `EnvironmentConflict` raised on parent_hash mismatch or forbidden-path write.
- [ ] Unit tests: fold determinism, conflict detection, forbidden path rejection.

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
- [ ] Re-run `2026-03-28-api-bootstrap` end-to-end. Architect produces delta `0001-…`. Fold succeeds. `environment.toml` materializes with pnpm commands.
- [ ] Implementer reads `environment.toml` from its mount, uses correct package manager.
- [ ] **Spec reaches blind scenarios for the first time in this phase.**

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

## Phase 5 — Shipped-scenarios as regression suite

**Goal:** Every successful ship contributes to a permanent regression suite that runs on subsequent specs.

### 5.1 Archive on ship
- [ ] Post-ship handler in `orchestrator.py`: copy `.dark-factory/scenarios/<spec>/*` → `.dark-factory/shipped-scenarios/<spec_id>/`.
- [ ] Tag each scenario file with header comments: `# spec_origin: <spec_id>` AND `# spec_type: <type>`.
- [ ] Append `ShippedSpec` row to `.dark-factory/plan/shipped.jsonl` with `env_hash_at_ship`.

### 5.2 Scenario runner consumes archive
- [ ] `darkfactory/scenarios/runner.py`: when `spec_type ∈ {feature, bugfix}`, scenario set = union of (this spec's new scenarios) + (all shipped-scenarios).
- [ ] Bootstrap: archive is empty / not run.

### 5.3 Budget + ordering + deferred surfaced in report
- [ ] Wall-clock budget per regression run, configurable in `config.toml` under `[pipeline.regression]`. Default: 10 minutes.
- [ ] Deterministic ordering: newest-shipped first within budget, then oldest. Hash-stable tiebreaker.
- [ ] Truncation reproducible: same input set + same budget = same selected subset. Logged to `events.jsonl`.
- [ ] Gate report adds `regression: {ran: N, deferred: M, deferred_specs: [list]}`.
- [ ] Reviewer and Oracle prompts updated to read both `new` and `regression` results AND the deferred count.

### 5.4 Gate distinguishes regression from new-scenario failures
- [ ] Scenario results report `{new: {pass, fail}, regression: {pass, fail, deferred}}`.
- [ ] Distinct exit codes: regression failure more severe than new-scenario failure.
- [ ] `agents/reviewer.md` and `agents/oracle.md` updated to consume the split.

**Phase 5 exit criteria:**
- [ ] Two-spec sequence: bootstrap ships → scenarios land in shipped-scenarios → feature spec runs and includes the bootstrap regression scenarios in its run → feature ships → its scenarios join the archive.

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
