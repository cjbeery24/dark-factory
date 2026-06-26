#!/usr/bin/env bash
# Phase-1 shim spike: validate that `claude -p` sustains multi-turn agentic
# tool-use + stream-json through a local Anthropic-Messages shim in front of
# a local Qwen3-Coder model.
#
# This runs OUTSIDE the dark-factory pipeline — it is a throwaway probe of the
# single assumption the whole feature rests on (design doc §2/§9, plan Phase 1).
#
# Usage:
#   ANTHROPIC_BASE_URL=http://localhost:3456 \
#   DF_SPIKE_MODEL=qwen3-coder:30b \
#   scripts/spike/local_llm_smoke.sh
#
# Env:
#   ANTHROPIC_BASE_URL   shim endpoint (required) — points claude at the local model
#   ANTHROPIC_AUTH_TOKEN auth token the shim expects (default: "sk-spike-local")
#   DF_SPIKE_MODEL       model id the shim routes (default: qwen3-coder:30b)
#   DF_SPIKE_MAXTURNS    max turns (default: 20)
#
# Exit 0 = all required capabilities PASS; non-zero = a capability FAILED.
set -uo pipefail

BASE_URL="${ANTHROPIC_BASE_URL:?set ANTHROPIC_BASE_URL to the shim endpoint}"
export ANTHROPIC_BASE_URL="$BASE_URL"
export ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-sk-spike-local}"
# Belt-and-suspenders: a stray real token would override the base-url backend.
unset CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY
# SPIKE FINDING (harness-only): when claude -p runs NESTED inside another Claude
# Code session, CLAUDECODE / CLAUDE_CODE_ENTRYPOINT neutralize
# --dangerously-skip-permissions (anti-nested-escalation), so Write/Bash get
# sandbox-blocked — this hits Opus identically (verified by control run) and is
# NOT a model/shim limitation. The real pipeline runs agents in Docker where the
# flag is honored, so this is moot there. Unset for a clean standalone probe.
unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT
# SPIKE FINDING: claude -p sends an extended-thinking field by default; Ollama
# rejects it for a non-thinking model (Qwen3-Coder Instruct) with HTTP 400
# "does not support thinking". Disabling thinking is REQUIRED for a non-thinking
# local backend — and is the design hook Phase 3 must set per local profile.
export MAX_THINKING_TOKENS="${MAX_THINKING_TOKENS:-0}"
export DISABLE_INTERLEAVED_THINKING="${DISABLE_INTERLEAVED_THINKING:-1}"
MODEL="${DF_SPIKE_MODEL:-qwen3-coder:30b}"
MAXTURNS="${DF_SPIKE_MAXTURNS:-20}"

WORK="$(mktemp -d -t df-llm-spike-XXXXXX)"
LOG_DIR="$WORK/logs"; mkdir -p "$LOG_DIR"
trap 'echo "[spike] artifacts in $WORK"' EXIT

echo "[spike] shim=$BASE_URL model=$MODEL maxturns=$MAXTURNS work=$WORK"

# --- Capability 1+2+3+4: multi-turn Read -> Write -> Bash in one session ----
# Seed a file the agent must Read, then Write a script, then Bash-run it.
printf 'the magic number is 4242\n' > "$WORK/input.txt"
TASK1="In the current directory: (1) Read input.txt and extract the integer. \
(2) Write a file solve.py that prints that integer multiplied by 2. \
(3) Run solve.py with python3 via the Bash tool and report the printed value. \
Use the Read, Write, and Bash tools — do not guess."

STREAM1="$LOG_DIR/multiturn.stream.jsonl"
( cd "$WORK" && claude -p \
    --dangerously-skip-permissions \
    --max-turns="$MAXTURNS" \
    --model="$MODEL" \
    --output-format=stream-json --verbose \
    "$TASK1" ) > "$STREAM1" 2> "$LOG_DIR/multiturn.err" &
C1=$!

# --- Capability 5: --disallowedTools actually binds against this backend -----
STREAM2="$LOG_DIR/denybash.stream.jsonl"
TASK2="Use the Bash tool to run 'echo denied-probe'. If you cannot use Bash, say NOBASH."
( cd "$WORK" && claude -p \
    --dangerously-skip-permissions \
    --max-turns=6 \
    --model="$MODEL" \
    --disallowedTools Bash \
    --output-format=stream-json --verbose \
    "$TASK2" ) > "$STREAM2" 2> "$LOG_DIR/denybash.err" &
C2=$!

wait $C1; RC1=$?
wait $C2; RC2=$?
echo "[spike] multiturn rc=$RC1  denybash rc=$RC2"

# --- Analysis (pure text; no jq dependency) ---------------------------------
pass=0; fail=0
check() { # name, condition(0=pass)
  if [ "$2" -eq 0 ]; then echo "  PASS  $1"; pass=$((pass+1));
  else echo "  FAIL  $1"; fail=$((fail+1)); fi
}
has() { grep -q "$2" "$1"; }   # file, pattern -> rc

echo "[spike] === capability report ==="
# stream-json integrity: every nonblank line parses as JSON
python3 - "$STREAM1" <<'PY'
import json,sys
bad=0
for ln in open(sys.argv[1]):
    ln=ln.strip()
    if not ln: continue
    try: json.loads(ln)
    except Exception: bad+=1
sys.exit(1 if bad else 0)
PY
check "stream-json lines parse" $?

has "$STREAM1" '"type":"tool_use"' ; tu=$?
# tool names appear in the stream-json tool_use blocks
has "$STREAM1" '"name":"Read"'  ; rd=$?
has "$STREAM1" '"name":"Write"' ; wr=$?
has "$STREAM1" '"name":"Bash"'  ; bs=$?
check "emits tool_use blocks"  $tu
check "uses Read tool"         $rd
check "uses Write tool"        $wr
check "uses Bash tool"         $bs

# multi-turn: more than one assistant message in the stream
n_assist=$(grep -c '"type":"assistant"' "$STREAM1" 2>/dev/null || echo 0)
[ "${n_assist:-0}" -gt 1 ]; check "multi-turn (>1 assistant turn): $n_assist" $?

# correct answer threaded through tools (4242*2 = 8484)
has "$STREAM1" '8484' ; check "correct computed result (8484) present" $?

# clean completion envelope
has "$STREAM1" '"type":"result"' ; check "emits final result envelope" $?
[ "$RC1" -eq 0 ] ; check "multiturn process exit 0" $?

# disallowedTools: Bash must NOT have been invoked in the deny run
if has "$STREAM2" '"name":"Bash"'; then dn=1; else dn=0; fi
check "--disallowedTools Bash blocked (no Bash tool_use)" $dn

echo "[spike] === totals: $pass pass / $fail fail ==="
echo "[spike] streams: $STREAM1"
echo "[spike]          $STREAM2"
[ "$fail" -eq 0 ]
