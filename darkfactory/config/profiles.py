"""Per-spec environment profile resolution.

Resolves per-spec profiles and field overrides into a new PipelineConfig.
Handles precedence: per-spec override > profile value > global config.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from pathlib import Path

from darkfactory.errors import ConfigError
from darkfactory.models.config import PipelineConfig

log = logging.getLogger(__name__)

# Regex patterns for metadata parsing
_METADATA_START_RE = re.compile(r'<!--\s*dark-factory-metadata')
_METADATA_END_RE = re.compile(r'-->')
_FIELD_RE = re.compile(r'^([a-z-]+):\s*(.*)$')

# Mapping from metadata field names to PipelineConfig field names
_METADATA_TO_CONFIG_FIELD = {
    "profile": None,  # Special case - not a config field
    "language": "language",
    "source-dirs": "source_dirs",
    "test-dir": "test_dir",
    "test-cmd": "test_cmd",
    "lint-cmd": "lint_cmd",
    "build-cmd": "build_cmd",
    "typecheck-cmd": "typecheck_cmd",
    "scenario-test-cmd": "scenario_test_cmd",
    "test-server-cmd":         "test_server_cmd",
    "test-base-url":           "test_base_url",
    "browser-server-cmd":      "browser_server_cmd",
    "browser-base-url":        "browser_base_url",
    "server-health-path":      "server_health_path",
    "server-startup-timeout":  "server_startup_timeout",
    "browser-startup-timeout": "browser_startup_timeout",
}

# Mapping from profile TOML keys to PipelineConfig field names
_PROFILE_TO_CONFIG_FIELD = {
    "language": "language",
    "source_dirs": "source_dirs",
    "test_dir": "test_dir",
    "test_cmd": "test_cmd",
    "lint_cmd": "lint_cmd",
    "build_cmd": "build_cmd",
    "typecheck_cmd": "typecheck_cmd",
    "scenario_test_cmd": "scenario_test_cmd",
    "test_server_cmd":         "test_server_cmd",
    "test_base_url":           "test_base_url",
    "browser_server_cmd":      "browser_server_cmd",
    "browser_base_url":        "browser_base_url",
    "server_health_path":      "server_health_path",
    "server_startup_timeout":  "server_startup_timeout",
    "browser_startup_timeout": "browser_startup_timeout",
}

# Config fields that must be integers (spec metadata values arrive as strings).
_INT_CONFIG_FIELDS = {"server_startup_timeout", "browser_startup_timeout"}


def resolve_profile(
    spec_file: Path,
    config: PipelineConfig,
) -> PipelineConfig:
    """Resolve per-spec profile and overrides into a new PipelineConfig.

    Reads the spec's dark-factory-metadata block for `profile:` and per-spec
    field overrides. Returns a new PipelineConfig with resolved values.

    Precedence (highest to lowest):
      1. Per-spec field override from metadata
      2. Profile value from config.profiles[name]
      3. Existing value in config (global defaults)

    Args:
        spec_file: Path to the spec markdown file.
        config: The base PipelineConfig (not mutated).

    Returns:
        A new PipelineConfig with profile-resolved values. If no profile or
        overrides are declared, returns the original config object unchanged.

    Raises:
        ConfigError: If profile: references a name not in config.profiles.
    """
    # Parse metadata from the spec file
    metadata = parse_spec_metadata(spec_file)

    # If no profile or overrides, return original config unchanged
    if not metadata:
        return config

    profile_name = metadata.get("profile")

    # Validate profile exists if specified
    if profile_name and profile_name not in config.profiles:
        raise ConfigError(
            f"Spec references profile '{profile_name}' but no [profile.{profile_name}] table found in config.toml"
        )

    # Get profile data if specified
    profile_data = config.profiles.get(profile_name, {}) if profile_name else {}

    # Start with current config values as base
    resolved_kwargs = {}

    # Track which fields were overridden for logging
    profile_overrides = []
    spec_overrides = []

    # Apply profile values and per-spec overrides
    for metadata_field, config_field in _METADATA_TO_CONFIG_FIELD.items():
        if config_field is None:  # Skip "profile" field
            continue

        # Get current config value
        current_value = getattr(config, config_field)

        # Check for profile value
        profile_value = None
        profile_toml_key = None
        for toml_key, field_name in _PROFILE_TO_CONFIG_FIELD.items():
            if field_name == config_field:
                profile_toml_key = toml_key
                if toml_key in profile_data:
                    profile_value = profile_data[toml_key]
                    # Empty string values in profiles are treated as "not set"
                    if profile_value == "":
                        profile_value = None
                break

        # Check for per-spec override
        spec_override = metadata.get(metadata_field)
        # Empty values after colon are treated as "not set"
        if spec_override == "":
            spec_override = None

        # Apply precedence: spec override > profile > current
        final_value = current_value

        if spec_override is not None:
            # Handle special case for source-dirs (colon-delimited)
            if metadata_field == "source-dirs":
                if ":" in spec_override:
                    final_value = [d.strip() for d in spec_override.split(":") if d.strip()]
                else:
                    final_value = [spec_override]
            else:
                if config_field in _INT_CONFIG_FIELDS:
                    try:
                        final_value = int(spec_override)
                    except ValueError:
                        raise ConfigError(
                            f"Spec field '{metadata_field}' must be an integer, got {spec_override!r}"
                        )
                else:
                    final_value = spec_override
            spec_overrides.append(f"{config_field}={final_value!r}")
        elif profile_value is not None:
            final_value = profile_value
            if profile_toml_key:
                profile_overrides.append(f"{config_field}={final_value!r}")

        # Known limitation: if the final_value equals the current default, this guard
        # silently drops the override. Users overriding a field back to its default value
        # will see no effect. See docs/SPECIFICATION-GUIDE.md for details.
        if final_value != current_value:
            resolved_kwargs[config_field] = final_value

    # If no changes, return original config
    if not resolved_kwargs:
        return config

    # Log the resolution
    log_parts = []
    if profile_name:
        log_parts.append(f"profile '{profile_name}'")
    if profile_overrides:
        log_parts.append(f"profile overrides: {', '.join(profile_overrides)}")
    if spec_overrides:
        log_parts.append(f"spec overrides: {', '.join(spec_overrides)}")

    if log_parts:
        log.info("Resolved %s", ", ".join(log_parts))

    # Create new config with resolved values
    return dataclasses.replace(config, **resolved_kwargs)


def parse_spec_metadata(spec_file: Path) -> dict[str, str]:
    """Extract dark-factory-metadata fields from a spec file.

    Returns a dict of field_name -> value for all recognized fields.
    Unrecognized fields are silently ignored.
    Fields with empty values are omitted from the result.

    Recognized fields: profile, language, source-dirs, test-dir,
    test-cmd, lint-cmd, build-cmd, typecheck-cmd, scenario-test-cmd,
    test-server-cmd, test-base-url, browser-server-cmd, browser-base-url,
    server-health-path, server-startup-timeout, browser-startup-timeout.
    """
    if not spec_file.exists():
        return {}

    metadata = {}
    in_metadata_block = False

    try:
        content = spec_file.read_text()
    except (OSError, IOError) as e:
        # If we can't read the file (permissions, encoding issues, etc.), return empty metadata
        log.debug("Failed to read spec file %s: %s", spec_file, e)
        return {}

    for line in content.splitlines():
        # Check for metadata block start
        if _METADATA_START_RE.search(line):
            in_metadata_block = True
            continue

        # Check for metadata block end
        if in_metadata_block and _METADATA_END_RE.search(line):
            break

        # Parse field lines within metadata block
        if in_metadata_block:
            match = _FIELD_RE.match(line.strip())
            if match:
                field_name, value = match.groups()
                field_name = field_name.strip()
                value = value.strip()

                # Only include recognized fields
                if field_name in _METADATA_TO_CONFIG_FIELD:
                    # Only include non-empty values (consistent with spec requirements)
                    if value:
                        metadata[field_name] = value

    return metadata