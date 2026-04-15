# Pipeline Modularity, Project-Hints, and Auto-Remediation — Plan

**Date:** 2026-04-14
**Status:** APPROVED v2 — neo verdict: ship-as-is. Two review cycles (v1 critique → v2 revision → v2 verification). v2 incorporates: layer-decision rubric, Sprint 0 (audit + harness + governance), C1 promoted to Sprint 1 with `remediation_outcome` event, Sprint 3 (A4) re-priced to 5-7 days with semantic-equivalence acceptance, Level 1 (Auto-safe) dropped from C5 with novel-rule cross-cutting hard rule, novelty-tracking threat model documented, Sprint 3 hard ordering constraint for `DEFAULT_RULES` migration, S0.2 sampling-variance risk note, C1 bootstrapping case for projects without `project-hints.toml`. Ready to kick off Sprint 0.
**Motivation:** Three Sprints of spec-reliability work (`docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md`) shipped real wins (A1/A2/A3/A6/B1-5/C2/E2), validated on a real spec — but in the process exposed an architectural debt: the pipeline core has been quietly absorbing language/ecosystem specifics that don't belong there.

`agents/architect.md` is 465 lines, ~80 of which are Node/TypeScript/pnpm/Drizzle specifics fused into a "general-purpose" agent prompt. `darkfactory/spec/sql_lint.py`, `library_symbols.py`, `stub_compile.py`, and `scenarios/proposal_rehearsal.py` shipped into `spec/` and `scenarios/` because that was the convenient location — not the right one. They constitute a TypeScript+Postgres adapter today, just not packaged as one. A1's consistency rule table hardcodes a Drizzle migration-discipline rule into framework code.

This is structurally incompatible with the goals (`docs/goals/dark-factory-intent.md`): "lights-out factory," "$15-20/feature," "internal tools, CRUD apps, config UIs" (which span Node, Python, Go, Rails, Django). Today, "Dark Factory" is honestly "Dark Factory for Node." The Yassine reference architecture proves the *workflow* generalizes, not the *prompts*.

This plan establishes a four-layer architecture, extracts the existing Node/TS coupling into a real adapter, formalizes a project-hints layer, and tightens the failure→remediation→re-run loop so that "pipeline failed because X" maps to a one-skill-or-CLI-command fix and a re-run.

## Non-goals

- Add a second ecosystem (Python, Go) end-to-end. That's the validation step *after* this plan ships. This plan provides the structure; proving the structure with a port is its own follow-up.
- Replace the spec authoring flow. Specs continue as today; project-hints absorbs project-policy decisions specs would otherwise repeat.
- Rewrite agents (oracle, reviewer, evaluator). They're already ecosystem-blind. Touch only the architect prompt (which isn't).
- Change the Docker barrier model. Adapters are Python modules + prompt fragments; barriers stay as filesystem masks.
- Build a fully autonomous failure loop (Level 4 in §C5). Stop at Level 2-3 (proposed-diff with one-command apply); full autonomous remediation is deferred until trust is calibrated.

## The four-layer architecture (north star)

| Layer | Holds | Created by | Lives in |
|---|---|---|---|
| **Spec** | Problem definition: FRs, ACs, domain constraints | Human (with `/df-write-spec` help) | `.dark-factory/specs/<spec>.md` |
| **Project-hints** | Project's standing policy decisions (migration mode, banned commands, preferred patterns) | Human (with `dark-factory hints init` + `/df-write-hints` help) + auto-detection for facts | `.dark-factory/project-hints.toml` |
| **Ecosystem adapter** | Toolchain knowledge for a language/stack (typecheck command, dep resolver, file globs, architect prompt addendum) | Framework code; per-ecosystem implementations | `darkfactory/adapters/<ecosystem>/` |
| **Pipeline core** | Orchestration, barriers, iteration, agent dispatch, gate harness, oracle | Framework code; ecosystem-blind | `darkfactory/pipeline/`, `darkfactory/architecture/` (engine half), `agents/` (ecosystem-blind half) |

**Cut line:** the core knows ecosystems exist and dispatches by name. It does not know what `tsc` is. Adapters know what `tsc` is. Project-hints know "this project chose Drizzle migrate-mode." Specs know "the user can pay for an order."

### Layer decision rubric (which layer owns a new rule?)

When you have a candidate rule, fact, or policy, ask in order:

1. **Is it true for every project that uses this tool/ecosystem?** (e.g., "TypeScript projects need a `tsconfig.json`"; "pnpm workspaces require per-package commands without `-r` unless explicitly requested.") → **adapter**.
2. **Is it true for this project but might be false for another project using the same tools?** (e.g., "this project uses Drizzle in migrate-mode, not push-mode"; "this org bans `--watch` flags in CI commands.") → **project-hints `[policy]`**.
3. **Is it true for this feature only and might be false for the next feature in the same project?** (e.g., "this endpoint must accept idempotency keys"; "this migration is online-only.") → **spec**.
4. **Is it auto-detectable from the project filesystem and stable across features?** (e.g., language, package manager, lockfile presence, workspace layout.) → **project-hints `[detected]`** (auto-written, not human-authored).

