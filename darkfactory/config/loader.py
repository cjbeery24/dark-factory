"""TOML config loading + validation.

Loads .dark-factory/config.toml using Python 3.11+ tomllib.
Also provides migration from legacy .dark-factory/config.sh.
"""

from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path
from typing import Any

from darkfactory.config.defaults import NESTED_SECTIONS, SECTION_MAP
from darkfactory.errors import ConfigError
from darkfactory.models.config import PipelineConfig

log = logging.getLogger(__name__)


def load_config(path: Path) -> PipelineConfig:
    """Load a TOML config file and return a validated PipelineConfig."""
    if not path.exists():
        log.info("no config file at %s, using defaults", path)
        return PipelineConfig()

    with path.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"invalid TOML in {path}: {e}") from e

    config = _build_config(data)

    # Apply deployment preset if configured.
    if config.deploy_preset != "none":
        from darkfactory.deployment.presets import apply_preset
        project_dir = str(path.parent.parent) if path.name == "config.toml" else str(path.parent)
        apply_preset(config, project_dir)

    return config


def _build_config(data: dict[str, Any]) -> PipelineConfig:
    """Build PipelineConfig from parsed TOML data."""
    kwargs: dict[str, Any] = {}

    for section_name, fields in SECTION_MAP.items():
        section = data.get(section_name, {})
        if not isinstance(section, dict):
            raise ConfigError(f"[{section_name}] must be a table, got {type(section).__name__}")
        for toml_key, field_name in fields.items():
            if toml_key in section:
                kwargs[field_name] = section[toml_key]

    # Nested sections (e.g., satisfaction.weights).
    for dotted_name, fields in NESTED_SECTIONS.items():
        parts = dotted_name.split(".")
        section = data
        for part in parts:
            section = section.get(part, {}) if isinstance(section, dict) else {}
        if isinstance(section, dict):
            for toml_key, field_name in fields.items():
                if toml_key in section:
                    kwargs[field_name] = section[toml_key]

    # [model.ids] — dynamic keys mapping role name → model ID.
    model_section = data.get("model", {})
    if isinstance(model_section, dict):
        ids_table = model_section.get("ids", {})
        if isinstance(ids_table, dict):
            kwargs["model_ids"] = {k: str(v) for k, v in ids_table.items()}

    # Deserialize [[twins]] from TOML array of tables.
    if "twins" in data:
        from darkfactory.twins.models import TwinSpec
        twins_data = data["twins"]
        if isinstance(twins_data, list):
            parsed_twins = []
            for i, t in enumerate(twins_data):
                try:
                    parsed_twins.append(TwinSpec(**t))
                except TypeError as e:
                    name = t.get("name", f"index {i}")
                    raise ConfigError(f"invalid [[twins]] entry '{name}': {e}") from e
            kwargs["twins"] = parsed_twins

    # Deserialize [[services]] from TOML array of tables.
    if "services" in data:
        from darkfactory.models.config import ServiceDef
        services_data = data["services"]
        if isinstance(services_data, list):
            parsed_services = []
            for i, s in enumerate(services_data):
                try:
                    parsed_services.append(ServiceDef(**s))
                except TypeError as e:
                    name = s.get("name", f"index {i}")
                    raise ConfigError(f"invalid [[services]] entry '{name}': {e}") from e
            kwargs["services"] = parsed_services

    # Deserialize canary_stages and canary_metrics from TOML arrays of tables.
    canary = data.get("canary", {})
    if isinstance(canary, dict):
        if "stages" in canary:
            from darkfactory.deployment.models import CanaryStage
            kwargs["canary_stages"] = [CanaryStage(**s) for s in canary["stages"]]
        if "metrics" in canary:
            from darkfactory.deployment.models import MetricThreshold
            kwargs["canary_metrics"] = [MetricThreshold(**m) for m in canary["metrics"]]

    # Extract [profile.*] tables — each profile name maps to a dict of its fields.
    profile_section = data.get("profile", {})
    if isinstance(profile_section, dict):
        kwargs["profiles"] = profile_section
    else:
        kwargs["profiles"] = {}

    return PipelineConfig(**kwargs)


# --- Legacy config.sh migration ---

