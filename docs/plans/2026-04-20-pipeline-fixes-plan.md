# Pipeline Fixes Plan — 2026-04-20 (revised after neo review)

**Context.** Reproducibility run `dc53cf0d` re-shipped `2026-03-28-database-foundation` via SHIP_WITH_GAPS at 82% pass rate, confirming the pipeline is robust to LLM variance but also surfacing four structural issues:

1. Substrate Verification fails on `substrate-psycopg-missing` — **the Postgres substrate adapter imports psycopg inside the peer verifier container `dark-factory-python`, and `psycopg` is simply not in `docker/lockfiles/python-tools/requirements.in`.** Neo's review corrected the original framing: this is a missing-dep, not a toolchain-dispatch problem. The adapter is database-substrate-specific, not project-language-specific, by design (comment at `substrate_phase.py:47-51`).
2. Import Contract gate emits false FAILs because the hand-rolled YAML parser in `render_import_contract_phase.py` can't read block-style lists.
3. SHIP_WITH_GAPS ships tenant-isolation defects (EC-4 fail-closed RLS) because the promotion mechanism has no author-driven veto.
4. Test-engineer generates scenarios that shell out to `pnpm db:migrate` with broken cwd assumptions; migrations don't belong in scenarios.

## Dependency graph

```
Phase A (pyyaml)          ──┐
Phase B (psycopg pin)     ──┤  parallel; share no files
Phase D (blocking ACs)    ──┘
                          
Phase C (migrations into substrate) depends on Phase B landing.

Phase E (end-to-end validation) runs after all four phases commit.
```

Neo's review corrected a false A→B dependency in the prior draft. A, B, D are independent; C depends on B.

---

## Phase A — Replace hand-rolled YAML with `py3-yaml`

**Problem.** `scripts/lib/render_import_contract_phase.py` re-implements YAML via `_parse_entry`, `_parse_inline_list`, `_strip_quotes`, and a line-based indent machine (~130 lines). `_parse_entry:226` reads `exports:` inline only, so block-style lists silently parse as `[]`, and every well-formed manifest fails the gate.

**Docker approach.** `dark-factory-base` uses apk pattern already. Install `py3-yaml` via apk (matches existing apk pinning in `versions.env`), not layer-on-dark-factory-python — the two images have different roles (base is the gate; python is the peer verifier).

**Change set.**

| File | Change |
|---|---|
| `docker/versions.env` | Add `PY3_YAML_VERSION` pin (lookup: `docker run --rm $NODE_IMAGE sh -c 'apk update -q && apk search -x py3-yaml'`) |
| `docker/Dockerfile.base` | Add `py3-yaml=${PY3_YAML_VERSION}` to `apk add` |
| `docker/build-images.sh` | Add `PY3_YAML_VERSION` to `_required_vars` |
| `scripts/lib/render_import_contract_phase.py` | Replace `_parse_test_integration` + helpers with `yaml.safe_load(text).get("test_integration", {})`. Keep the `EntryPoint` + `TestIntegration` dataclasses. Delete `_parse_inline_list`, `_strip_quotes`, `_parse_entry`, indent-tracker. |
| `tests/unit/test_import_contract_phase.py` | Rewrite helper-specific cases as `yaml.safe_load` regressions. Add new case: block-style manifest with 4 exports → parsed correctly. |

**Acceptance.**
- `docker run --rm --entrypoint python3 dark-factory-base -c "import yaml; print(yaml.__version__)"` succeeds.
- Import Contract gate run against `dc53cf0d` iter-2 manifest produces **PASS** (all 6 entry points have their 1-4 exports resolved).
- No new test failures in `tests/unit/test_import_contract_phase.py`.
- BARRIER_VIOLATION event count unchanged vs `dc53cf0d` baseline.

**Risk.** Trivial with `safe_load` discipline. Image grows ~1 MB.

---

## Phase B — Add `psycopg` to the Python verifier image

**Problem.** `darkfactory/adapters/postgres/adapter.py:419-430` imports `psycopg` inside the peer verifier and emits `substrate-psycopg-missing` when absent. `_VERIFIER_IMAGE = "dark-factory-python"` (substrate_phase.py:51) so the fix is entirely in that image's Python deps.

**Change set.**

| File | Change |
|---|---|
| `docker/lockfiles/python-tools/requirements.in` | Add `psycopg[binary]==X` (pin via latest stable — `pip index versions psycopg`) |
| `docker/lockfiles/python-tools/requirements.txt` | Regenerate via `bash docker/regenerate-lockfiles.sh`. **Note:** `Dockerfile.python:12` uses `pip install --no-deps`, so the compiled lockfile must carry both `psycopg==X` and `psycopg-binary==X` as separately-pinned lines. Verify after regen. |
| `docker/Dockerfile.python` | No change expected (lockfile install already in Dockerfile) — verify by re-read |

