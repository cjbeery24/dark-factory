"""TOML key → PipelineConfig field name mappings.

Default values live solely in PipelineConfig.__init__. These tables
only map TOML keys to dataclass field names for the config loader.
"""

from __future__ import annotations

# Each dict maps: TOML key → PipelineConfig field name.

CORE_FIELDS: dict[str, str] = {
    "language": "language",
    "source_dirs": "source_dirs",
    "test_dir": "test_dir",
    "test_cmd": "test_cmd",
    "scenario_test_cmd": "scenario_test_cmd",
    "dark_factory_home": "dark_factory_home",
    "corpus_path": "corpus_path",
    "corpus_archive_streams": "corpus_archive_streams",
}

AGENTS_FIELDS: dict[str, str] = {
    "max_turns": "agent_max_turns",
    "timeout": "agent_timeout",
    "retries": "agent_retries",
    "memory": "agent_memory",
    "cpus": "agent_cpus",
    "io_device": "agent_io_device",
    "io_read_bps": "agent_io_read_bps",
    "io_write_bps": "agent_io_write_bps",
}

GATES_FIELDS: dict[str, str] = {
    "timeout": "gate_timeout",
    "scenario_timeout": "scenario_timeout",
    "hard_gate_build": "hard_gate_build",
    "preflight_enabled": "preflight_enabled",
    "min_ship_pass_rate": "min_ship_pass_rate",
    "validate_scenarios": "validate_scenarios",
    "scenario_min_files": "scenario_min_files",
    "scenario_min_assertions_per_file": "scenario_min_assertions_per_file",
    "scenario_max_assertions_per_file": "scenario_max_assertions_per_file",
    "mutation_cmd": "mutation_cmd",
    "mutation_min_score": "mutation_min_score",
    "spec_sanitization_enabled": "spec_sanitization_enabled",
    # DEPRECATED: operational command fields below are migrated to
    # environment.toml by backcompat.ensure_environment_bootstrap() and
    # stripped from config.toml on the first pipeline run. Kept here so
    # the loader can still parse legacy configs during the migration window.
    "build_cmd": "build_cmd",
    "lint_cmd": "lint_cmd",
    "typecheck_cmd": "typecheck_cmd",
    "test_cmd": "test_cmd",
    "scenario_test_cmd": "scenario_test_cmd",
    "scenario_smoke_test": "scenario_smoke_test",
    "te_pre_acceptance_check": "te_pre_acceptance_check",
    "te_pre_acceptance_max_retries": "te_pre_acceptance_max_retries",
    "proposal_rehearsal_enabled": "proposal_rehearsal_enabled",
    "sast_compare_enabled": "sast_compare_enabled",
    "forbidden_patterns_from_ledger": "forbidden_patterns_from_ledger",
    "unresolved_contracts_hard_gate": "unresolved_contracts_hard_gate",
    "scenario_warm_session": "scenario_warm_session",
    "scenario_parallel_generation": "scenario_parallel_generation",
    "scenario_batch_size": "scenario_batch_size",
    "trajectory_stable_failure_threshold": "trajectory_stable_failure_threshold",
    "pre_gate_retries": "max_pre_gate_retries",
    "smoke_retries": "max_smoke_retries",
}

AUTONOMY_FIELDS: dict[str, str] = {
    "level": "autonomy_level",
    "max_iterations": "max_iterations",
    "max_total_agent_turns": "max_total_agent_turns",
    "dynamic_autonomy": "dynamic_autonomy",
    "dynamic_iterations": "dynamic_iterations",
}

ARCHITECTURE_FIELDS: dict[str, str] = {
    "enabled": "architecture_lock",
    "max_turns": "architect_max_turns",
}

DOCKER_FIELDS: dict[str, str] = {
    "network": "docker_network",
    "egress_filtering": "egress_filtering",
}

GIT_FIELDS: dict[str, str] = {
    "remote": "git_remote",
    "base_branch": "git_base_branch",
    "auto_pr": "auto_pr",
    "merge_queue": "merge_queue",
    "auto_merge_high_trust": "auto_merge_high_trust",
    "skip_hooks": "skip_hooks",
}