Worked example — the migrate-vs-push rule that motivated this plan: Drizzle ships both modes; both are supported by the tool. So it's not adapter-universal (rules out #1). It's project-stable for budget-brain-v2 (rules out #3). It's a *choice*, not a fact (rules out #4). → **`[policy]`**. The TypeScript adapter ships *zero* opinion about migration mode; only project-hints does.

If two layers seem to qualify, the **lower layer wins** (spec > policy > adapter). This avoids "the adapter says X but the project policy says Y" conflicts at runtime.

## Shape

Three tracks plus a Sprint 0 of pre-work. Each track is independently shippable but B5 depends on B1, A4 depends on A1+A2+A4-prereqs, and Tracks B/C consume the structured failure-event surface that lands in C1 (promoted to Sprint 1).

**Sprint 0 — Pre-work** (audit + harness + governance — no production code change)
**Track A — Ecosystem adapter layer** (refactor existing code, no new functionality)
**Track B — Project-hints layer + authoring tooling** (new artifact + new authoring surface)
**Track C — Failure → remediation loop** (tighten the existing cycle from "read error, edit file, retry" to "run command, approve diff, retry")

---

## Sprint 0 — Pre-work (must complete before Sprint 1)

Three deliverables. None ship code into the live pipeline; all reduce risk of the work that follows. None take more than two days; together they take ~5 days and de-risk the entire 6-week plan.

### S0.1 · Ecosystem-leakage audit (the "what else is leaky?" pass) — **DONE 2026-04-14**

**Target:** `docs/audits/2026-04-14-ecosystem-leakage.md`. ✅ Shipped after one Linus review cycle (v1 missed 7 load-bearing files + had 5 sloppy citations; v2 corrected). Audit identifies 54 load-bearing sites across 24 files. Notable surprises beyond the plan's known scope: `consistency.py::DEFAULT_RULES`, `scripts/quality-gate.sh` + `scripts/lib/scenarios.sh` (~400 lines of Bash dispatch), `pipeline/quality_gate.py::_dep_install_prefix`, `docker/mounts.py` pnpm-workspace overlays, `spec/contracts.py::_compile_vitest`, orchestrator hard tsconfig.json skip-gate, `agents/test-engineer.md` + `agents/proposal-rehearsal.md` (need A4 splits). Track A sprint plan updates listed in the audit's "Track A sprint plan updates" section (items 1-8) — to be merged into Sprint 2-3 sections before Sprint 2 kickoff.

**Method:** systematic grep across all repository surfaces — `agents/*.md`, `darkfactory/**/*.py`, `scripts/*.sh`, `.claude/skills/**/*.md`, the `docker/` setup, and `templates/*.md` — for these markers:

- Tool names: `tsc`, `pnpm`, `npm`, `yarn`, `vitest`, `jest`, `drizzle`, `prisma`, `npx`, `node_modules`, `package.json`, `tsconfig.json`, `pip`, `pytest`
- File-extension assumptions: `.ts`, `.tsx`, `.mts`, `.cts`, `.py`, `.go`
- Module-system assumptions: `import_extension`, `module_system`, `esm`, `cjs`
- Path assumptions: `apps/api`, `node_modules/`

For each match, classify: (a) ecosystem-blind (false positive — keep), (b) ecosystem-specific but cosmetic (e.g., example in a docstring — keep with a `# example:` comment), (c) load-bearing ecosystem coupling (must move into adapter). Output: a table with file:line, classification, and target adapter.

**Why first:** A2/A3/A4 currently list known leakage sites. The audit may turn up unknown ones (likely candidates: `agents/scenario-architect.md`, `agents/spec-validator.md`'s implicit Node assumptions in test-harness expectations, `scripts/quality-gate.sh`'s tool dispatch). If we discover four more leaky surfaces *after* starting Track A, the sprints get longer mid-flight. Find them now.

**Acceptance:** Audit doc lists every load-bearing leakage with a target adapter. Track A's sprint plan is updated to absorb any newly-discovered moves before Sprint 2 starts.

- [x] Audit shipped (`docs/audits/2026-04-14-ecosystem-leakage.md`).
- [ ] Sprint 2-3 sections amended with the 8 audit-derived additions (deferred until S0.2/S0.3 land — keeping sprint sections stable mid-Sprint-0).

**Effort:** ~1.5 days (systematic but mechanical). *Actual: ~half-day; one Linus review cycle.*

### S0.2 · Adapter contract test harness

**Target:** `tests/adapters/conftest.py` + `tests/adapters/test_adapter_contract.py`.

**What it does:** for any `EcosystemAdapter` registered under test, runs a fixed set of property checks:
- Implements every abstract method (caught by ABC, but assert in test for clearer failure)
- `detection_signals` returns non-empty tuple
- `consistency_rules()` returns a list (may be empty) of valid `ConsistencyRule` instances
- `architect_addendum()` returns a non-empty string when adapter is active
- `quality_gate_dispatch(<minimal_manifest>)` returns valid `GateCommands`

**Plus a "manifest equivalence" harness** for A4: takes (a) the same spec, (b) old prompt, (c) new prompt + addendum, runs both through a real architect call, and produces a structural diff (paths added/removed/value-changed in the manifest YAML). Lives at `tests/adapters/manifest_equivalence.py`. Used by A4 acceptance criterion.

**Why first:** A2/A3 acceptance currently says "byte-identical manifest" without a tool to measure that. A4's acceptance is the same claim against an LLM call (non-deterministic). Without this harness, every Track A sprint's acceptance step is "run pipeline, eyeball output, hope" — slow, error-prone, and not regression-proof.

**Acceptance:** Harness can be invoked against the current `TypescriptNodeAdapter`-shaped extracted modules (even before they implement `EcosystemAdapter` formally) and reports a baseline. Manifest equivalence harness produces a structural diff (not just text diff) of two architect runs against the same spec.

**Risk note:** the manifest-equivalence half of this harness depends on LLM sampling. If sampling variance turns out larger than the tolerance policy can absorb, every Track A sprint that uses it (A2, A3, A4) gets noisier acceptance signals. Mitigation budgeted in A4 acceptance ("3 runs each side; majority semantic equivalence"); if 3-run majority proves insufficient, escalate to 5-run majority before declaring the equivalence harness broken. If even 5-run majority is unstable, a follow-up plan to harden the harness (deterministic prompts via temperature=0, fixed seeds where supported) precedes proceeding to A4.

**Effort:** ~2 days. Most-leveraged ~2 days in this whole plan. *Actual: ~half-day; one Linus review cycle (4 ship-blocking findings addressed: tolerance/set-like catalogs corrected, glob support for nested set-like paths, `_stable_key` raises on missing canonical key, mock fixtures return non-empty real instances so positive type checks aren't vacuous).*

- [x] Harness shipped: `tests/adapters/_protocol.py` (placeholder Protocol — replaced by A1's real `EcosystemAdapter` ABC), `tests/adapters/conftest.py` (mock + opt-out + broken-violation fixtures), `tests/adapters/test_adapter_contract.py` (20 contract tests: positive + negative per check + catalog-drift guards), `tests/adapters/manifest_equivalence.py` (structural diff with `LOAD_BEARING`/`TOLERATED` policy, glob-matched set-like list normalization, N-of-N majority equivalence), `tests/adapters/test_manifest_equivalence.py` (20 tests covering identity, tolerance, load-bearing, mixed buckets, YAML round-trip, parse-error fail-loud, 3-of-3 + 5-of-5 majority).
- [x] All 40 tests pass. Element-type assertions exercise non-empty fixtures; `None`-branch of `proposal_rehearsal_typecheck` covered by separate `opt_out_adapter` fixture.
- [x] Known asymmetry on YAML round-trip documented in-source (`asdict` emits both `runtime.amendment_ops` and legacy flat fields; parser rebuilds the former from the latter — irrelevant to A4's symmetric LLM-vs-LLM use case).

### S0.3 · Adapter governance + deprecation lifecycle

**Target:** `docs/ADAPTER-GOVERNANCE.md`.

**Contents:**

1. **Bar for blessing a new adapter.** Minimum: implements `EcosystemAdapter` ABC, passes contract harness (S0.2), ships ≥1 worked example spec end-to-end, has a documented architect addendum ≤80 lines, has at least one consistency rule.
2. **Who decides.** Today: this is a one-person project; the user. Document this so future contributors don't assume an open-door PR policy.
3. **Status tiers.** `experimental` (passes ABC, no prod use), `supported` (has end-to-end pipeline run on a real spec), `core` (Dark Factory team-maintained — currently `typescript-node` and `postgres`). Pipeline behavior: experimental adapters print a banner; supported adapters run normally; core adapters get bug-priority.
4. **Deprecation lifecycle for re-exports** (closes neo's "release cycle of what?" critique). Replaced with: `DeprecationWarning` re-exports stay until the next *minor version* of `dark-factory` (`0.X` → `0.X+1`). Plan owners cut a minor version no more than monthly. Re-exports older than two minor versions are deletion candidates. Document the version cadence in `docs/RELEASE-CADENCE.md` (one paragraph).

**Why first:** these are policy decisions, not implementation. Easier to write before the politics matter.

**Acceptance:** Doc exists; references it from `CLAUDE.md` and `docs/QUICKSTART.md`. Versioning paragraph exists in `RELEASE-CADENCE.md`.

**Effort:** ~1 day. *Actual: ~half-day; one Linus review cycle (3 ship-blocking findings: forward references to nonexistent paths needed disclaimer, harness extension pattern was paraphrased instead of pointing at code-owned docstring, CHANGELOG roll-up clause invited skipped entries; plus 2 cleanups about reviewer-vs-CI honesty and demotion mechanics).*

- [x] `docs/ADAPTER-GOVERNANCE.md` shipped — five mandatory blessing criteria (implements ABC, passes harness, ships worked example with run report, addendum ≤80 lines, ≥1 real consistency rule); three status tiers with explicit promotion/demotion rituals; plan-owner ownership stated outright; deprecation lifecycle pinned to two-minor-version window with concrete `DeprecationWarning` example.
- [x] `docs/RELEASE-CADENCE.md` shipped — defines patch/minor/major bump triggers, anchors the deprecation clock to minor-version units (not wall-clock months) so cadence slips don't shorten user runway, requires per-tag CHANGELOG entry (no roll-up escape).
- [x] Referenced from `CLAUDE.md` ("Ecosystem adapters" subsection) and `docs/QUICKSTART.md` ("Next steps" bullet).

---

## Track A — Ecosystem adapter layer

### A1 · Adapter interface definition

**Target:** new `darkfactory/adapters/base.py` defining `EcosystemAdapter` ABC.

**Interface (sketch):**
```python
class EcosystemAdapter(ABC):
    name: str                    # "typescript-node", "postgres", "python-fastapi"
    detection_signals: tuple[str, ...]  # files/markers that indicate this ecosystem applies

    @abstractmethod
    def detect(self, project_dir: Path) -> bool: ...

    @abstractmethod
    def precheck(self, spec: Spec, project_dir: Path) -> list[Finding]: ...

    @abstractmethod
    def consistency_rules(self) -> list[ConsistencyRule]: ...
    """Default rules this ecosystem ships; project-hints can add more."""

    @abstractmethod
    def architect_addendum(self) -> str: ...
    """Prompt fragment injected into architect prompt when this adapter is active."""

    @abstractmethod
    def quality_gate_dispatch(self, manifest: Manifest) -> GateCommands: ...

    @abstractmethod
    def proposal_rehearsal_typecheck(self, scratch_dir: Path) -> list[Finding] | None: ...
    """Return None if this ecosystem doesn't support C2 rehearsal."""
```

**Acceptance:** Interface compiles. ABC is enforced. Instantiating an incomplete adapter fails at module-import time (typing + ABC machinery).

**Effort:** trivial. ~80 LOC including docstrings. ~1 day. *Actual: ~half-day; one Linus review cycle (no ship-blockers, accepted with one nice-to-have applied: test for the `@property`-override pattern in addition to the dataclass-field pattern).*

- [x] `darkfactory/adapters/__init__.py` + `darkfactory/adapters/base.py` shipped — ABC with 6 abstract methods + 2 abstract properties (`name`, `detection_signals` enforced via `@property + @abstractmethod` so omission fails at construction, not first attribute access).
- [x] Concrete types pinned: `precheck` takes `SpecModel`, `quality_gate_dispatch` takes `ArchitectureManifest` (not Protocols — there's only one of each in the repo, so indirection had no payoff).
- [x] S0.2 placeholder `tests/adapters/_protocol.py` deleted; catalogs (`REQUIRED_METHODS`/`REQUIRED_ATTRS`) moved to test-internal `tests/adapters/_catalog.py`; `_MissingPrecheck` standalone fixture deleted (replaced by stronger ABC-construction tests).
- [x] Harness migrated: `_MockAdapter` and `_OptOutAdapter` now subclass `EcosystemAdapter` directly; 4 new ABC-enforcement tests prove construction-time refusal for missing-all / missing-one-method / missing-one-property; `test_catalog_matches_abc_abstract_surface` is the tripwire against catalog drift; both documented patterns (dataclass-field default *and* `@property` override) have pinning tests.
- [x] 45/45 tests pass. No regressions in broader suite (pre-existing 112 failures / 23 errors in unrelated paths unchanged; pass count 2741 → 2746).

**Deferred to A2 per Linus review** (capture so they don't get lost):
- Tighten `Finding.severity` with `Literal["error", "warning", "info"]` or a `__post_init__` runtime guard when A2 first consumes `Finding`.
- Pin `GateCommands` empty-string sentinel ("skip phase") with a test in the A2 harness.
- Move governance's 80-line addendum cap from documentation-only to a check in whatever A2/A3 harness validates concrete adapters against governance.
- Decide on `__init_subclass__`-based registration vs `__subclasses__()` scan when the registry lands in A5.

---

### A2 · Extract TypeScript+Node adapter from existing modules

**Target:** new `darkfactory/adapters/typescript/` containing implementations moved from existing locations.

**Moves:**
- `darkfactory/spec/library_symbols.py` → `darkfactory/adapters/typescript/library_symbols.py`
- `darkfactory/spec/stub_compile.py` → `darkfactory/adapters/typescript/stub_compile.py`
- `darkfactory/scenarios/proposal_rehearsal.py` → `darkfactory/adapters/typescript/proposal_rehearsal.py`
- New: `darkfactory/adapters/typescript/__init__.py` exports `TypescriptNodeAdapter` implementing `EcosystemAdapter`
- New: `darkfactory/adapters/typescript/architect_addendum.md` (extracted prompt fragment — see A4)

**What stays in `spec/` and `scenarios/`:** anything that's actually ecosystem-blind (e.g., the spec model, scenario invocation orchestration). Verified by grep — anything that imports `tsc`, walks `node_modules`, or knows about TypeScript moves.

**Backward compatibility:** `spec/__init__.py` re-exports old symbols with `DeprecationWarning`. Lifecycle per S0.3: removed two minor versions after deprecation.

**Acceptance:**
- Existing budget-brain-v2 spec runs through pipeline. Validation uses the S0.2 contract harness + manifest equivalence harness — pre/post-move runs must produce manifests with zero structural diffs in load-bearing paths (`commands.*`, `runtime.*`, `services.*`, `tier1_amendment_ops.*`, `layout.*`), and identical findings sets per gate.
- All existing tests pass after import paths updated. Contract harness from S0.2 confirms `TypescriptNodeAdapter` satisfies the ABC.
- `from darkfactory.spec.sql_lint import ...` emits DeprecationWarning, still functions.

**Effort:** medium. The moves are mechanical; the validation pass uses the S0.2 harness so it's *measurable*, not eyeballed. ~3-4 days. *Actual: ~half-day; two Linus review cycles (round 1: 7 items — 3 must-fix, 4 should-fix; round 2: ACCEPT with one optional contract-comment applied).*

- [x] Three modules moved via `git mv` from `darkfactory/spec/{library_symbols,stub_compile}.py` and `darkfactory/scenarios/proposal_rehearsal.py` into `darkfactory/adapters/typescript/`. `proposal_rehearsal.py`'s sibling private-helper imports updated; no other source-side edits needed during the move.
- [x] `TypescriptNodeAdapter` (`darkfactory/adapters/typescript/adapter.py`) implements the A1 ABC: dataclass with `name="typescript-node"`, `detection_signals=("package.json","tsconfig.json","pnpm-lock.yaml")`, optional `project_dir` field (registry will set in A5). `precheck` calls `check_library_symbols` + `check_stub_roundtrip` and translates findings via `_lib_finding_to_finding` / `_stub_finding_to_finding`. `quality_gate_dispatch` reads `manifest.runtime.amendment_ops["commands.*"]` into `GateCommands`. `proposal_rehearsal_typecheck` builds `Proposal` objects from `*.ts` files in `scratch_dir` and **delegates to `typecheck_proposals`** — single canonical tsc codepath, no parallel implementation (collapsed in Linus round 2).
- [x] `architect_addendum.md` placeholder shipped; A4 fills with extracted Node guidance from `agents/architect.md`. Within governance line cap (now executable, see below).
- [x] `consistency_rules()` returns `[]` plus `extra_consistency_rules` field for A5/Sprint 3 injection. Migrating `DEFAULT_RULES` from `architecture/consistency.py` deferred per the Sprint 3 hard-ordering constraint (touching it during A2 would couple the move to a behavior change the equivalence harness can't yet certify).
- [x] Deprecation shims at all three old paths: emit single `DeprecationWarning` at import time naming both old + new paths, re-export only public symbols (private helpers are sibling-imported through the new path directly), and ship as real files on disk so removal is a real diff. `pyproject.toml` adds `filterwarnings = ["default::DeprecationWarning"]` so the migration signal surfaces in test summaries.
- [x] Internal call sites updated to import through new paths (no self-inflicted deprecation warnings in pipeline code): `darkfactory/spec/precheck.py`, `darkfactory/pipeline/orchestrator.py`.
- [x] Contract harness parametrized over `_CONCRETE_ADAPTERS = (("mock", ...), ("typescript-node", ...))` with stable per-adapter test ids. Adding a new adapter (A3 Postgres) is one tuple entry. Negative tests stay tied to `broken_adapter_factory` (ecosystem-blind violations).
- [x] **3 of 4 deferred-from-A1 items addressed:** (1) `Finding.severity` is `Literal["error","warning","info"]` with `__post_init__` runtime guard backed by `test_finding_severity_vocabulary_is_closed`. (2) `GateCommands.phases()` exposes the closed phase set; `test_gate_commands_empty_means_skip_phase` pins the empty-string sentinel meaning. (3) Governance 80-line addendum cap moved from prose to executable check (`test_architect_addendum_within_governance_line_cap`); the constant lives in `base.py` as `GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP` and the governance doc cites it by qualified name. *Item 4 (registry-vs-`__init_subclass__`) stays deferred to A5 where the registry actually lands.*
- [x] Tests moved with imports rewritten: `tests/unit/{library_symbols,stub_compile,proposal_rehearsal}` → `tests/unit/adapters/typescript/`. New `test_deprecation_shims.py` (3 tests) pins (a) one warning emitted per import naming both paths, (b) re-exports are `is`-identical to new module's symbols (silent `isinstance` breaks otherwise), (c) shim files exist on disk (so future deletion is a real diff, not an `importlib`/`__getattr__` trick).
- [x] 161/161 tests pass in adapter/typescript suites (60 in `tests/adapters/`, 101 in `tests/unit/adapters/`). Full suite: 113 failed / 2777 passed / 23 errors. Net pass delta vs A1 baseline: +31 (matches expected new-test additions). Net failure delta: +1, verified pre-existing on the A1 commit (`tests/unit/test_sa_validation.py::TestCheck10AssertionUpperBound::test_fail_over_limit` — string-format change in unrelated SA validation code; nothing in adapter/spec/scenarios/typescript paths).

**Deferred to A5 (registry dispatch) per Linus round 1**:
- Add a real-project integration test for `TypescriptNodeAdapter.proposal_rehearsal_typecheck` happy path (currently covered indirectly by `tests/unit/adapters/typescript/test_proposal_rehearsal.py::typecheck_proposals` tests, which the adapter delegates to).
- Harden `_op_str` against `None` / non-str / non-list values from a malformed manifest (raise rather than silently coerce to `""` or `"{...}"`).
- Add log when `quality_gate_dispatch` produces an empty command for a phase the architect was expected to emit.

**Deferred to A4 (architect prompt split)**:
- Fill `darkfactory/adapters/typescript/architect_addendum.md` with the extracted Node/TypeScript/pnpm/Drizzle guidance currently in `agents/architect.md`. A2 ships the placeholder + the wiring; A4 owns the content extraction + manifest-equivalence verification.

---

### A3 · Extract Postgres adapter from existing SQL lint

**Target:** new `darkfactory/adapters/postgres/` with `sql_lint.py` moved from `darkfactory/spec/sql_lint.py`.

**Why separate from typescript:** SQL is not Node-specific. A Python+SQLAlchemy project would also want SQL fence linting. Splitting Postgres into its own adapter keeps the Node adapter focused and gives Python projects a path to the same SQL check.

**Implementation:** `PostgresAdapter` implements `EcosystemAdapter` but its `detect()` triggers on Postgres dialect signals (e.g., `services.postgresql` in env contract, `postgres://` in connection strings, presence of `*.sql` files using PG-only syntax). Multiple adapters can be active simultaneously.

**Acceptance:** Same harness-validated equivalence as A2, applied to SQL fences. S0.2 contract harness confirms `PostgresAdapter` satisfies the ABC. A test project with Postgres in env contract activates this adapter; one without does not.

**Effort:** low. ~2 days.

---

### A4 · Architect prompt split (core + ecosystem addendum)

**Target:** `agents/architect.md` reduced to ~250 lines (ecosystem-blind core); per-adapter addendum files (e.g., `darkfactory/adapters/typescript/architect_addendum.md`) carry the specifics.

**What moves out of `architect.md`:**
- Lines 137-152 (DATABASE SCHEMA SETUP — Drizzle/Prisma/Django/Alembic worked examples)
- Lines 154-164 (MONOREPO COMMAND SCOPING — pnpm-specific recursive-flag prohibitions)
- Lines 156-216 (TEST INTEGRATION CONTRACT — `module_system`/`import_extension`/`tsconfig.json`)
- Any other Node-specific section identified during the move

**What stays in `architect.md`:**
- Role definition, barriers, manifest output shape
- CWD rule (cross-cutting — every ecosystem has a working directory)
- "Respect spec Constraints exactly"
- "Complement Tier 1 contracts"
- Manifest schema (with `<ecosystem_addendum>` slot for adapter-injected sections)

**Spawn-time composition:** the architect orchestrator (`darkfactory/pipeline/orchestrator.py::_step_architecture_lock`) loads core prompt + active adapters' addenda + project-hints policy section, concatenates, and spawns the agent with the composed prompt.

**Acceptance:**
- *Manifest equivalence under tolerance policy* (not byte-identical). The S0.2 manifest equivalence harness compares pre/post-split runs against the same Node spec; structural diff must show: zero paths added/removed, zero values changed in `commands.*`, `runtime.*`, `services.*`, `tier1_amendment_ops.*`, `layout.*`. Whitespace, comment ordering, and prose-field rephrasing in `notes` / `rationale` fields are tolerated. Sampling: 3 runs each side; majority semantic equivalence across runs.
- A non-Node test fixture (synthetic; no need for real Python project) shows the Node addendum is NOT injected.
- If the equivalence harness flags a load-bearing field change, the split is rolled back and the prompt fragment is reworked. No "ship anyway, fix later."

**Why the change from byte-identical:** LLM output is non-deterministic. Demanding byte-equal output across two prompt formulations against a sampling LLM is a sink. The harness from S0.2 enforces the *meaningful* invariant (manifest structure + load-bearing values) without chasing whitespace and ordering noise.

**Effort:** medium-high. The split is straightforward; the equivalence-with-tolerance validation is the genuine work. **5-7 days** (revised from 3 days; LLM equivalence chasing is the riskiest single sprint deliverable in this plan).

---

### A5 · Adapter registry + ecosystem detection + fail-fast dispatch

**Target:** new `darkfactory/adapters/__init__.py` containing the registry; modify `darkfactory/setup/detect.py` to populate detected ecosystems; modify pipeline entrypoint to dispatch via registry.

**Detection:** existing `detect.py` already detects Node/Python via lockfile presence. Extend it to populate `project-hints.toml` `[detected].active_adapters = ["typescript-node", "postgres"]` (one or more).

**Dispatch:**
- At pipeline start, load active adapters from `[detected].active_adapters`.
- For each pipeline step that has ecosystem-specific work, iterate active adapters and let each contribute (or short-circuit if none apply).
- **Fail-fast:** if `[detected].active_adapters` is empty OR contains an adapter name not registered, abort with a clear error before any LLM call: `"No adapter for ecosystem 'rust'. Available: typescript-node, postgres. Install or implement an adapter at darkfactory/adapters/rust/."`

**Escape hatch:** `--no-adapter` flag on `dark-factory run` for users who want to experiment with a partial-functioning pipeline (LLM agents only, no precheck, no consistency rules). Not recommended; explicitly logged as a degraded run.

**Acceptance:**
- Running `dark-factory run` against a project with no adapter declared aborts with the registry error and a remediation hint.
- Running against budget-brain-v2 (Node+Postgres declared) activates both adapters and runs identically to today.
- Unit test confirms a missing-adapter project cannot proceed past detection.

**Effort:** medium. ~3 days.

---

## Track B — Project-hints layer + authoring tooling

### B1 · project-hints.toml schema + loader — **DONE 2026-04-14**

**Target:** new `darkfactory/architecture/project_hints.py`. ✅ Shipped after two Linus review cycles. Loader reads `.dark-factory/project-hints.toml` into frozen dataclasses; strict structural validation, forward-compat unknown-key WARN; legacy synthesis from `hints.json` only (no heuristic sniffing of `environment.toml`). 37 unit tests.

**Schema (TOML):**
```toml
# .dark-factory/project-hints.toml

[detected]                    # auto-written by `dark-factory init`, refreshed by `init --update`
language = "node"
package_manager = "pnpm"
workspace_layout = "monorepo"
typescript = true
test_runner = "vitest"
active_adapters = ["typescript-node", "postgres"]
lockfiles = ["pnpm-lock.yaml"]

[policy]                      # human-authored, additive over time
[policy.workspace]
scope = "apps/api"
banned_recursive = ["pnpm -r", "pnpm --recursive"]

[policy.migrations]
mode = "cli-only"
command = "pnpm db:migrate"

[[policy.consistency_rules]]  # feeds A1 — see B5
name = "migrate-mode-vs-push-mode"
spec_patterns = ["db:migrate", "never auto-applied"]
forbidden_env = ["drizzle-kit push", "db:push"]
env_paths = ["commands.*"]
description = "Spec mandates CLI-only migrations; push-mode bypasses migration journal"

[[policy.consistency_rules]]
name = "no-recursive-pnpm-on-monorepo"
spec_patterns = ["scoped to apps/api"]
forbidden_env = ["pnpm -r", "pnpm --recursive"]
env_paths = ["commands.*"]
description = "Monorepo workspace requires per-package commands"
```

**Loader API:**
```python
def load_project_hints(project_dir: Path) -> ProjectHints:
    """Read, validate against schema, return typed dataclass. Empty file → empty hints."""

@dataclass(frozen=True)
class ProjectHints:
    detected: DetectedFacts
    policy: PolicySection           # may be empty
```

**Validation:** strict schema. Unknown fields → log warning (forward-compat). Malformed structure → loud error (typo protection).

**Backward compat:** if `project-hints.toml` doesn't exist but `environment.toml` and `hints.json` do, loader synthesizes a `[detected]` section from them and warns "run `dark-factory init --migrate-hints` to consolidate."

**Acceptance:**
- Loader reads valid file, returns typed object.
- Loader rejects malformed file with line-number-pointing error.
- Empty `[policy]` section yields functional empty PolicySection (no errors).
- Backward-compat synthesis from `environment.toml` + `hints.json` produces equivalent typed object.

**Effort:** low-medium. ~2-3 days including schema validation. *Actual: ~half-day; two Linus review cycles (round 1: 3 MUST-FIX + 5 SHOULD-FIX + 4 NICE-TO-HAVE; round 2: ACCEPT with two non-blocking nits applied).*

- [x] `darkfactory/architecture/project_hints.py` shipped: `ProjectHints`, `DetectedFacts`, `PolicySection`, `WorkspacePolicy`, `MigrationsPolicy`, `ConsistencyRuleSpec` (all `@dataclass(frozen=True)`) + `load_project_hints()`. Uses stdlib `tomllib`; raises `ConfigError` (from `darkfactory.errors`) on structural failure.
- [x] Strict-but-lenient validation: wrong types on known fields raise `ConfigError` with a dotted path (`policy.consistency_rules[0].name`); unknown sections/keys log WARN (forward-compat). Bool-vs-int subclass trap guarded (`schema_version = true` refused). Duplicate consistency-rule names rejected (copy-paste bug guard + C1 dedup key).
- [x] **Honest acceptance note:** "line-number-pointing" (plan AC #2) is only true for TOML parse errors — `tomllib` drops positions once values are constructed, so structural errors carry a dotted path rather than a line number. Documented in the module docstring; not worth writing a second parser for.
- [x] Backward-compat synthesis: loads only `hints.json` (NOT heuristic-sniffs `environment.toml` commands for `tsc`/`vitest`/etc. — that kind of substring detection belongs in the B2 migration CLI). `synthesized_from_legacy=True` flag on the returned `ProjectHints` tells B3 write-back consumers the instance must not be silently round-tripped.
- [x] `schema_version = 1` field added with current-version pin test (per open question #3 recommendation).
- [x] Consistency rules kept as raw strings (no regex compilation in loader) — B5's consumer in `architecture/consistency.py` compiles them so regex errors surface with rule-name context, not loader stack traces.
- [x] 37 tests in `tests/unit/test_project_hints.py` — happy-path, strict-type rejection, duplicate-name rejection, forward-compat WARN, TOML parse errors with path, legacy synthesis (hints.json only, env.toml only no-op, malformed JSON toleration), dataclass frozen-ness, `CURRENT_SCHEMA_VERSION` pin, non-dict section rejection (the Linus round-1 MUST-FIX #3 regression guard).
- [x] Full unit suite: 2348 passed / 67 pre-existing failed (unchanged vs A2 baseline) / 2415 total.

**Deferred / handoff notes:**
- **B5 handoff:** `_require_str_tuple` rejects empty strings uniformly. If B5 ever needs `env_paths = [""]` to mean "match anywhere," the restriction must be relaxed here — not worked around in `consistency.py`. Flagged so the rule doesn't get reinvented.
- **B3 handoff:** the `synthesized_from_legacy` flag is defensive — no write-back path exists yet. B3's `dark-factory hints` CLI MUST honor it (refuse to persist a synthesized instance without running migration). Prose warning is in the module docstring; enforcement is a B3 responsibility.

---

### B2 · Migrate environment.toml + hints.json into project-hints.toml [detected] — **DONE 2026-04-14**

**Target:** `darkfactory/setup/init.py` — add migration path that consolidates the two existing files. ✅ Shipped after two Linus review cycles. Writer is policy-preserving by default; `--migrate-hints` consolidates the legacy `hints.json` and prompts (or `--yes`) for its deletion. `environment.toml` is *not* touched — scoped out as a separate runtime contract.

**Behavior:**
- `dark-factory init` on a fresh project: writes `project-hints.toml` with `[detected]` filled in and `[policy]` as a commented-out scaffold showing example rules.
- `dark-factory init --update` on an existing project: re-runs detection, refreshes `[detected]`, leaves `[policy]` alone.
- `dark-factory init --migrate-hints` on a project with old `environment.toml` + `hints.json`: reads both, writes consolidated `project-hints.toml`, prompts before deleting old files (default keep until validated).

**Scaffold template** (written into `[policy]` section as comments on first init):
```toml
# [policy.migrations]
# mode = "cli-only"           # uncomment if your project uses migrate-mode (recommended for prod)
# command = "pnpm db:migrate" # the command the architect should set as commands.setup

# [[policy.consistency_rules]]
# name = "example-rule"
# spec_patterns = ["..."]
# forbidden_env = ["..."]
# env_paths = ["commands.*"]
# description = "..."
```

**Acceptance:**
- Fresh `dark-factory init` on synthetic Node project produces valid `project-hints.toml` with `[detected]` populated and commented `[policy]` scaffold.
- `init --update` preserves `[policy]` while refreshing `[detected]`.
- `init --migrate-hints` on budget-brain-v2 produces consolidated file equivalent to old separate files.
- Existing `environment.toml` + `hints.json` remain readable until explicit deletion (no breaking change for projects that haven't migrated).

**Effort:** medium. Migration path is the slow part — needs care to not break existing projects. ~3-4 days. *Actual: ~half-day; two Linus review cycles (round 1: 3 MUST-FIX + 5 SHOULD-FIX + test gaps; round 2: ACCEPT with one non-blocking style nit noted).*

- [x] `DetectionResult` enriched with `typescript`, `test_runner`, `workspace_layout`, `lockfiles` fields — populated via dependency inspection (not command-string sniffing). Honors B1's "no heuristic sniffing" principle.
- [x] CLI flags `--migrate-hints` and `--yes` added to the `init` subparser (`darkfactory/cli.py`); wired through `_cmd_init` → `init_project`. `--yes` help text explicitly scoped to migration-only deletion prompts.
- [x] `_write_project_hints(project_dir, detection)` in `darkfactory/setup/init.py`:
  - **Policy-preserving always.** No `preserve_policy` switch — the writer has no business deciding when to discard hand-authored `[policy]`. Scaffold is emitted only when no file exists or the existing file has empty `[policy]`.
  - **Raises `ConfigError` on unparseable existing file** (not silent overwrite). `init_project` catches and surfaces in `InitResult.errors`; original file content is never touched on failure.
  - **Symlink guard** (`_reject_symlink`) refuses to write when the target path is a symlink — prevents blast-radius redirect bugs.
  - **Single source of truth for detection**: reads `workspace_layout` / `lockfiles` from the enriched `DetectionResult` (no second `detect_hints` walk).
  - `active_adapters` imports `TypescriptNodeAdapter.name` from the adapter class rather than hardcoding `"typescript-node"` — rename-safe today; A5 (adapter registry) replaces this with a registry lookup later.
- [x] `_migrate_legacy_hints(project_dir, *, non_interactive, yes)`: symlink guard + `_try_unlink` closure that catches `OSError` on cleanup failure (logs warning + prints to console, but does not abort init — the `project-hints.toml` write already succeeded).
- [x] `environment.toml` is intentionally NOT touched by migration — scope is `hints.json` only. Regression test `test_environment_toml_is_never_touched` pins it.
- [x] Tests: 19 new tests (43 total in `test_init.py`, was 16 pre-B2). Coverage: fresh-init scaffold, adapter-name coupling, round-trip through B1 loader, policy preservation under re-run (Linus Issue #3 regression guard), unparseable TOML raises (Linus Issue #1), symlink rejection at both write and delete sites (Linus Issue #2), robust-delete OSError handling (Linus Issue #7), legacy consolidation actually wins over stale `hints.json` content, environment.toml protection.
- [x] Full unit suite delta: +19 passing tests, zero new regressions. Pre-existing failures stable (rollback + sa_validation).

**Deferred / handoff notes:**
- **A5 handoff:** `_detection_to_active_adapters` in `init.py` is the one place that maps detection → adapter names. When A5 (adapter registry) ships, rewrite this as a registry lookup (`registry.adapters_for(detection)`) rather than a hardcoded `if` chain. The docstring flags this explicitly. Until then, adding a new adapter requires editing this function — flagged so it doesn't get reinvented.
- **B3 handoff:** the `synthesized_from_legacy` flag is now concretely meaningful — post-migration, the written file is canonical (flag=False via round-trip). B3's `dark-factory hints` CLI can trust that a loaded `ProjectHints` with `synthesized_from_legacy=False` came from disk, not an ad-hoc synthesis.
- **Non-blocking style nit (Linus Round 2):** `result.errors is None` guards at `init.py:106-107` and `148-149` are technically unreachable (`InitResult.__post_init__` initializes errors to `[]`). Kept for consistency with the pre-existing idiom; fold into a future cleanup that either drops the guards or tightens the field annotation to non-optional.
- **Budget-brain-v2 migration not yet run.** Acceptance criterion "init --migrate-hints on budget-brain-v2 produces consolidated file equivalent to old separate files" is satisfied synthetically by `test_migration_consolidated_facts_win_over_legacy` (Node filesystem + lying legacy → consolidated file reflects filesystem). Real-project run scheduled for the Sprint 3 migration window (per B5's hard ordering constraint).

---

### B3 · `dark-factory hints init` CLI scaffolding

**Target:** new subcommand in `darkfactory/cli.py`.

**Subcommands:**
- `dark-factory hints init` — interactive walkthrough for first-time `[policy]` setup. Asks ~5-10 baseline questions (migration mode? banned commands? preferred test runner if multiple detected?). Writes `[policy]` section.
- `dark-factory hints add <rule-spec>` — append-only safe add of a single rule. Used by skill (B4) and remediation flow (C2). Validates against schema before write.
- `dark-factory hints list` — pretty-print current `[policy]` for human review.
- `dark-factory hints lint` — validate `project-hints.toml` against schema, report issues.

**Acceptance:**
- `hints init` on a fresh project produces a non-trivial `[policy]` section based on user answers.
- `hints add` rejects malformed rules; accepts well-formed ones; never silently overwrites existing rules with the same name (forces explicit `--force` flag).
- `hints list` produces stable, human-readable output (testable via golden file).

**Effort:** medium. CLI plumbing + interactive prompts. ~3 days.

---

### B4 · `/df-write-hints` skill (interactive + from-failure modes)

**Target:** new `.claude/skills/df-write-hints/SKILL.md`.

**Modes:**

1. **`init` mode** — `/df-write-hints init`. Walks user through baseline policy setup. Same questions as `dark-factory hints init` CLI but in a conversational format with Claude offering rationale and examples. Useful for new users who'd rather chat than answer CLI prompts.

2. **`add` mode** — `/df-write-hints add "ban any --watch flag"`. Takes a natural-language description, drafts a structured rule, shows preview, applies on confirm.

3. **`from-failure` mode** *(the killer feature)* — `/df-write-hints from-failure <run-id>`. Reads the failure event from `.dark-factory/events.jsonl`, identifies the contradiction class, drafts a project-hints rule that would have caught it, presents diff, applies on confirm, suggests `dark-factory run <spec>` to retry.

**from-failure workflow detail:**
1. Read failure event from events.jsonl matching `run_id`.
2. If event type is `consistency_violation`, extract `rule_name`, `env_path`, `env_match`, `spec_match`, `description`.
3. Check if a rule with this `name` already exists in `[policy.consistency_rules]`. If yes, surface the existing rule and ask "the rule already exists — was it not applied? Inspect detection logic." If no, propose adding it.
4. Show user the proposed addition (full TOML block).
5. On approval: invoke `dark-factory hints add` to write it.
6. Suggest next step: re-run the spec to confirm the contradiction is now caught at validate time (B5) and/or the architect was nudged away from it.

**For non-A1 failure classes:** the skill also handles spec-quality findings (validator score < 100, divergence HIGH), proposing spec edits instead of hint additions. Routing logic per failure type — see C1 for the structured event format that makes this dispatch reliable.

**Acceptance:**
- `from-failure` on a real budget-brain-v2 A1 abort run-id produces a valid TOML block matching the `migrate-mode-vs-push-mode` rule.
- `init` walks through ≥5 questions and writes a syntactically valid `[policy]` section.
- Skill never writes without explicit user confirmation.
- Skill suggests the right next CLI command (`dark-factory run <spec>`) after applying.

**Effort:** medium. ~3-4 days including from-failure dispatch logic.

---

### B5 · A1 consistency rules read from [policy.consistency_rules]

**Target:** `darkfactory/architecture/consistency.py` — `DEFAULT_RULES` becomes a *fallback*; project-hints rules take precedence; combined set is what's checked.

**Behavior change:**
- Before: `check_spec_env_consistency` used hardcoded `DEFAULT_RULES`.
- After: caller passes `rules=adapter.consistency_rules() + project_hints.policy.consistency_rules` (concatenation; both may be empty). `DEFAULT_RULES` retained as a constant for testing/fallback only.

**Adapter contributes baseline rules:** `TypescriptNodeAdapter.consistency_rules()` returns the cross-cutting rules that apply to all Node projects (e.g., "no `process.exit` outside index.ts," "no recursive workspace commands without `bootstrap` spec_type"). Project-hints adds project-specific rules on top.

**Migration (ordering matters):** the existing `migrate-mode-vs-push-mode` rule moves from `DEFAULT_RULES` to budget-brain-v2's `project-hints.toml` `[policy.consistency_rules]` (it's a Drizzle-specific project decision, not a framework universal). **Hard ordering constraint within Sprint 3:** budget-brain-v2's `project-hints.toml` MUST contain the rule *before* `DEFAULT_RULES` is shrunk in framework code. Otherwise, the 2026-04-14 15:21 failure class stops being caught for a window between framework deploy and project hint addition. Sprint 3 acceptance fails if any project that previously passed A1 by virtue of `DEFAULT_RULES` would no longer have coverage post-migration. The framework's `DEFAULT_RULES` shrinks to zero or near-zero rules — most are project- or adapter-specific.

**Acceptance:**
- budget-brain-v2 with its `project-hints.toml` rule populated catches the same A1 violation it caught before (regression test against the 2026-04-14 15:21 run artifacts).
- A project with empty `[policy]` runs without firing A1 (no false positives, no missing rules).
- Adapter-baseline rules fire on appropriate violations (test fixture: a Node project violating "no recursive workspace commands").

**Effort:** low-medium. ~2 days. Mostly plumbing.

---

## Track C — Failure → remediation loop

### C1 · Structured failure events with remediation metadata *(Sprint 1 — promoted)*

**Promoted to Sprint 1** because every downstream piece (B4 from-failure, C2 remediate CLI, C3 failure-time UX, C4 rerun, C5 policy calibration) consumes this schema. Landing it first means every Track A refactor *also* emits these events as it migrates, giving us real failure-distribution data before C5 commits to a taxonomy. Without this, C5's level definitions are guesses.

**Target:** every pre-implementer gate (A1, A2, A3, A6, C2, validator, precheck) emits a structured event to `events.jsonl` with consistent fields:

```json
{
  "type": "remediation_target",
  "schema_version": 1,
  "run_id": "...",
  "spec_id": "...",
  "failure_class": "consistency_violation" | "spec_quality" | "precheck_finding" | "stub_compile_error" | "proposal_typecheck_failure",
  "rule_or_check": "migrate-mode-vs-push-mode",
  "remediation_type": "add_project_hint" | "edit_spec" | "edit_env_contract" | "manual",
  "remediation_target_path": ".dark-factory/project-hints.toml" | ".dark-factory/specs/<spec>.md" | "...",
  "auto_applicable": true | false,
  "suggested_diff": "...",
  "rationale": "Spec contains 'db:migrate' at line 124 but architect emitted 'drizzle-kit push' in commands.setup",
  "next_command": "dark-factory remediate <run-id>",
  "novel_rule_name": true | false
}
```

**`novel_rule_name`** indicates whether this `rule_or_check` has been observed before in any prior `remediation_target` event for this project. Used by C5: novel-on-first-occurrence triggers human confirmation regardless of policy level.

**Plus a paired `remediation_outcome` event** emitted on the *next* run after a `remediation_target`:

```json
{
  "type": "remediation_outcome",
  "schema_version": 1,
  "run_id": "<the rerun's id>",
  "remediation_run_id": "<the original failing run's id>",
  "rule_or_check": "migrate-mode-vs-push-mode",
  "remediation_type": "add_project_hint",
  "outcome": "fixed" | "same_failure_again" | "different_failure" | "shipped" | "unresolved",
  "applied_at": "<ISO timestamp>",
  "applied_by": "human" | "auto" | "ci"
}
```

This one-line addition to every retry is the calibration data C5 needs. Without it, "≥10 successful Level 2-3 remediations" in C5 is unmeasurable. With it, we can compute per-rule success rates, identify false-positive-prone rules, and gate Level 4 introduction on real evidence.

**Why this matters:** the from-failure skill, the remediate CLI, the failure-time UX, *and the policy auto-graduation logic* all consume these events. A consistent schema means one parser, multiple front-ends, and one source of truth for "did the proposed fix actually work?"

**Acceptance:**
- A1 fires → events.jsonl contains a `remediation_target` event with `auto_applicable: true`, `novel_rule_name` accurately set, and `suggested_diff` containing the proposed TOML block.
- Validator score < 100 → events.jsonl contains a `remediation_target` event with `auto_applicable: false` and `rationale` summarizing the gap.
- After remediation + rerun, the rerun emits exactly one `remediation_outcome` event correlating to the original `remediation_target`'s run_id.
- Schema versioned (`schema_version: 1`) so future changes don't silently break consumers.
- **Bootstrapping case:** projects with no `project-hints.toml` yet (Sprint 1 lands C1 alongside B1; ordering between the two is independent within Sprint 1). C1 must emit events even when the policy-loader returns empty hints — events.jsonl writes never depend on policy lookup. Verified by unit test: a project with no `project-hints.toml` triggers a synthetic gate failure and produces a valid `remediation_target` event.

**Effort:** medium. Touches every gate's error path. ~4 days (was 3-4; the outcome event + bootstrapping case add correlation logic).

---

### C2 · `dark-factory remediate <run-id>` CLI

**Target:** new subcommand in `darkfactory/cli.py`.

**Behavior:**
- Read the most recent `remediation_target` event for the given run_id (or `--latest` for the most recent run).
- For `auto_applicable: true` failures (A1 contradictions, missing project-hints rules): show the proposed diff, on confirm apply it via `dark-factory hints add` or equivalent.
- For `auto_applicable: false` failures (spec quality, divergence): print a structured remediation prompt with file/line pointer, exit. Suggest invoking `/df-write-spec` for spec edits or `/df-write-hints from-failure` for richer guidance.
- `--apply` flag skips confirmation (for CI use; default is interactive).
- `--rerun` flag invokes `dark-factory run <spec>` after successful apply (chains the loop).
- `--dry-run` flag shows what would be applied without applying.

**Acceptance:**
- `dark-factory remediate <run-id>` on the budget-brain-v2 2026-04-14 15:21 run abort proposes the migrate-vs-push rule as a project-hints addition; on `--apply --rerun` would write the rule and re-trigger the pipeline.
- Confirmation flow rejects on `n` answer, applies on `y`.
- Dry-run prints diff and exits with code 0 (no side effect).

**Effort:** medium. CLI plumbing + diff rendering + rerun chaining. ~3 days.

---

### C3 · Failure-time UX: actionable next-step printed

**Target:** every `DarkFactoryError` raised by a gate prints, in addition to the error, the remediation command:

```
ERROR: Architecture manifest contradicts spec constraints:
  - spec↔env contradiction [migrate-mode-vs-push-mode]: ...

To fix: dark-factory remediate <run-id>
   or:  /df-write-hints from-failure <run-id>   (in Claude Code)
```

**Implementation:** modify the orchestrator's error-handler to look up the most recent `remediation_target` event for the failing run and append the next-command line.

**Acceptance:** every failure mode that has a structured remediation_target prints a one-line next-command. Failures without one (true crashes, internal bugs) print a generic "see logs at events.jsonl" line.

**Effort:** low. ~1 day.

---

### C4 · `--rerun` flag for one-command remediation+retry

**Target:** `dark-factory remediate --apply --rerun <run-id>` chains apply → wait for write → invoke `dark-factory run <spec-id>`.

**Edge cases:**
- If apply succeeds but the original run failed at architect (so spec wasn't broken, env was), re-trigger from the architect step, not from validator (saves time + cost).
- If apply requires a spec edit (auto_applicable: false), print "this remediation requires manual spec edits; re-run after editing." Don't auto-rerun in this case.
- If a re-run would create infinite loop (same failure twice), abort with "remediation didn't resolve the failure — manual intervention needed."

**Acceptance:**
- One-command remediation+retry on a known A1 contradiction: rule added, spec re-run, A1 passes (because architect now respects the project-hints rule via the architect addendum read flow — see §C5 for autonomy class detail).
- Infinite-loop guard fires after 2 identical failures.

**Effort:** medium. Re-trigger logic + loop guard. ~2-3 days.

---

### C5 · Auto-remediation policy (which classes can auto-apply, which require approval)

**Target:** documented policy in `docs/AUTO-REMEDIATION.md` + enforcement in C2 dispatch.

**Levels (3 in v1; Level-0 deliberately omitted):**

| Level | Failure class | Action | Default |
|---|---|---|---|
| **Propose** | A1 contradiction with no existing rule; missing project-hint inferable from the contradiction | Show diff, require human `y/n`. CI: require explicit `--apply` flag. | Default for `remediate` |
| **Guided** | Spec-quality finding (validator score, divergence) | Print remediation prompt; suggest `/df-write-spec`; no auto-write | Default for `remediate` |
| **Manual** | Stub compile errors, proposal typecheck failures requiring code-shape rethink | Print error + reference; no auto-action | Informational only |

**Why no "Auto-safe" level (dropped from v1):** the original v1 draft proposed an Auto-safe level for "A1 contradiction with exact-match rule already in `[policy.consistency_rules]` (defensive re-add)." Neo correctly observed this describes a *bug*, not a safe auto-fix: if the rule is already present and A1 still fired, re-adding the same rule won't help — the failure is in detection or rule-application, not in rule absence. Auto-applying it would silently mask a deeper bug. Dropped.

**Cross-cutting hard rule (applies regardless of level):**

> **First occurrence of any novel `rule_or_check` in this project always requires human confirmation.** Automation never fires on a rule it has never seen succeed before. Implementation: C2 reads `novel_rule_name: true` from the C1 event; if true, force interactive prompt even when `--apply` is passed in CI. Override only via explicit `--accept-novel` flag, which is logged as a degraded run.

This closes the failure mode where a one-off architect bug produces a misleading rule that gets auto-installed and pollutes hints across all future runs.

**Novelty-tracking threat model (explicit):** novelty is computed by scanning `.dark-factory/events.jsonl`, which is a repo-writable file. A repo-writer could pre-seed a fabricated `remediation_target` event with any `rule_or_check` name, causing future real occurrences to be marked as non-novel and bypass the confirmation gate. This is **not** a security boundary against repo-writers — anyone who can write `events.jsonl` can also write `project-hints.toml` directly, so the attack surface is unchanged. The novelty rule is a guard against *accidental* auto-install of buggy one-off rules from honest pipeline runs, not a guard against an adversarial repo-writer. If a stronger boundary is needed later (e.g., supply-chain-aware deployments), move novelty tracking to a pipeline-signed state file (`.dark-factory/state/rule_seen.json` with a hash chain) written only by C1 emission. Documenting this here so future contributors don't re-litigate the threat model.

**Why not Level 4 (full autonomous loop):** trust calibration via the `remediation_outcome` event added in C1. Once we have evidence-based per-rule success rates (≥10 successful remediations of the same `rule_or_check`, ≥80% `outcome=fixed`), graduate that *specific rule* to a future Level 4. We graduate rules, not classes — fine-grained, evidence-based, reversible. Implementation deferred until the data exists.

**Acceptance:**
- Policy is documented.
- `dark-factory remediate` honors the policy (Propose: confirm or `--apply`; Guided: never auto-write; Manual: never auto-action).
- Novel-rule guard fires on a synthetic test case (rule never seen → confirm required even with `--apply`); requires `--accept-novel` to bypass.
- `remediation_outcome` events accumulate per rule_or_check; `dark-factory hints stats` (B3 sub-command) prints success rates.

**Effort:** low-medium. Documentation + dispatch + novel-rule guard + stats sub-command. ~2 days.

---

## Sequencing

**Sprint 0 — Pre-work** (audit + harness + governance — no production code change):
- S0.1: Ecosystem-leakage audit
- S0.2: Adapter contract test harness + manifest equivalence harness
- S0.3: Adapter governance + deprecation lifecycle docs

Cumulative effort: ~5 days. Ship: audit doc, harness, governance doc. De-risks all subsequent sprints.

**Sprint 1 — Foundations + failure-event surface** (no behavior change beyond event emission):
- B1: project-hints.toml schema + loader
- B2: migrate environment.toml + hints.json
- A1: adapter interface definition
- **C1 (promoted from Sprint 5): structured failure events + remediation_outcome event**

Cumulative effort: ~10 days. Ship: project-hints.toml exists in budget-brain-v2; loader works; adapter interface compiles; every gate emits structured remediation_target events; nothing user-visible changes yet.

**Sprint 2 — Adapter extraction** (refactor with backward-compat, validated by S0.2 harness):
- A2: extract TypeScript+Node adapter
- A3: extract Postgres adapter
- A5: adapter registry + dispatch + fail-fast

Cumulative effort: ~10 days. Ship: existing pipeline runs identically (validated by manifest equivalence harness); adapter modules are now in `adapters/`; registry dispatches correctly.

**Sprint 3 — Architect prompt split + project-hints policy** (the user-visible boundary shift):
- A4: architect prompt split *(re-priced 5-7 days; LLM equivalence chasing is the riskiest single deliverable)*
- B5: A1 reads from project-hints

Cumulative effort: ~7-9 days (was ~5; A4 re-priced). Ship: architect prompt is half its current length; consistency rules are project-policy data, not framework code.

**Sprint 4 — Authoring tooling** (human-facing):
- B3: `dark-factory hints` CLI (incl. `hints stats` sub-command, since `remediation_outcome` data already accumulating from C1)
- B4: `/df-write-hints` skill (init + add + from-failure modes)

Cumulative effort: ~7 days. Ship: humans can author hints interactively; from-failure mode works against real failure events from Sprint 1's C1 emission.

**Sprint 5 — Remediation loop** (the failure→fix→retry tightening):

*Critical-path order — if Sprint 5 slips, ship in this order:*
1. **C2: `dark-factory remediate` CLI** *(must ship)*
2. **C3: failure-time UX** *(must ship — without it, users have to locate run-id manually, which kills the "as simple as possible" promise)*
3. C5: auto-remediation policy
4. C4: `--rerun` flag

Cumulative effort: ~7 days (was ~10; C1 already shipped in Sprint 1). Ship: failed pipeline prints next-command, one CLI invocation remediates, optional `--rerun` chains retry.

**Sprint 6 — Validation** (proves the abstraction):
- Take the same budget-brain-v2 specs through the new pipeline; confirm no behavioral regression.
- Author a synthetic Python project with a stub `python-fastapi` adapter (intentionally minimal, just enough to detect "this isn't Node, do nothing TS-specific"); run a Python spec through the pipeline; confirm fail-fast on missing precheck adapter rather than misleading TS-style precheck output.
- Replay the budget-brain-v2 2026-04-14 15:21 failure end-to-end against the new pipeline. Measure: keystrokes, decisions, wall-time. Compare to the success criteria walkthrough.

Cumulative effort: ~5 days.

**Total: ~7 weeks** of focused work (revised from 6 — Sprint 0 added, A4 re-priced). Realistically calendar **3 months** with normal interruptions for a solo + Claude operator. Sprint 6 is the most slip-prone; if it slips, the rest still ships value, but the multi-ecosystem claim is unproven until 6 lands.

## Success criteria

A dry-run of the budget-brain-v2 2026-04-14 15:21 failure scenario against the post-refactor pipeline should produce:

1. **Same A1 catch.** The migrate-vs-push contradiction still aborts the pipeline post-architect.
2. **Cleaner error message.** The error includes `To fix: dark-factory remediate 9e844d34426d4717` as the next-command.
3. **One-command remediation works.** `dark-factory remediate --apply --rerun 9e844d34426d4717` writes the project-hints rule and re-runs the spec; the re-run passes A1 (because the architect addendum now reads project-hints policy and includes the explicit prohibition in its prompt).
4. **`/df-write-hints from-failure` produces equivalent result** in a Claude Code session.
5. **Architect prompt is ≤250 lines.** Down from 465. Node specifics live in `adapters/typescript/architect_addendum.md`.
6. **Project-hints.toml exists and drives consistency rules.** Hardcoded `DEFAULT_RULES` is empty or near-empty.
7. **Synthetic Python spec fails-fast cleanly.** "No adapter for ecosystem 'python'. Implement at darkfactory/adapters/python/." — not "TypeScript precheck found 7 errors" (today's failure mode).
8. **`remediation_outcome` events accumulate.** After step 3, exactly one `remediation_outcome` event with `outcome=fixed` and `remediation_run_id=9e844d34426d4717` exists in events.jsonl. `dark-factory hints stats` shows the rule's success count incrementing.

If all eight hold, the framework structurally supports multi-ecosystem operation, has a clean failure→remediation loop, has the data needed to calibrate trust toward future autonomy, and is honest about what it supports.

### User-experience walkthrough (the "as simple as possible" check)

Concrete keystroke/decision comparison for the budget-brain-v2 2026-04-14 15:21 failure:

**Today (no plan):**
1. Read error in terminal → identify root cause (requires Drizzle/migrate-mode domain knowledge): ~30s
2. Open spec file, locate §4 Constraints, edit the env hint or constraint phrasing: ~2 min
3. Re-run `dark-factory run 2026-03-28-database-foundation`: ~5s
- **Total: ~3 steps, ~3 min, 1 high-judgment decision (what to edit), requires domain expertise.**

**Post-plan (assuming Sprint 5 ships C2+C3 minimum):**
1. Read error; the next-command line says `To fix: dark-factory remediate 9e844d34426d4717`: ~5s
2. Paste-and-run that command. CLI shows a TOML diff of the proposed project-hints rule. Press `y`: 1 keystroke + ~10s review
3. Re-run `dark-factory run 2026-03-28-database-foundation`: ~5s — OR pass `--rerun` to step 2 to chain it: 0 extra steps
- **Total: 2 commands, 1 decision (`y/n` on a pre-drafted diff), domain expertise NOT required because the diff is computed from the failure event.**

**Post-plan (assuming C4 also ships):**
1. Read error; the next-command line says `To fix: dark-factory remediate --apply --rerun 9e844d34426d4717`: ~5s
2. Paste-and-run that command. Diff shown, prompt for `y` (novel-rule guard fires on first occurrence per C5 cross-cutting rule). Press `y`. Pipeline re-runs automatically.
- **Total: 1 command, 1 keystroke decision, end-to-end remediation+retry.**

**Bar to clear:** post-plan version must be measurably better than today on (a) keystrokes, (b) domain expertise required, (c) decision count. Walkthrough confirms all three improve. If Sprint 5 ships C2+C3 only, we still hit the bar (3 steps → 2 commands, no domain expertise needed). If Sprint 5 also ships C4, we hit a stronger bar (1 command, 1 keystroke).

**This is why C3 is critical-path:** if C3 slips and only C2 ships, users have to grep events.jsonl for the run-id themselves before running `remediate`. That undoes most of the simplicity gain.

## Open questions

1. **TOML vs JSON for project-hints.** TOML is human-friendlier for the `[policy]` section; JSON is friendlier for tooling. Recommendation: TOML (matches existing environment.toml convention).
2. **One adapter or multiple per project?** budget-brain-v2 needs both TypeScript+Node and Postgres. Registry should support multiple active adapters per project. Confirmed multi-adapter is in scope.
3. **Schema versioning for project-hints.** Add `schema_version = 1` field at top of `[detected]`? Useful for future migrations. Recommendation: yes.
4. **What about ecosystem detection ambiguity?** A project might be Node (frontend) + Python (backend). Detection produces `active_adapters = ["typescript-node", "python-fastapi"]`. Pipeline runs both adapters' precheck/consistency/etc. Spec metadata could declare `target_ecosystem` to scope a spec to one adapter — open question whether this is needed for v1.
5. **Auto-remediation for spec edits.** Currently classified Manual/Guided only. Could a future iteration auto-edit spec markdown for known-safe transformations (e.g., adding a positive `commands.setup` value to §4)? Only with very high confidence — defer.
6. **Existing post-mortem rule (B4 from prior plan) intersects this plan.** B4-Option-2 pruned the escalation report narrative. Does the new remediation flow change escalation? Likely yes — escalation should also use the structured `remediation_target` event format. Wire this in during Sprint 5.

## Not in this plan / Deferred

- **Adding Python or Go ecosystem adapters end-to-end.** Validation step in Sprint 6 stubs a minimal adapter; a real port is a follow-up plan.
- **Project-hints inference from existing specs.** Speculative; defer until human authoring proves friction.
- **Level 4 fully-autonomous remediation loop.** Trust must be calibrated before auto-applying without approval; defer until ≥10 successful Level 2-3 remediations.
- **Cross-project hints library.** A future world where common policy decisions ("CLI-only migrations") are shipped as installable rule packs (`dark-factory hints install @dark-factory/migrations-discipline`). Defer — premature to extract before we have multiple projects to learn from.
- **GUI / dashboard for hints management.** CLI + skill is the v1 surface. GUI is a UX nicety, not structurally required.
- **Migrating B1-B5 of the prior plan into adapter knowledge.** The Sprint 1-3 work landed mechanics in pipeline core (iteration loop, classifier hard-fail, oracle prior-iter context). Those are correctly ecosystem-blind and stay where they are. No re-architecture needed.

## Retracted / explicitly out-of-scope

- **"Push ecosystem specifics into the spec."** This was the user's initial proposal; rejected as wrong direction. Specs become heavier and non-portable; spec authors become DevOps engineers. Adapter layer + project-hints is the correct decomposition.
- **"Make pipeline core fully ecosystem-blind with zero adapter knowledge."** Core must know *that* adapters exist and dispatch by name. It does not need to know *what* they do internally.
- **"Auto-detect project policy from existing code."** Some signals are detectable (test runner, package manager); explicit policy decisions (migration mode preference, banned commands) are choices, not detectable facts. Auto-detection covers `[detected]` only; `[policy]` is human-authored.
