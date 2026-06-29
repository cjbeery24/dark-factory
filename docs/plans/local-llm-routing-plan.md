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
    thinking field)). *(Pre-merge review: a planned `temperature: float=-1.0` field was
    DROPPED — sampling is a host/shim-server property with no claude-CLI injection path in the
    shipped host-bridge architecture, so it could never be consumed by this layer. Set it on
    the MLX/shim server config instead.)*
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

## Phase 4 — Proxy-allowlisted host bridge (default-OFF) — ✅ DONE (reviewed + Docker-validated)

> **Manual Docker checks DONE 2026-06-27 (Docker Desktop, rebuilt proxy image):** an `--internal`
> container reached a host HTTP server on a non-443 port (`host.docker.internal:11434`) THROUGH
> tinyproxy → **200**; a non-allowlisted host (`example.com`) → **403**; the baseline
> `api.anthropic.com` allowlist stayed intact (→404, not filtered); the entrypoint append is
> idempotent across `docker restart` (1 filter line). **The check CAUGHT A REAL BUG:** tinyproxy
> filters on the **bare host** for plain-HTTP proxying (strips the port), so the committed strict
> `^host:PORT$` regex was DENIED (403) — the agent would never reach the model. FIXED to the
> optional-port `^host\.docker\.internal(:\d+)?$` (the existing `api.anthropic.com` convention).
> Consequence: the port CANNOT be pinned at the tinyproxy layer for http:// — the allowlist grants
> the WHOLE host, accepted because it's the operator's own machine (loopback-equivalent). This
> empirically OVERRIDES Codex's P1 "port-pin" (false premise that tinyproxy presents `host:port`)
> and vindicates Neo's residual-risk #1. "Verify against the produced artifact, not the review
> theory." Regex fix is its own commit on the branch.