SATISFACTION_FIELDS: dict[str, str] = {
    "min_score": "min_satisfaction_score",
    "multi_run_n": "multi_run_n",
    "multi_run_k": "multi_run_k",
    "plateau_threshold": "plateau_threshold",
    "tier1_weight": "tier1_weight",
    "tier2_weight": "tier2_weight",
}

SATISFACTION_WEIGHTS_FIELDS: dict[str, str] = {
    "contract": "weight_contract",
    "happy_path": "weight_happy_path",
    "edge_cases": "weight_edge_cases",
    "error_conditions": "weight_error_conditions",
    "state_sequence": "weight_state_sequence",
    "security": "weight_security",
    "property_based": "weight_property_based",
    "adversarial": "weight_adversarial",
}

COST_FIELDS: dict[str, str] = {
    "budget_usd": "cost_budget_usd",
    "per_input_token": "cost_per_input_token",
    "per_output_token": "cost_per_output_token",
    "est_input_tokens_per_turn": "cost_est_input_tokens_per_turn",
    "est_output_tokens_per_turn": "cost_est_output_tokens_per_turn",
    "correction_factor": "cost_correction_factor",
    "alert_thresholds": "cost_alert_thresholds",
}

ANOMALY_FIELDS: dict[str, str] = {
    "max_agent_turns": "anomaly_max_agent_turns",
    "max_spec_turns": "anomaly_max_spec_turns",
    "max_spec_wall_s": "anomaly_max_spec_wall_s",
    "max_files_written": "anomaly_max_files_written",
    "baseline_window": "anomaly_baseline_window",
}

TRUST_FIELDS: dict[str, str] = {
    "min_history": "trust_min_history",
}

PLAN_FIELDS: dict[str, str] = {
    "max_concurrent": "max_concurrent_specs",
    "token_budget": "plan_token_budget",
    "max_wall_time_s": "plan_max_wall_time_s",
    "conflict_strategy": "conflict_strategy",
    "worktree_cleanup_on_ship": "worktree_cleanup_on_ship",
    "worktree_cleanup_on_fail": "worktree_cleanup_on_fail",
}

NOTIFICATIONS_FIELDS: dict[str, str] = {
    "slack_webhook_url": "slack_webhook_url",
    "webhook_url": "webhook_url",
    "notify_on_spec_shipped": "notify_on_spec_shipped",
    "notify_on_spec_failed": "notify_on_spec_failed",
    "notify_on_spec_escalated": "notify_on_spec_escalated",
    "notify_on_spec_hold": "notify_on_spec_hold",
    "notify_on_plan_complete": "notify_on_plan_complete",
    "notify_on_pr_created": "notify_on_pr_created",
    "notify_on_budget_alert": "notify_on_budget_alert",
    "notify_on_anomaly": "notify_on_anomaly",
    "notify_on_regression_blocked": "notify_on_regression_blocked",
    "notify_on_canary_failed": "notify_on_canary_failed",
    "notify_on_rollback": "notify_on_rollback",
    "notify_on_spec_start": "notify_on_spec_start",
    "notify_on_scenarios_generated": "notify_on_scenarios_generated",
    "notify_on_gate_complete": "notify_on_gate_complete",
    "notify_on_no_ship": "notify_on_no_ship",
}

REGRESSION_FIELDS: dict[str, str] = {
    "enabled": "regression_enabled",
    "timeout": "regression_timeout",
    "policy": "regression_policy",
    "on_ship": "regression_on_ship",
    "budget_seconds": "regression_budget_seconds",
    "avg_spec_seconds": "regression_avg_spec_seconds",
}

TEST_SERVER_FIELDS: dict[str, str] = {
    "base_url": "test_base_url",
    "server_cmd": "test_server_cmd",
    "setup_cmd": "server_setup_cmd",
    "startup_timeout": "server_startup_timeout",
    "health_path": "server_health_path",
    "env": "server_env",
    "browser_base_url": "browser_base_url",
    "browser_server_cmd": "browser_server_cmd",
    "browser_startup_timeout": "browser_startup_timeout",
}

IMPLEMENTER_FIELDS: dict[str, str] = {
    "write_dirs": "implementer_write_dirs",
}

