"""PipelineConfig dataclass — replaces ~60 bash global variables."""

from __future__ import annotations

from dataclasses import dataclass, field

from darkfactory.errors import ConfigError


@dataclass
class PipelineConfig:
    """All pipeline configuration in one typed structure."""

    # Core
    language: str = "node"
    source_dirs: list[str] = field(default_factory=lambda: ["src"])
    test_dir: str = "tests"
    test_cmd: str = ""
    scenario_test_cmd: str = ""
    dark_factory_home: str = "dark-factory"

    # Agents
    agent_max_turns: int = 200
    agent_timeout: int = 600
    agent_retries: int = 1
    agent_memory: str = "4g"
    agent_cpus: str = "4"
    agent_io_device: str = ""
    agent_io_read_bps: str = ""
    agent_io_write_bps: str = ""

    # Gates
    gate_timeout: int = 300
    hard_gate_build: bool = False
    min_ship_pass_rate: int = 80
    validate_scenarios: str = "strict"
    scenario_min_files: int = 3
    scenario_min_assertions_per_file: int = 1
    mutation_cmd: str = ""
    build_cmd: str = ""
    lint_cmd: str = ""
    typecheck_cmd: str = ""

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

    # Satisfaction weights
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

    # Notifications
    slack_webhook_url: str = ""
    webhook_url: str = ""

    # Regression
    regression_enabled: bool = False
    regression_timeout: int = 300
    regression_policy: str = "block"
    regression_on_ship: bool = False

    # Implementer
    implementer_write_dirs: str = ""

    # Deploy
    deploy_cmd: str = ""
    deploy_base_url: str = ""
    deploy_health_endpoint: str = "/health"
    deploy_health_timeout: int = 60
    deploy_teardown_cmd: str = ""

    # Model
    model_id: str = "claude-sonnet-4-20250514"

    # Auth (informational only — tokens come from env vars)
    token_expires: str = ""

    def __post_init__(self) -> None:
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
