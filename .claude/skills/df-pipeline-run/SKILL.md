---
name: df-pipeline-run
description: Full pipeline run from clean state — reset target project, cleanup, sync agents, rebuild images, run pipeline with Monitor, commit run report
argument-hint: "<spec-name> [target-project-path]"
---

# Dark Factory — Full Pipeline Run

Runs the complete pipeline workflow from a clean state. Works from either the dark-factory framework repo or the target project itself.

## Arguments

- `<spec-name>` — required. The spec to run (no `.md` suffix), e.g. `2026-03-28-database-foundation`.
- `[target-project-path]` — optional. Path to the target project. Defaults to `.` (current directory). Typical usage from dark-factory: `/df-pipeline-run <spec-name> ../budget-brain-v2`.

Resolve the path once at the start and treat it as `$TARGET` for the rest of the run. All subsequent commands that touch the target project use this path explicitly — never rely on `cd` persistence. The pipeline run report is still written to `<dark-factory-repo>/docs/pipeline-run-reports/` (step 7), not to the target project.

## Pre-flight

Before starting, verify:
1. `$TARGET/.dark-factory/specs/[spec-name].md` exists
2. `$TARGET/.dark-factory/config.toml` exists (warn only — will be created by step 2 if absent)
3. `dark-factory` CLI is on PATH (`which dark-factory`)
4. Spec has PASS validation — `$TARGET/.dark-factory/reviews/[spec-name]-validation.status` with `verdict=PASS`, `blockers=0`, `divergence` not `HIGH`. If not, stop and tell the user to run `/df-write-spec` and `dark-factory validate` first.
5. Target project working tree state — run `cd $TARGET && git status --short` and classify the output:
   - **Clean** (no output): proceed.
   - **Only spec/plan files changed** (`.dark-factory/specs/**`, `.dark-factory/plans/**`): stop and tell the user to commit the spec first — a fresh run on uncommitted spec changes produces a run report that's impossible to correlate with a commit.
   - **Implementation code changed** (`apps/`, `src/`, `packages/`, `pnpm-lock.yaml`, `package.json`, etc.): this is leftover output from a prior pipeline run that didn't ship, or in-progress human work. Go to step 0 before touching anything else.
   - **Runtime artifacts changed** (`.dark-factory/scenarios/`, `.dark-factory/reports/`, `.dark-factory/state/`, etc.): step 1 will wipe these. No action needed here.

## Step 0: Reset target project implementation code (only if pre-flight #5 detected impl changes)

Prior pipeline runs can leave uncommitted changes in implementation directories (`apps/api/src/`, `packages/`, lockfiles, etc.) from iterations that didn't ship. The pipeline must start from the last committed baseline, or the new architect/implementer gets contaminated by the prior run's attempts.

**Do not silently wipe — uncommitted impl code may be the user's in-progress work, not pipeline residue.**

1. Show the user the uncommitted changes: `cd $TARGET && git status --short` and `git diff --stat HEAD` (the stat, not the full diff — full diff is too noisy).
2. Ask the user explicitly which path to take:
   - **Reset to HEAD** (`git reset --hard HEAD && git clean -fd <impl-paths>`) — appropriate when the changes are all prior-iteration pipeline output.
   - **Stash first, then reset** (`git stash push -m "pre-pipeline-run <spec-name>" -- <impl-paths>`) — appropriate when some of the changes might be salvageable.
   - **Abort the run** — appropriate when the user wants to commit or review the changes first.
3. Only proceed after the user picks an option. Do not assume — the blast radius of `git clean -fd` on shared state is high.

`.dark-factory/` artifacts (scenarios, reports, state) are handled by step 1 and do not need to be touched here.

## Step 1: Clean runtime artifacts (target project only)

Remove stale pipeline artifacts from the **target project's** `.dark-factory/`. Do NOT touch `specs/`, `plans/`, `templates/`, `agents/`, `architecture/`, `project-hints.toml`, `config.toml`.

```bash
rm -rf $TARGET/.dark-factory/scenarios/ $TARGET/.dark-factory/reports/ $TARGET/.dark-factory/audit/ $TARGET/.dark-factory/state/ $TARGET/.dark-factory/failures/ $TARGET/.dark-factory/events.jsonl
```

## Step 2: Sync agents and templates

```bash
(cd $TARGET && dark-factory init --update)
```

This refreshes `$TARGET/.dark-factory/agents/` and `$TARGET/.dark-factory/templates/` from the installed `darkfactory` package. It preserves `config.toml` and `project-hints.toml`. Use the subshell `(cd $TARGET && ...)` form so the outer working directory doesn't shift — the dark-factory repo is often the caller's working directory and we want to stay there for the run report step later.

## Step 3: Rebuild docker images

```bash
bash $(python3 -c "import darkfactory, pathlib; print(pathlib.Path(darkfactory.__file__).parent.parent)")/docker/build-images.sh <language>
```

Choose language based on the project (e.g. `node`, `python`, `go`). For node projects this builds: `dark-factory-node`, `dark-factory-python`, `dark-factory-alpine`, `dark-factory-playwright-node`. Run in foreground; typical completion ~30-60s.

