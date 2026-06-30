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
claude -p   →  ANTHROPIC_BASE_URL=https://host.docker.internal:8443  →  TLS shim (/v1/messages)  →  Qwen3-Coder
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
base_url         = "https://host.docker.internal:8443"  # TLS-terminating shim, reached via the egress proxy (see §7)
auth_token_env   = "DF_LOCAL_LLM_TOKEN"          # env var holding the shim token (not the value)
ca_cert          = "~/.config/dark-factory-local/shim-cert.pem"  # PEM the agent trusts (self-signed shim cert); mounted RO + NODE_EXTRA_CA_CERTS
model            = "qwen3-coder-30b"
max_output_tokens = 8192                          # Qwen's real cap, not Sonnet's 64000
family           = "local"                        # reserved cost-domain tag; NOT consumed today (cost-zeroing derives from backend/base_url — see §8)

# NOTE (2026-06-30): base_url MUST be https://. claude -p reaches its backend ONLY via a
# CONNECT/TLS tunnel through the egress proxy (even for the host bridge), so the local shim
# MUST terminate TLS. Stand a TLS terminator (stunnel/caddy/small forwarder) in front of the
# plain-HTTP shim (ccr) on a port; trust its self-signed cert via ca_cert. http:// is rejected
# at config time (it would connect, then hang the TLS-over-plain-HTTP handshake ~174s).

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
object carrying `base_url`, `auth_token`, `model`, `max_output_tokens`, `max_concurrency`.

The two existing resolution sites consume the profile's fields:
- `docker/args.py` → `--model = profile.model`
- `agents.py` → `max_output_tokens = profile.max_output_tokens` (Phase 3 extends
  `resolve_max_output_tokens` to honor an explicit per-profile cap; a custom backend
  carries an explicit positive cap — `__post_init__` requires it — so it no longer falls
  through to the 64000 sonnet/haiku default).

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

## 7. Networking (Phase 4) — host model reached through the existing proxy

> **Design pivot (2026-06-26), reflecting what shipped.** The original §7 proposed an
> internal-only `df-local-llm` **sidecar** because it was wrongly assumed an `--internal` agent
> container couldn't reach a host-run model. That was corrected: agents already reach
> `api.anthropic.com` **through tinyproxy** (the proxy straddles the internal + external
> networks and enforces a hostname allowlist; `docker/filter-*.txt`, `FilterDefaultDeny Yes`).
> The shipped path uses that same proxy — there is **no sidecar container**.

Containers run on `--internal` networks and reach the outside world only through tinyproxy.
The local model server runs on the **host** (M4 Max GPU intact); the agent containers stay
100% `--internal` and **never touch the host directly** — they POST to their existing
`HTTP_PROXY` (tinyproxy) with the target host in the request, and the *proxy* (already on the
external net) resolves and connects.

- The local endpoint is **one more allowlist entry** on the proxy, injected per-run only when
  a local profile is configured (`local_llm.py::proxy_allow_for_profiles` →
  `proxy-entrypoint.sh` appends `DF_EXTRA_ALLOW_REGEX`; default-OFF, idempotent, fail-closed).
- The allowlist regex is `^host\.docker\.internal(:\d+)?$`, granting the host on **any port**
  (the operator's own machine, loopback-equivalent). `ModelProfile.__post_init__` *requires*
  `host.docker.internal` and rejects loopback for this reason. It also *requires* `https://`
  (reversed 2026-06-30): claude -p reaches its backend ONLY via a CONNECT/TLS tunnel through the
  proxy, so the shim must terminate TLS — an http:// backend connects then hangs the
  TLS-over-plain-HTTP handshake. CONNECT on the backend's (non-443) port is permitted by the
  merged `DF_EXTRA_CONNECT_PORT` mechanism (`proxy_connect_ports_for_profiles`), and the agent
  trusts the shim's self-signed cert via `ca_cert` (mounted RO + `NODE_EXTRA_CA_CERTS`); TLS
  verification stays ON.
- The widening is scoped **per `net_type`**: a local role used only in analysis does not open
  egress on the `execution` network where built code + blind scenarios run.

**Barrier classification.** This touches the **egress/exfiltration** control (the tinyproxy
allowlist), NOT the information barrier (test/code/review audience). Per
`docs/AUTHORIZATION-MODEL.md`, the two audience-escalation / `barriers.py` triggers do **not**
fire. It is a real, documented egress widening (to the operator's own host, opt-in,
default-OFF) — **not** "stricter than today's egress." The Linux GPU-sidecar path is dropped
from this phase (revisit only if a user wants the model itself containerized).

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
- Cost/telemetry: local agents cost `$0` but still emit token counts. The scorecard zeroes
  cost for `backend == "local"` lines (derived from the profile's `base_url`, recorded in
  telemetry) and must not divide by Anthropic pricing. (The `family` field is a reserved tag,
  not the cost signal — see §5.)

---

## 9. Risks & open questions (gated by the spike)

| Risk | Mitigation |
|------|------------|
| Shim tool-use/streaming unreliable | **Phase 1 spike: DONE → CONDITIONAL GO** (`local-llm-spike-results.md`). Multi-turn tool-use works; but ~17% tool-call-format leak rate (temp 0) compounds over multi-step tasks → keep judges on Claude; a leak-recovery parser gates a trustworthy local Implementer. |
| Non-thinking local model 400s on the thinking field | **Phase 1 finding:** local profile injects `MAX_THINKING_TOKENS=0` + `DISABLE_INTERLEAVED_THINKING=1` (`ModelProfile.disable_thinking`, default True). |
| Local Implementer adds NO-SHIP iterations (net token loss) | One-spec A/B (Qwen-Impl vs Sonnet-Impl) on a known-good spec, measured through the real gate. Public benchmarks don't transfer. |
| `--disallowedTools` binding when backend ≠ Anthropic | Enforced client-side by Claude Code; verify in spike. |
| Real OAuth token leaking to the local shim | Phase 3: local profile injects **only** `ANTHROPIC_BASE_URL` + shim token; the real token is withheld from local containers. |
| Output-token cap mis-derived for Qwen | Phase 3: profile carries an explicit `max_output_tokens` (`__post_init__` requires it for a custom backend), so it no longer hits the 64000 default. |
| Parallel fan-out (TE batches) thrashes one local GPU | Phase 3: `ModelProfile.max_concurrency`; `_te_batch_workers` clamps to it when TE → local. Mostly moot since TE stays on Claude (§6, §7a). |

---

## 10. Phasing (see `docs/plans/local-llm-routing-plan.md`)

1. **Spike** — stand up Qwen + shim, validate `claude -p` tool-use end-to-end. Gate.
2. **`ModelProfile` config** — primitive + TOML + validation + resolution (backward-compat).
3. **Backend env injection** — profile-aware auth/base_url/model/cap in the container args.
4. **Host-bridge networking** — reach the host model server through the existing egress proxy
   (one allowlist entry, default-OFF); see §7. (The originally-planned internal-only sidecar
   was dropped — pivot in §7.)
5. **Offline mode + telemetry** — `--offline` flag, degraded labeling, `backend == "local"` cost=0.

Each code phase ships **default-OFF** (no profile configured ⇒ identical to today) and is
gated by Linus + Codex review per the worktree workflow.
