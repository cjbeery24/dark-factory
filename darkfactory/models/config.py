"""PipelineConfig dataclass — replaces ~60 bash global variables."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from darkfactory.errors import ConfigError

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
    coverage_max_regen_attempts: int = 2
    # When the test-engineer flags a coverage gap whose cause is an
    # architect-fixable missing entry point (TE cannot invent imports per
    # Hard Rule #8), route the gap back to the architect to amend
    # ``test_integration.entry_points`` before TE retries. Hard cap on
    # amend calls bounds the extra cost of the resilient path.
    coverage_architect_amend: bool = True
    coverage_architect_amend_max_calls: int = 2
    mutation_cmd: str = ""
    mutation_min_score: int = 0
    spec_sanitization_enabled: bool = True
    # Phase 4 (2026-04-24): Track C Proposal 4 mechanism-token gate.
    # Phase 2 enabled this as its delta; Phase 4 disables it by default after
    # N=2 INSUFFICIENT verdict + neo critical review concluded the strip
    # starved downstream agents. Findings are still computed and logged as
    # warnings for observability; ``block`` severity hits become advisory
    # rather than fatal. Reversible: flip to True to restore Phase 2 behavior.
    p4_mechanism_token_gate_enabled: bool = False
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
        if self.agent_retries < 0:
            raise ConfigError(f"agent_retries must be >= 0, got {self.agent_retries}")
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
