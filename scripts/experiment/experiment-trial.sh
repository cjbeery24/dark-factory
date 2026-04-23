#!/usr/bin/env bash
# Runs one trial within a phase. Assumes experiment-phase-setup.sh has
# already run for this phase. Safe to invoke repeatedly within a phase;
# each invocation produces one trial record in $LOGDIR/trials.jsonl.
#
# Terminal events watched: SPEC_SHIPPED, SPEC_ESCALATED, SPEC_FAILED.
# These are the only three terminal SPEC_* events emitted by the
# pipeline (verified against darkfactory/models/events.py and recent
# archive events.jsonl). Any other SPEC_* is non-terminal and must not
# be matched.
set -euo pipefail

: "${DF:?}" "${TARGET:?}" "${SPEC:?}" "${PHASE:?}"
: "${TARGET_PINNED_COMMIT:?}" "${LOGDIR:?}"

WALL_TIME_CAP_SECS="${WALL_TIME_CAP_SECS:-5400}"   # default 90 min

TRIAL_ID="${PHASE}-$(date +%Y%m%d-%H%M%S)"
TRIAL_META="${LOGDIR}/${TRIAL_ID}.meta.json"
TRIAL_LOG="${LOGDIR}/${TRIAL_ID}.pipeline.log"
TRIAL_RESULT="${LOGDIR}/${TRIAL_ID}.result.json"

mkdir -p "$LOGDIR"

# --- Pre-trial assertions ---
TARGET_SHA=$(cd "$TARGET" && git rev-parse HEAD)
[ "$TARGET_SHA" = "$TARGET_PINNED_COMMIT" ] \
  || { echo "ERROR: target on $TARGET_SHA, not pinned $TARGET_PINNED_COMMIT"; exit 1; }
(cd "$TARGET" && git diff --quiet && git diff --cached --quiet) \
  || { echo "ERROR: target has uncommitted changes"; exit 1; }
[ -f "$TARGET/.dark-factory/specs/${SPEC}.md" ] \
  || { echo "ERROR: spec not found at $TARGET/.dark-factory/specs/${SPEC}.md"; exit 1; }

# --- Record trial metadata ---
DF_SHA=$(cd "$DF" && git rev-parse HEAD)
DF_BRANCH=$(cd "$DF" && git branch --show-current)
cat > "$TRIAL_META" <<EOF
{
  "trial_id": "${TRIAL_ID}",
  "phase": "${PHASE}",
  "spec": "${SPEC}",
  "df_branch": "${DF_BRANCH}",
  "df_sha": "${DF_SHA}",
  "target_sha": "${TARGET_SHA}",
  "started_at": "$(date -u +%FT%TZ)"
}
EOF

# --- Skill Step 1: wipe cross-spec runtime (preserve archive/, specs/, config.toml) ---
for d in scenarios reports reviews audit state failures scratch resolution-check holdouts; do
  rm -rf "${TARGET}/.dark-factory/${d}"
done
rm -f "${TARGET}/.dark-factory/events.jsonl"

# --- Skill Step 4: clean orphan containers/networks ---
docker ps -a --filter label=df-run-id -q | xargs -r docker rm -f 2>/dev/null || true
docker network ls --filter name=df- -q | xargs -r docker network rm 2>/dev/null || true

# --- Launch pipeline in the background ---
START_TIME=$(date +%s)
(cd "$TARGET" && dark-factory run "$SPEC" -v) > "$TRIAL_LOG" 2>&1 &
PIPELINE_PID=$!

EVENTS_FILE="${TARGET}/.dark-factory/events.jsonl"

# Wait up to 60s for events.jsonl to appear
for _ in $(seq 1 60); do
  [ -f "$EVENTS_FILE" ] && break
  sleep 1
done
[ -f "$EVENTS_FILE" ] || {
  echo "ERROR: events.jsonl never appeared; killing pipeline"
  kill "$PIPELINE_PID" 2>/dev/null || true
  exit 1
}

# --- Watch for terminal event or wall-clock cap ---
# Three terminal events: SPEC_SHIPPED, SPEC_ESCALATED, SPEC_FAILED.
TERMINAL_REGEX='"event":[[:space:]]*"(SPEC_SHIPPED|SPEC_ESCALATED|SPEC_FAILED)"'
while true; do
  if grep -Eq "$TERMINAL_REGEX" "$EVENTS_FILE" 2>/dev/null; then
    break
  fi
  if ! kill -0 "$PIPELINE_PID" 2>/dev/null; then
    echo "WARN: pipeline exited without terminal event"
    break
  fi
  ELAPSED=$(( $(date +%s) - START_TIME ))
  if [ "$ELAPSED" -gt "$WALL_TIME_CAP_SECS" ]; then
    echo "WARN: exceeded ${WALL_TIME_CAP_SECS}s cap; killing pipeline"
    kill "$PIPELINE_PID" 2>/dev/null || true
    break
  fi
  sleep 5
done
wait "$PIPELINE_PID" 2>/dev/null || true
WALL_TIME=$(( $(date +%s) - START_TIME ))

# Preserve pipeline stdout log before next trial overwrites /tmp/
if [ -f "/tmp/df-run-${SPEC}.log" ]; then
  cp "/tmp/df-run-${SPEC}.log" "${LOGDIR}/${TRIAL_ID}.stdout.log" || true
fi

# --- Analyze archive ---
NEW_ARCHIVE=$(ls -1dt ${TARGET}/.dark-factory/archive/*${SPEC}* 2>/dev/null | head -1 || true)
python3 "${DF}/scripts/experiment/analyze_trial.py" \
  --archive "${NEW_ARCHIVE:-NONE}" \
  --meta "$TRIAL_META" \
  --wall-time "$WALL_TIME" \
  --out "$TRIAL_RESULT"

# Append to experiment log (one JSON per line)
python3 -c "import json; print(json.dumps(json.load(open('$TRIAL_RESULT'))))" \
  >> "${LOGDIR}/trials.jsonl"

# --- Reset target for next trial ---
cd "$TARGET"
git reset --hard "$TARGET_PINNED_COMMIT"
# Clean untracked files, preserve archive/ and specs/
git clean -fd -e '.dark-factory/archive' -e '.dark-factory/specs'

# Double-check the reset
(git diff --quiet && git diff --cached --quiet) \
  || { echo "ERROR: target not clean after reset"; git status --short; exit 1; }

# --- Cooldown ---
sleep 30

OUTCOME=$(python3 -c "import json; print(json.load(open('$TRIAL_RESULT')).get('outcome', '?'))")
echo "=== ${TRIAL_ID} → ${OUTCOME} in ${WALL_TIME}s ==="
