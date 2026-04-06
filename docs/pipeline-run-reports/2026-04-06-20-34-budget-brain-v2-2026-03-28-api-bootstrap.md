# Pipeline Run Report — 2026-04-06 20:34 UTC

- **Repo:** budget-brain-v2
- **Spec:** 2026-03-28-api-bootstrap
- **Run ID:** b5a74b9ae9b44d4e
- **Outcome:** NO-SHIP after iter 1, iter 2 also failed gate; pipeline killed during iter 3 setup at user request (no path to ship visible).

## Pre-run setup

- Reset budget-brain-v2: `git restore .`, `git clean -fd .dark-factory/`, removed runtime dirs (`scenarios`, `reports`, `audit`, `state`, `failures`, `events.jsonl`, `architecture`).
- Cleared `__pycache__` under dark-factory.
- Synced `agents/` and `templates/` from dark-factory → `.dark-factory/`.
- `pip install -e ~/sites/dark-factory` — successful.
- `dark-factory build node` — built (cached layers, OK).
- Cleaned orphan df containers/networks.

## Run 1 — `dark-factory run 2026-03-28-api-bootstrap`

### Phase timeline (from events.jsonl)

| Phase | Wall (s) | Notes |
|---|---|---|
| spec-validator (att 1) | 162.9 | exit 0, hit 200 turn cap |
| architect (att 1) | 156.2 | exit 0, hit 200 turn cap |
| scenario-architect generate (att 1) | 156.6 | hit 200 turn cap |
| COVERAGE_GAP | — | 16 uncovered anchors (AC-1,2,4,5,6,7,10; EC-1..9) |
| scenario-architect coverage_regen | 187.4 | COVERAGE_PASS, anchor_count=23 |
| **iter 1** implementer att 1 | 230.5 | hit 200 turn cap |
| iter 1 implementer (re-run) | 194.7 | hit 200 turn cap |
| iter 1 implementer (re-run) | 61.4 | hit 200 turn cap |
| iter 1 SMOKE | — | **FAIL** — see error below |
| iter 1 GATE | — | exit 1, pass_rate 0, server_error true, scenario_total=1 |
| iter 1 ITER_COST | — | turns=600, est $126 |
| iter 1 reviewer | 103.5 | exit 0 |
| iter 1 oracle (att 1) | 58.5 | exit 0 |
| iter 1 oracle (re-run) | 52.7 | exit 0 → **NO-SHIP** |
| **iter 2** implementer att 1 | 311.2 | hit 200 turn cap |
| iter 2 implementer (re-run) | 214.6 | hit 200 turn cap |
| iter 2 SMOKE | — | **FAIL** — pnpm-lock.yaml outdated |
| iter 2 GATE | — | exit 2, pass_rate 0, server_error true |
| iter 2 ANOMALY_DETECTED | — | spec_turns: 1000 > max 800 |
| iter 2 TRAJECTORY_CLASSIFIED | — | **PLATEAU** — "Fundamentally different approach needed" |
| iter 2 reviewer | running… | killed by user before completion |

Total wall time observed: ~33 min before kill.

### Smoke failure — iter 1 (npm workspace protocol)

```
server_setup_cmd failed (exit 1):
npm warn workspaces @budget-brain/api in filter set, but no workspace folder present
npm error code EUNSUPPORTEDPROTOCOL
npm error Unsupported URL Type "workspace:": workspace:*
```

The implementer used `npm` for a pnpm workspace. The repo has `pnpm-workspace.yaml` and `pnpm-lock.yaml`, but `package.json` deps reference `workspace:*` which only pnpm/yarn understand. The configured `server_setup_cmd` invoked npm.

### Iter-1 oracle feedback (excerpt)

> **Critical Blocking Issue:** Server startup failure prevents all runtime verification… `npm warn workspaces @budget-brain/api in filter set, but no workspace folder present` … `npm error Unsupported URL Type "workspace:": workspace:*` … **Required Fix:** Ensure all workspace dependencies use proper pnpm workspace protocol… `pnpm --filter @budget-brain/api dev` should start the server successfully.

### Smoke failure — iter 2 (pnpm lockfile drift)

The implementer apparently switched to pnpm, but mutated `apps/api/package.json` (changed `@budget-brain/shared` from `workspace:*` to `*`) without regenerating the lockfile, so frozen-lockfile install fails:

```
ERR_PNPM_OUTDATED_LOCKFILE  Cannot install with "frozen-lockfile" because pnpm-lock.yaml is not up to date with <ROOT>/apps/api/package.json
specifiers in the lockfile ({"@budget-brain/shared":"workspace:*", ...}) don't match
specs in package.json ({"@budget-brain/shared":"*", ...})
```

