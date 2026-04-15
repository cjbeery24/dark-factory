"""TypeScript + Node ecosystem adapter (A2 extraction).

Houses everything the framework knows about TypeScript / pnpm / Node
that used to be smeared across ``darkfactory/spec/`` and
``darkfactory/scenarios/``:

- ``library_symbols`` — A3 spec-import → declared-dependency check
  (Node-builtins-aware, pnpm-workspace-aware).
- ``stub_compile`` — A6 §5 Interface Contract round-trip via
  ``tsc --noEmit`` against the project's pinned tsconfig.
- ``proposal_rehearsal`` — C2 LLM-emitted proposal-test typecheck
  (reuses A6's tsc helpers).
- ``adapter`` — ``TypescriptNodeAdapter`` glues the three above into
  the ``EcosystemAdapter`` ABC surface.
- ``architect_addendum.md`` — prompt fragment injected into the
  architect's prompt when this adapter is active. Currently a stub
  pointing at the architect-prompt sections that will migrate here in
  A4; A4 fills it with the real Node-specific guidance extracted from
  ``agents/architect.md``.

Old import paths under ``darkfactory.spec`` / ``darkfactory.scenarios``
keep working via deprecation shims for two minor versions per the
governance lifecycle (``docs/ADAPTER-GOVERNANCE.md``); the shims emit
``DeprecationWarning`` on import.
"""

from darkfactory.adapters.typescript.adapter import TypescriptNodeAdapter

__all__ = ["TypescriptNodeAdapter"]
