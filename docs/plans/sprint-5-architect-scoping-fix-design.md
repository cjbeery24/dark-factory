# Sprint 5 (first "move the number") — architect command-scoping false-positive

> Design for Neo review BEFORE implementation. Goal: turn the empirical 0/3
> clean-ship baseline into a first clean ship by fixing the DETERMINISTIC
> blocker that aborted the simplest spec (a pure function) at the architect step.

## Root cause (investigated, file:line-cited)

A benchmark run of `2026-04-08-shared-parse-amount` (code in
`packages/shared/src/`) aborted at the architect step after 3 manifest retries.
Two errors:

1. **MONOREPO_SCOPING_VIOLATION (the deterministic blocker).** The architect
   correctly emitted `commands.install: "cd packages/shared && pnpm install"`
   (its prompt, `agents/architect.md:92`, mandates cd-into-the-target-workspace).
   The M1 scoping validator (`darkfactory/spec/manifest.py:1145` →
   `adapters/typescript/adapter.py:411-469`) checks the command target against
   `config.source_dirs`. But budget-brain-v2's `environment.toml:11` has
   `source_dirs=["apps/api/src"]` (drifted from later API/DB specs), and the
   spec declares no per-spec `source-dirs` override. `packages/shared` is
   neither ancestor/equal/child of `apps/api/src` → violation fires EVERY
   attempt. The architect **cannot** fix it — feature specs are forbidden from
   setting `layout.source_dirs` (`architect.md:44`). Unfixable → wasted retries.
   (This spec SHIPPED clean 2026-04-09 when source_dirs was correctly scoped —
   it's a drift regression, not architect flakiness.)
2. **data_models parse error (secondary, architect LLM variance).** The
   architect hallucinated a `data_models[1].fields[0]` with a blank name/type
   for a spec that has NO data models. `manifest.py:456` rejects it. This is a
   legit parse error — different malformed output appeared on each retry.

## Scope of THIS change (tight)

- **In:** fix the framework so a `cd <workspace>` command that is consistent
  with where the manifest's own `file_map` puts files is NOT a false-positive,
  even when the global `source_dirs` has drifted.
- **Out (NOT this change):** the architect's LLM-variance errors (hallucinated
  data_models, type-only-export mistakes). Those are separate; a clean run
  avoids them, and the proven-shippable 2026-04-09 run shows the architect CAN
  produce a good manifest here. Also out: any project-side edit to
  budget-brain-v2's environment.toml/spec — the framework fix should unblock
  WITHOUT requiring per-project config surgery (that's the whole point).

## Proposed fix (Neo to rule between A and B)

**Option A — file_map-derived scope consistency (proposed, durable).**
In the command-scope check (`adapter.py:_is_scope_consistent` /
`validate_command_scope`, and `manifest.py:validate_command_scoping`), treat a
command target as consistent if it is ancestor/equal/child of **any source_dir
OR any directory in the manifest's `file_map`**. Rationale: the manifest's
file_map is the architect's own declaration of where code goes; a command scoped
to where files actually land is self-consistent regardless of a stale external
`source_dirs`. Validates *internal manifest consistency* instead of against a
drift-prone external config. Still fires when the command targets a workspace
that NEITHER source_dirs NOR file_map covers (the real cross-workspace bug the
guard exists for).

**Option B — downgrade to warning when source_dirs is the global default.**
When `source_dirs` came from the global/default (not a per-spec/profile
override), the validator can't trust it to scope a differently-located spec →
emit `log.warning` instead of a hard fail. Simpler, but weaker (loses the guard
entirely for default-config projects) and requires detecting "default vs
explicit" provenance.

**Proposal: A.** It keeps the guard's protective value (catches genuinely
out-of-scope commands) while removing the stale-config false-positive class. B
is a blunter instrument.

## Authorization-model check

The M1 scoping validator emits a gate report row / SPEC_FAILED — it is an
Implementer-visible gate? No: the architect manifest gate runs pre-implementer
and its errors are architect-facing (re-prompt) + abort. Changing it does not
promote any string into a barrier-restricted reader. Trigger #1 (audience
escalation): inert. Trigger #2 (changed surfaces): touches `manifest.py` +
`adapters/typescript/adapter.py` scope logic — declare in commit body. Will
confirm with the worked example during implementation.

## Tests
- Unit (`tests/unit/test_manifest.py` + adapter tests): a `cd packages/shared`
  command with `source_dirs=['apps/api/src']` but a `file_map` under
  `packages/shared/src` → CONSISTENT (was: violation). A command targeting
  `packages/other` with neither source_dirs nor file_map coverage → still
  VIOLATION. Existing correctly-scoped cases unchanged.