# Mapping from bash variable names to (TOML section, TOML key).
_BASH_TO_TOML: dict[str, tuple[str, str]] = {
    "LANGUAGE": ("core", "language"),
    "SOURCE_DIRS": ("core", "source_dirs"),
    "SOURCE_DIR": ("core", "source_dirs"),
    "TEST_DIR": ("core", "test_dir"),
    "TEST_CMD": ("core", "test_cmd"),
    "SCENARIO_TEST_CMD": ("core", "scenario_test_cmd"),
    "DARK_FACTORY_HOME": ("core", "dark_factory_home"),
    "AGENT_MAX_TURNS": ("agents", "max_turns"),
    "AGENT_TIMEOUT": ("agents", "timeout"),
    "AGENT_RETRIES": ("agents", "retries"),
    "AGENT_MEMORY": ("agents", "memory"),
    "AGENT_CPUS": ("agents", "cpus"),
    "AGENT_IO_DEVICE": ("agents", "io_device"),
    "AGENT_IO_READ_BPS": ("agents", "io_read_bps"),
    "AGENT_IO_WRITE_BPS": ("agents", "io_write_bps"),
    "GATE_TIMEOUT": ("gates", "timeout"),
    "HARD_GATE_BUILD": ("gates", "hard_gate_build"),
    "MIN_SHIP_PASS_RATE": ("gates", "min_ship_pass_rate"),
    "SCENARIO_VALIDATION": ("gates", "validate_scenarios"),
    "SCENARIO_MIN_FILES": ("gates", "scenario_min_files"),
    "SCENARIO_MIN_ASSERTIONS_PER_FILE": ("gates", "scenario_min_assertions_per_file"),
    "MUTATION_CMD": ("gates", "mutation_cmd"),
    "BUILD_CMD": ("gates", "build_cmd"),
    "LINT_CMD": ("gates", "lint_cmd"),
    "TYPECHECK_CMD": ("gates", "typecheck_cmd"),
    "AUTONOMY_LEVEL": ("autonomy", "level"),
    "MAX_ITERATIONS": ("autonomy", "max_iterations"),
    "MAX_TOTAL_AGENT_TURNS": ("autonomy", "max_total_agent_turns"),
    "DYNAMIC_AUTONOMY": ("autonomy", "dynamic_autonomy"),
    "DYNAMIC_ITERATIONS": ("autonomy", "dynamic_iterations"),
    "DOCKER_NETWORK": ("docker", "network"),
    "EGRESS_FILTERING_ENABLED": ("docker", "egress_filtering"),
    "GIT_REMOTE": ("git", "remote"),
    "GIT_BASE_BRANCH": ("git", "base_branch"),
    "AUTO_PR": ("git", "auto_pr"),
    "MERGE_QUEUE_ENABLED": ("git", "merge_queue"),
    "AUTO_MERGE_HIGH_TRUST": ("git", "auto_merge_high_trust"),
    "GITOPS_SKIP_HOOKS": ("git", "skip_hooks"),
    "MIN_SATISFACTION_SCORE": ("satisfaction", "min_score"),
    "SCENARIO_MULTI_RUN_N": ("satisfaction", "multi_run_n"),
    "SCENARIO_MULTI_RUN_K": ("satisfaction", "multi_run_k"),
    "TRAJECTORY_PLATEAU_THRESHOLD": ("satisfaction", "plateau_threshold"),
    "SCENARIO_WEIGHT_CONTRACT": ("satisfaction.weights", "contract"),
    "SCENARIO_WEIGHT_HAPPY_PATH": ("satisfaction.weights", "happy_path"),
    "SCENARIO_WEIGHT_EDGE_CASES": ("satisfaction.weights", "edge_cases"),
    "SCENARIO_WEIGHT_ERROR_CONDITIONS": ("satisfaction.weights", "error_conditions"),
    "SCENARIO_WEIGHT_STATE_SEQUENCE": ("satisfaction.weights", "state_sequence"),
    "SCENARIO_WEIGHT_SECURITY": ("satisfaction.weights", "security"),
    "SCENARIO_WEIGHT_PROPERTY_BASED": ("satisfaction.weights", "property_based"),
    "SCENARIO_WEIGHT_ADVERSARIAL": ("satisfaction.weights", "adversarial"),
    "COST_BUDGET_USD": ("cost", "budget_usd"),
    "COST_PER_INPUT_TOKEN": ("cost", "per_input_token"),
    "COST_PER_OUTPUT_TOKEN": ("cost", "per_output_token"),
    "COST_EST_INPUT_TOKENS_PER_TURN": ("cost", "est_input_tokens_per_turn"),
    "COST_EST_OUTPUT_TOKENS_PER_TURN": ("cost", "est_output_tokens_per_turn"),
    "COST_ALERT_THRESHOLDS": ("cost", "alert_thresholds"),
    "ANOMALY_MAX_AGENT_TURNS": ("anomaly", "max_agent_turns"),
    "ANOMALY_MAX_SPEC_TURNS": ("anomaly", "max_spec_turns"),
    "ANOMALY_MAX_SPEC_WALL_S": ("anomaly", "max_spec_wall_s"),
    "ANOMALY_MAX_FILES_WRITTEN": ("anomaly", "max_files_written"),
    "ANOMALY_BASELINE_WINDOW": ("anomaly", "baseline_window"),
    "TRUST_MIN_HISTORY": ("trust", "min_history"),
    "MAX_CONCURRENT_SPECS": ("plan", "max_concurrent"),
    "PLAN_TOKEN_BUDGET": ("plan", "token_budget"),
    "PLAN_MAX_WALL_TIME_S": ("plan", "max_wall_time_s"),
    "CONFLICT_STRATEGY": ("plan", "conflict_strategy"),
    "WORKTREE_CLEANUP_ON_SHIP": ("plan", "worktree_cleanup_on_ship"),
    "WORKTREE_CLEANUP_ON_FAIL": ("plan", "worktree_cleanup_on_fail"),
    "NOTIFY_SLACK_WEBHOOK_URL": ("notifications", "slack_webhook_url"),
    "NOTIFY_WEBHOOK_URL": ("notifications", "webhook_url"),
    "REGRESSION_ON_SHIP": ("regression", "on_ship"),
    "REGRESSION_POLICY": ("regression", "policy"),
    "REGRESSION_TIMEOUT": ("regression", "timeout"),
    "TEST_BASE_URL":           ("test_server", "base_url"),
    "TEST_SERVER_CMD":         ("test_server", "server_cmd"),
    "SERVER_STARTUP_TIMEOUT":  ("test_server", "startup_timeout"),
    "SERVER_HEALTH_PATH":      ("test_server", "health_path"),
    "BROWSER_BASE_URL":        ("test_server", "browser_base_url"),
    "BROWSER_SERVER_CMD":      ("test_server", "browser_server_cmd"),
    "BROWSER_STARTUP_TIMEOUT": ("test_server", "browser_startup_timeout"),
    "DEPLOY_CMD": ("deploy", "cmd"),
    "DEPLOY_BASE_URL": ("deploy", "base_url"),
    "DEPLOY_HEALTH_ENDPOINT": ("deploy", "health_endpoint"),
    "DEPLOY_HEALTH_TIMEOUT": ("deploy", "health_timeout"),
    "DEPLOY_TEARDOWN_CMD": ("deploy", "teardown_cmd"),
    "DF_MODEL_ID": ("model", "id"),
    "DF_TOKEN_EXPIRES": ("auth", "token_expires"),
}