> **Review status:** Neo design-APPROVED. Codex clean (P1 strict port-pin + P2 ipaddress-loopback
> fixed & confirmed). **Linus pass DONE 2026-06-26** (succeeded on the 4th try — earlier "stalls"
> were slow-first-token + 3 genuine first-turn hangs): egress mechanism SOUND ("no unintended-host
> path"), default-OFF byte-identical confirmed, cred-loop precedence exact. But **2 BLOCKING in
> `proxy-entrypoint.sh` to fix before commit:**
>   - **BLOCKING-1 (idempotency):** the ENTRYPOINT appends on every `docker start`, and filter files
>     persist in the writable layer → a restart RE-APPENDS the regex (file grows unbounded). Make
>     idempotent: `grep -qxF` guard each line, OR truncate-then-write a sidecar filter file so a
>     restart reproduces identical bytes.
>   - **BLOCKING-2 (`set -e` silent-dead-proxy):** if a filter file is read-only/FS-quirk, the `>>`
>     fails, `set -e` aborts before `exec tinyproxy` → ALL egress dies with an unrelated-looking
>     error and zero breadcrumb. Keep fail-closed but make it LOUD: on append failure, emit a
>     `>&2` FATAL naming the file, then exit 1.
>   FIX-ITs: comment the `proxy_allow_for_profiles` tuple-aliasing (`result[net_type]` lists mutated
>   in place); delete the dead `if net_type not in result` guard; strengthen the
>   `_EXECUTION_ROLE_VALUES` source-of-truth comment (the consistency test already asserts
>   set-equality ✔). NITs: `c.isprintable()` over `ord(c)<32`; "is loopback" msg wrong for
>   `0.0.0.0` (unspecified); document the loopback check is lexical-only (doesn't resolve names);
>   make `urlsplit` import consistent (module-top vs function-local).
> **BLOCKING + FIX-ITs + NITs APPLIED** (entrypoint now idempotent via `grep -qxF` + loud
> fail-closed via FATAL-to-stderr reading from a file-redirect; dead guard deleted; aliasing
> comment; `isprintable()`; loopback msg + lexical-only doc; urlsplit module-top). New entrypoint
> shell tests: `test_idempotent_across_restarts`, `test_loud_fatal_on_unwritable_filter`,
> `test_blank_lines_in_multiline_regex_skipped`. Full suite 4830 passed / 3 skipped.
> **Linus re-confirm: ACCEPT / "ship it"** — both BLOCKING resolved, no new issues (verified the
> file-redirect-not-pipe abort semantics, `-F/-x/--` idempotency, `&&`-guarded grep vs `set -e`).
> Phase-4 code review now fully clean (Neo design + Codex + Linus).
> **STILL OWED before Phase 4 is "done": the 2 manual Docker checks** (http:// through tinyproxy to
> a non-443 port end-to-end; proxy image rebuild reachability) — need Docker + `build-images.sh` +
> a host model server. Phase 4 code is commit-ready. Phases 1-3 committed at bb7192c.


**Implementation status (2026-06-26):** code complete, Neo design-APPROVED, full unit suite
4826 passed / 3 skipped (incl. a real-shell test of the entrypoint blank-line guard). The
port-pin uses `^host(:PORT)?$` (optional port) — robust whether tinyproxy presents the filter
the host with or without the port, so it never silently denies the model regardless of that
behavior (resolves residual #1 in code rather than gating on the Docker check). **Still gating
the results doc (Neo):** (1) confirm `claude`/node honors `HTTP_PROXY` for an `http://` base_url
through tinyproxy to a non-443 port end-to-end; (2) confirm the proxy image rebuild + a local
agent actually reaches the host model. Both need Docker + `bash docker/build-images.sh` + the
host model server — deferred to a hands-on validation pass.



**Design pivot (2026-06-26):** the original "internal-only sidecar" was scoped because I wrongly
believed an `--internal` agent container couldn't reach a host-run model. Caleb corrected this:
agents already reach `api.anthropic.com` **through tinyproxy** (the proxy straddles the internal
+ external networks and enforces a hostname allowlist; `docker/filter-*.txt`,
`FilterDefaultDeny Yes`). The local model server is just **one more allowlist entry** — reached
through the SAME proxy path. The model runs on the **host** (M4 Max GPU intact); the agent
containers stay 100% `--internal` and **never touch the host** — they POST to their existing
`HTTP_PROXY` (tinyproxy) with the target host in the request, and the *proxy* (already on the
external net) resolves + connects. **No barrier relaxation, no non-internal network, no sidecar,
GPU works on Mac.** The Linux GPU-sidecar path is dropped from this phase (revisit only if a user
wants the model itself containerized).

**Barrier classification:** this touches the **egress/exfiltration** control (tinyproxy
allowlist), NOT the information barrier (test/code/review audience). So AUTHORIZATION-MODEL.md's
two triggers (audience escalation; `barriers.py`/report-row/`scenario_lint` changes) do **not**
fire — no readable-artifact or restricted-agent-read change. **BUT** egress filtering is a named
security control in CLAUDE.md's container-boundary table, so widening it is governed by the
**CLAUDE.md security model** even though AUTHORIZATION-MODEL.md doesn't gate it — "two triggers
don't fire" ≠ "no security review." Posture: operator-controlled, opt-in, one entry like
`api.anthropic.com`, **Neo design-reviewed** (APPROVE-with-revisions, folded below).

**Security rationale (Neo should-fix #8):** the widened host is `host.docker.internal` =
**host-gateway, loopback-equivalent — the operator's OWN machine**, not a third-party internet
domain. The added egress surface is materially weaker than allowlisting an external host. The
inherent trade-off: the host model server runs OUTSIDE the egress jail (full host net), so
trusting a local profile = trusting that server not to forward POSTed data — analogous to
trusting `api.anthropic.com`, but it's operator-controlled local software.

**Changes:**
- **DRY the credential-var list (Linus Phase-3 carry):** the "real Anthropic credential env
  vars" literal exists in THREE places — `config.py::_RESERVED_CRED_ENV_VARS`,
  `agents.py:~796` (real-token injection arm), and `loader.py:316 _AUTH_TOKENS`. Consolidate to
  ONE shared constant so a future Bedrock/Vertex credential can't silently desync the guard.
- `docker/Dockerfile.proxy` — entrypoint appends operator-configured allowlist regex lines from
  an env var (`DF_EXTRA_ALLOW_REGEX`, newline-joined) to BOTH baked filter files before exec'ing
  tinyproxy. `docker/filter-*.txt` stays the single source of truth; one-time proxy-image rebuild.
  (Chosen over a runtime-mounted full filter file, which would require duplicating/packaging the
  base allowlist in Python — desync risk.)
  - **MUST #1 — empty-guard (security + byte-identical):** the entrypoint MUST
    `if [ -n "${DF_EXTRA_ALLOW_REGEX:-}" ]; then printf '%s\n' "$DF_EXTRA_ALLOW_REGEX" >> "$F"; fi`.
    An unconditional append on the default path writes a BLANK line into a `FilterDefaultDeny`
    extended-regex file — an empty pattern can match-all = catastrophic allowlist failure. The
    guard makes default-OFF byte-identical in filter CONTENT (the image hash changes from the new
    entrypoint — that's fine; the behavioral invariant is what matters). Use `printf '%s\n'` (not
    `echo`) so backslashes/`$` in the regex aren't re-interpreted.
  - **MUST #5 — stale-image failure mode:** an old `dark-factory-proxy:latest` ignores
    `DF_EXTRA_ALLOW_REGEX` → `FilterDefaultDeny` silently denies the host → confusing "model
    unreachable." Document the rebuild requirement + symptom in the plan/results; consider a cheap
    startup sanity log line ("DF_EXTRA_ALLOW_REGEX set but entrypoint is the pre-Phase-4 image").
- **New** `darkfactory/docker/local_llm.py` —
  `proxy_allow_for_profiles(config) -> (regex_lines, hosts)`: for each local profile that routes
  through the proxy, emit the allowlist regex + bare host for `--add-host`. Pure/unit-testable.
  - **MUST #2 — anchor + escape + reject metacharacters:** the regex is
    `^` + `re.escape(host)` + `:` + `re.escape(port)` + `$` (port-pinned, see SHOULD #6). REJECT
    (ConfigError) any base_url host containing newline/whitespace/control chars BEFORE it reaches
    the file — newline injection = arbitrary extra allowlist lines. Adversarial test: `host\nevil.com`.
  - **MUST #6 (promoted from should) — port-pin the regex:** emit `^host\.docker\.internal:PORT$`
    (the model's actual port), NOT `(:\d+)?` — the latter grants the agent EVERY host port (SSH
    etc.). The Host header always carries the non-default port, so the pinned form matches.
    (Verify in the manual Docker check that tinyproxy presents `host:port` to the filter.)
- **`ModelProfile` / config validation (MUST #3 + #4):**
  - **MUST #3 — loopback is an error, not a silent skip:** a host-bridge `base_url` of
    `localhost`/`127.0.0.1` makes an internal agent connect to ITSELF and silently fail. Validate:
    a custom-backend `base_url` host must be the host bridge (`host.docker.internal`), not loopback
    → ConfigError with an actionable message.
  - **MUST #4 — enforce `http://` scheme (the `ConnectPort 443` precondition):** an
    `https://host.docker.internal:PORT` base_url would CONNECT-tunnel to a non-443 port, which
    `ConnectPort 443` BLOCKS. A host-bridge profile MUST use `http://` (or the operator must widen
    `ConnectPort`) → validate the scheme, ConfigError otherwise. This is a hard precondition, not
    a footnote.
- `darkfactory/docker/networks.py` (`setup_proxies`) — gains `extra_allow_regex: list[str]` and
  `add_hosts: list[str]`. When set: pass `-e DF_EXTRA_ALLOW_REGEX="<lines>"` and
  `--add-host host.docker.internal:host-gateway` (PROXY resolves the host; auto on Docker Desktop,
  required on Linux). Empty args ⇒ byte-identical `docker run`.
  - **SHOULD #7 — execution-network widening:** appending to BOTH filter files also opens the host
    on the `execution` network (implementer's built code + blind scenarios run there). If feasible,
    scope the append to only net_types whose assigned roles have a local profile; at MINIMUM state
    it as an accepted widening (adversarial built/test code could reach the host model port).
- `darkfactory/pipeline/orchestrator.py` (`:454-455`) — compute the extra-allow set from config
  and thread it into `setup_proxies` when local profiles are configured.
- **NO change to `NO_PROXY`** — the agent MUST go through the proxy to reach the host (an
  internal container can't reach the host directly); a NO_PROXY entry would (wrongly) tell it to
  connect directly and fail. This REVERSES the original sidecar plan (Neo-confirmed correct).

**Tests:** `tests/unit/test_local_llm_proxy.py` (new) — `proxy_allow_for_profiles` emits correct
port-pinned escaped regex + host; loopback/https/bad-host raise ConfigError; metacharacter
injection (`host\nevil.com`) rejected; `setup_proxies` dry-run includes `-e DF_EXTRA_ALLOW_REGEX`
+ `--add-host` only when extra hosts present; byte-identical proxy `docker run` args when no local
profile. Entrypoint empty-guard covered by a shell assertion (`bash`-level) or documented manual check.
**Verify:** `pytest tests/unit/test_local_llm_proxy.py tests/unit/test_networks*.py -q`.
**AC:** AC-4 (agents stay `--internal`; egress widened by exactly one operator-named host:port).
**Residual risk (validate when Docker available):** `claude`/node honors `HTTP_PROXY` for an
`http://` base_url through tinyproxy to a non-443 port (plain HTTP proxying, not CONNECT — should
work; `ConnectPort 443` only restricts HTTPS tunnels). Confirm end-to-end + that tinyproxy filters
on `host:port` (enables the port-pin). Note in results until confirmed.

---

## Phase 5 — Offline mode + telemetry/cost (default-OFF) — ✅ DONE (Linus SHIP + Codex clean)

Shipped: `--offline` flag (`apply_offline_mode` clobbers `agent_model_profiles` to the sole
local profile; errors on zero/ambiguous); telemetry `backend` field (schema v3, additive) →
scorecard prices a local line at **$0-known** (fixes the bug where one local agent voided a whole
hybrid run's cost) + `local_invocations` in RunUsage/ScorecardRow (the single, telemetry-derived
degradation signal). Reviewer-hardened: deleted the set-but-dead `config.offline`/`config.degraded`
fields (Linus BLOCKING + Codex P3 — persisted-but-not-consumed); threaded `is_local` from one
dispatch resolution (Linus FIX-IT). `tests/unit/test_offline_mode.py` + telemetry backend-field
test; full suite 4840 passed / 3 skipped.

> **FILED FOLLOW-UP (Linus Phase-5 condition):** consolidate the THREE independent
> `resolve_model_profile(role, config)` calls per agent invocation (telemetry in `invoke_agent`;
> env injection in `_build_full_agent_docker_args`; `--model` in `build_claude_args`) into ONE
> resolution threaded through `_build_full_agent_docker_args` + `build_claude_args` +
> `start_agent_session`. Currently provably-equal (pure fn + immutable config per-invocation, with
> an invariant comment at the `invoke_agent` site); consolidate to make it same-object rather than
> faithful-by-determinism. Not a correctness bug — a DRY/latent-drift cleanup.

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
