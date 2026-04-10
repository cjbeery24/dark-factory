"""Resolved config diagnostics — shows which layer set each operational field."""

from __future__ import annotations

from darkfactory.models.config import PipelineConfig

# Operational fields whose provenance is tracked for diagnostics.
_OPERATIONAL_FIELDS = (
    "language",
    "source_dirs",
    "test_dir",
    "build_cmd",
    "lint_cmd",
    "typecheck_cmd",
    "test_cmd",
    "setup_cmd",
    "test_server_cmd",
    "server_setup_cmd",
    "server_health_path",
    "server_startup_timeout",
)


def resolved_config_dump(config: PipelineConfig) -> str:
    """Return a human-readable dump of operational fields with provenance.

    Example output::

        build_cmd = "pnpm build"  # source: environment.toml
        lint_cmd = ""  # source: default
    """
    lines: list[str] = ["Resolved operational config:"]
    for field_name in _OPERATIONAL_FIELDS:
        value = getattr(config, field_name, "")
        source = config.get_provenance(field_name)
        lines.append(f"  {field_name} = {value!r}  # source: {source}")
    return "\n".join(lines)
