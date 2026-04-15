"""Per-ecosystem adapters.

The pipeline core dispatches ecosystem-specific behavior — TypeScript
build commands, Postgres SQL linting, pnpm dependency installation,
etc. — through ``EcosystemAdapter`` implementations defined under this
package. The core knows nothing about any specific ecosystem; that
knowledge lives behind the ABC at ``base.py``.

Sprint 1 status: ``base.py`` ships the ABC (A1).
``typescript/TypescriptNodeAdapter`` lands in A2; ``postgres/`` arrives
in A3. The package is the public surface that adapter authors import
from; ``base`` is internal to that surface.

Public surface and lifecycle policy: ``docs/ADAPTER-GOVERNANCE.md``.
"""

from darkfactory.adapters.base import (
    GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP,
    SEVERITIES,
    EcosystemAdapter,
    Finding,
    GateCommands,
    Severity,
)

__all__ = [
    "EcosystemAdapter",
    "Finding",
    "GateCommands",
    "GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP",
    "SEVERITIES",
    "Severity",
]
