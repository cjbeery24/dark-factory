# Investigation — "TS Tier-B 2/3" escalation root cause (2026-06-11)

**Status:** diagnosis complete (from archived trace, no new runs needed). Fix design pending Neo.

## TL;DR — the plan's hypothesis is falsified
The N=3 battery's one escalation (notes-crud-ts run 2, archive
`20260611-161212-notes-crud-ts-c9d47e15`) was **NOT** reviewer/oracle
non-determinism. The **reviewer never ran** — the quality gate died at the
**Setup** stage before any scenario executed, on all 3 iterations. Root cause is
**stale cross-spec env-contract pollution** + **implementer non-determinism on the
`df-setup.sh` escape hatch** + a **setup-failure-mislabeled-as-PLATEAU** trap.

## Evidence chain (all from the archived trace)

### 1. Gate died at Setup, every iteration (scenario_total=0)
`c9d47e15/final/events.jsonl`:
- iter 1/2/3: `GATE_COMPLETED exit_code=2 pass_rate=0 satisfaction=0 scenario_total=0`
- iter 2/3: `TRAJECTORY_CLASSIFIED trajectory=PLATEAU`
- `ESCAPE_HATCH_FEEDBACK reason=TRAJECTORY_PLATEAU` → `SPEC_ESCALATED iterations=3`

`c9d47e15/iter-*/reports/notes-crud-ts-quality-gate.md` (all 3 iters identical):
```
| Install | PASS | explicit: cd apps/api && pnpm install |
| Setup   | FAIL | exit 1 |
### Setup Failure Details
No config path provided, using default 'drizzle.config.json'
/project/apps/api/drizzle.config.json file does not exist
```

### 2. The stale setup command is database-foundation's env delta, surviving the reset
- `notes-crud-ts` spec §4: "No database, in-memory only."
- Merged `environment.toml` at run time carries `setup = "cd apps/api && npx
  drizzle-kit migrate"` + a full `[services.postgres]` block.
- These come from `environment.deltas/0005-2026-03-28-database-foundation.toml`
  (`op=set commands.setup = "...drizzle-kit migrate"`, postgres service).
- That delta is **untracked**. Battery reset_cmd = `git reset --hard 2e5e0d7 &&
  git clean -fd -e .dark-factory`:
  - `git reset --hard` only touches **tracked** files (delta is untracked → survives)
  - `git clean -e .dark-factory` **excludes** `.dark-factory/` (delta lives there → survives)
- Baseline `environment.toml` @ 2e5e0d7 (tracked) has only `build` + `install`.
  The orchestrator re-merges the surviving untracked deltas at run start →
  in-memory spec inherits the DB env-contract.
- Source-pollution sibling: [[project_reset_source_between_different_spec_runs]],
  one layer down (env-contract instead of source tree).

### 3. The substrate-owner detects the mismatch but is powerless
Both escalated AND shipping runs: substrate-prep `commands_emitted: []`. Its
report explicitly: "environment.toml references drizzle-kit migrate but
drizzle-kit is not installed ... spec is in-memory ... configuration mismatch."
It can only *emit prep commands*; it cannot remove/override a stale
`commands.setup`. So the bad command runs every iteration.

### 4. The ship/escalate split = implementer non-determinism on df-setup.sh
Mechanism: `scripts/quality-gate.sh:1042-1047` — if an executable
`scripts/df-setup.sh` exists, the gate runs `bash scripts/df-setup.sh` **instead
of** `commands.setup`. `agents/implementer.md:69` frames df-setup.sh as "*if the
spec requires database tables/migrations*, create scripts/df-setup.sh with the
correct ORM command" — i.e. for ADDING a correct migration, NOT for neutralizing
a WRONG inherited one.
- **Shipping run 41a91182** (and babfba25): iter-1 Setup FAIL on drizzle (same as
  escalated); iter-2 Setup **PASS via `bash scripts/df-setup.sh`** — implementer
  wrote the shim (untracked file, so not in the tracked implementer-changes.patch).
  Oracle note: "database setup configuration corrected to align with in-memory
  constraint." → scenarios ran → ship.
- **Escalated run c9d47e15**: implementer correctly read "in-memory, no DB," saw
  no reason to write a df-setup.sh → stale `drizzle-kit migrate` failed all 3 iters.

### 5. PLATEAU trap burns the iterations
With `scenario_total=0` and `pass_rate` history `[0,0,0]`, the trajectory
classifier labels it PLATEAU ("Fundamentally different approach needed"). The
feedback DOES diagnose it ("§4 environment contract mismatch ... ESCALATION
RECOMMENDED ... environment.toml line 7 includes setup = drizzle-kit migrate") but
there is no mechanism to act on it; the implementer can't reliably fix an
architecture-level `environment.toml`, and the generic PLATEAU directive doesn't
tell it to write a neutralizing df-setup.sh. 3 iters → escalate.

## Two separable issues

### Issue 1 — Measurement hygiene (the dominant correction)
The Tier-B N=3 battery reset is **not hermetic**: it resets tracked source but
leaves prior specs' untracked env-contract deltas in place. So `notes-crud-ts` ran
on database-foundation's DB env-contract. **"TS Tier-B 2/3" is a contaminated
measurement**; the escalation is cross-spec env-contract pollution, not a
meta-agent variance ceiling. The plan's "meta-agent reliability is the Tier-B
rate-limiter" conclusion rests on this contaminated run. Honest re-measure needs a
hermetic reset (env-contract consistent with the source baseline).

### Issue 2 — Framework robustness (real, smaller, generalizable)
A persistent **setup-stage** gate failure (`scenario_total=0`) is:
- mislabeled as an implementation **PLATEAU** (it's a config error, not a plateau),
- "fixable" only via implementer luck on the df-setup.sh escape hatch,
- diagnosed-but-unactioned by the feedback layer.
Even though the trigger here is a fixture artifact, fast/deterministic handling of
a config-stage failure beats burning 3 iters on a mislabeled PLATEAU.

## Candidate fixes (for Neo)
- **A (hygiene):** make the battery/`--fresh` reset hermetic — clear other specs'
  env-contract deltas (regenerate environment.toml for the spec under test).
  Cheapest, highest-integrity; likely turns Tier-B → 3/3. Doesn't fix the framework.
- **B1 (deterministic, generalizable):** a `commands.setup` referencing a tool
  provably absent from the project manifest (package.json/lockfile) AND not
  required by the current architect manifest should be SKIPPED, not executed to a
  hard failure. Mechanical (the substrate-owner already computes this finding every
  time). Risk: false-skip of a genuinely-needed-but-uninstalled setup → gate on the
  architect-manifest "no setup required" signal to stay safe. Touches
  quality-gate.sh Setup resolution → read docs/AUTHORIZATION-MODEL.md.
- **B2 (routing):** detect persistent setup-stage failure (scenario_total=0 across
  iters from Setup, not implementation) and escalate immediately with the precise
  env-contract diagnosis instead of a generic PLATEAU. Fails faster; does NOT move
  the rate up.
- **B3 (prompt, weak):** strengthen implementer.md:69 so a failing `commands.setup`
  the spec contradicts → deterministically write a no-op df-setup.sh. Band-aid
  against the project's deterministic-fix philosophy.

RECOMMENDATION going into Neo: A (restore measurement integrity + honest re-measure)
is mandatory and first; B1 is the generalizable deterministic framework fix worth
doing if Neo agrees the robustness gap justifies a gate change.