# Bash vars that hold colon-delimited lists → TOML arrays.
_COLON_DELIMITED = {"SOURCE_DIRS", "SOURCE_DIR"}
# Bash vars that hold comma-delimited lists → TOML arrays.
_COMMA_DELIMITED = {"COST_ALERT_THRESHOLDS"}
# Bash vars that are booleans (true/false strings).
_BOOLEANS = {
    "HARD_GATE_BUILD", "DYNAMIC_AUTONOMY", "DYNAMIC_ITERATIONS",
    "EGRESS_FILTERING_ENABLED", "AUTO_PR", "MERGE_QUEUE_ENABLED",
    "AUTO_MERGE_HIGH_TRUST", "GITOPS_SKIP_HOOKS", "REGRESSION_ON_SHIP",
    "WORKTREE_CLEANUP_ON_SHIP", "WORKTREE_CLEANUP_ON_FAIL",
}
# Bash vars that are integers.
_INTEGERS = {
    "AGENT_MAX_TURNS", "AGENT_TIMEOUT", "AGENT_RETRIES", "GATE_TIMEOUT",
    "MIN_SHIP_PASS_RATE", "SCENARIO_MIN_FILES", "SCENARIO_MIN_ASSERTIONS_PER_FILE",
    "AUTONOMY_LEVEL", "MAX_ITERATIONS", "MAX_TOTAL_AGENT_TURNS",
    "MIN_SATISFACTION_SCORE", "SCENARIO_MULTI_RUN_N", "SCENARIO_MULTI_RUN_K",
    "TRAJECTORY_PLATEAU_THRESHOLD", "COST_EST_INPUT_TOKENS_PER_TURN",
    "COST_EST_OUTPUT_TOKENS_PER_TURN", "ANOMALY_MAX_AGENT_TURNS",
    "ANOMALY_MAX_SPEC_TURNS", "ANOMALY_MAX_SPEC_WALL_S",
    "ANOMALY_MAX_FILES_WRITTEN", "ANOMALY_BASELINE_WINDOW",
    "TRUST_MIN_HISTORY", "MAX_CONCURRENT_SPECS", "PLAN_TOKEN_BUDGET",
    "PLAN_MAX_WALL_TIME_S", "REGRESSION_TIMEOUT", "DEPLOY_HEALTH_TIMEOUT",
    "SCENARIO_WEIGHT_CONTRACT", "SCENARIO_WEIGHT_HAPPY_PATH",
    "SCENARIO_WEIGHT_EDGE_CASES", "SCENARIO_WEIGHT_ERROR_CONDITIONS",
    "SCENARIO_WEIGHT_STATE_SEQUENCE", "SCENARIO_WEIGHT_SECURITY",
    "SCENARIO_WEIGHT_PROPERTY_BASED", "SCENARIO_WEIGHT_ADVERSARIAL",
    "SERVER_STARTUP_TIMEOUT", "BROWSER_STARTUP_TIMEOUT",
}
# Bash vars that are floats.
_FLOATS = {"COST_BUDGET_USD", "COST_PER_INPUT_TOKEN", "COST_PER_OUTPUT_TOKEN"}
# Auth tokens — never written to TOML.
_AUTH_TOKENS = {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}

