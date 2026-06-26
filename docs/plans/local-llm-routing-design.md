# Design: Local-LLM Routing (Hybrid + Offline)

**Status:** Proposed (worktree `feat/local-llm-routing`)
**Author:** Dark Factory framework
**Date:** 2026-06-26
**Related:** `docs/AUTHORIZATION-MODEL.md` (barrier-change rules), `docs/INTEGRATION.md` (egress), `project_bb_spec4_te_exploration_bound` (long-context motivation)

---

## 1. Goal

Allow the pipeline to route agents to a **local LLM** — exclusively (offline mode)
or in a **hybrid** mix with the default Anthropic-hosted Claude models. Two user-facing
capabilities:

1. **Global configurable model/backend** — one switch sets the default for every agent.
2. **Per-phase / per-agent configuration** — each role can be pinned to a different
   backend (e.g. `implementer = local`, `reviewer = claude`).

### Value propositions (honestly ranked)

| Prop | Strength | Notes |
|------|----------|-------|
| **Confidentiality / on-prem** | Strong | A fully-local run never sends source to a third party. |
| **Outage / offline resilience** | Strong, but **degraded** | Keeps the pipeline *moving* during an Anthropic outage at materially lower quality — not parity. Label such runs degraded. |
| **Token economy** | Real but conditional | Only a net win when the local model is strong enough that it does **not** add NO-SHIP iterations (every failed iteration re-runs downstream agents → costs more, not less). |

---

## 2. Model choice — `Qwen3-Coder-30B-A3B-Instruct` (4-bit)

Target hardware: **Apple M4 Max MacBook Pro, 48 GB unified memory.**

| Factor | Why this model |
|--------|----------------|
| **Footprint** | MoE 30.5B total / 3.3B active. 4-bit (MLX or GGUF Q4_K_M) ≈ **17–18 GB weights**, leaving ~20 GB of usable unified memory (macOS exposes ~36–40 GB to the GPU) for KV cache + system. |
| **Speed** | Only 3.3B params activate per token; M4 Max memory bandwidth → ~40–70 tok/s decode, which a multi-turn agentic harness needs. |
| **Agentic fitness** | Purpose-trained for tool-use / function calling with native tool-call format — the single biggest risk reducer vs a generic instruct model. |
| **Context** | 256K native context (extrapolatable). Directly relevant to heavy-spec Test-Engineer truncation (`project_bb_spec4_te_exploration_bound`). |

**Rejected alternatives:** `Qwen3-Coder-480B-A35B` (flagship; needs a cluster — not local).
Dense `Qwen2.5-Coder-32B` fits at 4-bit (~18–20 GB) but activates all 32B params → slower,
and the user specifically wants MoE.

**Quant is a config knob, not a hardcode.** Baseline 4-bit for the spike; 48 GB leaves
headroom to try **Q5_K_M / 6-bit MLX (~22–25 GB)** if 4-bit quality is borderline.

**Serving stack (validated in Phase 1):** **MLX-LM** (`mlx_lm.server`, Apple-native) serves the
model on an OpenAI-compatible endpoint; **claude-code-router** (`ccr`) translates to the
**Anthropic Messages API** (`/v1/messages`) that `claude -p` requires. The spike found that
**Ollama leaks Qwen tool-calls ~17% of the time but MLX-LM is clean (0%)** — so MLX-LM is the
recommended serving stack on Apple Silicon; Ollama is a fallback. `ccr` uses native parsing
(`maxtoken` transformer only — **not** `enhancetool`, which broke Qwen parsing). See
`local-llm-spike-results.md`.

---

## 3. How a local LLM enters the pipeline

Every agent runs `claude -p --dangerously-skip-permissions` inside a Docker container
(`docker/Dockerfile.base` ENTRYPOINT). That harness — barriers, tool-use, stream-json
parsing — is non-negotiable infrastructure. So we do **not** replace Claude Code; we
**point its backend** at a local shim via env vars:

```
claude -p   →  ANTHROPIC_BASE_URL=http://df-local-llm:4000   →  shim (/v1/messages)  →  Qwen3-Coder
            →  ANTHROPIC_AUTH_TOKEN=<shim-token>
            →  --model=qwen3-coder-30b           (interpreted by the shim's router)
```

**Critical property: this changes ZERO barrier surface.** The agent is still Claude Code,
in the same masked container, with the same `--disallowedTools` and tmpfs overlays. No
directory mask changes; no Reviewer-only signal is promoted to Implementer-readable. Per
`docs/AUTHORIZATION-MODEL.md` this is **not** an authorization-model change. The only new
surface is **egress** (Phase 4): the container must reach the local endpoint — handled by
an internal-only sidecar, which is *stricter*, not looser, than today's external egress.

---

## 4. What already exists (≈60% built)

