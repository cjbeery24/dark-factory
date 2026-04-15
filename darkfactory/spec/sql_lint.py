"""Deprecation shim — module moved to ``darkfactory.adapters.postgres.sql_lint``.

Sprint 2 A3 (``docs/plans/2026-04-14-pipeline-modularity-and-remediation.md``)
extracted this module out of ``spec/`` into the Postgres ecosystem
adapter. SQL dialect knowledge belongs to the Postgres adapter, not
the pipeline spec-package.

Lifecycle (per ``docs/RELEASE-CADENCE.md`` + ``docs/ADAPTER-GOVERNANCE.md``):

1. **Now (deprecation):** import emits a single ``DeprecationWarning``
   pointing callers at the new path. Every public symbol listed in
   ``__all__`` is re-exported.
2. **Next minor release:** warning re-emphasized in CHANGELOG; shim
   stays.
3. **Two minor releases from now:** shim removed; ``ImportError`` is
   the explicit failure callers must fix.

Mirrors the A2 shim pattern at ``darkfactory/spec/library_symbols.py``
so the lifecycle contract is uniform across ecosystem extractions. The
``DeprecationWarning`` fires at import time (not at attribute access)
so the migration signal is loud. Python defaults silence
``DeprecationWarning`` outside ``__main__``; our ``pyproject.toml``
``[tool.pytest.ini_options]`` sets ``filterwarnings =
["default::DeprecationWarning"]`` so the warning surfaces in the test
summary.

Internal state (``_PGLAST_AVAILABLE``) is deliberately NOT re-exported.
Tests and callers that monkeypatch implementation state must import
from ``darkfactory.adapters.postgres.sql_lint`` directly — the shim's
contract is the public API only.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "darkfactory.spec.sql_lint moved to "
    "darkfactory.adapters.postgres.sql_lint (Sprint 2 A3). "
    "Update imports; this shim is removed two minor releases from now.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export every public symbol the previous module exposed. The
# explicit list (rather than ``import *``) keeps the public surface
# auditable — when adding/removing a public symbol from the new module,
# update this list deliberately rather than relying on ``__all__``
# inference.
from darkfactory.adapters.postgres.sql_lint import (  # noqa: E402, F401
    SqlLintFinding,
    lint_sql_fences,
    sql_fences_present,
)

__all__ = [
    "SqlLintFinding",
    "lint_sql_fences",
    "sql_fences_present",
]
