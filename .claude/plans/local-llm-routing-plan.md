# Plan: Local-LLM Routing (feat/local-llm-routing)

**Design doc:** `docs/plans/local-llm-routing-design.md`
**Workflow:** worktree → plan → neo APPROVE → per-phase implement (verify + Linus + Codex) → wrap.
**Verify command (this repo, Python — not dotnet):** `pip install -e ".[dev]"` then
`pytest tests/unit/<file> -q`; broader regression `pytest tests/unit -q`.
**Invariant across all code phases:** default-OFF. No profile configured ⇒ byte-for-byte
today's behavior (existing `[model.ids]` keeps working).

### Acceptance criteria (from the goal)
- **AC-1 Global model/backend** — one config switch sets the default for every agent.
- **AC-2 Per-agent/per-phase** — each role pinnable to a distinct backend.
- **AC-3 Hybrid run** — Claude + local agents in the *same* run, no cross-contamination of
  auth (real token never reaches a local container).
- **AC-4 Barrier-neutral** — no directory-mask change, no Reviewer→Implementer signal
  promotion (per `docs/AUTHORIZATION-MODEL.md`).
- **AC-5 Offline** — `--offline` forces all-local and labels the run degraded.
- **AC-6 Concurrency-safe** — parallel fan-out (TE batches) clamps to a local profile's
  capacity instead of thrashing one GPU.

---

## Phase 1 — Shim spike (DE-RISK GATE; no production code) — ✅ DONE: CONDITIONAL GO

**Outcome (see `docs/plans/local-llm-spike-results.md`):** core assumption VALIDATED —
`claude -p` → ccr → Ollama → `qwen3-coder:30b` does full multi-turn agentic tool-use (clean
Write→Bash→8484, 3 turns; ~94 tok/s; 20 GB resident on 48 GiB). Three config gotchas found +
fixed (thinking-disable; remove `enhancetool`; nested-sandbox is harness-only, proven via an
Opus control). **Real risk:** ~17% tool-call-format leak rate even at temp 0 → keep the
verification spine on Claude; a leak-recovery parser gates a trustworthy local Implementer.
Findings are folded into Phases 2–3 below.

**Goal:** prove `claude -p` sustains multi-turn tool-use + stream-json through a shim in
front of Qwen3-Coder-30B-A3B before any plumbing is trusted.

