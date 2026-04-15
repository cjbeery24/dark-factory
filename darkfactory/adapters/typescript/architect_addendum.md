<!--
A4 placeholder. The Node/TypeScript-specific guidance currently
inside `agents/architect.md` lines 137-216 (DATABASE SCHEMA SETUP /
MONOREPO COMMAND SCOPING / TEST INTEGRATION CONTRACT) migrates here
in A4. A2 ships only the surface so the registry has something to
inject; A4 ships the content with the equivalence-harness validation
the plan requires.

Until A4 lands, this addendum is intentionally minimal. The real
TypeScript-specific reasoning still lives in `agents/architect.md` and
is exercised end-to-end there. Removing it from `architect.md` *before*
the equivalence harness can prove no manifest regression would skip the
A4 acceptance criterion.

Governance cap: 80 non-blank lines. Enforced by the contract harness
(`test_architect_addendum_within_governance_line_cap`).
-->

## TypeScript + Node addendum

This adapter is active because the project declares a Node toolchain
(`package.json` and/or `tsconfig.json` at the project root). The
following expectations hold for every manifest you emit while it is
active:

- **Module resolution:** the project's `tsconfig.json` is the source
  of truth for module/strict/path settings. Reuse it; do not invent
  parallel resolution rules in the manifest.
- **Dependency declaration:** every package referenced from spec §5
  Interface Contract fences must already be a `dependency` or
  `devDependency` somewhere in the workspace tree. Library precheck
  enforces this; if precheck flagged an undeclared package, fix the
  spec or the project's `package.json` rather than asking the
  implementer to add the dep ad-hoc.
- **Stub round-trip:** spec §5 fences are typechecked with
  `tsc --noEmit` against the pinned library surface before the
  architect runs. A failure there means the spec's declared call shape
  doesn't match the installed library — re-author the spec, do not
  shape the manifest around the broken call.
- **Migrations and schema setup:** project-hints `[policy]` decides
  migrate-mode vs push-mode, not this adapter and not the architect.
  Read `.dark-factory/project-hints.toml` (when present) and respect
  the project's stated discipline.