- Verify: re-run `2026-04-08-shared-parse-amount` benchmark → should clear the
  architect step (scoping no longer blocks). NOTE: may still abort on architect
  LLM variance (data_models) — that's a separate, non-deterministic issue; a
  clean architect manifest → CLEAN_SHIP (impl already at HEAD).

## Neo review — REWORK → corrected design (detect_workspaces anchor)

Option A is **circular** and rejected: validating the architect's command
against the architect's own `file_map` cannot catch a coherent-but-wrong
manifest (whole manifest built for the wrong workspace) — the exact failure M1
exists to catch. `source_dirs` is currently the ONLY external workspace anchor;
Option A removes it with no backstop. Option B is also unsound (profiles.py
drops overrides equal to default, so "default vs explicit" provenance can't be
reconstructed).

**Corrected fix:** anchor the command-scope check on
`TypeScriptAdapter.detect_workspaces(project_dir)` (adapter.py:327) — it reads
`pnpm-workspace.yaml` / `package.json#workspaces`, which is **machine-
authoritative and drift-proof** (the repo won't install/build if it's wrong) and
**external to the manifest** (non-circular — still fails `cd apps/legacy` when
that isn't a declared workspace). This capability exists today and is unused for
scope validation.
- When `detect_workspaces()` returns a non-empty set: a command target is
  consistent iff it is (a subdir of) a real declared workspace. Drop the
  drift-prone source_dirs comparison in that case.
- When it returns `[]` (non-monorepo / non-pnpm): fall back to the existing
  source_dirs check, with a logged caveat.
- Wiring: thread `workspaces` (or `project_dir`) into `validate_command_scope`
  (adapter.py:411) + `validate_command_scoping` (manifest.py:1145); orchestrator
  (orchestrator.py:2385) computes `adapter.detect_workspaces(project_dir)` and
  passes it. **ABC signature change** (`base.py:657`) → governed by
  ADAPTER-GOVERNANCE.md; make the new param optional/additive (default None) to
  keep it backward-compatible; declare in commit body (changed-surfaces trigger).
- **Residual accepted:** "real-but-wrong workspace" (e.g. `cd apps/web` for a
  packages/shared spec) isn't caught by the workspace check alone — but the
  drifted source_dirs check doesn't catch it either, so this is no regression
  and is strictly better than today (catches non-workspaces; no drift
  false-positive).

**Verification (Neo Q4):** the data_models hallucination is a SEPARATE,
retryable LLM-variance error. Once scoping stops eating all 3 retries, success
depends on ≥1 clean architect attempt of 3. So verify with **N≥3 benchmark
runs and report the clean-architect / clean-ship RATE** — a single post-fix
data_models abort is consistent with the fix working (lost dice roll), NOT a
failed fix. Don't claim "unblocked" from one run.

**Q5 (resolved):** do NOT pre-fix budget-brain-v2's source_dirs — the drift IS
the test oracle. Keep it drifted; the framework fix must prove it on the failing
case. Belt-and-suspenders goes in a unit-test fixture, not a project edit.

**Citation fix:** the on-disk blocking command is `commands.test` (not
`commands.install`); the fix must cover ALL command keys (the architect scopes
all of them).

**Caveat:** clearing M1 may reveal a SECOND deterministic blocker — this proves
M1 is the *first* abort, not the *only* one. Budget for the next wall.

## Original open questions for Neo (superseded by the review above)
- **Q1:** Option A vs B? (proposed A.)
- **Q2:** Should `file_map`-derived scope also feed the OTHER command keys
  (build/test/lint), or just install? (proposed: all command-scope checks, for
  consistency.)
- **Q3:** Is validating command scope against the architect's own file_map
  (internal consistency) a sound replacement for the external-source_dirs check,
  or does it lose a real protection? What genuine bug does the current
  source_dirs-based check catch that file_map-based wouldn't?
- **Q4:** Scope guard — confirm we should NOT also fix the architect
  data_models/type-only-export variance in this change (separate, LLM-side).
- **Q5:** Should I ALSO correct budget-brain-v2's drifted source_dirs (per-spec
  `source-dirs` override) as belt-and-suspenders, or rely solely on the
  framework fix to prove it works without project surgery? (Leaning: rely on the
  framework fix — proving it unblocks without project edits is the stronger
  demonstration.)