_ASSIGNMENT_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$')


def parse_bash_config(path: Path) -> dict[str, str]:
    """Parse a bash config.sh into key=value pairs.

    Only simple assignments are supported. Raises ConfigError on
    shell metacharacters or complex expressions.
    """
    result: dict[str, str] = {}
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Strip optional 'export' prefix.
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        m = _ASSIGNMENT_RE.match(stripped)
        if not m:
            raise ConfigError(f"{path}:{lineno}: not a simple assignment: {stripped}")
        key, value = m.group(1), m.group(2)
        # Strip surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        # Reject shell metacharacters.
        if any(c in value for c in ("$", "`", "(", ")", "{", "}", ";", "|", "&")):
            raise ConfigError(f"{path}:{lineno}: shell metacharacter in value: {value}")
        result[key] = value
    return result


def migrate_config(input_path: Path, output_path: Path) -> str:
    """Convert a bash config.sh to TOML format.

    Returns the TOML string and writes it to output_path.
    """
    raw = parse_bash_config(input_path)

    # Group by TOML section.
    sections: dict[str, list[str]] = {}
    for bash_var, value in raw.items():
        if bash_var in _AUTH_TOKENS:
            continue
        if bash_var not in _BASH_TO_TOML:
            log.warning("unknown config variable: %s (skipped)", bash_var)
            continue
        section, toml_key = _BASH_TO_TOML[bash_var]
        toml_value = _convert_value(bash_var, value)
        if toml_value is None:
            continue
        sections.setdefault(section, []).append(f"{toml_key} = {toml_value}")

    # Build TOML output.
    lines: list[str] = []
    for section_name in _section_order():
        if section_name not in sections:
            continue
        lines.append(f"[{section_name}]")
        for entry in sections[section_name]:
            lines.append(entry)
        lines.append("")

    toml_str = "\n".join(lines)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(toml_str)
    return toml_str


def _convert_value(bash_var: str, value: str) -> str | None:
    """Convert a bash value string to TOML representation."""
    if not value:
        return None

    if bash_var in _COLON_DELIMITED:
        items = [v for v in value.split(":") if v]
        return "[" + ", ".join(f'"{item}"' for item in items) + "]"

    if bash_var in _COMMA_DELIMITED:
        items = [v.strip() for v in value.split(",") if v.strip()]
        return "[" + ", ".join(items) + "]"

    if bash_var in _BOOLEANS:
        return "true" if value.lower() == "true" else "false"

    if bash_var in _INTEGERS:
        return value

    if bash_var in _FLOATS:
        return value

    # Default: string.
    return f'"{value}"'


def _section_order() -> list[str]:
    """Return TOML sections in canonical order."""
    return [
        "core", "agents", "gates", "autonomy", "docker", "git",
        "satisfaction", "satisfaction.weights", "cost", "anomaly",
        "trust", "plan", "notifications", "regression", "implementer",
        "test_server", "deploy", "model", "auth",
    ]
