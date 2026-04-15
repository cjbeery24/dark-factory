"""Deprecation shim — module moved to ``darkfactory.adapters.typescript.stub_compile``.

Sprint 1 A2 (``docs/plans/2026-04-14-pipeline-modularity-and-remediation.md``)
extracted this module out of ``spec/`` into the TypeScript ecosystem
adapter. See the sibling ``library_symbols`` shim for full lifecycle
notes; the policy is identical.

Public symbols are re-exported. Private helpers (``_find_tsc``,
``_synthesize_tsconfig``, etc.) are intentionally not re-exported
through the shim — they were never part of the public surface; the
sibling ``proposal_rehearsal`` adapter module imports them through
the new path directly.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "darkfactory.spec.stub_compile moved to "
    "darkfactory.adapters.typescript.stub_compile (Sprint 1 A2). "
    "Update imports; this shim is removed two minor releases from now.",
    DeprecationWarning,
    stacklevel=2,
)

from darkfactory.adapters.typescript.stub_compile import (  # noqa: E402, F401
    InterfaceStub,
    StubCompileFinding,
    check_stub_roundtrip,
    extract_interface_stubs,
)

__all__ = [
    "InterfaceStub",
    "StubCompileFinding",
    "check_stub_roundtrip",
    "extract_interface_stubs",
]