| Need | Already present | Anchor |
|------|-----------------|--------|
| Global model | `model_id = "claude-sonnet-4-6"` | `models/config.py:455` |
| Per-role model | `model_ids: dict[str,str]` keyed by `AgentRole.value` | `models/config.py:456` |
| `[model.ids]` TOML parse | dynamic-key dict | `config/loader.py:73-78` |
| Resolution (two sites) | `model_override or model_ids.get(role.value, model_id)` | `docker/args.py:219`, `agents.py:778`, `agents.py:922` |
| Per-role override **template** | `agent_timeout_overrides` — parse + validate + `.get(role.value, default)` | `loader.py:80-88`, `config.py:558-572`, `agents.py:927` |
| **Per-role env injection hook** | auth token + `CLAUDE_CODE_MAX_OUTPUT_TOKENS` injected per-agent | `agents.py:767-780` |
| Sidecar networking **precedent** | service containers attach to run network with `--network-alias` | `quality_gate.py:1508` |

`[model.ids]` already sets `--model` per role today. The gap is purely: **a model string
alone cannot switch *backends*** — a local agent also needs `ANTHROPIC_BASE_URL` +
`ANTHROPIC_AUTH_TOKEN` swapped, per-role, in a hybrid run.

---

## 5. Core design — `ModelProfile`

Generalize `model_ids: dict[str,str]` (role → model string) into named **profiles**
(backend + model + cap) plus a role → profile mapping. Bare `model_ids` stays valid for
backward compatibility (= "default backend, change `--model` only").

```toml
# A profile bundles a backend.
[model.profiles.local]
base_url         = "http://df-local-llm:4000"   # shim endpoint
auth_token_env   = "DF_LOCAL_LLM_TOKEN"          # env var holding the shim token (not the value)
model            = "qwen3-coder-30b"
max_output_tokens = 8192                          # Qwen's real cap, not Sonnet's 64000
family           = "local"                        # cost.py rate = 0

# "default" profile is implicit = Anthropic-hosted, real OAuth/API token, model_id.

# Role → profile mapping (per-agent / per-phase config).
[agents.models]
escalation        = "local"
defect-classifier = "local"
implementer       = "local"     # only after the spike + A/B prove it ships
# everything unset → "default" (Claude)
```

**Resolution (`resolve_model_profile(role, config) -> ModelProfile`)** — precedence:
call-time override → `[agents.models][role]` → global default profile. Returns a profile
object carrying `base_url`, `auth_token`, `model`, `max_output_tokens`, `family`.

The two existing resolution sites consume the profile's fields:
- `docker/args.py:219` → `--model = profile.model`
- `agents.py:778` → `max_output_tokens = profile.max_output_tokens` (Phase 3 extends
  `resolve_max_output_tokens` to honor an explicit per-profile cap; `family="local"`
  no longer falls through to the 64000 default).

---

## 6. Which agents go local (the quality reality)

Even with an agentic-grade local coder, **the verification spine stays on Claude.**

| Keep on Claude (judgment / barrier-critical) | Safe to localize (bounded / low-stakes) |
|----------------------------------------------|------------------------------------------|
| Test Engineer, QA Holdout — *they are the correctness oracle*; a weak one silently writes worse blind tests | Escalation (terminal report writer) |
| Reviewer, Oracle — ship/no-ship judgment | Defect Classifier (bounded tagging) |
| Architect — hard design reasoning | Substrate Owner (emit prep commands) |
| Implementer — **candidate**, but only after spike + one-spec A/B proves ship-rate holds | Spec Validator (borderline; pilot) |

Rationale: a weak **Implementer** merely causes more iterations (recoverable, measurable).
A weak **Test Engineer / Reviewer / Oracle** corrupts the oracle itself — unrecoverable,
because that *is* the thing that decides correctness. Localize the producer, never the judge.

---

## 7. Networking (Phase 4)

Containers run on `--internal` networks (`networks.py:57`) and reach Anthropic only through
tinyproxy (`docker/filter-analysis.txt`). The local endpoint is reached via a **sidecar**,
mirroring service containers:

- Run the shim (+model, or the shim forwarding to a host/LM-Studio server) as a container
  with `--network-alias df-local-llm`, attached **only to internal agent networks**
  (`df-{run}-analysis`, `df-{run}-execution`) — **never** the external network. A local agent
  thus reaches the model and *nothing else*: stricter isolation than a Claude agent.
- Add `df-local-llm` to `NO_PROXY` (`docker/args.py:34`) so the agent reaches it directly,
  not through tinyproxy. No proxy-allowlist change needed.
- **Rejected — host bridge** (`--add-host host.docker.internal:host-gateway` + proxy
  allowlist): more flexible (reuse a host Ollama with GPU) but punches a host-reachability
  hole that cuts against the barrier model. Offered only as an opt-in escape hatch, off by
  default.

---

## 7a. Concurrency: a local backend is capacity-limited (interaction with TE batch parallelization)

The recent TE batch parallelization (`scenario_batch_parallel`, default OFF) fans a single
Test-Engineer generation into N batches run **concurrently as separate `claude -p`
containers** — up to `_te_batch_workers` (default **2**, override `scenario_batch_max_workers`)
at once (`orchestrator.py:3846-3864`, `:3665-3678`). Each container opens its own multi-turn
session against the **same backend**.

