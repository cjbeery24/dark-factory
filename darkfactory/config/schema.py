"""Config schema validation.

Type and range validation is handled by PipelineConfig.__post_init__.
This module provides spec name validation and raw TOML structure checks.
"""

from __future__ import annotations

import re

from darkfactory.errors import ConfigError

_SPEC_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$')


def validate_spec_name(name: str) -> None:
    """Validate a spec name matches the allowed pattern."""
    if not _SPEC_NAME_RE.match(name):
        raise ConfigError(
            f"invalid spec name {name!r}: must match [a-zA-Z0-9][a-zA-Z0-9._-]*"
        )
