# Phase 1 — Shim Spike Results

**Date:** 2026-06-26 · **Machine:** Apple M4 Max, 48 GiB · **Driven by:** Bash (live)
**Stack:** `claude -p` (2.1.193) → `claude-code-router` 2.0.0 (`ccr`, :3456) → Ollama → `qwen3-coder:30b` (30.5B-A3B, Q4_K_M)

## Verdict: **GO** (with MLX-LM serving)

The core assumption is **validated** — `claude -p` sustains full multi-turn agentic tool-use
through a local-Qwen shim. The ~17% tool-call leak seen on **Ollama** is **eliminated by
switching the serving stack to MLX-LM** (Apple-native): the full end-to-end smoke that *failed
3/10 on Ollama passes 10/10 through ccr→MLX*, at comparable throughput. The remaining caveat
is efficiency, not correctness (the local model takes more turns per task), so the standing
policy holds: pilot the localizable roles; **keep the verification spine (TE/QA/Reviewer/
Oracle) on Claude.**

### Ollama vs MLX-LM (same model, quant ≈4-bit, temp 0)

| Metric | Ollama (`qwen3-coder:30b`, Q4_K_M) | **MLX-LM** (`mlx-community/...-4bit`) |
|--------|-----------------------------------|--------------------------------------|
| Tool-call adherence (direct, repeated) | 5/6 structured (**~17% leak**) | **10/10 structured (0% leak)** |
| Full end-to-end agentic smoke (`claude -p`→ccr→server) | **3/10 PASS** (multi-turn task leaked) | **10/10 PASS** |
| Decode throughput (warm, M4 Max) | ~94 tok/s | ~85 tok/s |
| Resident | 20 GB | ~16 GB |

**Decision:** adopt **MLX-LM** as the serving stack for local profiles. `ccr` config:
native parsing (`maxtoken` only), provider `api_base_url: http://localhost:8081/v1/chat/completions`.

**Concurrency data point:** with *both* Ollama (20 GB) and MLX (16 GB) models loaded at once,
throughput collapsed to ~4.6 tok/s — one M4 Max ≈ one model's worth of capacity. Reinforces
the per-profile `max_concurrency` clamp (plan Phase 3) and one-model-per-box sidecar (Phase 4).

**Efficiency caveat:** the local model used **21 assistant turns** for the 3-step Read→Write→
Bash task (a frontier model needs ~3). Correctness is fine; turn-count (≈ cost/latency) is the
real local penalty — measured properly by the follow-up ship-rate A/B, not this micro-task.

## What worked (proven, with traces)

| Result | Evidence |
|--------|----------|
| **Full agentic loop** Write→Bash→report | clean run: `TOOL_USE Write{solve.py} → File created`, `TOOL_USE Bash{python3 solve.py} → 8484`, `FINAL err:False turns:3`; `solve.py` actually written |
| **Read tool + multi-turn** | 10–12 turn runs; model read input.txt, computed 8484 |
| **Ollama tool parsing** (native) | direct `/v1/chat/completions` → structured `tool_calls`, `finish_reason: tool_calls` |
| **stream-json integrity** | all lines parse; clean `result` envelope |
| **Performance** | 20 GB resident, 100% GPU, **~94 tok/s decode**, ~198 tok/s prefill, 32K ctx — comfortable on 48 GiB |

## Gotchas found (and fixes)

1. **Thinking must be disabled.** `claude -p` sends an extended-thinking field by default;
   Ollama 400s for the non-thinking Qwen Instruct model (`"does not support thinking"`).
   **Fix:** `MAX_THINKING_TOKENS=0` + `DISABLE_INTERLEAVED_THINKING=1`. → **Phase 3 design
   hook:** a local profile must inject these env vars alongside `ANTHROPIC_BASE_URL`.

2. **`enhancetool` transformer breaks Qwen tool parsing.** ccr's `enhancetool` text-parses
   tool calls itself, but its parser doesn't recognize Qwen's `<function=…>` format → every
   call leaked as raw text. **Fix:** remove `enhancetool`; use Ollama's native parsing
   (`maxtoken` only). The *idea* of a text-parse fallback is right — see Risk below.

3. **Nested-session sandbox (harness-only).** When `claude -p` runs inside another Claude
   Code session, `CLAUDECODE` / `CLAUDE_CODE_ENTRYPOINT` neutralize
   `--dangerously-skip-permissions` → Write/Bash got sandbox-blocked. **Verified via control
   run: the real Anthropic backend (claude-opus-4-8) hit the *identical* blocks** — so this
   is NOT a model/shim limitation. Moot in the real pipeline (agents run in Docker where the
   flag is honored). **Fix (spike only):** `unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT`.

