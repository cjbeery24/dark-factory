"""Deprecation shim — module moved to ``darkfactory.adapters.typescript.library_symbols``.

Sprint 1 A2 (``docs/plans/2026-04-14-pipeline-modularity-and-remediation.md``)
extracted this module out of ``spec/`` into the TypeScript ecosystem
adapter. This shim keeps existing call sites working while downstream
projects migrate.

Lifecycle (per ``docs/RELEASE-CADENCE.md`` + ``docs/ADAPTER-GOVERNANCE.md``):

1. **Now (deprecation):** import emits a single ``DeprecationWarning``
   pointing callers at the new path. Every public symbol listed in
   ``__all__`` is re-exported.
2. **Next minor release:** warning re-emphasized in CHANGELOG; shim
   stays.
3. **Two minor releases from now:** shim removed; ``ImportError`` is
   the explicit failure callers must fix.

The ``DeprecationWarning`` fires at import time (not at attribute
access) so the migration signal is loud. Python defaults silence
``DeprecationWarning`` outside ``__main__``; our ``pyproject.toml``
``[tool.pytest.ini_options]`` sets ``filterwarnings =
["default::DeprecationWarning"]`` so the warning surfaces in the test
summary. Outside the test runner the warning is subject to the host
process's warning filter.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "darkfactory.spec.library_symbols moved to "
    "darkfactory.adapters.typescript.library_symbols (Sprint 1 A2). "
    "Update imports; this shim is removed two minor releases from now.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export every public symbol the previous module exposed. The
# explicit list (rather than ``import *``) keeps the public surface
# auditable — when adding/removing a public symbol from the new module,
# update this list deliberately rather than relying on ``__all__``
# inference.
from darkfactory.adapters.typescript.library_symbols import (  # noqa: E402, F401
    LibrarySymbolFinding,
    PackageManifest,
    ProjectDependencyIndex,
    build_dependency_index,
    check_library_symbols,
    extract_spec_imports,
)

__all__ = [
    "LibrarySymbolFinding",
    "PackageManifest",
    "ProjectDependencyIndex",
    "build_dependency_index",
    "check_library_symbols",
    "extract_spec_imports",
]
