# Phase 1 — Local-LLM Shim Spike Runbook

**Goal:** prove `claude -p` sustains multi-turn agentic tool-use + stream-json through a
local Anthropic-Messages shim in front of `Qwen3-Coder-30B-A3B`. This gates the whole
feature (design doc §9). If tool-use fails through the shim → STOP and reassess.

**Target machine:** Apple M4 Max, 48 GiB unified memory.

## Stack

```
claude -p  →  ANTHROPIC_BASE_URL=http://localhost:<port>  →  shim (/v1/messages)  →  Ollama  →  qwen3-coder:30b
```

- **Serving:** Ollama (`brew install ollama`), model `qwen3-coder:30b` (30B-A3B, 4-bit, ~18 GB).
- **Shim (Anthropic Messages translation):** `claude-code-router` (primary) or LiteLLM (fallback).
  Ollama speaks OpenAI; the shim must translate the Anthropic `/v1/messages` shape *including
  tool-use + streaming*.

## Steps (driven via Bash in the session; heavy parts run on the host)

```bash
# 1. Install + serve Ollama
brew install ollama
ollama serve &                      # background daemon on :11434

# 2. Pull the model (~18 GB; one-time)
ollama pull qwen3-coder:30b

# 3. Install the shim (one of):
#    a) claude-code-router
npm install -g @musistudio/claude-code-router
#       configure ~/.claude-code-router/config.json with an Ollama provider, then `ccr start`
#    b) LiteLLM (fallback)
pip install 'litellm[proxy]'
#       litellm --config litellm.yaml   # maps a model to ollama/qwen3-coder:30b, serves /v1/messages

# 4. Run the smoke probe through the shim
ANTHROPIC_BASE_URL=http://localhost:<shim-port> \
DF_SPIKE_MODEL=qwen3-coder:30b \
scripts/spike/local_llm_smoke.sh
```

## Pass criteria (the smoke script asserts each)

| Capability | Why it matters |
|------------|----------------|
| stream-json lines parse | the pipeline reconstructs the result envelope from the stream |
| emits `tool_use` blocks | agentic work is impossible without it |
| uses Read / Write / Bash | the three tools every agent leans on |
| multi-turn (>1 assistant turn) | agents iterate, not one-shot |
| correct computed result (8484) | the model actually threads tool outputs back into reasoning |
| final `result` envelope + exit 0 | clean completion, not a hang/crash |
| `--disallowedTools Bash` blocked | the defense-in-depth barrier still binds on a non-Anthropic backend |

## Results

See `local-llm-spike-results.md` (filled in after the run): PASS/FAIL per capability,
tok/s, peak memory, chosen quant + shim, and the GO/NO-GO call for Phases 2–5.
