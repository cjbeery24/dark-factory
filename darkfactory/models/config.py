"""PipelineConfig dataclass — replaces ~60 bash global variables."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from darkfactory.errors import ConfigError

if TYPE_CHECKING:
    from darkfactory.models.agent import AgentRole

log = logging.getLogger(__name__)

_DNS_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


@dataclass
class ServiceDef:
    """A service container required during quality gate execution (e.g., Postgres)."""

    name: str               # Network alias (e.g., "postgres")
    image: str              # Docker image (e.g., "postgres:16-alpine")
    env: dict[str, str] = field(default_factory=dict)
    health_cmd: str = ""    # e.g., "pg_isready -U postgres"
    health_timeout: int = 30
    inject_env: dict[str, str] = field(default_factory=dict)
    # inject_env vars are passed to the SERVER container, not this service
    depends_on: list[str] = field(default_factory=list)
    # Phase 4.3: topological startup ordering. Each entry is a service name
    # that must be healthy before this service starts.

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("service name must be non-empty")
        if not self.image:
            raise ConfigError(f"service '{self.name}' must have an image")
        if not _DNS_LABEL_RE.match(self.name):
            raise ConfigError(
                f"service name '{self.name}' must be DNS-safe "
                "(lowercase alphanumeric + hyphens)"
            )


# The real Anthropic credential env vars, in injection-PRECEDENCE order (the
# default backend injects the first one present). SINGLE SOURCE OF TRUTH — also
# consumed by pipeline/agents.py (default-backend injection) and config/loader.py
# (bash-migration auth handling). Adding a credential here (e.g. a future Bedrock
# token) updates the auth-leak guard AND the injection in lockstep, so they can't
# silently desync (Linus Phase-3 carry).
REAL_ANTHROPIC_CRED_ENV_VARS: tuple[str, ...] = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
)
# A custom-backend profile must NOT name any of these as its auth_token_env (else
# the local arm would read + re-emit the real token into the local container —
# Codex Phase-3 P1).
_RESERVED_CRED_ENV_VARS = frozenset(REAL_ANTHROPIC_CRED_ENV_VARS)

# 127.x inet_aton shorthand (e.g. "127.1", "127.0.1") that ipaddress.ip_address
# does NOT parse but the OS resolver treats as 127.0.0.x loopback.
_DOTTED_127_SHORTHAND = re.compile(r"^127(\.\d{1,3}){1,3}$")


def _is_loopback_host(host: str) -> bool:
    """True if ``host`` is loopback/unspecified in any spelling (Phase-4 MUST #3,
    Codex P2). Catches ``localhost``, the 127.0.0.0/8 + ::1 + 0.0.0.0 IP ranges in
    every valid form (incl. expanded IPv6 ``0:0:0:0:0:0:0:1`` and ``127.0.1.1``),
    and the ``127.x`` inet_aton shorthand that ``ipaddress`` won't parse.

    An --internal agent container pointed at loopback connects to ITSELF and
    silently fails to reach the host model server, so these must be rejected.
    """
    import ipaddress
    h = (host or "").lower()
    if h == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_loopback or ip.is_unspecified
    except ValueError:
        return bool(_DOTTED_127_SHORTHAND.match(h))


@dataclass
class ModelProfile:
    """A named backend profile — *where* an agent's ``claude -p`` sends requests.

    Generalizes the per-role ``model_ids`` string into a full backend bundle so a
    hybrid run can route some agents to Anthropic and others to a local LLM in the
    same run. The implicit ``"default"`` profile is the Anthropic-hosted backend
    (built from ``PipelineConfig.model_id`` + the real OAuth/API token); it is NOT
    represented here and an explicit ``[model.profiles.default]`` is rejected.

    A profile with a non-empty ``base_url`` is a *custom backend* — e.g. a local
    MLX-LM/Ollama model behind a claude-code-router shim, reached by injecting
    ``ANTHROPIC_BASE_URL``. See ``docs/plans/local-llm-routing-design.md`` and the
    Phase-1 spike (``docs/plans/local-llm-spike-results.md``). This dataclass only
    describes the backend; the env injection itself is Phase 3 (agents.py).
    """

    base_url: str = ""          # ANTHROPIC_BASE_URL; empty => Anthropic default endpoint
    auth_token_env: str = ""    # NAME of the env var holding the backend's token (never the value)
    model: str = ""             # --model value; empty => inherit PipelineConfig.model_id
    max_output_tokens: int = 0  # CLAUDE_CODE_MAX_OUTPUT_TOKENS; REQUIRED (>0) for a custom backend
    # Reserved cost-domain tag — NOT currently consumed. Cost-zeroing derives from
    # backend/base_url (scorecard) and per-model rates key off `model`; this is kept
    # as a forward-compat knob a future cost.py rate-override can read in-process.
    family: str = ""
    max_concurrency: int = 0    # 0 = unbounded; caps parallel fan-out (e.g. TE batches) on a capacity-limited local box
    disable_thinking: bool = True   # custom backends: inject MAX_THINKING_TOKENS=0 (a non-thinking local model 400s on the thinking field — Phase-1 finding)

    def __post_init__(self) -> None:
        # String fields: reject non-strings (e.g. a TOML bool/int). A truthy
        # non-str base_url would otherwise be classified as a custom backend with a
        # bogus URL; a non-str disable_thinking would mis-gate the Phase-3 thinking
        # injection. Validate here so the asymmetry (paranoid on ints, blind on the
        # rest) does not let a fat-fingered TOML through. (Linus FIX-IT, Codex #1.)
        for _fname in ("base_url", "auth_token_env", "model", "family"):
            _val = getattr(self, _fname)
            if not isinstance(_val, str):
                raise ConfigError(
                    f"model profile {_fname} must be a string, got {_val!r}"
                )
        if type(self.disable_thinking) is not bool:
            raise ConfigError(
                f"model profile disable_thinking must be a bool, got "
                f"{self.disable_thinking!r}"
            )
        # type() (not isinstance) so a TOML bool — an int subclass — is rejected,
        # mirroring agent_timeout_overrides validation.
        if type(self.max_output_tokens) is not int or self.max_output_tokens < 0:
            raise ConfigError(
                f"model profile max_output_tokens must be a non-negative int, "
                f"got {self.max_output_tokens!r}"
            )
        if type(self.max_concurrency) is not int or self.max_concurrency < 0:
            raise ConfigError(
                f"model profile max_concurrency must be a non-negative int "
                f"(0 = unbounded), got {self.max_concurrency!r}"
            )
        # A custom backend (non-empty base_url) MUST carry an explicit positive
        # output cap. Otherwise resolve_max_output_tokens (Phase 3) falls through to
        # the 64000 sonnet/haiku family default — wrong for a local model. (Neo rev-3.)
        if self.base_url and self.max_output_tokens <= 0:
            raise ConfigError(
                f"model profile with base_url={self.base_url!r} must set "
                "max_output_tokens > 0 (a custom backend has no known family default)"
            )
        # Auth-leak guard (Codex P1): a custom backend's auth_token_env must NOT
        # alias a real Anthropic credential variable. Otherwise the Phase-3 local
        # arm would read the real token from os.environ and re-emit it INTO the
        # local container as ANTHROPIC_AUTH_TOKEN — defeating the "real token never
        # reaches a local backend" guarantee via config. Use a distinct shim-token env.
        if self.base_url and self.auth_token_env in _RESERVED_CRED_ENV_VARS:
            raise ConfigError(
                f"model profile auth_token_env must not be a real Anthropic "
                f"credential variable ({self.auth_token_env!r}) — a custom backend "
                "reads it and would leak the real token into the local container; "
                "use a distinct shim-token env var"
            )
        # base_url format validation (Phase-4 host-bridge preconditions). The host
        # is appended to the egress proxy's allowlist, so it must be safe + reachable.
        if self.base_url:
            # MUST #2: reject whitespace/non-printable BEFORE parsing — a newline in
            # base_url would inject an arbitrary extra line into the proxy filter
            # file. ``not c.isprintable()`` covers C0/C1 controls + DEL (cleaner and
            # more complete than ``ord(c) < 32``); ``isspace`` adds the printable
            # space.
            if any(c.isspace() or not c.isprintable() for c in self.base_url):
                raise ConfigError(
                    f"model profile base_url contains whitespace/control characters: "
                    f"{self.base_url!r}"
                )
            try:
                _parsed = urlsplit(self.base_url)
            except ValueError as e:
                raise ConfigError(
                    f"model profile base_url is not a valid URL ({self.base_url!r}): {e}"
                ) from e
            # MUST #4: tinyproxy's ConnectPort 443 blocks an https CONNECT to a
            # non-443 port; a host-bridge backend is reached by plain-HTTP proxying,
            # so require http://.
            if _parsed.scheme != "http":
                raise ConfigError(
                    f"model profile base_url must use http:// (got "
                    f"{_parsed.scheme or '<none>'}://) — the egress proxy plain-HTTP-"
                    "proxies a custom backend; an https CONNECT to a non-443 port is "
                    "blocked by tinyproxy's ConnectPort 443"
                )
            _host = _parsed.hostname or ""
            if not _host:
                raise ConfigError(
                    f"model profile base_url has no host: {self.base_url!r}"
                )
            # MUST #3: loopback/unspecified → an internal agent connects to itself,
            # silently dead. NOTE: this check is LEXICAL only (it does not resolve
            # DNS) — it stops the agent container's OWN loopback, not an operator who
            # names a host that happens to resolve to loopback; that's the operator's
            # choice and not a security boundary here.
            if _is_loopback_host(_host):
                raise ConfigError(
                    f"model profile base_url host {_host!r} is loopback/unspecified — "
                    "an internal agent container would connect to itself; use the host "
                    "bridge (host.docker.internal) so the egress proxy reaches the host "
                    "model server"
                )


@dataclass
class PipelineConfig:
    """All pipeline configuration in one typed structure."""

    # Core
    language: str = "node"
    package_manager: str = ""
    source_dirs: list[str] = field(default_factory=lambda: ["src"])
    test_dir: str = "tests"
    test_cmd: str = ""
    scenario_test_cmd: str = ""
    dark_factory_home: str = "dark-factory"

    # Agents
    agent_max_turns: int = 200
    agent_timeout: int = 900
    # L2 (bb spec-4 post-mortem): per-role timeout override in seconds, keyed by
    # role value (e.g. {"test-engineer": 1800}). Empty default → every role uses
    # agent_timeout. Justified by the measured cause: per-turn extended-thinking
    # latency is intrinsic+systemic, and the test-engineer needs more turns
    # (69–109) than fit in 900s on a heavy spec, while analysis agents (33–62
    # turns) finish in 480–794s. Loaded from [agents.timeout_overrides] in TOML.
    agent_timeout_overrides: dict[str, int] = field(default_factory=dict)
    agent_retries: int = 1
    agent_memory: str = "4g"
    agent_cpus: str = "4"
    agent_io_device: str = ""
    agent_io_read_bps: str = ""
    agent_io_write_bps: str = ""

    # Gates
    gate_timeout: int = 300
    # Per-scenario-gate timeout in seconds. 0 = use gate_timeout. Decouples scenario execution time budget from build/lint/test gates.
    scenario_timeout: int = 0
    hard_gate_build: bool = False
    preflight_enabled: bool = True
    # Phase 1.2: cap implementer re-entry at 1 retry per category. Rationale:
    # if 200 + 200 = 400 turns can't move the needle, the next 200 won't either.
    # Iterate at the iteration boundary instead of re-launching from scratch.
    # Config can override upward for debugging, but the default is "one retry."
    max_pre_gate_retries: int = 3  # pre-gate retries before falling through to full gate (3 = 3 checks + 2 fix attempts)
    max_smoke_retries: int = 1  # server smoke retries before falling through to full gate
    min_ship_pass_rate: int = 80
    validate_scenarios: str = "strict"
    scenario_min_files: int = 3
    scenario_min_assertions_per_file: int = 1
    scenario_max_assertions_per_file: int = 30
    sa_max_retries: int = 2
    # Bounded re-invocations of the Test Engineer when it exits 0 but writes NO
    # scenario files (and no sentinel) — a transient agent no-op (observed
    # intermittently on python-fastapi). Distinct from sa_max_retries
    # (validation-failure regen) and coverage_max_regen_attempts (coverage-gap
    # regen): this is the "produced nothing at all" class. After exhaustion the
    # step aborts with the same error, preserving an always-empty signal.
    scenario_empty_retry_attempts: int = 2
    # F Move 2c (2026-06-16): run the TE scenario-gen empty/misnamed-retry loop
    # in a WARM persistent container so a retry resumes the same Claude session
    # via `claude --continue` (mirrors the implementer S1 pre-gate loop) and the
    # TE fixes its output in-context (e.g. renames misnamed files) instead of a
    # cold clear-and-regenerate that re-derives everything and can inject a new
    # defect. Degrades gracefully to the cold path if the session container
    # fails to start. Kill-switch — set False to force the legacy cold behavior.
    scenario_warm_session: bool = True
    # F Move 2b (2026-06-17): run the Test Engineer and QA Holdout scenario-
    # generation agents CONCURRENTLY instead of serially. They are independent
    # (each reads only the spec; the TE container masks .dark-factory/holdouts/
    # and the QA Holdout container masks .dark-factory/scenarios/; they write
    # disjoint dirs), so this saves ~10 min on a ~60-min run. This is the FIRST
    # intra-spec concurrency. Shipped DEFAULT OFF behind a flag; flipped to ON
    # after spec-4 (database-foundation) SHIPPED end-to-end on the merged tree
    # (roll-forward by config flip, not code revert — same posture as
    # te_pre_acceptance_check / sast_compare_enabled). When OFF (set per-project),
    # the path is byte-for-byte the serial flow (QA then TE, no thread).
    # dry_run also forces serial. Runs two 4 GB/4-CPU agent containers at once.
    scenario_parallel_generation: bool = True
    # P1 TE-decomposition (2026-06-24) — bound the Test Engineer's per-turn
    # reasoning scope to avoid the monolithic generate turn exceeding the model's
    # max output tokens (run15: one ~16.5K-token think = the cap-overflow that
    # blocked the campaign). When > 0, the orchestrator splits the anchor manifest
    # (AC/FR/EC ids) into batches of this size and invokes the TE once per batch
    # (sequential, cold) so each turn only reasons over K anchors; files accumulate
    # into the same scenarios_dir and the existing SA-validate + coverage floor run
    # unchanged over the assembled suite. 0 = DISABLED = byte-for-byte the current
    # monolithic single-invocation path (default; roll-forward by config flip, same
    # posture as scenario_parallel_generation). Does NOT enumerate sub-anchor
    # Scenario-Intents — decomposition is AC/FR/EC granularity only.
    # NOTE: batches run sequentially cold, so wall time scales ~N×(per-role TE
    # timeout) in the worst case; size K so each batch comfortably fits one turn.
    # Requires coverage_floor_enabled=True (the floor backstops empty/misnamed/
    # clobbered batches — enforced in __post_init__).
    scenario_batch_size: int = 0
    # S2 (2026-06-26) — when scenario_batch_size > 0 produces > 1 batch, run the
    # batches CONCURRENTLY (ThreadPoolExecutor fan-out) instead of sequentially.
    # Each batch is still a COLD <=K-anchor invocation (per-turn thinking unchanged
    # — no mega-think regression); parallelism changes only wall-clock, collapsing
    # the gen stage from Sigma(batches) to ~max(batch) (~2.2x median; see
    # docs/dev/2026-06-25-te-batch-parallelization.md). Mirrors the proven
    # scenario_parallel_generation posture (default OFF; dry_run and single-batch
    # force serial; the flag-OFF path is byte-for-byte the sequential loop). Each
    # cold batch container reserves 4 GB/4 CPU like every agent — see
    # scenario_batch_max_workers for the resource-derived concurrency cap.
    scenario_batch_parallel: bool = False
    # Concurrency cap for scenario_batch_parallel. 0 = DERIVE: the CONSERVATIVE
    # min(n_batches, 2) — the TE+QA path has validated exactly 2 concurrent
    # 4 GB/4 CPU agent containers in production, and with scenario_parallel_generation
    # ON the nested QA Holdout reserves one more slot (2 batch-workers + 1 QA = 3
    # containers is already at the edge of a typical 16 GB host). A positive value
    # OVERRIDES the derivation (clamped to n_batches) for a provisioned host whose
    # headroom is min(cpu//4, mem_gb//4) minus the QA reserve.
    scenario_batch_max_workers: int = 0
    # P2 Validator-Bridge (2026-06-24, d2fecbf-class barrier change, default OFF).
    # When OFF the blind-holdout NO-SHIP feedback is byte-identical to today's fixed
    # BINARY string ("Blind adversarial validation failed"). When ON, the gate
    # additionally relays one templated, spec-anchor-only line per failing holdout
    # whose author-declared spec_anchor RESOLVES to a real anchor in the locked spec,
    # built BY TEMPLATE from the QA-Holdout-authored manifest
    # (.dark-factory/holdouts/<spec>/holdout-bridge.json), never by parsing test
    # output/source. The relay decision is SOLELY anchor-resolution (no category gate,
    # no security-domain screen): adversarial/security holdouts anchor to the
    # requirement whose robustness they probe and relay as an anchor pointer; truly-
    # unanchorable holdouts fall to binary via the anchor-resolution check. The relay
    # is a STRICT SUPERSET of the binary signal (the binary sentence is always
    # appended) and fails-to-binary on any missing/unparseable input. See
    # darkfactory/audit/holdout_bridge.py. Roll-forward by one config flip (same
    # posture as scenario_parallel_generation).
    holdout_bridge_enabled: bool = False
    # ROADMAP.md Phase 2 B1 — host-side TE pre-acceptance §4a check.
    # When enabled, the orchestrator runs ``scripts/lib/scenario_lint.py``
    # against TE-emitted scenarios on the host (no container) AFTER the
    # Test Engineer's existing SA-validation retry loop and BEFORE the
    # iteration loop. Hits are routed back to the TE for retry; budget
    # exhaustion aborts with classification ``scenario_author_retry_exhausted``.
    # Default OFF for the first release per ROADMAP.md "Risk" mitigation —
    # roll-forward is one config flip, not a code revert.
    #
    # The retry budget is SEPARATE from the Implementer iteration cap
    # (Decision §7): each agent owns its own retry quota.
    # ``te_pre_acceptance_max_retries`` counts retries only — the initial
    # lint check is always run. Total attempts = 1 + max_retries:
    #   - max_retries=0 → one lint check, abort on any hit, no retry
    #   - max_retries=3 → one lint check + up to 3 TE retries (4 attempts)
    te_pre_acceptance_check: bool = False
    te_pre_acceptance_max_retries: int = 3
    # Anchor coverage floor — block scenario gen from advancing to implementer
    # if any declared FR/AC/EC anchor has zero scenarios mapping to it.
    coverage_floor_enabled: bool = True
    # Behavioral-credit rule (default OFF; corpus-validate before flipping). When
    # ON, the coverage floor credits a Tier-2 scenario for an anchor ONLY if the
    # scenario imports >=1 project module (ground-truth via the adapters'
    # parse_test_imports). Closes the lexical-credit hole where a source-grep
    # (`readFileSync(src).toContain(...)`) or empty `it('[AC-N]')` earns full
    # coverage with zero behavioral verification (F-1d FR-2 routing shipped this
    # way). Tier-1 `00-tier1-` contract files + markdown scenarios are exempt.
    # NECESSARY-not-sufficient (an import-then-grep still passes the FLOOR — the
    # blind-holdout gate is the sufficient layer). Project-import prefixes are
    # derived from the architect manifest's test_integration.entry_points (NOT a
    # tsconfig parse) — same ground-truth as the validator's import-specifier check.
    require_behavioral_credit: bool = False
    coverage_max_regen_attempts: int = 2
    # When the test-engineer flags a coverage gap whose cause is an
    # architect-fixable missing entry point (TE cannot invent imports per
    # Hard Rule #8), route the gap back to the architect to amend
    # ``test_integration.entry_points`` before TE retries. Hard cap on
    # amend calls bounds the extra cost of the resilient path.
    coverage_architect_amend: bool = True
    coverage_architect_amend_max_calls: int = 2
    # Phase-1 vehicle classifier (suggest-only). When ON, a coverage-floor abort
    # asks a focused host-side LLM, per uncovered anchor, WHICH vehicle the author
    # should declare (+ its verification-impact tier), shifting the taxonomy +
    # inference burden off the author. SUGGEST-ONLY: grants no credit, changes no
    # gate, never auto-applies; ABSTAIN / any failure falls back to the static
    # menu (with `claude` absent every anchor ABSTAINs and the renderer suppresses
    # the per-anchor note, so the abort matches the pre-Phase-1 menu). The author
    # applies via a spec edit; Phase-0's front-of-run precheck backstops an applied
    # `subprocess` routing. See scenarios/vehicle_classifier.py.
    vehicle_classifier_enabled: bool = True
    mutation_cmd: str = ""
    mutation_min_score: int = 0
    spec_sanitization_enabled: bool = True
    # Durable cross-run agent-metrics corpus (observability). Each agent
    # invocation's stream summary is appended to an append-only, user-level
    # JSONL that survives run resets / --fresh / manual clears (the structural
    # fix for the run3 sidecar-loss). Default location ~/.dark-factory/corpus/
    # agent-metrics.jsonl; set corpus_path to override. Metrics-only — no
    # artifact content — so it crosses no information barrier. Empty string =
    # use the default path. corpus_archive_streams additionally copies each raw
    # sidecar into ~/.dark-factory/corpus/<run_id>/ for future re-derivation.
    corpus_path: str = ""
    corpus_archive_streams: bool = True
    # Phase 4 (2026-04-24): Track C Proposal 4 mechanism-token gate.
    # Phase 2 enabled this as its delta; Phase 4 disables it by default after
    # N=2 INSUFFICIENT verdict + neo critical review concluded the strip
    # starved downstream agents. Findings are still computed and logged as
    # warnings for observability; ``block`` severity hits become advisory
    # rather than fatal. Reversible: flip to True to restore Phase 2 behavior.
    p4_mechanism_token_gate_enabled: bool = False
    # Sprint 30 (SAST swap, compare-mode): run a vendored, externally-maintained
    # Semgrep ruleset over the implementation source ALONGSIDE the bespoke regex
    # scanner and write a per-iteration regex-vs-Semgrep compare artifact. This is
    # ADVISORY ONLY — it does NOT gate, does NOT feed security_high_count, does NOT
    # alter any score; the regexes remain the sole security authority. The artifact
    # measures (offline) which bespoke regexes Semgrep can subsume, per the demote
    # bar in docs/curation-audit.md. Default OFF (opt-in measurement); requires the
    # `dark-factory[sast]` extra (semgrep). Graceful no-op if semgrep is absent.
    sast_compare_enabled: bool = False
    # Validator-determinism pilot (2026-06-23): derive deterministic
    # forbidden_patterns from the consistency-ledger's ``forbids`` claims
    # (the validator detects MUST-NOTs reliably but elevates them to a
    # blocker only ~1/3 of runs — coin-flip). When ON, a ``forbids`` claim
    # naming a code call (e.g. F-1c's ``migrate(``) becomes a forbidden
    # pattern the existing pre-gate scans against implementer source,
    # taking the elevation decision off the validator's coin-flip. Merged
    # (union) with architect-authored patterns; never replaces them.
    # Flipped ON 2026-06-23 after corpus-validation: ran the extractor over
    # all 119 real consistency-ledgers → exactly ONE pattern emitted
    # (``re:\bmigrate\s*\(`` on database-foundation), every other spec's
    # forbids = behavioral/prose → {}, ZERO false positives. The empty-arg-
    # in-prose residual did not materialize. Roll-back is one flip if a
    # future spec surfaces a false positive. See
    # darkfactory/pipeline/ledger_forbidden_patterns.py +
    # docs/dev/2026-06-23-validator-determinism-pilot.md.
    forbidden_patterns_from_ledger: bool = True
    build_cmd: str = ""
    lint_cmd: str = ""
    typecheck_cmd: str = ""
    scenario_smoke_test: bool = True
    # C2 (proposal-test typecheck rehearsal): run ONE LLM pass between
    # architect and scenario generation to prove AC/FR/EC anchors are
    # unit-testable. Aborts the pipeline if ≥30% fail tsc or any AC
    # proposal trips a banned pattern. Opt-out lever for cheap specs
    # where the $1-3 cost isn't justified. No-ops automatically on
    # non-TS projects.
    proposal_rehearsal_enabled: bool = True
    trajectory_stable_failure_threshold: float = 0.7

    # F (2026-06-16): the in-run `unresolved_contracts` check is LLM-sketch-
    # based, non-deterministic, and emits false positives that contradict the
    # spec bytes (run 2f013c56 hallucinated already-resolved divergences,
    # aborting 3 runs). Default False = ADVISORY: parse + log, never abort.
    # The deterministic contract-resolution check (Move 2) replaces it as the
    # real gate; the downstream blind-scenario gate still catches genuinely
    # under-constrained surfaces. Set True to restore the legacy hard gate.
    unresolved_contracts_hard_gate: bool = False

    # Stable-failure SHIP override
    stable_failure_ship_override: bool = True
    stable_failure_min_ratio: float = 0.8
    stable_failure_min_iterations: int = 2

    # Architecture Lock
    architecture_lock: bool = True
    architect_max_turns: int = 100

    # Autonomy
    autonomy_level: int = 3
    max_iterations: int = 3
    max_total_agent_turns: int = 0
    dynamic_autonomy: bool = False
    dynamic_iterations: bool = False

    # Docker
    docker_network: str = "df-pipeline"
    egress_filtering: bool = True

    # Git
    git_remote: str = "origin"
    git_base_branch: str = "main"
    auto_pr: bool = False
    # Commit the durable ship record (shipped.jsonl + scenario archive) on SHIP,
    # INDEPENDENT of auto_pr — so a ship survives a working-copy reset that wipes
    # gitignored runtime state (the way the database-foundation ship row was
    # lost). Best-effort. Set False to manage the ledger commit entirely by hand.
    commit_ledger_on_ship: bool = True
    merge_queue: bool = False
    auto_merge_high_trust: bool = False
    skip_hooks: bool = False

    # Satisfaction
    min_satisfaction_score: int = 0
    multi_run_n: int = 1
    multi_run_k: int = 1
    plateau_threshold: int = 5

    # Satisfaction weights — tier-level
    tier1_weight: int = 60   # Weight for deterministic Tier 1 contract tests
    tier2_weight: int = 40   # Weight for SA-generated Tier 2 adversarial tests

    # Satisfaction weights — per-category (Tier 2 only)
    weight_contract: int = 100
    weight_happy_path: int = 100
    weight_edge_cases: int = 80
    weight_error_conditions: int = 90
    weight_state_sequence: int = 80
    weight_security: int = 120
    weight_property_based: int = 70
    weight_adversarial: int = 60

    # Cost
    cost_budget_usd: float = 0.0
    cost_per_input_token: float = 0.000015
    cost_per_output_token: float = 0.000075
    cost_est_input_tokens_per_turn: int = 4000
    cost_est_output_tokens_per_turn: int = 2000
    cost_correction_factor: float = 1.0
    cost_alert_thresholds: list[int] = field(default_factory=lambda: [50, 80, 100])

    # Anomaly
    anomaly_max_agent_turns: int = 150
    anomaly_max_spec_turns: int = 800
    anomaly_max_spec_wall_s: int = 7200
    anomaly_max_files_written: int = 50
    anomaly_baseline_window: int = 20

    # Trust
    trust_min_history: int = 5

    # Plan
    max_concurrent_specs: int = 3
    plan_token_budget: int = 0
    plan_max_wall_time_s: int = 0
    conflict_strategy: str = "abort"
    worktree_cleanup_on_ship: bool = True
    worktree_cleanup_on_fail: bool = False

    # Enterprise features (off by default — see docs/CONFIGURATION.md § Feature Tiers)
    enterprise_enabled: bool = False

    # Notifications
    slack_webhook_url: str = ""
    webhook_url: str = ""
    # Shell command hooks — supports {key} substitution, fire-and-forget.
    # See docs/CONFIGURATION.md for available variables per hook.
    notify_on_spec_shipped: str = ""
    notify_on_spec_failed: str = ""
    notify_on_spec_escalated: str = ""
    notify_on_spec_hold: str = ""
    notify_on_plan_complete: str = ""
    notify_on_pr_created: str = ""
    notify_on_budget_alert: str = ""
    notify_on_anomaly: str = ""
    notify_on_regression_blocked: str = ""
    notify_on_canary_failed: str = ""
    notify_on_rollback: str = ""
    notify_on_spec_start: str = ""
    notify_on_scenarios_generated: str = ""
    notify_on_gate_complete: str = ""
    notify_on_no_ship: str = ""

    # Regression
    regression_enabled: bool = False
    regression_timeout: int = 300
    regression_policy: str = "block"
    regression_on_ship: bool = False
    # Phase 5.3: wall-clock budget for a regression run (seconds). When the
    # archive grows past what fits in budget, the selector keeps the
    # newest-shipped specs first, then the oldest, and defers the middle.
    regression_budget_seconds: int = 600
    # Estimated mean wall-time per spec for budget arithmetic. Used only for
    # selection — the actual run still respects regression_timeout per spec.
    regression_avg_spec_seconds: int = 60

    # Test server (for HTTP/Journey scenarios)
    test_base_url: str = ""
    test_server_cmd: str = ""
    server_setup_cmd: str = ""  # run before server starts (e.g., migrations)
    setup_cmd: str = ""  # run after install, before gates (e.g., database migrations)
    pre_setup_cmds: list[str] = field(default_factory=list)  # run before setup_cmd (env preconditions)
    # Wall-clock budget for setup_cmd. Migrations that exceed this are
    # structurally stuck (blocked on a lock, waiting on a missing service) —
    # killing them with diagnostics beats hanging until the Python-level
    # container timeout fires with no breadcrumbs. 300s is generous for
    # typical ORM migrations (<30s) while still catching wedged processes
    # well before the container-level GATE_TIMEOUT default of 600s.
    # Projects with legitimately long-running migrations should override
    # via ``config.toml [runtime] setup_timeout`` rather than lower the
    # default here (a shorter default trips on legit cases; a longer one
    # defeats the point).
    setup_timeout: int = 300
    server_startup_timeout: int = 30
    server_health_path: str = "/health"
    server_env: dict[str, str] = field(default_factory=dict)  # extra env vars for server container
    browser_base_url: str = ""
    browser_server_cmd: str = ""
    browser_startup_timeout: int = 60

    # Service sidecars (e.g., Postgres, Redis) — started before the test server
    services: list[ServiceDef] = field(default_factory=list)

    # Implementer
    implementer_write_dirs: str = ""

    # Deploy
    deploy_preset: str = "none"
    health_check_strategy: str = "http"
    health_check_url: str = ""
    health_check_host: str = ""
    health_check_port: int = 0
    health_check_container: str = ""
    deploy_cmd: str = ""
    deploy_base_url: str = ""
    deploy_health_endpoint: str = "/health"
    deploy_health_timeout: int = 60
    deploy_teardown_cmd: str = ""
    deploy_cmd_allow_shell: bool = False
    deploy_compose_file: str = ""
    deploy_env: dict[str, str] = field(default_factory=dict)
    deploy_ttl_seconds: int = 3600
    deploy_rollback_cmd: str = ""
    rollback_timeout: int = 120
    rollback_window_seconds: int = 3600
    rollback_poll_interval: int = 60
    rollback_monitor_cmd: str = ""

    # Canary
    canary_enabled: bool = False
    canary_shift_cmd: str = ""
    canary_rollback_cmd: str = ""
    canary_lock_timeout: int = 600
    canary_stages: list = field(default_factory=list)  # list[CanaryStage] after loader
    canary_metrics: list = field(default_factory=list)  # list[MetricThreshold] after loader

    # Feature flags
    flag_create_cmd: str = ""
    flag_enable_cmd: str = ""
    flag_disable_cmd: str = ""
    flag_check_cmd: str = ""

    # Migration safety
    migration_check_enabled: bool = False
    migration_check_policy: str = "warn"
    migration_dirs: list[str] = field(default_factory=lambda: ["migrations/", "db/migrate/", "alembic/versions/"])
    migration_extra_patterns: dict[str, list[str]] = field(default_factory=dict)

    # Model. NOTE: a dated model ID is a staleness liability — claude-sonnet-4-20250514
    # was retired mid-2026 and silently broke every agent (exit 1, "model may not
    # exist") until bumped here. Backlog: make this resilient (track current Sonnet /
    # fail-fast with an actionable message / validate at startup) rather than re-pinning
    # a dated ID that will rot again. cost.py keys on family ("sonnet") so it needs no update.
    model_id: str = "claude-sonnet-4-6"
    model_ids: dict[str, str] = field(default_factory=dict)
    # Local-LLM routing (default-OFF; empty => behaviour identical to today).
    # model_profiles: named backend bundles loaded from [model.profiles.<name>].
    # agent_model_profiles: role value -> profile name, from [agents.models]
    # (mirrors agent_timeout_overrides). An unset role resolves to the implicit
    # "default" Anthropic profile. See resolve_model_profile + ModelProfile.
    model_profiles: dict[str, ModelProfile] = field(default_factory=dict)
    agent_model_profiles: dict[str, str] = field(default_factory=dict)

    # Per-response output-token ceiling passed to each agent container as
    # CLAUDE_CODE_MAX_OUTPUT_TOKENS. Claude Code defaults this to 32000 when
    # unset; a single agent turn that needs more (the test-engineer emitting
    # many scenario files at once — bb spec-3, S33 dump) hits Claude Code's
    # "exceeded the 32000 output token maximum" and `claude -p` retries until
    # the agent_timeout SIGKILL (a ~15-min hang masquerading as a timeout). 0
    # (the default) means auto-derive from each role's resolved model family
    # (sonnet/haiku→64000, opus/fable→128000); a positive override forces that
    # value. Model-derived, not a flat constant, so an opus agent isn't capped
    # at the sonnet ceiling. See agents.resolve_max_output_tokens.
    max_output_tokens: int = 0

    # Transient failure retry
    transient_retry_max: int = 2
    transient_retry_base_s: float = 30.0

    # Strategy switching
    strategy_switch_enabled: bool = False
    strategy_max_switches: int = 2

    # Digital twins
    twins_mode: str = "manual"  # "manual" or "auto"; orchestrator selects start_auto() when "auto"
    twins: list = field(default_factory=list)  # list[TwinSpec] after loader

    # Scale validation
    scale_concurrency: int = 4

    # Auth (informational only — tokens come from env vars)
    token_expires: str = ""

    # Profiles (populated from [profile.*] TOML tables)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Provenance tracking — records which config layer set each field.
    # Not part of the config itself; excluded from equality and repr.
    _provenance: dict[str, str] = field(
        default_factory=dict, repr=False, compare=False,
    )

    def set_provenance(self, field_name: str, source: str) -> None:
        """Record which layer set a config field (e.g. 'config.toml:[gates]')."""
        self._provenance[field_name] = source

    def get_provenance(self, field_name: str) -> str:
        """Return the layer that set a field, or 'default' if unset."""
        return self._provenance.get(field_name, "default")

    def apply_offline_mode(self) -> None:
        """Force EVERY agent role to the single configured local backend profile
        (Phase 5 ``--offline``). Requires exactly one custom-backend profile (a
        ``[model.profiles.<name>]`` with ``base_url`` set); raises ConfigError on
        zero or ambiguous.

        CLOBBERS ``agent_model_profiles`` wholesale — any pre-existing per-role
        mapping is discarded (the flag's contract is "all roles, one backend"). It
        is NOT a "merge local in" helper. The run's degradation is then visible in
        the scorecard's ``local_invocations`` (telemetry-derived, the single source
        of truth — a redundant ``config.degraded`` flag was removed as set-but-dead,
        Linus/Codex Phase-5 review).
        """
        from darkfactory.models.agent import AgentRole
        local = sorted(n for n, p in self.model_profiles.items() if p.base_url)
        if len(local) == 0:
            raise ConfigError(
                "--offline requires exactly one local model profile (a "
                "[model.profiles.<name>] table with base_url set); none configured"
            )
        if len(local) > 1:
            raise ConfigError(
                f"--offline is ambiguous: {len(local)} local profiles configured "
                f"({', '.join(local)}); define exactly one [model.profiles.*] with "
                "base_url, or map roles explicitly via [agents.models] without --offline"
            )
        self.agent_model_profiles = {r.value: local[0] for r in AgentRole}

    def __post_init__(self) -> None:
        # Ensure _provenance is never shared after dataclasses.replace().
        self._provenance = dict(self._provenance)
        # test_base_url and test_server_cmd may both be empty — valid when an
        # external/CI server supplies the URL at runtime via $TEST_BASE_URL.
        # The gate will fail at runtime with a clear message if --base-url is missing.
        if self.max_pre_gate_retries < 0:
            raise ConfigError("max_pre_gate_retries must be >= 0")
        if self.gate_timeout < 1:
            raise ConfigError(f"gate_timeout must be >= 1, got {self.gate_timeout}")
        if self.scenario_timeout < 0:
            raise ConfigError("scenario_timeout must be >= 0")
        if self.scenario_batch_size < 0:
            raise ConfigError("scenario_batch_size must be >= 0 (0 disables batching)")
        # Linus C1: the batched TE path skips the monolithic empty/misnamed-retry
        # loop and relies ENTIRELY on the coverage floor to backstop empty,
        # misnamed, or filename-clobbered batches (uncovered anchor → floor regen).
        # Disabling the floor while batching is on removes that backstop → silent
        # coverage loss. Enforce the safety coupling at config time.
        if self.scenario_batch_size > 0 and not self.coverage_floor_enabled:
            raise ConfigError(
                "scenario_batch_size > 0 requires coverage_floor_enabled=True "
                "(the batched Test-Engineer path relies on the coverage floor to "
                "backstop empty/misnamed/clobbered batches)"
            )
        if self.server_startup_timeout < 1:
            raise ConfigError(
                f"server_startup_timeout must be >= 1, got {self.server_startup_timeout}"
            )
        if self.browser_startup_timeout < 1:
            raise ConfigError(
                f"browser_startup_timeout must be >= 1, got {self.browser_startup_timeout}"
            )
        if self.autonomy_level not in (1, 2, 3):
            raise ConfigError(f"autonomy_level must be 1, 2, or 3, got {self.autonomy_level}")
        if not 0 <= self.min_ship_pass_rate <= 100:
            raise ConfigError(f"min_ship_pass_rate must be 0-100, got {self.min_ship_pass_rate}")
        if not 0 <= self.min_satisfaction_score <= 100:
            raise ConfigError(f"min_satisfaction_score must be 0-100, got {self.min_satisfaction_score}")
        if self.max_iterations < 1:
            raise ConfigError(f"max_iterations must be >= 1, got {self.max_iterations}")
        if self.agent_max_turns < 1:
            raise ConfigError(f"agent_max_turns must be >= 1, got {self.agent_max_turns}")
        if self.architect_max_turns < 1:
            raise ConfigError(f"architect_max_turns must be >= 1, got {self.architect_max_turns}")
        if self.agent_timeout < 1:
            raise ConfigError(f"agent_timeout must be >= 1, got {self.agent_timeout}")
        # L2: per-role overrides must be real int seconds >= 1 (Linus+Codex: a
        # fat-fingered ``test-engineer = 0`` would silently DEFEAT the override
        # whose whole purpose is more wall). ``type(t) is int`` rejects TOML bool
        # (an int subclass) and float; warn (not fail) on a role key that doesn't
        # match a known agent so a misspelling like ``test_engineer`` is visible
        # rather than a silent no-op that burns a run.
        for role, t in self.agent_timeout_overrides.items():
            if type(t) is not int or t < 1:
                raise ConfigError(
                    f"agent_timeout_overrides[{role!r}] must be an integer "
                    f">= 1, got {t!r}"
                )
        from darkfactory.models.agent import AgentRole  # local: avoid import cycle
        _known_roles = {r.value for r in AgentRole}
        for role in self.agent_timeout_overrides:
            if role not in _known_roles:
                log.warning(
                    "agent_timeout_overrides has unknown role %r (no such agent) "
                    "— it will never apply; known roles: %s",
                    role, sorted(_known_roles),
                )
        # Local-LLM routing validation (reuses _known_roles above).
        # "default" is the reserved implicit Anthropic profile — an explicit
        # [model.profiles.default] would create two sources of truth for the
        # default backend, so reject it (Neo rev-5).
        if "default" in self.model_profiles:
            raise ConfigError(
                "[model.profiles.default] is reserved — configure the default "
                "(Anthropic) backend via [model] id / max_output_tokens, not a "
                "'default' profile"
            )
        # agent_model_profiles maps a role value to a profile name. Warn (not fail)
        # on an unknown role so a typo is visible rather than a silent no-op; FAIL
        # on a mapping that points at an undefined profile (it would silently fall
        # back to default and mask the misconfiguration).
        for role, pname in self.agent_model_profiles.items():
            if role not in _known_roles:
                log.warning(
                    "agent_model_profiles has unknown role %r (no such agent) "
                    "— it will never apply; known roles: %s",
                    role, sorted(_known_roles),
                )
            if pname != "default" and pname not in self.model_profiles:
                raise ConfigError(
                    f"agent_model_profiles[{role!r}] -> {pname!r} is not a defined "
                    f"profile (define [model.profiles.{pname}] or use 'default')"
                )
        if self.agent_retries < 0:
            raise ConfigError(f"agent_retries must be >= 0, got {self.agent_retries}")
        # type() (not isinstance) so a TOML bool — an int subclass — is rejected.
        # Without this a ``[model] max_output_tokens = true`` passes here but then
        # crashes resolve_model_profile's synthesized default ModelProfile (which
        # rejects bool), failing a config this validator accepted. (Codex #3.)
        if type(self.max_output_tokens) is not int or self.max_output_tokens < 0:
            raise ConfigError(
                f"max_output_tokens must be a non-negative int (0 = auto-derive), "
                f"got {self.max_output_tokens!r}"
            )
        if self.language not in ("node", "python", "go", "rust", "unknown"):
            raise ConfigError(f"language must be node/python/go/rust, got {self.language!r}")
        if self.conflict_strategy not in ("abort", "serialize", "warn"):
            raise ConfigError(f"conflict_strategy must be abort/serialize/warn, got {self.conflict_strategy!r}")
        if self.regression_policy not in ("block", "warn"):
            raise ConfigError(f"regression_policy must be block/warn, got {self.regression_policy!r}")
        if self.multi_run_k > self.multi_run_n:
            raise ConfigError(f"multi_run_k ({self.multi_run_k}) cannot exceed multi_run_n ({self.multi_run_n})")
        if self.canary_enabled and not self.canary_rollback_cmd:
            raise ConfigError("canary_rollback_cmd is required when canary_enabled=true")
        if self.canary_stages:
            percents = [s.percent for s in self.canary_stages]
            if percents != sorted(percents):
                raise ConfigError(f"canary_stages percent must be monotonically increasing, got {percents}")
        if self.migration_check_policy not in ("warn", "block"):
            raise ConfigError(f"migration_check_policy must be 'warn' or 'block', got {self.migration_check_policy!r}")
        if self.transient_retry_max < 0:
            raise ConfigError(f"transient_retry_max must be >= 0, got {self.transient_retry_max}")
        if self.transient_retry_base_s < 0:
            raise ConfigError(f"transient_retry_base_s must be >= 0, got {self.transient_retry_base_s}")
        if self.strategy_max_switches < 0:
            raise ConfigError(f"strategy_max_switches must be >= 0, got {self.strategy_max_switches}")
        if self.cost_correction_factor <= 0:
            raise ConfigError(f"cost_correction_factor must be > 0, got {self.cost_correction_factor}")
        if self.deploy_preset not in ("none", "docker-compose", "k8s-argo"):
            raise ConfigError(f"deploy_preset must be none/docker-compose/k8s-argo, got {self.deploy_preset!r}")
        if self.health_check_strategy not in ("http", "tcp", "docker", "command"):
            raise ConfigError(f"health_check_strategy must be http/tcp/docker/command, got {self.health_check_strategy!r}")
        if self.twins_mode not in ("manual", "auto"):
            raise ConfigError(f"twins_mode must be manual/auto, got {self.twins_mode!r}")
        if self.scale_concurrency < 1:
            raise ConfigError(f"scale_concurrency must be >= 1, got {self.scale_concurrency}")
        if not 0.0 < self.trajectory_stable_failure_threshold <= 1.0:
            raise ConfigError(
                f"trajectory_stable_failure_threshold must be in (0.0, 1.0], "
                f"got {self.trajectory_stable_failure_threshold}"
            )
        if self.tier1_weight < 0 or self.tier2_weight < 0:
            raise ConfigError("tier1_weight and tier2_weight must be >= 0")
        if self.tier1_weight == 0 and self.tier2_weight == 0:
            raise ConfigError("tier1_weight and tier2_weight cannot both be 0")
        if not 0.0 <= self.stable_failure_min_ratio <= 1.0:
            raise ConfigError(
                f"stable_failure_min_ratio must be in [0.0, 1.0], "
                f"got {self.stable_failure_min_ratio}"
            )
        if self.stable_failure_min_iterations < 1:
            raise ConfigError(
                f"stable_failure_min_iterations must be >= 1, "
                f"got {self.stable_failure_min_iterations}"
            )
        if self.sa_max_retries < 0:
            raise ConfigError(f"sa_max_retries must be >= 0, got {self.sa_max_retries}")
        if self.scenario_empty_retry_attempts < 0:
            raise ConfigError(
                f"scenario_empty_retry_attempts must be >= 0, got "
                f"{self.scenario_empty_retry_attempts}"
            )
        if self.scenario_max_assertions_per_file < 1:
            raise ConfigError(
                f"scenario_max_assertions_per_file must be >= 1, "
                f"got {self.scenario_max_assertions_per_file}"
            )

    @property
    def category_weights(self) -> dict[str, int]:
        """Return category name → weight mapping."""
        return {
            "contract": self.weight_contract,
            "happy_path": self.weight_happy_path,
            "edge_cases": self.weight_edge_cases,
            "error_conditions": self.weight_error_conditions,
            "state_sequence": self.weight_state_sequence,
            "security": self.weight_security,
            "property_based": self.weight_property_based,
            "adversarial": self.weight_adversarial,
        }

    # *_cmd fields that should be validated as safe script paths.
    _CMD_FIELDS = (
        "deploy_cmd", "deploy_teardown_cmd", "deploy_rollback_cmd",
        "canary_shift_cmd", "canary_rollback_cmd",
        "flag_create_cmd", "flag_enable_cmd", "flag_disable_cmd", "flag_check_cmd",
        "rollback_monitor_cmd",
    )
    # Fields where deploy_cmd_allow_shell skips validation (legacy escape hatch).
    _LEGACY_SHELL_FIELDS = ("deploy_cmd", "deploy_teardown_cmd")

    def validate(self, project_dir: str) -> None:
        """Validate command paths and other project-dir-dependent config."""
        from darkfactory.deployment.command import validate_command
        for field_name in self._CMD_FIELDS:
            value = getattr(self, field_name, "")
            if not value:
                continue
            if self.deploy_cmd_allow_shell and field_name in self._LEGACY_SHELL_FIELDS:
                continue
            validate_command(value, project_dir, field_name)


def resolve_model_profile(role: AgentRole, config: PipelineConfig) -> ModelProfile:
    """Resolve the backend ``ModelProfile`` for an agent ``role``.

    Precedence: per-role ``agent_model_profiles[role.value]`` > the implicit
    ``"default"`` Anthropic profile. The default profile is synthesized from
    ``config.model_id`` — empty ``base_url`` means the real OAuth/API token is used
    and extended thinking stays enabled.

    A call-time model-id override (e.g. strategy switching's ``model_override``) is
    deliberately NOT applied here: it overrides ``profile.model`` at the injection
    site (Phase 3), keeping *backend selection* (this function) and *model string*
    as separate concerns. This is the single source of truth consumed by both the
    ``--model`` flag (docker/args.py) and the output-token-cap derivation
    (pipeline/agents.py), so the two can never diverge.

    ``role.value`` is the only attribute touched, so the function does not import
    ``AgentRole`` at runtime (avoids the config<->agent import cycle).
    """
    name = config.agent_model_profiles.get(role.value, "default")
    if name == "default":
        # The default Anthropic profile honors the legacy per-role [model.ids]
        # override (role → model string) as its model, so existing model_ids config
        # keeps working unchanged. Precedence overall: explicit profile mapping >
        # [model.ids] > global model_id.
        return ModelProfile(
            base_url="",
            model=config.model_ids.get(role.value, config.model_id),
            max_output_tokens=config.max_output_tokens,  # 0 => downstream family-derive
            family="",
            disable_thinking=False,  # Anthropic models support extended thinking
        )
    prof = config.model_profiles.get(name)
    if prof is None:
        # PipelineConfig.__post_init__ already rejects an undefined mapping; this
        # guards a programmatically-built config that bypassed that validation.
        raise ConfigError(
            f"agent_model_profiles[{role.value!r}] -> {name!r} is not a defined "
            "model profile"
        )
    return prof