**Work (mostly a runbook + scripts; the heavy model download/serve runs on the user's M4 Max):**
- `docs/plans/local-llm-spike-runbook.md` — exact steps: pull `qwen3-coder:30b` (4-bit) via
  Ollama/LM-Studio; start the shim (claude-code-router **or** LiteLLM) exposing `/v1/messages`;
  set `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`.
- `scripts/spike/local_llm_smoke.sh` — drives a throwaway `claude -p` (OUTSIDE the pipeline)
  through a scripted task exercising: Bash tool, Read, Write, a multi-turn edit, and a
  `--disallowedTools` deny. Captures stream-json to a log.
- `docs/plans/local-llm-spike-results.md` — filled in after the run: PASS/FAIL per capability,
  tok/s, peak memory, chosen quant, chosen shim.

**Files:** new files only (docs + scripts/spike/). No `darkfactory/` change.
**Tests:** none (manual spike). The smoke script IS the test.
**AC:** gates AC-3/AC-4 viability (if tool-use fails through the shim, the feature is blocked).
**Risks:** I (Claude Code) provide the harness; the user runs the 17 GB download + serve. If
the environment permits, drive via Bash. **STOP-AND-REASSESS if the smoke fails.**

---

## Phase 2 — `ModelProfile` config primitive (default-OFF)

**Goal:** AC-1, AC-2 at the config layer. Backward-compatible with `[model.ids]`.

**Changes:**
- `darkfactory/models/config.py`
  - New `@dataclass ModelProfile` (fields: `base_url: str=""`, `auth_token_env: str=""`,
    `model: str=""`, `max_output_tokens: int=0`, `family: str=""`, `max_concurrency: int=0`,
    **`disable_thinking: bool=True`** (Phase-1 finding: non-thinking local backends 400 on the
    thinking field), **`temperature: float=-1.0`** (−1 = leave default; lets a local profile
    lower temp to improve tool-format adherence — the ~17% leak lever)).
  - `PipelineConfig.model_profiles: dict[str, ModelProfile] = field(default_factory=dict)`
    (near `model_ids`, `:456`).
  - `PipelineConfig.agent_model_profiles: dict[str,str] = field(default_factory=dict)`
    (role.value → profile name; mirrors `agent_timeout_overrides`).
  - `__post_init__` validation (mirror `:558-572`): profile name non-empty; `max_output_tokens`
    / `max_concurrency` int ≥ 0; `family` free string; **warn** (not fail) on an
    `agent_model_profiles` role not in `AgentRole`; **fail** on a mapping pointing at an
    undefined profile name.
  - **Reserved name `"default"` (Neo rev-5):** `"default"` is the implicit Anthropic profile
    (built from `model_id` + real auth + family-derived cap). A user `[model.profiles.default]`
    table is **rejected** with an actionable error ("configure the default backend via `[model]`
    `id`/`max_output_tokens`, not a `default` profile") — avoids two sources of truth for the
    default. Test pins both: implicit `"default"` always resolves; explicit `default` profile
    errors.
  - **Custom-backend cap is mandatory (Neo rev-3):** any profile with a non-empty `base_url`
    (i.e. a non-Anthropic backend) MUST set `max_output_tokens > 0`. Validation failure
    otherwise — removes the ambiguity where `family="local"` would silently fall through to the
    64000 sonnet/haiku default in `resolve_max_output_tokens` and ship a wrong cap for Qwen.
- `darkfactory/config/loader.py`
  - Parse `[model.profiles.<name>]` tables (after `[model.ids]`, `:73-78`) → `ModelProfile`s.
  - Parse `[agents.models]` (after `[agents.timeout_overrides]`, `:84-88`) → `agent_model_profiles`.
- `darkfactory/config/defaults.py` — no SECTION_MAP change needed (dynamic-key tables handled
  in loader, like `model.ids`); document the two new tables in the config reference.
- **New** `resolve_model_profile(role, config, override="") -> ModelProfile` (in
  `models/config.py` or a small `models/profiles.py`): precedence override →
  `agent_model_profiles[role.value]` → implicit `"default"` profile (built from `model_id` +
  real auth + family-derived cap). **Single source of truth** consumed by Phase 3's two sites.

**Tests:** `tests/unit/test_model_profiles.py` (new) — parse, validation (good/bad), resolution
precedence, backward-compat (`model_ids` still yields `--model` with default backend), implicit
default profile.
**Verify:** `pytest tests/unit/test_model_profiles.py tests/unit/test_config*.py -q`.
**AC:** AC-1, AC-2.
**Risks:** config-surface creep; keep `ModelProfile` minimal. Backward-compat regression →
the config test suite is the guard.

---

## Phase 3 — Backend env injection + concurrency clamp (default-OFF) — ✅ DONE (Linus + Codex clean)

Shipped: profile-aware credential injection (local arm first/mutually-exclusive, real token
withheld; thinking-disable env; profile-derived cap), `--model` + telemetry `resolved_model`
via `resolve_model_profile`, TE `max_concurrency` hard-ceiling clamp. Reviewer hardening:
Codex P1 → `ModelProfile` rejects `auth_token_env ∈ {CLAUDE_CODE_OAUTH_TOKEN, ANTHROPIC_API_KEY}`
for custom backends (`_RESERVED_CRED_ENV_VARS`); Linus FIX-IT → telemetry attributes local
agents to their real model. `tests/unit/test_backend_injection.py` + resolver tests; full
suite 4807 passed / 3 skipped.

**Goal:** AC-3, AC-4, AC-6. Make the container actually talk to the resolved backend.

**Changes:**
- `darkfactory/pipeline/agents.py`
  - `_build_full_agent_docker_args` (`:767-780`): replace the bare auth block with
    profile-aware injection via `resolve_model_profile(role, config, model_override)`.
    **Structural mutual-exclusion (Neo rev-1)** — the local arm is ordered FIRST and returns
    its own credential set; it must NOT fall through to the real-token arms:
    ```
    if profile.base_url:           # local/custom backend
        inject ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN (shim) ONLY
    elif token:    inject CLAUDE_CODE_OAUTH_TOKEN
    elif api_key:  inject ANTHROPIC_API_KEY
    ```
    Fail-fast with an actionable error if `auth_token_env` is set but unset in the environment.
    - **Thinking-disable (Phase-1 finding):** when `profile.disable_thinking` (default True for a
      custom backend), also inject `MAX_THINKING_TOKENS=0` + `DISABLE_INTERLEAVED_THINKING=1` in
      the local arm — without these Ollama 400s a non-thinking model. (`CLAUDECODE` /
      `CLAUDE_CODE_ENTRYPOINT` nesting is NOT relevant here — the pipeline's Docker containers
      are not nested Claude sessions; that gotcha was spike-harness-only.)
    - `max_output_tokens` derived from the profile: pass `profile.max_output_tokens` as the
      existing `override` arg to `resolve_max_output_tokens`. Since Phase 2 validation forces a
      custom-backend profile to set a positive cap, the local case never falls through to the
      family default (Neo rev-3).
  - `resolved_model` at `:922` and the cap derivation at `:778` consume the profile's `model`.
  - **ALIASING TRAP (Linus, Phase-2 review carry):** `resolve_model_profile` returns the
    **shared** `ModelProfile` object stored in `config.model_profiles` (pinned by
    `test_resolve_returns_shared_profile_object`). The injection MUST apply a call-time
    `model_override` to a **copy** (`dataclasses.replace(profile, model=override)`) or a local
    variable — **never** by assigning `profile.model = override` on the resolved object, which
    would silently rewrite the config profile for every other agent resolving to that same
    named profile. Add an aliasing regression test in Phase 3.
- `darkfactory/docker/args.py` (`build_claude_args`, `:219-221`): `--model = profile.model`
  via the same resolver (not `model_ids.get` directly), so the two sites can never diverge.
- `darkfactory/pipeline/orchestrator.py` (`_te_batch_workers`, `:3665-3678`): **resolve inside
  the function** via `resolve_model_profile(AgentRole.TEST_ENGINEER, config)` — no fictional
  `role` arg (this path is TE-specific). The clamp is a **hard ceiling layered AFTER** the
  existing `scenario_batch_max_workers` override (`:3676-3677`): `result = min(result,
  profile.max_concurrency)` when `max_concurrency > 0`. So a local `max_concurrency=1` **wins
  over** a user's `scenario_batch_max_workers=4` (else the override re-thrashes the GPU) —
  Neo rev-2. Default profile has no cap → unchanged. (QA Holdout stays on Claude per design §6,
  so its nested parallelism is not a local concurrency vector — noted, not wired.)

**Tests:** `tests/unit/test_backend_injection.py` (new) —
- default profile → real token, no `ANTHROPIC_BASE_URL`.
- local profile → `ANTHROPIC_BASE_URL` + shim token present, **real token absent**.
- missing `auth_token_env` value → ConfigError/clear raise.
- `--model` and `CLAUDE_CODE_MAX_OUTPUT_TOKENS` reflect the profile.
- `_te_batch_workers` clamps under a capacity-limited local TE profile.
**Verify:** `pytest tests/unit/test_backend_injection.py tests/unit/test_agents*.py tests/unit/test_docker_args*.py -q`.
**AC:** AC-3, AC-4 (assert no barrier/mask code touched), AC-6.
**Risks:** **auth leak is the high-stakes item** — the "real token absent for local" test is
mandatory and Linus/Codex must confirm it. Barrier-neutrality: this phase touches only env
vars + worker count, not `barriers.py`/`mounts.py` — call that out explicitly for review.

---

## Phase 4 — Sidecar networking (default-OFF)

**Goal:** make the local endpoint reachable from masked containers without weakening barriers.

**Changes:**
- **DRY the credential-var list (Linus Phase-3 carry):** the "real Anthropic credential env
  vars" literal now exists in THREE places — `config.py::_RESERVED_CRED_ENV_VARS`,
  `agents.py:~796` (the real-token injection arm), and `loader.py:316 _AUTH_TOKENS`. Consolidate
  to ONE shared constant so a future Bedrock/Vertex credential can't silently desync the
  auth-leak guard. Do this here before the sidecar work adds more credential surface.
- `darkfactory/docker/networks.py` — optional `setup_local_llm_sidecar(run_id, config)`:
  start the shim container with `--network-alias df-local-llm`, attached **only** to the
  internal `analysis` + `execution` networks (**never** `external`). Gated by a profile flag
  (`sidecar = true`) — if false, assume the user runs the shim themselves (host bridge escape
  hatch, off by default).
- **Teardown ordering (Neo rev-4):** the sidecar is a *container* attached to internal nets,
  so it must be removed **before** `cleanup_networks` runs `docker network rm` (an attached
  container blocks net removal — same reason proxy teardown is its own function called before
  `cleanup_networks`). Add `teardown_local_llm_sidecar` and register it with the run's
  `cleanup` handler (`orchestrator.py:~450`) so an aborted/crashed run does not leak the
  container or the model held in GPU memory.
- `darkfactory/docker/args.py` (`build_proxy_env_args`, `:34`): add the local-LLM alias to
  `NO_PROXY` so agents reach it directly, not through tinyproxy.
- `darkfactory/pipeline/orchestrator.py` (`:454-455`): call the sidecar setup alongside
  `setup_networks`/`setup_proxies` when a local profile is active.

**Tests:** `tests/unit/test_local_llm_sidecar.py` (new) — sidecar attaches to internal nets
only (assert `external` NOT in the attach list); `NO_PROXY` contains the alias; no-op when no
local profile configured.
**Verify:** `pytest tests/unit/test_local_llm_sidecar.py tests/unit/test_networks*.py -q`.
**AC:** AC-4 (sidecar is internal-only ⇒ *stricter* egress, not looser).
**Risks:** networking is barrier-adjacent — the "internal-only, never external" assertion is
the security crux for Linus/Codex. GPU passthrough into a sidecar container on macOS is
limited; the host-bridge escape hatch covers that case (documented, opt-in).

---

## Phase 5 — Offline mode + telemetry/cost (default-OFF)

**Goal:** AC-5; correct accounting for local agents.

**Changes:**
- `darkfactory/cli.py` — `--offline` flag → sets every role's `agent_model_profiles` to the
  configured local profile (or errors if none defined) and marks the run degraded.
- Scorecard/run-report — a `degraded: true` / `backend: local` label so a local-degraded ship
  is never silently compared against a Claude ship.
- `darkfactory/intelligence/cost.py` (MODEL_RATES) — `family="local"` → rate 0; ensure the
  scorecard does not divide by Anthropic pricing for local agents (token counts still recorded).

**Tests:** `tests/unit/test_offline_mode.py` (new) — `--offline` maps all roles to local;
errors with no local profile; run labeled degraded; `cost.py` returns 0 for `family="local"`.
**Verify:** `pytest tests/unit/test_offline_mode.py tests/unit/test_cost*.py -q`.
**AC:** AC-5.
**Risks:** Implementer still needs npm/pypi for installs — document that "offline" = "no
Anthropic" not "air-gapped" unless deps are vendored.

---

## Cross-cutting / out of scope
- **Not** an authorization-model change (no mask/barrier promotion). Phases 3–4 explicitly
  call this out for review.
- A real Qwen-as-Implementer **ship-rate A/B** is follow-up empirical work, not a code phase
  (public benchmarks don't transfer — measure through the real gate).
- Each phase merges default-OFF; the feature is inert until a profile is configured.