**Acceptance.**
- `docker run --rm --entrypoint python3 dark-factory-python -c "import psycopg; print(psycopg.__version__)"` succeeds.
- Substrate Verification phase on a Postgres-adapter spec produces real GUC/RLS findings, not `substrate-psycopg-missing`.
- Python-only unit tests in `tests/unit/test_postgres_substrate_rules.py` continue to pass without psycopg (they use mocks and the import-in-try-except path).

**Risk.** `psycopg[binary]` bundles a libpq statically; image grows ~3 MB. Benign.

---

## Phase C — Move migrations to Substrate Verification

**Depends on Phase B** landing (substrate phase must be functional before we rely on it more).

**Problem.** Run 2's test-engineer produced AC-3 and AC-4 scenarios invoking `execSync('cd apps/api && pnpm db:migrate')` from a cwd without `apps/api`. Two "impl bugs" were scenario bugs. Migrations are substrate-setup, not scenario concerns.

**Change set.**

| File | Change |
|---|---|
| `darkfactory/adapters/postgres/adapter.py` (or adapter base) | Expose `migration_command` + `migration_cwd` as adapter config. Default for Node+Postgres: `pnpm db:migrate` from repo-root or `apps/api/`, chosen by layout detection. |
| `darkfactory/pipeline/substrate_phase.py` | Substrate Verification runs migrations **before** the probe. New envelope field: `migrations_ran: int`. |
| `darkfactory/pipeline/substrate_verifier_entry.py` | Execute the adapter-declared migration_command with cwd, capture stdout/stderr, fail the envelope if non-zero exit. |
| `agents/test-engineer.md` | Add rule: "scenarios assume migrations have run; never invoke `db:migrate`, `db:generate`, or other schema-changing commands." |
| `scripts/lib/scenario_lint.py` (new) | Pre-gate scenario lint: greps generated `.test.ts`/`.test.js` for `cd .* && ` inside `execSync`/`spawnSync` and fails with a clear pointer. |
| `scripts/quality-gate.sh` | Invoke `scenario_lint.py` between scenario-gen and blind-scenario execution. Fail the gate on lint errors. |

**Not in scope.** `fixture_helpers[]` architect manifest field — speculative until a real project needs it. Leave for when an actual use case surfaces.

**Acceptance.**
- Re-running `2026-03-28-database-foundation` no longer produces AC-3/AC-4-style cwd-crash scenarios.
- Substrate findings envelope for a Node+Postgres spec includes `migrations_ran >= 1`.
- Envelope back-compat: no existing envelope field renamed or removed. Existing consumers (`render_substrate_phase.py`, any test fixtures) tolerate the new `migrations_ran` field. Verify by re-running `test_render_substrate_phase.py` and `test_substrate_phase.py` unchanged.
- Scenario lint fires on any `cd X && ` pattern in generated scenarios; PASS on clean runs.

**Risk.** Projects with legitimate per-test DB fixtures (rare) would be blocked. Accept this trade; ship the escape hatch only when there's a concrete request.

---

## Phase D — Blocking ACs end-to-end

**Independent of A/B/C.** Can land first if we want.

**Problem.** `should_ship_with_gaps` (`darkfactory/pipeline/iteration.py:96-116`) promotes NO-SHIP → SHIP on three scalars (`stable_failure_ratio`, `pass_rate`, `iteration`). No author-driven veto. Both database-foundation runs shipped broken fail-closed RLS (EC-4).

