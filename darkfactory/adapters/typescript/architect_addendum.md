<!--
A4 migrated the MONOREPO COMMAND SCOPING and TEST INTEGRATION
CONTRACT sections out of `agents/architect.md` into this file.
DATABASE SCHEMA SETUP moved to the Postgres addendum (see
`darkfactory/adapters/postgres/architect_addendum.md`). Migration
discipline (migrate-mode vs push-mode) is project policy and lives
in the Postgres addendum — do not duplicate it here.

Governance cap: 80 non-blank lines. Enforced by the contract harness
(`test_architect_addendum_within_governance_line_cap`).
-->

## TypeScript + Node addendum

- **Bootstrap runtime shape:** `language: node`; `package_manager`
  matches the lockfile (`pnpm`/`npm`/`yarn`); `commands.build`,
  `commands.test`, `commands.server_start` use that package manager
  (e.g., `pnpm build`, `pnpm test`, `pnpm dev`). Chain test commands
  only when the spec requires it (e.g., `pnpm test && pnpm test:e2e`).
- **Dependency manifest:** `target: package.json` — the workspace's,
  not the root, on monorepos.
- **Module resolution:** the project's `tsconfig.json` is the source
  of truth for module/strict/path settings. Reuse it; do not invent
  parallel resolution rules in the manifest.
- **Dependency declaration:** every package referenced from spec §5
  Interface Contract fences MUST already be a `dependency` or
  `devDependency` somewhere in the workspace tree. Library precheck
  enforces this; if precheck flagged an undeclared package, fix the
  spec or the project's `package.json` rather than asking the
  implementer to add the dep ad-hoc.
- **Stub round-trip:** spec §5 fences are typechecked with
  `tsc --noEmit` against the pinned library surface before the
  architect runs. A failure there means the spec's declared call
  shape doesn't match the installed library — re-author the spec.

### Monorepo command scoping

For Node monorepos (detected via `pnpm-workspace.yaml`, top-level
`apps/`/`packages/`, or `workspaces` in `package.json`), the core
prompt's MONOREPO COMMAND SCOPING rule applies. Do NOT use recursive
flags that fan out across workspaces (`pnpm -r`/`--recursive`,
`yarn workspaces foreach`, `npm --workspaces`, `nx run-many`,
`turbo run --filter='*'`, `lerna run`). Correct form for a spec on
`apps/api`: every `commands.*` does `cd apps/api && <tool>`.

### test_integration contract

The core manifest schema declares a `test_integration` block (see
`agents/architect.md` Step 5 for the YAML shape). Node/TypeScript
specs MUST populate every field — this is what prevents module-
resolution mismatches from crashing blind scenarios at import time.

**`module_system` + `import_extension`:**
- `esm` when tsconfig has `"module": "NodeNext" | "ESNext" | "Node16"`
  AND package.json has `"type": "module"`. Pair with
  `import_extension: ".js"` (TS imports relative paths with `.js`).
- `cjs` when tsconfig has `"module": "CommonJS"` and package.json
  lacks `"type": "module"` (or sets `"commonjs"`). Pair with
  `import_extension: ""` (empty string, NOT the word `"none"`).
- `dual` only when shipping both ESM+CJS via conditional `exports`
  with a matching build step; when in doubt, pick `esm`.

**`specifier`:** each entry_point's `specifier` is the literal string
the Test Engineer writes inside `import { ... } from '<specifier>'`
from a file under `.dark-factory/scenarios/<spec>/*.test.ts`. Three
strategies:

1. **Relative path (when in doubt, pick this).** Compute relative from
   `.dark-factory/scenarios/<spec>/` to the entry-point file, then
   swap the source extension for `import_extension`. Works without
   additional config. Example: path `apps/api/src/server.ts` with
   `import_extension: ".js"` →
   `specifier: "../../../apps/api/src/server.js"`. For a CJS project
   with `import_extension: ""`, the specifier omits the extension
   entirely → `specifier: "../../../src/server"`.
2. **tsconfig.paths alias.** Use only if the project's tsconfig
   already declares `paths` AND the vitest config honors them (e.g.,
   via `vite-tsconfig-paths`). Verify both — guessing produces
   import crashes.
3. **Bare specifier.** Only for published workspace packages with
   conditional `exports`. Rare in feature specs.

**`entry_points.exports`:** at least one named export is required.
`default` exports are discouraged — they resolve differently under
ESM/CJS interop and produce blind-scenario import crashes the Test
Engineer cannot debug. List named symbols only. **Do NOT list
type-only names** (`export type`, `export interface`, `export {
type X }`) — these are erased at compile time and fail the Import
Contract gate's `Object.keys()` check. Structural types belong in
spec §5 Interface Contract, not in the manifest's runtime exports.

The Test Engineer writes the specifier verbatim; the Implementer
MUST ensure it resolves to `path` with all declared `exports` as
named exports of the resolved module.

Apply the CWD rule to `vitest_config` / `tsconfig` paths: if the
server runs from `apps/api/`, the config files are
`apps/api/vitest.config.ts` / `apps/api/tsconfig.json`, not root.
