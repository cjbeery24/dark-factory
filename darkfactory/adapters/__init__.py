"""Per-ecosystem adapters.

The pipeline core dispatches ecosystem-specific behavior — TypeScript
build commands, Postgres SQL linting, pnpm dependency installation,
etc. — through ``EcosystemAdapter`` implementations defined under this
package. The core knows nothing about any specific ecosystem; that
knowledge lives behind the ABC at ``base.py``.

Sprint 1 status: ``base.py`` ships the ABC (A1). Concrete adapters
(``typescript/``, ``postgres/``) land in A2 and A3.

Public surface and lifecycle policy: ``docs/ADAPTER-GOVERNANCE.md``.
"""

from darkfactory.adapters.base import (
    EcosystemAdapter,
    Finding,
    GateCommands,
)

__all__ = ["EcosystemAdapter", "Finding", "GateCommands"]
