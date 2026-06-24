#!/bin/bash
# Validator non-determinism confirming PILOT — incorporates dual-review fixes:
#  - SERIAL execution (batch=1) to remove rate-throttling confound (Neo+Codex)
#  - interleaved OFF/ON arm order so any drift balances across arms
#  - blind: clear prior validation artifacts in each isolated copy
#  - PRESERVE EVERYTHING per run: full .dark-factory/ (reviews, status, ledger,
#    precheck JSON, events, state, streams/failures, audit, debug/ prompt-dump),
#    run.log, the assembled validator prompt (DF_DUMP_VALIDATOR_PROMPT), spec hash
#  - delete NOTHING; operator analyzes raw data post-run
# Framed as a PILOT: rough marginal + instrumentation validation + truncation/path
# diagnosis. NOT a +-0.15 measurement (that needs ~40-80/arm; size from this).
set -u
SPEC="2026-03-30-auth-schemas-middleware"
SRC="~/sites/budget-brain-v2"
BASE="~/sites/.validator-confirm"
RESULTS="$BASE/RESULTS.txt"
DF="$(command -v dark-factory)"
mkdir -p "$BASE"
: > "$RESULTS"
log() { echo "[$(date '+%H:%M:%S')] $*" >> "$RESULTS"; }

# 10 OFF + 4 ON, interleaved (OFF-heavy per review: OFF arm answers the mechanism)
ARMS=(off on off off off on off off off on off off off on)
SRC_HASH=$(shasum -a 256 "$SRC/.dark-factory/specs/$SPEC.md" | cut -d' ' -f1)
log "SERIAL confirming pilot: ${#ARMS[@]} runs (10 OFF + 4 ON), interleaved, full capture"
log "spec sha256: $SRC_HASH (must match in every copy)"

i=0
for arm in "${ARMS[@]}"; do
  i=$((i+1)); rn=$(printf "%02d" $i)
  C="$BASE/run-$rn-$arm"
  rm -rf "$C"; mkdir -p "$C"
  rsync -a --exclude node_modules --exclude .git --exclude dist \
        --exclude '.dark-factory/archive' --exclude '.dark-factory/_archive*' \
        --exclude '.dark-factory/failures' --exclude '.dark-factory/reports' \
        "$SRC/" "$C/" 2>/dev/null
  # verify identical spec bytes
  ch=$(shasum -a 256 "$C/.dark-factory/specs/$SPEC.md" | cut -d' ' -f1)
  [ "$ch" != "$SRC_HASH" ] && log "run-$rn WARNING spec hash mismatch: $ch"
  # blind: framework preserves prior validation.* on cleanup, so clear explicitly
  rm -f "$C/.dark-factory/reviews/$SPEC"-validation.* \
        "$C/.dark-factory/reviews/$SPEC"-consistency-ledger.yaml 2>/dev/null
  rm -rf "$C/.dark-factory/state/$SPEC"* 2>/dev/null
  flag=""; [ "$arm" = "off" ] && flag="--no-adapter"
  log "run-$rn ($arm) START"
  t0=$(date +%s)
  ( cd "$C" && DF_DUMP_VALIDATOR_PROMPT="$C/.dark-factory/debug" "$DF" validate "$SPEC" $flag > "$C/run.log" 2>&1 )
  dt=$(( $(date +%s) - t0 ))
  ST="$C/.dark-factory/reviews/$SPEC-validation.status"
  MD="$C/.dark-factory/reviews/$SPEC-validation.md"
  v=$(grep -o 'verdict=[A-Z]*\|divergence=[A-Z]*\|blockers=[0-9]*\|consistency=[A-Z]*' "$ST" 2>/dev/null | tr '\n' ' ')
  ci=$(grep -m1 -E '^### CI-1' "$MD" 2>/dev/null | sed -E 's/^### CI-?1[ :|—-]*//' | cut -c1-60)
  prompt_dumped=$(test -f "$C/.dark-factory/debug/$SPEC-validator-prompt.txt" && echo yes || echo NO)
  streams=$(find "$C/.dark-factory/failures" -name 'stream_*' 2>/dev/null | wc -l | tr -d ' ')
  log "run-$rn ($arm) DONE ${dt}s | $v | prompt_dumped=$prompt_dumped streams=$streams | CI-1: ${ci:-none}"
done
log "ALL RUNS COMPLETE — raw data preserved under $BASE (delete nothing)"