## Step 4: Clean orphaned Docker containers and networks

```bash
docker ps -a --filter label=df-run-id -q | xargs -r docker rm -f 2>/dev/null
docker network ls --filter name=df- -q | xargs -r docker network rm 2>/dev/null
```

## Step 5: Kick off pipeline and set up Monitor

Launch the pipeline in the background and set up a persistent Monitor on `events.jsonl`:

```bash
# Subshell so the outer CWD stays put:
(cd $TARGET && dark-factory run <spec-name>) > /tmp/df-run-<spec-name>.log 2>&1 &
```

Note the PID. Then start a Monitor that parses events.jsonl and emits one-line summaries:

```python
# Monitor command (in the tool call's command field):
sleep 3 && tail -f $TARGET/.dark-factory/events.jsonl | python3 -u -c "
import sys, json, time
start = time.time()
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try: ev = json.loads(line)
    except json.JSONDecodeError:
        print(f'[UNPARSEABLE] {line[:120]}', flush=True); continue
    elapsed = time.time() - start
    etype = ev.get('event', ev.get('type', '?'))
    parts = [f'[{elapsed:6.0f}s]', etype]
    for k in ('role','iteration','status','wall_seconds','pass_rate','gate_exit_code','smoke_result','decision','server_error','scenario_count'):
        v = ev.get(k, '')
        if v != '': parts.append(f'{k}={v}')
    msg = ev.get('message', '')
    if msg: parts.append(msg[:100])
    print(' '.join(parts), flush=True)
"
```

Set `persistent: true` and `timeout_ms: 3600000` (1 hour) on the Monitor.

## Step 6: Track progress — react to events, do NOT poll

- **Do NOT use Bash to poll progress.** Wait for Monitor notifications.
- When a phase-transition event arrives, use Bash ONCE to inspect the relevant artifact:
  - After architect completes (first `AGENT_COMPLETED` at ~60-300s): `cat $TARGET/.dark-factory/architecture/environment.toml` and `cat $TARGET/.dark-factory/architecture/substrate-manifests/<spec>.toml`
  - After scenario architect completes: `ls $TARGET/.dark-factory/scenarios/`
  - After `GATE_COMPLETED`: `cat $TARGET/.dark-factory/reports/<spec>-quality-gate-summary.md`
  - After `SMOKE_COMPLETED`: check for `server_error` field in the event
- **Hard wall-clock cap: 90 minutes.** If the monitor's elapsed time exceeds 5400s, abort: `kill <pipeline-pid>` and `TaskStop` the monitor.

Terminal events: `SPEC_SHIPPED`, `SPEC_NOSHIP`, `SPEC_ESCALATED`, or a pipeline process exit. When one arrives, move to step 7.

## Step 7: Generate pipeline run report

Write a markdown report to `<dark-factory-repo>/docs/pipeline-run-reports/<YYYY-MM-DD-HH-MM>-<target-project-basename>-<spec-name>.md`. Include:

- **Outcome**: SHIP / NO-SHIP / ESCALATED
- **Iterations**: how many ran, pass rate per iteration
- **Wall time**: total elapsed
- **State file excerpt**: `cat $TARGET/.dark-factory/state/<spec>.state`
- **Quality gate summary**: full contents of `$TARGET/.dark-factory/reports/<spec>-quality-gate-summary.md` (not a summary — the actual report)
- **Substrate verification**: contents of `$TARGET/.dark-factory/reports/<spec>-substrate-findings.json` if present
- **Notable events**: `SMOKE_COMPLETED` results, any `BARRIER_PATH_ECHO_DETECTED`, coverage gap events
- **Server/service errors**: tail (200 lines) of any `$TARGET/.dark-factory/reports/<spec>/*.log` on startup failure
- **Audit log spot-check**: grep the scenario architect's docker run argv in `$TARGET/.dark-factory/audit/*.log` to verify source_dirs masks are correct
- **Diagnostic excerpts, not just summaries** — include relevant log lines verbatim so the report stands alone as a post-mortem artifact

## Step 8: Commit the run report

```bash
# Run from dark-factory repo (the run report lives here, not in the target project):
git add docs/pipeline-run-reports/<new-report>.md
git commit -m "$(cat <<'EOF'
docs(pipeline-run): <spec-name> — <outcome> in <N> iterations

<one-line observation about what the run revealed>
EOF
)"
```

Do NOT push unless the user asks.

## Failure modes to watch for

- **Pipeline exits early (< 30s)**: check `/tmp/df-run-<spec>.log` — usually a validation or config error before any agent runs.
- **Monitor stops receiving events but pipeline still running**: the `events.jsonl` tail may have broken. Restart the Monitor; check `ps` for the pipeline PID.
- **No `SMOKE_COMPLETED` for a bootstrap spec**: the smoke gate is advisory on iter 1 but fatal from iter 2. If smoke is absent, the gate container may be crashing — check logs.
- **`BARRIER_PATH_ECHO_DETECTED` events**: an agent tried to read a masked path. Capture the offending path for the report.