Against Anthropic's API, 2 concurrent sessions is nothing. Against **one** local Qwen on a
single M4 Max it is the whole ballgame:

- The local server either **serializes** the two sessions (Ollama `OLLAMA_NUM_PARALLEL`),
  erasing the wall-clock benefit of fanning out, **or** runs them truly concurrently — at
  which point two long-context KV caches contend for the same ~20 GB headroom and risk
  eviction / OOM / throughput collapse. Either way the *concurrency* win does not exist on
  one box.
- Queued batches also inflate each batch's effective wall time → `agent_timeout` /
  `agent_timeout_overrides` can fire on the waiting batch.

**Two conclusions:**

1. **Mostly moot under the recommended hybrid.** The TE is on the keep-on-Claude side
   (§6) — it *is* the correctness oracle. So the parallel fan-out keeps going to Anthropic
   and parallelizes fine; the local model is not in that path. The concurrency conflict is
   resolved by the same rule that protects quality. Good convergence, not a coincidence:
   the roles worth parallelizing heavily are the expensive judges we keep on Claude.

2. **The general primitive still needs a guard.** A `ModelProfile` for a local backend is a
   **capacity-limited resource** (effectively 1, maybe a small N, concurrent sessions per
   box). Any parallel fan-out whose role resolves to a local profile must **clamp its worker
   count to that profile's capacity** — otherwise the pipeline spawns 2 containers that thrash
   one GPU. Concretely: `_te_batch_workers` must cap to the local profile's `max_concurrency`
   when TE → local (likely 1). This is **Phase 3** work (resolution-time), tracked as a risk
   row below, and applies to any future parallel-agent path, not just the TE.

**Silver lining — decomposition survives serialization.** Batching has *two* benefits:
(a) **concurrency** (unavailable on one local box) and (b) **decomposition** — splitting a
huge TE generation into smaller per-anchor-subset outputs, which is what defuses the
output-token-cap / long-hang failure mode (`project_bb_spec4_te_exploration_bound`). Benefit
(b) holds even with `scenario_batch_parallel = false` (serial batches). So the sensible
combo *if* someone insists on a local TE is `scenario_batch_size > 0` **+**
`scenario_batch_parallel = false`, leaning on Qwen's 256K context for the rest.

## 8. Offline mode (Phase 5)

`--offline` forces every role to the local profile and **labels the run degraded** in the
scorecard (a local-degraded ship must never be silently compared against a Claude ship).

**Honest caveats:**
- The **Implementer** still needs npm/pypi/etc. for package installs (`filter-execution.txt`).
  A truly air-gapped run requires vendored deps; otherwise "offline" means "no Anthropic,
  but registries still reachable."
- Cost/telemetry: local agents cost `$0` but still emit token counts. `cost.py` must map
  `family="local"` → rate 0 and the scorecard must not divide by Anthropic pricing.

---

## 9. Risks & open questions (gated by the spike)

| Risk | Mitigation |
|------|------------|
| Shim tool-use/streaming unreliable | **Phase 1 spike: DONE → CONDITIONAL GO** (`local-llm-spike-results.md`). Multi-turn tool-use works; but ~17% tool-call-format leak rate (temp 0) compounds over multi-step tasks → keep judges on Claude; a leak-recovery parser gates a trustworthy local Implementer. |
| Non-thinking local model 400s on the thinking field | **Phase 1 finding:** local profile injects `MAX_THINKING_TOKENS=0` + `DISABLE_INTERLEAVED_THINKING=1` (`ModelProfile.disable_thinking`, default True). |
| Local Implementer adds NO-SHIP iterations (net token loss) | One-spec A/B (Qwen-Impl vs Sonnet-Impl) on a known-good spec, measured through the real gate. Public benchmarks don't transfer. |
| `--disallowedTools` binding when backend ≠ Anthropic | Enforced client-side by Claude Code; verify in spike. |
| Real OAuth token leaking to the local shim | Phase 3: local profile injects **only** `ANTHROPIC_BASE_URL` + shim token; the real token is withheld from local containers. |
| Output-token cap mis-derived for Qwen | Phase 3: profile carries explicit `max_output_tokens`; `family="local"` no longer hits the 64000 default. |
| Parallel fan-out (TE batches) thrashes one local GPU | Phase 3: `ModelProfile.max_concurrency`; `_te_batch_workers` clamps to it when TE → local. Mostly moot since TE stays on Claude (§6, §7a). |

---

## 10. Phasing (see `.claude/plans/local-llm-routing-plan.md`)

1. **Spike** — stand up Qwen + shim, validate `claude -p` tool-use end-to-end. Gate.
2. **`ModelProfile` config** — primitive + TOML + validation + resolution (backward-compat).
3. **Backend env injection** — profile-aware auth/base_url/model/cap in the container args.
4. **Sidecar networking** — internal-only local-LLM container + NO_PROXY.
5. **Offline mode + telemetry** — `--offline` flag, degraded labeling, `family="local"` cost=0.

Each code phase ships **default-OFF** (no profile configured ⇒ identical to today) and is
gated by Linus + Codex review per the worktree workflow.