So iter-1 fixed-by-rewriting-the-wrong-thing: the implementer changed the package.json spec rather than matching the working `workspace:*` form. Lockfile drift broke the same smoke check.

### State at kill

```
status=running
completed_step=2
last_decision=NO-SHIP
iteration=2
smoke_result=failed
pass_rate=0
gate_exit_code=2
server_error=true
```

Reviewer container `priceless_chebyshev` was running iter-2 review when SIGTERM was sent. Pid 900 exited cleanly; one orphaned network had already been torn down (got "network not found" on rm).

### Quality gate summary (iter 2)

```
| Gate                          | Status |
| Build                         | FAIL   |
| Lint                          | FAIL   |
| Type Check                    | FAIL   |
| Self-Tests                    | SKIP   |
| Blind Scenarios (executable)  | FAIL   |
| Mutation Testing              | SKIP   |
| Server Setup                  | FAIL   |
Result: FAIL
```

All non-skipped gates failed downstream of the server-setup failure (build/lint/type-check were presumably never reached due to install failure).

## Findings & Metrics

**Pipeline-mechanics observations**

1. **Repeated AGENT_START with same `attempt:1`.** Implementer launched 3 times in iter 1 and 2 times in iter 2, oracle launched twice in iter 1, all reporting `attempt:1` and `turns=200` (max). Looks like the runner re-invokes a role when it hits the 200-turn cap, but the attempt counter is not incrementing in the emitted event. Worth verifying whether this is just a logging defect or whether retries genuinely re-run from scratch (the iter-1 cost of 600 turns / $126 suggests they did, and is significant). Anomaly detector picked it up only on iter 2 (`spec_turns: turns=1000 > max=800`), not iter 1 (which was already 1000+ turns: spec-validator 200 + architect 200 + scenario gen 200 + scenario regen 200 + impl 600 = 1400).
2. **State file not updated mid-iteration.** During implementer execution, state shows `phase=` (empty), only updates after gate. Made progress monitoring rely on `events.jsonl` and `docker ps`.
3. **Container logs not persisted on smoke failure.** `.dark-factory/reports/2026-03-28-api-bootstrap/` directory does not exist even after two smoke failures. The server_error string in events is the only diagnostic; the documented "200-line tail" of container logs was not produced. Possible regression in log-persistence path on smoke failure.
4. **Coverage regen worked correctly** (16 uncovered → COVERAGE_PASS with 23 anchors after one regen pass).
5. **Plateau classifier fired at iter 2** with the right verdict ("fundamentally different approach needed"); strategy guidance is good but the pipeline still proceeded into iter 3.

**Spec/test signal**

6. **Cost.** Iter 1: 600 turns / ~$126. Iter 2: 400 turns / ~$84. ~$210 burned without ever booting the server. Very high cost-per-signal because every iteration is gated by the same broken `server_setup_cmd` and never reaches blind scenarios.
7. **Implementer regression behavior.** Iter 1 used npm and got the workspace protocol error. Iter 2 "fixed" it by editing `package.json` to drop `workspace:*` in favor of `*`, breaking the frozen lockfile. The feedback document was clear and prescriptive ("use `pnpm --filter @budget-brain/api dev`") but the implementer chose to mutate package.json instead of switching the run command. This suggests either (a) the implementer cannot edit `server_setup_cmd` config (so it works around it by editing source), or (b) the feedback isn't pointing it at the right knob.
8. **Server setup is not under spec/agent control.** The root cause is configuration: `server_setup_cmd` uses npm against a pnpm workspace. If the implementer has no way to fix project config, the iteration loop is structurally incapable of unblocking — every retry will fail at the same point. This is the strongest reason the run was unlikely to ship and was killed.

**Recommended pipeline improvements**

- Surface `server_setup_cmd` and similar config as part of the iteration feedback loop, or have the implementer be allowed to propose config changes.
- Persist container stdout/stderr to `.dark-factory/reports/{spec}/*.log` on smoke failure (claimed in CLAUDE.md, not happening).
- Fix the `attempt` field on AGENT_START re-runs so the events log accurately reflects retries.
- Have the anomaly detector / plateau classifier ABORT (rather than continue) when the same `server_error` string repeats across iterations — there is no information in iteration N+1 that wasn't already in iteration N.
- Update state file on entering each phase, not just after gate, so monitoring tools don't have to read events.jsonl.

## Result

Run killed during iter-2 reviewer / iter-3 implementer transition. Both completed iterations failed at server setup with no scenario ever running. Plateau detected. No path to ship visible — same root cause persists across iterations and the implementer has no apparent affordance to fix the underlying configuration.