**Data-flow plumbing (addresses neo's blocker B3).**

Current state (verified via grep):
- `SpecRun` (`darkfactory/models/pipeline.py:103`) has `stable_failure_names: set[str]` and `ship_with_gaps: bool` but NO `failing_ac_ids` and NO `ac_blocking`.
- `IterationState` has no AC attribution.

Required additions:

| Symbol | Field | Purpose |
|---|---|---|
| `SpecRun` | `ac_blocking: dict[str, bool]` (default `{}`) | Populated once by the spec parser, consumed by ship policy. |
| `IterationState` | `failing_ac_ids: set[str]` (default `set()`) | Populated by `run_quality_gate` from scenario AC attribution; empty on gate skip. |
| `SpecRun.spec_hash` | No change needed | Already `sha256_file(spec_file)` at `orchestrator.py:320`; any text change (new column, new section) invalidates automatically. |

**Change set.**

| File | Change |
|---|---|
| `templates/*.md` (8 files) | Add `Blocking` column to AC table (after `Vehicle`). Add `## Non-Negotiable Acceptance Criteria` section after Problem Statement. |
| `darkfactory/spec/spec_model.py` | Extend AC model with `blocking: bool`. |
| `darkfactory/spec/generator.py` | Parse `Blocking` column from AC table. Parse `## Non-Negotiable Acceptance Criteria` section. No `spec_hash` change needed — `spec_hash = sha256_file(spec_file)` (`orchestrator.py:320`) already invalidates on any text change. |
| `darkfactory/spec/validator_output.py` | Surface `blocking_ac_ids: list[str]` in validation status artifact. |
| `darkfactory/spec/precheck.py` or validator | New structural rule: `## Non-Negotiable Acceptance Criteria` section must exist. If non-empty, every listed AC ID must resolve to a row in the AC table. If empty, justification line must be present. Blocking column must be consistent with section listing. |
| `darkfactory/models/pipeline.py` | Add `ac_blocking` to `SpecRun`, `failing_ac_ids` to `IterationState`. |
| `darkfactory/pipeline/orchestrator.py` | Populate `SpecRun.ac_blocking` from parsed spec at pipeline start. |
| `darkfactory/pipeline/quality_gate.py` | Populate `IterationState.failing_ac_ids` from scenario AC attribution. |
| `darkfactory/pipeline/iteration.py:96` `should_ship_with_gaps` | Add clause: `and not any(spec_run.ac_blocking.get(ac_id, False) for ac_id in last_iter.failing_ac_ids)` |
| `~/.claude/skills/df-write-spec/SKILL.md` | Insert "Blocking Pass" between Precision Gap and final validation (workflow detailed in prior message). |
| `scripts/migrate_specs_add_blocking.py` (new, one-shot) | Walk any spec dir, add empty `## Non-Negotiable Acceptance Criteria` section + empty `Blocking` column where missing. Prompt human to fill in. |
| Tests | Unit: parser extracts Blocking column; validator hard-fails on missing section / unresolved AC IDs / empty-without-justification; ship-policy clause (mock failing blocking AC → no ship, mock failing non-blocking → promotion still possible). Integration: spec with EC-4 tagged blocking → SPEC_ESCALATED instead of SPEC_SHIPPED when EC-4 fails. |

**Migration.** 5 specs exist in dark-factory's own `.dark-factory/specs/` (2026-03-11-*.md); plus unknown count across target projects. Migration script is tractable at this scale. Do not ship a "grace period" — promote the validator rule to hard error at phase commit.

**Acceptance.**
- Unit tests above all pass.
- Validator run on a pre-migration spec produces a structured error pointing to the missing section.
- Validator run after migration script + author fill-in passes.
- End-to-end: spec with `EC-4` in the blocking list, EC-4 failing, `should_ship_with_gaps` otherwise eligible → pipeline produces SPEC_ESCALATED (or SPEC_NOSHIP), not SPEC_SHIPPED.
- End-to-end: same spec, EC-4 resolved → ships normally.

**Risk.**
- Every existing spec becomes invalid at phase commit. Migration script mitigates (5 specs in-repo; target projects get the script too).
- Author under-tags. Mitigated by the `/df-write-spec` Blocking Pass being the primary line of defense. Validator catches structural errors; the Pass catches semantic ones.
- Every AC blocks → SHIP_WITH_GAPS never fires. `/df-write-spec` pushes back on this case.

---

## Phase E — End-to-end validation run

After A, B, C, D commit, run the full pipeline against `budget-brain-v2` on `2026-03-28-database-foundation`:
1. Migrate the spec (add Non-Negotiable ACs section with EC-3, EC-4).
2. Run `dark-factory run 2026-03-28-database-foundation`.
3. **Phase E success criterion:** SPEC_ESCALATED (or SPEC_NOSHIP) when EC-4 still fails. This proves the blocking-AC mechanism works end-to-end — `should_ship_with_gaps` refuses to ship a spec with a failing blocking AC.
4. **Bonus, not a gate:** if the implementer produces a correct fail-closed RLS on this run, Phase E also confirms Substrate Verification passes, Import Contract passes, and no `cd X && ` scenario crashes occur. Not required for Phase E success.
5. Strengthening EC-4 handling in the implementer preamble is a **separate follow-up work item**, not part of Phase E. Tracked in the project punch list, not this plan.

Commit the Phase E run report under `docs/pipeline-run-reports/`.

---

## Report correction

The committed report `f458980` (`docs/pipeline-run-reports/2026-04-20-17-22-budget-brain-v2-2026-03-28-database-foundation-reproducibility.md`) contains a wrong narrative in the "Change Validation" section: it claims the Import Contract phase "correctly detected" a real defect. It didn't — it false-FAILed because of the parser bug Phase A fixes.

**Correction commit, lands between Phase A and Phase B:** edit the relevant paragraphs in-place; add a short "Correction 2026-04-20" note at the top citing Phase A's commit.

---

## Rollout / validation

Each phase's commit must:
1. Rebuild affected docker images.
2. Pass all unit tests that touch changed files.
3. Pass a lightweight smoke test where applicable (e.g., Phase A: invoke the gate phase against the archived `dc53cf0d` manifest; Phase B: run the verifier against a live Postgres sidecar; Phase D: invoke the validator against a migrated spec).

Phase E is the only phase that requires a full pipeline run.

## Explicit non-goals

- Preamble invariants mechanism (the long-term redesign for "add worked example" doom loop) — tracked separately, not in this plan.
- BARRIER_VIOLATION detector redesign — tracked as advisory-only hygiene work.
- Go/Rust substrate verifiers — speculative until adapters exist.