DEPLOY_FIELDS: dict[str, str] = {
    "preset": "deploy_preset",
    "health_check_strategy": "health_check_strategy",
    "health_check_url": "health_check_url",
    "health_check_host": "health_check_host",
    "health_check_port": "health_check_port",
    "health_check_container": "health_check_container",
    "twins_mode": "twins_mode",
    "cmd": "deploy_cmd",
    "base_url": "deploy_base_url",
    "health_endpoint": "deploy_health_endpoint",
    "health_timeout": "deploy_health_timeout",
    "teardown_cmd": "deploy_teardown_cmd",
    "cmd_allow_shell": "deploy_cmd_allow_shell",
    "compose_file": "deploy_compose_file",
    "env": "deploy_env",
    "ttl_seconds": "deploy_ttl_seconds",
    "rollback_cmd": "deploy_rollback_cmd",
    "rollback_timeout": "rollback_timeout",
    "rollback_window_seconds": "rollback_window_seconds",
    "rollback_poll_interval": "rollback_poll_interval",
    "rollback_monitor_cmd": "rollback_monitor_cmd",
}

CANARY_FIELDS: dict[str, str] = {
    "enabled": "canary_enabled",
    "shift_cmd": "canary_shift_cmd",
    "rollback_cmd": "canary_rollback_cmd",
    "lock_timeout": "canary_lock_timeout",
    # canary_stages and canary_metrics are handled specially in the loader.
}

FLAGS_FIELDS: dict[str, str] = {
    "create_cmd": "flag_create_cmd",
    "enable_cmd": "flag_enable_cmd",
    "disable_cmd": "flag_disable_cmd",
    "check_cmd": "flag_check_cmd",
}

MIGRATION_FIELDS: dict[str, str] = {
    "check_enabled": "migration_check_enabled",
    "check_policy": "migration_check_policy",
    "dirs": "migration_dirs",
    "extra_patterns": "migration_extra_patterns",
}

MODEL_FIELDS: dict[str, str] = {
    "id": "model_id",
    "max_output_tokens": "max_output_tokens",
}

TRANSIENT_RETRY_FIELDS: dict[str, str] = {
    "max": "transient_retry_max",
    "base_s": "transient_retry_base_s",
}

STRATEGY_FIELDS: dict[str, str] = {
    "switch_enabled": "strategy_switch_enabled",
    "max_switches": "strategy_max_switches",
}

SCALE_FIELDS: dict[str, str] = {
    "concurrency": "scale_concurrency",
}

AUTH_FIELDS: dict[str, str] = {
    "token_expires": "token_expires",
}

ENTERPRISE_FIELDS: dict[str, str] = {
    "enabled": "enterprise_enabled",
}

# Section name → field mapping (for iteration in loader).
SECTION_MAP: dict[str, dict[str, str]] = {
    "core": CORE_FIELDS,
    "agents": AGENTS_FIELDS,
    "gates": GATES_FIELDS,
    "autonomy": AUTONOMY_FIELDS,
    "architecture": ARCHITECTURE_FIELDS,
    "docker": DOCKER_FIELDS,
    "git": GIT_FIELDS,
    "satisfaction": SATISFACTION_FIELDS,
    "cost": COST_FIELDS,
    "anomaly": ANOMALY_FIELDS,
    "trust": TRUST_FIELDS,
    "plan": PLAN_FIELDS,
    "notifications": NOTIFICATIONS_FIELDS,
    "regression": REGRESSION_FIELDS,
    "implementer": IMPLEMENTER_FIELDS,
    "test_server": TEST_SERVER_FIELDS,
    "deploy": DEPLOY_FIELDS,
    "canary": CANARY_FIELDS,
    "flags": FLAGS_FIELDS,
    "migration": MIGRATION_FIELDS,
    "model": MODEL_FIELDS,
    "scale": SCALE_FIELDS,
    "auth": AUTH_FIELDS,
    "enterprise": ENTERPRISE_FIELDS,
}

# Nested sections with special handling.
NESTED_SECTIONS: dict[str, dict[str, str]] = {
    "satisfaction.weights": SATISFACTION_WEIGHTS_FIELDS,
    "agents.transient_retry": TRANSIENT_RETRY_FIELDS,
    "agents.strategy": STRATEGY_FIELDS,
}
