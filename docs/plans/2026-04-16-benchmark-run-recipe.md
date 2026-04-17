# Benchmark Run Recipe — database-foundation

**Purpose**: Repeatable recipe for running the database-foundation pipeline
against budget-brain-v2 to measure improvement plan progress. Builds on the
existing `/df-pipeline-run` skill with added metric capture.

**When to use**: After implementing any change from the
[improvement plan](2026-04-16-agent-reliability-and-measurement.md), run
this recipe to measure the effect.

---

## Quick Reference (copy-paste version)

```bash
# === RESET ===
TARGET=~/sites/budget-brain-v2
SPEC=2026-03-28-database-foundation

# 1. Reset impl code to last commit
cd $TARGET && git reset --hard HEAD && git clean -fd apps/ packages/

# 2. Clean runtime artifacts (keep specs, config, hints, architecture contract)
rm -rf $TARGET/.dark-factory/{scenarios,reports,audit,state,failures,reviews}
rm -f  $TARGET/.dark-factory/events.jsonl

# 3. Sync agents
cd $TARGET && dark-factory init --update

# 4. Clean orphaned Docker resources
docker ps -a --filter label=df-run-id -q | xargs docker rm -f 2>/dev/null
docker network ls --filter name=df- -q | xargs docker network rm 2>/dev/null

# === RUN ===
# 5. Launch pipeline (from target project dir)
cd $TARGET && dark-factory run $SPEC --fresh > /tmp/df-run-$SPEC.log 2>&1 &
echo "PID: $!"

# === MONITOR ===
# 6. Monitor events (use Claude's Monitor tool, not tail)

# === RESULTS ===
# 7. After terminal event, capture metrics
cat $TARGET/.dark-factory/state/$SPEC.state
cat $TARGET/.dark-factory/reports/$SPEC-quality-gate-summary.md
```

---

## Detailed Steps

### Prerequisites (verify once per session)

```bash
# Docker running?
docker info > /dev/null 2>&1 && echo "OK" || echo "START DOCKER"

# dark-factory CLI installed?
which dark-factory && dark-factory --version

# Auth token set?
[ -n "$CLAUDE_CODE_OAUTH_TOKEN" ] && echo "OAuth OK" || \
[ -n "$ANTHROPIC_API_KEY" ] && echo "API key OK" || echo "NO AUTH"

# Images built? (rebuild if dark-factory code changed)
docker images --format '{{.Repository}}:{{.Tag}}' | grep dark-factory
```

If images need rebuilding:
```bash
bash $(python3 -c "import darkfactory, pathlib; \
  print(pathlib.Path(darkfactory.__file__).parent.parent)")/docker/build-images.sh node
```

### Step 1: Reset Target Project

**Why**: The Implementer writes code directly to the mounted project. Prior
run attempts leave uncommitted files that contaminate the next run.

```bash
TARGET=~/sites/budget-brain-v2
SPEC=2026-03-28-database-foundation

# Show what we're about to reset
cd $TARGET && git status --short && git diff --stat HEAD

# Reset implementation code (apps/, packages/, lockfiles)
cd $TARGET && git reset --hard HEAD && git clean -fd apps/ packages/
```

**IMPORTANT**: This is destructive. Only reset `apps/` and `packages/` — not
the entire repo. Spec files, config, and hints are preserved.

### Step 2: Clean Runtime Artifacts

```bash
rm -rf $TARGET/.dark-factory/scenarios/ \
       $TARGET/.dark-factory/reports/ \
       $TARGET/.dark-factory/audit/ \
       $TARGET/.dark-factory/state/ \
       $TARGET/.dark-factory/failures/ \
       $TARGET/.dark-factory/reviews/
rm -f  $TARGET/.dark-factory/events.jsonl
```

**Preserve**: `specs/`, `plans/`, `architecture/` (environment.toml, deltas),
`config.toml`, `project-hints.toml`.

**Note**: We DO clean `reviews/` — prior feedback files from failed runs
interfere with fresh iterations. The existing `/df-pipeline-run` skill does
not clean reviews; we do here because our benchmark needs isolation between
runs.

### Step 3: Sync and Clean Docker

```bash
# Sync agent definitions and templates
cd $TARGET && dark-factory init --update

# Clean orphaned containers/networks from prior runs
docker ps -a --filter label=df-run-id -q | xargs docker rm -f 2>/dev/null
docker network ls --filter name=df- -q | xargs docker network rm 2>/dev/null
```

### Step 4: Launch Pipeline

```bash
cd $TARGET && dark-factory run $SPEC --fresh > /tmp/df-run-$SPEC.log 2>&1 &
PIPELINE_PID=$!
echo "Pipeline PID: $PIPELINE_PID"
```

The `--fresh` flag forces a clean start, ignoring any `escalated` or other
terminal state from prior runs.

### Step 5: Monitor with Monitor Tool

Use Claude's Monitor tool on events.jsonl — NOT Bash polling (per user
feedback):