## The real risk: tool-call format inconsistency

Even at **temperature 0**, the model intermittently omits the `<tool_call>` wrapper Ollama's
parser needs, so the call leaks as text and the agent loop stalls:

```
adherence test (multi-tool prompt, temp 0, 6 runs): STRUCTURED ×5, LEAKED ×1  (~17%)
```

At the default temp 0.7 the leak is more frequent. A per-call leak probability `p` makes a
clean `N`-call run only `(1−p)^N` likely — at `p=0.17, N=5` that's ~40%. **This is the
local-quantized-model agentic ceiling**, and it directly reinforces the design policy:

- **Keep the verification spine on Claude** (TE/QA/Reviewer/Oracle) — a leak there corrupts
  the oracle. The Implementer can absorb leaks via the existing retry/iteration loop (at a
  cost/iteration penalty).
- **Mitigations (follow-up, before trusting a local Implementer):**
  1. Low temperature (helps, insufficient alone) — `ModelProfile` should carry a sampling knob.
  2. A **Qwen-format-aware leak-recovery parser** in the shim (the right version of what
     `enhancetool` attempts) — recovers leaked calls rather than preventing them. Highest-value.
  3. Higher-precision quant (Q6/Q8) — more memory, likely better adherence.

## Follow-up experiments (status)

1. **MLX-LM server instead of Ollama** — ✅ **DONE → ADOPTED.** Eliminated the leak (0/10 vs
   Ollama's 17%); full smoke 10/10. This is now the recommended serving stack (see verdict).
2. **Unsloth / mlx-community fixed-template build** — partly moot now (MLX already clean). The
   MLX quant *is* an mlx-community build; its template is what fixed parsing. (NB:
   `--tool-call-parser qwen3_coder` is a **vLLM/SGLang** flag, not Ollama/ccr — and vLLM is
   weak on Apple Silicon, so the Mac lever was MLX, not vLLM.)
3. **Leak-recovery parser in the shim** — **downgraded** from production-gate to optional
   defense-in-depth, since MLX removed the leak. Still worth a `ccr` transformer if a future
   model/quant regresses. Lives at the shim/sidecar layer (Phase 4), not `ModelProfile`.
4. **Higher-precision quant (Q5/Q6)** — only if a harder workload shows residual leakage on MLX.

## Design option: a "reasoning" local profile (distinct from this "execution" profile)

This spike used the **execution** (non-thinking) Coder model. For roles that benefit from
explicit planning (a local Implementer/Planner), a **reasoning** sibling fits 48 GiB and
*does* support thinking — so it avoids the 400 and gives CoT, at higher latency/token cost:
- General **Qwen3 30B-A3B / 32B** (toggleable thinking via `--think` / `/no_think`),
- **DeepSeek-R1-Distill-Qwen-32B**, or **GLM-/Gemma-class** 27–32B tool-callers.

This maps cleanly onto the `ModelProfile` design: a `reasoning-local` profile with
`disable_thinking=False` for the Implementer, an `execution-local` profile for bounded roles,
and the verification spine on the `default` (Claude) profile — exactly the hybrid the design
recommends. Captured as a follow-up; not required for Phases 2–5.

## Reproduce

```bash
brew install ollama && OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_CONTEXT_LENGTH=32768 ollama serve &
ollama pull qwen3-coder:30b
npm i -g @musistudio/claude-code-router
cp scripts/spike/ccr-config.json ~/.claude-code-router/config.json && ccr restart   # maxtoken only, NO enhancetool
ANTHROPIC_BASE_URL=http://localhost:3456 scripts/spike/local_llm_smoke.sh
```

## Implications for Phases 2–5

- **Phase 3 (env injection):** a local profile injects `ANTHROPIC_BASE_URL`,
  `ANTHROPIC_AUTH_TOKEN`, **`MAX_THINKING_TOKENS=0`**, **`DISABLE_INTERLEAVED_THINKING=1`**,
  and withholds the real token. `ModelProfile` gains a `disable_thinking: bool = True` (for
  non-thinking backends) and an optional `temperature`/sampling field.
- **Shim recommendation:** `claude-code-router` with native Ollama parsing (no `enhancetool`);
  a leak-recovery transformer is follow-up work that gates a trustworthy local Implementer.
- **Scorecard:** any local run is **degraded** until the leak-recovery lands — never compare
  a local ship against a Claude ship without the label.