```
Monitor command:
  tail -f $TARGET/.dark-factory/events.jsonl | python3 -u -c "
  import sys, json, time
  start = time.time()
  for line in sys.stdin:
      line = line.strip()
      if not line: continue
      try: ev = json.loads(line)
      except: continue
      elapsed = time.time() - start
      etype = ev.get('event', ev.get('type', '?'))
      parts = [f'[{elapsed:6.0f}s]', etype]
      for k in ('role','iteration','status','wall_seconds','pass_rate',
                'gate_exit_code','smoke_result','decision'):
          v = ev.get(k, '')
          if v != '': parts.append(f'{k}={v}')
      print(' '.join(parts), flush=True)
  "
```

**Terminal events to watch for**: `SPEC_SHIPPED`, `SPEC_FAILED`,
`SPEC_ESCALATED`, `SPEC_NOSHIP`.

**Hard wall-clock cap**: 90 minutes. If exceeded, `kill $PIPELINE_PID`.

### Step 6: Capture Results

After the pipeline exits, capture all metrics:

```bash
SPEC=2026-03-28-database-foundation
TARGET=~/sites/budget-brain-v2

echo "=== STATE ==="
cat $TARGET/.dark-factory/state/$SPEC.state

echo ""
echo "=== QUALITY GATE SUMMARY ==="
cat $TARGET/.dark-factory/reports/$SPEC-quality-gate-summary.md

echo ""
echo "=== SCENARIO COUNTS ==="
cat $TARGET/.dark-factory/reports/$SPEC-scenario-counts.env 2>/dev/null || echo "(not found)"

echo ""
echo "=== EVENTS SUMMARY ==="
grep -E "AGENT_COMPLETED|GATE_COMPLETED|SPEC_" $TARGET/.dark-factory/events.jsonl | \
  python3 -c "
import sys, json
for line in sys.stdin:
    ev = json.loads(line.strip())
    etype = ev.get('event','?')
    parts = [etype]
    for k in ('role','iteration','wall_seconds','pass_rate','gate_exit_code','decision'):
        v = ev.get(k,'')
        if v != '': parts.append(f'{k}={v}')
    print(' | '.join(parts))
"
```

### Step 7: Record in Improvement Plan

Update the Results tables in
`docs/plans/2026-04-16-agent-reliability-and-measurement.md` with the
captured metrics. Compare against baseline:

| Metric | Baseline | This Run | Delta |
|--------|----------|----------|-------|
| pass_rate | 0 | | |
| iterations | 3 (escalated) | | |
| status | escalated | | |
| gate_exit_code | 2 | | |
| monorepo_scoping_violation* | true | | |
| missing_manifest_files* | >=1 | | |
| oracle_metric_delta* | 57 | | |
| scenario_import_violations* | >0 | | |
| prompt_word_count (architect)* | ~8,093 | | |
| prompt_word_count (TE)* | ~9,192 | | |

*Metrics marked with * require the corresponding validator from the
improvement plan to be implemented before they can be measured. Until then,
manually check the artifacts.

### Step 8: Commit Run Report

Write the run report to `docs/pipeline-run-reports/` per the existing
`/df-pipeline-run` convention, then commit from the dark-factory repo.

---

## Manual Metric Checks (until validators are implemented)

Until Phase 1-2 validators are built, check these manually after each run:

**Monorepo scoping (M1)**: Read `$TARGET/.dark-factory/architecture/environment.toml`
and verify `commands.test` targets `apps/api`, not `packages/shared`.

**Missing files (M4)**: Read the manifest at
`$TARGET/.dark-factory/architecture/$SPEC-manifest.yaml`, find all
`action: create` entries, verify each file exists on disk.

**Oracle accuracy (M3)**: Compare the state file's `pass_rate` against what
the Oracle claims in `$TARGET/.dark-factory/reviews/$SPEC-feedback-iter*.md`.

**Import violations (G2)**: Read scenario test files at
`$TARGET/.dark-factory/scenarios/$SPEC/*.test.ts`, check that all
`import ... from` specifiers match `entry_points[*].specifier` in the manifest.

**Prompt word count (M5)**: Until instrumented, estimate from agent definition
word counts + spec word count + context. Use the baseline table in the
improvement plan.

---

## Automating Multiple Runs (A/B testing)

For the A/B protocol (Phase 3), wrap this recipe in a loop:

```bash
for i in 1 2 3 4 5; do
  echo "=== RUN $i ==="

  # Reset
  cd $TARGET && git reset --hard HEAD && git clean -fd apps/ packages/
  rm -rf $TARGET/.dark-factory/{scenarios,reports,audit,state,failures,reviews}
  rm -f $TARGET/.dark-factory/events.jsonl

  # Run
  cd $TARGET && dark-factory run $SPEC --fresh > /tmp/df-run-$SPEC-$i.log 2>&1

  # Capture
  cp $TARGET/.dark-factory/state/$SPEC.state /tmp/df-results-$i.state
  cp $TARGET/.dark-factory/events.jsonl /tmp/df-results-$i.jsonl 2>/dev/null
  cp $TARGET/.dark-factory/reports/$SPEC-quality-gate-summary.md \
     /tmp/df-results-$i-gate.md 2>/dev/null

  echo "Run $i complete: $(grep status /tmp/df-results-$i.state)"
done
```

Cost: ~$1.50/run. 5 runs = ~$7.50.
