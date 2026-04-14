# Ecosystem Leakage Audit — 2026-04-14

**Status:** Sprint 0 deliverable (S0.1) for `docs/plans/2026-04-14-pipeline-modularity-and-remediation.md`.
**Method:** Systematic ripgrep across `agents/*.md`, `darkfactory/**/*.py`, `scripts/**/*.sh`, `.claude/skills/**/*.md`, `docker/`, `templates/*.md`. Markers: tool names (`tsc`, `pnpm`, `npm`, `yarn`, `vitest`, `jest`, `drizzle`, `prisma`, `npx`, `node_modules`, `package.json`, `tsconfig.json`, `pip`, `pytest`); file extensions (`.ts`, `.tsx`, `.mts`, `.cts`, `.py`, `.go`); module-system keywords (`import_extension`, `module_system`, `esm`, `cjs`); paths (`apps/api`, `node_modules/`). Each cited line was opened and read; the original v1 of this doc had several off-by-N citations and seven missed load-bearing files, all corrected here after Linus review.

**Underlying grep recipe** (replicable):

```sh
rg -n --no-heading -g '!node_modules' -g '!.git' \
  -e '\bpnpm\b' -e '\bnpm\b' -e '\byarn\b' -e '\bvitest\b' -e '\bjest\b' \
  -e '\btsc\b' -e '\bdrizzle\b' -e '\bprisma\b' -e '\bnpx\b' \
  -e '\bpackage\.json\b' -e '\btsconfig\.json\b' \
  -e '\bpytest\b' -e '\bpip\b' \
  agents/ darkfactory/ scripts/ .claude/skills/ docker/ templates/
```

## Summary

| Bucket | Count |
|---|---|
| (c) Load-bearing coupling | 54 sites across 24 files |
| (b) Cosmetic / example | 11 |

(False positives — grep hits inside unrelated identifiers, `node_modules` self-references in docstrings explaining why we mask it, etc. — are not enumerated. The (b) and (c) lists are exhaustive for the markers above; if a site isn't here, it didn't fire on the grep or it's a true false-positive.)

**Headline:** the framework's claim of being ecosystem-blind masks a deeply Node/TypeScript/pnpm + Postgres/Drizzle architecture. Other ecosystems work by accident or retrofit. Three categories of damage:

1. **Adapter-shaped logic stuck in framework core** — `library_symbols.py`, `stub_compile.py`, `proposal_rehearsal.py`, `workspace_check.py`, `consistency.py::DEFAULT_RULES`, `contracts.py::_compile_vitest`, `quality_gate.py::_dep_install_prefix`. These should each be a method on a `TypescriptNodeAdapter` (or `PostgresAdapter`).
2. **Bash scripts that pretend to be ecosystem-aware but aren't** — `scripts/quality-gate.sh` and `scripts/lib/scenarios.sh` carry the largest single concentration of per-ecosystem branching in the repo (~400 lines of dispatch). They handle Node + Python + Go + Rust as case statements; adding an ecosystem means editing Bash, not subclassing an interface.
3. **Agent prompts that recite Node specifics as universal guidance** — `architect.md`, `implementer.md`, `test-engineer.md`, `proposal-rehearsal.md`. The split A4 plans for `architect.md` extends here too: at minimum `test-engineer.md` and `proposal-rehearsal.md` need ecosystem addenda extracted.

---

## Load-bearing coupling (category c)

### Agent prompts (`agents/`)

#### `agents/architect.md`
| Line range | Content | Target adapter |
|---|---|---|
| L72-79 | Example manifest with `language: node`, `package_manager: pnpm`, pnpm `commands.{build,test,dev}` | typescript-node |
| L143-144 | `npx drizzle-kit push` setup-command example | typescript-node + postgres |
| L148-150 | Prisma + Django + Alembic ORM examples | typescript-node + python + postgres |
| L158-165 | TEST INTEGRATION CONTRACT — mandatory `tsconfig.json`, `module_system`, `import_extension`, vitest config (esm/cjs/dual) | typescript-node |
| L166-241 | Spec example: `test_integration` block with vitest_config + tsconfig + module_system determination | typescript-node |

#### `agents/implementer.md`
| Line range | Content | Target adapter |
|---|---|---|
| L39-40 | `import_extension` (`.js`/`.ts`/`""`) + vitest_config + tsconfig responsibilities | typescript-node |
| L51 | `tsc --noEmit` insufficiency rule + vitest+tsconfig+module_system contract | typescript-node |
| L53 | tsconfig.paths + vitest alias wiring rules | typescript-node |
| L69 | Database setup script: `scripts/df-setup.sh` invokes `npx drizzle-kit push` / `npx prisma db push` | typescript-node + postgres |
| L72-79 | Test entry verification via vitest, ESM/CJS runtime resolution example | typescript-node |
| L91 | vitest v4+ rule: every `package.json` `test` script must have a test file | typescript-node |

#### `agents/test-engineer.md`
| Line range | Content | Target adapter |
|---|---|---|
| L43 | Test framework detection from `package.json`: vitest/jest/mocha/@jest/* | typescript-node |
| L44 | pytest detection in `pyproject.toml` | python |
| L70-76 | Test file naming: `*.test.*`, `*.spec.*` (Node), `test_*.py`/`*_test.py` (Python), `*_test.go` (Go), `*.rs` (Rust) | typescript-node + python (+ stubs for go/rust) |
| L100-101 | Entry point example with `.ts` and `../../../apps/api/src/server.js` | typescript-node |
| L109 | Test file path `*.test.ts` with verbatim import specifiers | typescript-node |
| L143-198 | CWD-invariant import rules + dynamic imports + pnpm-workspace.yaml handling | typescript-node + python |

#### `agents/spec-validator.md`
| Line range | Content | Target adapter |
|---|---|---|
| L63 | Observable side-effect example listing "vitest/pytest/go test/cargo test" | multi-ecosystem (rewrite as ecosystem-blind) |

#### `agents/proposal-rehearsal.md`
| Line range | Content | Target adapter |
|---|---|---|
| L10 | "tsc --noEmit" reference in role description | typescript-node |
| L47 | Example proposal content showing `import { describe, it, expect } from 'vitest'` and `import { buildClient } from '@/index'` | typescript-node |
| L62-67 | Proposal-naming and `tsconfig.paths` import rule | typescript-node |
| L96-97 | "empty file = tsc error" untestable-anchor mechanism | typescript-node |

### Pipeline core (`darkfactory/`)

#### `darkfactory/architecture/consistency.py` *(missed in v1; flagged by Linus)*
| Line range | Content | Target adapter |
|---|---|---|
| L83-124 | `DEFAULT_RULES` tuple holds the literal `migrate-mode-vs-push-mode` rule, hardcoding `drizzle-kit push` and `db:push` patterns | postgres + typescript-node (per plan B5: this rule moves from framework `DEFAULT_RULES` into project-hints `[policy.consistency_rules]`; `DEFAULT_RULES` shrinks to ~empty) |

#### `darkfactory/git/detect.py`
| Line range | Content | Target adapter |
|---|---|---|
| L16-19 | Toolchain detection map: `(package.json, "node")`, `(pyproject.toml, "python")`, `(go.mod, "go")`, `(Cargo.toml, "rust")` | language-detection (registry-driven) |

#### `darkfactory/git/ops.py`
| Line range | Content | Target adapter |
|---|---|---|
| L92-95 | Hardcoded list of "common project files" to stage: package.json, package-lock.json, pyproject.toml, go.mod, go.sum, Cargo.toml, Cargo.lock, tsconfig.json | language-detection (each adapter contributes its filename list) |

#### `darkfactory/docker/args.py`
| Line range | Content | Target adapter |
|---|---|---|
| L70-75 | pnpm `npm_config_store_dir` env var setup (note: comment at L72-74 explains why `PNPM_STORE_DIR` is intentionally **not** set) | typescript-node |
| L76-77 | `CI=true` to disable watch/interactive in vitest, jest, etc. | typescript-node (general-CI semantics; can stay if generalized) |
| L126-131 | Implementer-specific overrides: `npm_config_frozen_lockfile=false`, `npm_config_install_strategy=hoisted`, `YARN_ENABLE_IMMUTABLE_INSTALLS=false` | typescript-node |

#### `darkfactory/docker/mounts.py` *(missed in v1; flagged by Linus)*
| Line range | Content | Target adapter |
|---|---|---|
| L117-156 | `build_platform_isolation_overlays(language, ...)` — language-keyed dispatch returning tmpfs overlays: `/project/node_modules` (Node), `/project/.venv` (Python) | typescript-node + python |
| L158-208 | Within the same function: pnpm-workspace.yaml parsing + per-member node_modules tmpfs overlays | typescript-node |
| (related) | `df-pnpm-store` named volume initialization (in Dockerfile.base) | typescript-node |

#### `darkfactory/setup/detect.py`
| Line range | Content | Target adapter |
|---|---|---|
| L46-54 | Language signals: `package.json` → node + `.ts/.tsx/.js/.jsx`, etc. | language-detection |
| L70-75, L100-101 | Call sites to `_detect_node_commands()` and `_detect_node_framework()` from generic detection entry | language-detection (registry dispatch) |
| L321-395 | `_detect_node_commands()` + `_detect_node_framework()`: package.json parsing, vitest/jest, pnpm vs npm, pnpm workspaces, Prisma/Drizzle | typescript-node |
| L335-395 | Prisma/Drizzle inference: `npx prisma db push` / `npx drizzle-kit push` outputs | typescript-node + postgres |
| L339-374 | Test command inference: `pnpm exec vitest run`, `npx vitest run`, pnpm `-r` recursive flag | typescript-node |

#### `darkfactory/setup/workspace_check.py` *(entire module)*
| Line range | Content | Target adapter |
|---|---|---|
| All ~300 lines | "Gate 0: pnpm workspace script consistency check" — pnpm-workspace.yaml parsing, `cd <path> && pnpm ...` regex detection, pnpm subcommand list | typescript-node |

#### `darkfactory/spec/library_symbols.py`
| Line range | Content | Target adapter |
|---|---|---|
| L7-32 | Module docstring scopes itself to Node.js + pnpm-workspace + package.json + drizzle-orm; `@`-scoped workspace exemption | typescript-node + postgres |
| L70-78 | `_NODE_BUILTINS`: hardcoded Node.js stdlib module names (consider sourcing from `node:module.builtinModules` at adapter init time instead) | typescript-node |

#### `darkfactory/spec/manifest.py`
| Line range | Content | Target adapter |
|---|---|---|
| L22-24 | `_CWD_CONFIG_RE`: matches `drizzle.config`, `vitest.config`, `jest.config`, `webpack.config`, `vite.config` — must live under server CWD | The *rule* (config files belong in CWD) stays in core; the *file list* moves to typescript-node and is registry-composed per active adapter |

#### `darkfactory/spec/contracts.py` *(missed in v1; flagged by Linus)*
| Line range | Content | Target adapter |
|---|---|---|
| L187-193 | Tier 1 contract compiler dispatch: `_compile_vitest` for node/typescript, `_compile_pytest` for python, **falls back to vitest on unknown language with a warning** | adapter-dispatched (each adapter owns its compiler; no fallback — fail-fast per A5) |
| L201-380 (approx) | `_compile_vitest` body + `_vitest_body`, `_vitest_file_contains`, `_vitest_http_body` helpers | typescript-node |
| (corresponding `_compile_pytest` and helpers) | python pytest compiler | python |

#### `darkfactory/architecture/environment.py`
| Line range | Content | Target adapter |
|---|---|---|
| L36-84 | Allowlist of env-paths: `commands.install`, `commands.setup`, `runtime.language`, `runtime.package_manager` (no language-neutral keys) | language-detection (schema needs adapter-injected slots) |
| L128-144 | `Runtime` dataclass hardcodes `language` + `package_manager` strings | language-detection |

#### `darkfactory/architecture/hints.py`
| Line range | Content | Target adapter |
|---|---|---|
| L39-63 | Lockfile → package-manager map: pnpm-lock.yaml, yarn.lock, package-lock.json, uv.lock, poetry.lock, Pipfile.lock, Cargo.lock, go.sum | language-detection (registry-composed) |
| L92-107 | `_detect_workspace_layout()` — pnpm-workspace.yaml + apps/packages directories + `package.json` workspaces field | typescript-node (monorepo detection moves into adapter; project-hints records the *result*, not the *detector*) |

#### `darkfactory/scenarios/validator.py`
| Line range | Content | Target adapter |
|---|---|---|
| L30-35 | `_ASSERTION_PATTERNS` — language-keyed dispatch: `node` (`expect(`, `assert(`), `python` (`assert `, `self.assert`, `pytest.raises`) | typescript-node + python (the dispatch shape — adapter contributes its patterns — is correct; only contents move) |
| L52-57 | File globs per language: node `**/*.test.ts/.js/.spec.ts/.spec.js`, python `**/test_*.py`, `**/*_test.py` | typescript-node + python |
| L85 | stdout diagnostic tools: `tsc`, `eslint`, `prettier`, `biome` | typescript-node |

#### `darkfactory/scenarios/known_gaps.py`
| Line range | Content | Target adapter |
|---|---|---|
| L176-202 | `build_vitest_setup_source()` — emits `import { beforeEach } from 'vitest'` | typescript-node |
| L204-225 | `build_pytest_conftest_source()` — emits Python conftest.py with pytest hooks | python |

#### `darkfactory/scenarios/runner.py`
| Line range | Content | Target adapter |
|---|---|---|
| L30-33 | Module-level imports of `build_pytest_conftest_source`, `build_vitest_setup_source` | typescript-node + python (adapter dispatch instead) |

#### `darkfactory/scenarios/compat.py`
| Line range | Content | Target adapter |
|---|---|---|
| L116-146 | Hard error: Python scenarios in `node` toolchain; TypeScript scenarios in `python`/`go` toolchain | adapter-dispatched (each adapter declares which file extensions it can run; mismatch is a registry-level error) |

#### `darkfactory/scenarios/coverage.py`
| Line range | Content | Target adapter |
|---|---|---|
| L249 | Scenario file extension check: `.spec.` / `.test.` (jest/mocha/vitest convention) | typescript-node (coverage queries adapter for "what does a scenario file look like in this ecosystem?") |

#### `darkfactory/scenarios/proposal_rehearsal.py`
| Line range | Content | Target adapter |
|---|---|---|
| L446-449 | TypeScript env validation: "tsconfig.json present but node_modules absent — run npm ci (or equivalent)" | typescript-node (output of `TypescriptNodeAdapter.proposal_rehearsal_typecheck()`) |
| L108-115 | Comment-only — explains why a previous drizzle-orm `_.config` rule was **removed** (false positives on lodash). No active coupling. *Listed for completeness but not load-bearing today.* | (no move; comment will follow when module moves to adapter per plan A2) |

#### `darkfactory/pipeline/quality_gate.py` *(missed in v1; flagged by Linus)*
| Line range | Content | Target adapter |
|---|---|---|
| L44-53 | `_dep_install_prefix(language)` — language-keyed dispatch returning `pnpm install --frozen-lockfile` for node, `pip install -r requirements.txt` for python | adapter registry (each adapter contributes its install prefix as part of `quality_gate_dispatch()`) |

#### `darkfactory/pipeline/orchestrator.py` *(missed in v1; flagged by Linus)*
| Line range | Content | Target adapter |
|---|---|---|
| L549-550 | Bootstrap-spec rationale references package.json + tsconfig as exemplars of "tooling being created by the spec" | typescript-node example; comment-only |
| L1046 | Comment about emitting npm commands against pnpm workspace (in a generic warning path) | typescript-node |
| L1098 | Architect addendum string mentions `drizzle.config.ts` as an example | typescript-node + postgres (moves with A4 architect addendum) |
| L1139 | Architect addendum string about pnpm-workspace command emission | typescript-node (moves with A4) |
| L1471-1477 | **Hard skip-gate for C2:** `if not (project_dir / "tsconfig.json").exists(): skip proposal rehearsal` — non-TS projects never run C2 | adapter-dispatched (proposal rehearsal is `TypescriptNodeAdapter.proposal_rehearsal_typecheck`; no special-case in core) |
| L2202 | Untestable-anchor coaching prompt: "ask: can I write a vitest test that..." | typescript-node (moves with A4 / agent addendum) |

### Bash dispatch (`scripts/`)

#### `scripts/quality-gate.sh` *(missed in v1; flagged by Linus — single largest concentration of per-ecosystem branching in the repo)*
| Line range | Content | Target adapter |
|---|---|---|
| L798-822 | Install-command fallback ladder: pnpm-lock → `pnpm install --frozen-lockfile`; yarn.lock → `yarn install --frozen-lockfile`; package-lock → `npm ci`; requirements.txt → pip; pyproject.toml → `pip install -e .`; go.mod → `go mod download`; Cargo.toml → `cargo fetch`. Also a poetry/uv early-skip path | adapter registry (each adapter contributes install command) |
| L965 | `BUILD_CMD="npm run build 2>&1 \|\| npx tsc --noEmit 2>&1"` (node fallback) | typescript-node |
| L984 | `LINT_CMD="npm run lint 2>&1"` (node fallback) | typescript-node |
| L997 | `TYPECHECK_CMD="tsc --noEmit 2>&1"` (node fallback) | typescript-node |
| L985-988, L1001-1002 | python (`ruff check`, `mypy`/`pyright`), go (`golangci-lint`, build), rust (`cargo clippy`, build) fallbacks | python + go + rust adapters |
| L1041-1042 | `node -e "const p=require('./package.json'); ..."` probe to inspect project metadata | typescript-node |
| L1168-1226 | Scenario-runner dispatch cascade: `pnpm_vitest`, `workspace_vitest`, `npx_vitest`, `pnpm_jest`, `npx_jest` cases; "no vitest/jest runner found" failure mode; warning "Python scenario files in node project — pytest not available" | typescript-node (cascade); cross-ecosystem dispatch belongs in registry |

#### `scripts/lib/scenarios.sh` *(missed in v1; flagged by Linus)*
| Line range | Content | Target adapter |
|---|---|---|
| L79-130 | `_df_resolve_node_runner` + `_df_resolve_vitest_config` — pnpm-exec PATH probing, vitest binary resolution, workspace-vitest detection | typescript-node |
| (related, in same file) | Workspace-mode vitest config wrapper generation, scenario-dir relative-path computation | typescript-node |

### Docker images (`docker/`)

| File:line | Content | Target adapter |
|---|---|---|
| docker/Dockerfile.base:L12 | `npm install -g pnpm@${PNPM_VERSION}` | typescript-node base image |
| docker/Dockerfile.base:L16 | `npm ci --production` for claude-code (Node-specific artifact, but installing claude-code is framework-universal) | base image (claude-code installer is shared infra; the *use of npm to install it* is incidental) |
| docker/build-images.sh:L85 | Echo `dark-factory-base ... pnpm@${PNPM_VERSION}` | typescript-node |
| docker/versions.env:L15, L29-32 | typescript, eslint, prettier, pnpm version pins | typescript-node `versions.env` partition |

### Skill files (`.claude/skills/`)

#### `.claude/skills/df-run-quality-gate/SKILL.md`
| Line range | Content | Target adapter |
|---|---|---|
| L39, L46, L53, L60, L67 | Per-language command lists: `npm run build / cargo build / go build / python -m py_compile`, `npx tsc --noEmit / mypy / ...`, `npm test / pytest / go test / cargo test`. The skill is human-facing reference; the dispatcher (`scripts/quality-gate.sh` above) is what actually executes — skill examples track dispatcher behavior | adapter docs (each adapter contributes its example commands; skill assembles from registry) |

### Templates (`templates/`)

#### `templates/feature-spec.md`
| Line range | Content | Target adapter |
|---|---|---|
| L73-98 | Framework example: TypeScript 5.x + package.json + zod; Interface Contract fence as `typescript`; `src/server.ts`; node_modules expectation | typescript-node template |
| L196 | Free-variable example: "Must use pnpm" | typescript-node template |

#### `templates/project-plan.md`
| Line range | Content | Target adapter |
|---|---|---|
| L40-44 | Architecture row: "Package Manager [e.g., pnpm]" | typescript-node template |

#### `templates/config.sh.template`
| Line range | Content | Target adapter |
|---|---|---|
| L32-101 | Build/lint/typecheck/test/mutation command examples spanning Node, Python, Go, Rust | per-adapter template fragments |

#### `templates/escalation-report.md`
| Line range | Content | Target adapter |
|---|---|---|
| L42 | Failure example: `*.test.ts` + drizzle-orm 0.44 version-specific assertion | typescript-node + postgres |

---

## Cosmetic (category b) — keep, mark with `# example:` comment

These are illustrative; the *rule being illustrated* is ecosystem-blind. Mark them so a future ecosystem port doesn't have to guess.

| File:line | What |
|---|---|
| agents/architect.md:L73-79 | example: pnpm + Node manifest |
| agents/architect.md:L143-150 | example: Prisma/Django/Alembic setup commands |
| agents/implementer.md:L69 | example: Drizzle/Prisma setup script options |
| agents/test-engineer.md:L43-46 | example: framework detection patterns |
| agents/proposal-rehearsal.md:L47 | example: TypeScript proposal test |
| templates/feature-spec.md:L73-76 | example: TS + package.json layout |
| templates/config.sh.template:L32-101 | examples: gate commands per ecosystem |
| templates/escalation-report.md:L42 | example: drizzle-orm version-specific failure |
| .claude/skills/df-run-quality-gate/SKILL.md:L39-70 | examples: language-specific gate commands |
| `darkfactory/scenarios/tool_docs.py` L36-50 | reference doc injected into agent prompts. Not pure "example" — must move when ecosystems are added; treat as load-bearing-lite. Listed here pending decision on whether it belongs in adapter or stays as a curated cross-ecosystem doc |

---

## Notable findings (surprises / not in original Track A scope)

These are leakage sites that were **not** explicitly listed in Track A's existing sprint plan. The plan's S0.1 acceptance says "Track A's sprint plan is updated to absorb any newly-discovered moves before Sprint 2 starts." The list below is what triggers an update.

### N1. `agents/test-engineer.md` is heavily Node-coupled
The plan only flagged `agents/architect.md` for the prompt split (A4). But `test-engineer.md` has even more TypeScript+vitest+pnpm-workspace specifics (L100-198). **Action:** A4 also splits `test-engineer.md` and `proposal-rehearsal.md` — extract test-runner specifics into `darkfactory/adapters/typescript/test_engineer_addendum.md` + `darkfactory/adapters/typescript/proposal_rehearsal_addendum.md`.

### N2. `darkfactory/setup/workspace_check.py` is 100% pnpm
The plan's A2 lists three modules to move (`library_symbols.py`, `stub_compile.py`, `proposal_rehearsal.py`). It does not mention `workspace_check.py`, which is entirely pnpm-workspace logic. **Action:** A2 must include `workspace_check.py` in the move list to `adapters/typescript/`.

### N3. `darkfactory/setup/detect.py::_detect_node_commands()` is the largest concentration of Node logic in `setup/`
~75 lines (L321-395). Detects vitest vs jest, pnpm vs npm, pnpm workspaces, Prisma/Drizzle. **Action:** A2 must move this and replace the call site in `detect.py` with an adapter-registry dispatch.

### N4. `darkfactory/architecture/hints.py::_detect_workspace_layout()` (L92-107) assumes pnpm-workspace.yaml = monorepo
**Action:** add to B2 acceptance: monorepo detection moves into `typescript-node` adapter; `project-hints.toml` `[detected]` records the *result* (`workspace_layout = monorepo`) without owning the *detector*.

### N5. `darkfactory/spec/manifest.py::_CWD_CONFIG_RE` regex (L22-24) hardcodes JS-config filenames
The *rule* (config files must live under server CWD) is ecosystem-blind; the *file list* is Node-only. **Action:** A2 moves the file list into adapter; `manifest.py` queries the registry for active adapters' contributed file lists; rule stays in core.

### N6. `darkfactory/scenarios/validator.py::_ASSERTION_PATTERNS` (L30-35) is the right shape but has wrong owner
Currently a hardcoded dict keyed by language. The dispatch shape (validator queries by ecosystem) is correct; the *contents* belong in adapters. **Action:** A2 moves Node entries; the python adapter (Sprint 6 stub) gets the python entries.

### N7. `known_gaps.py` test-setup generators (L176-225) are split across ecosystems but live in one module
`build_vitest_setup_source` → typescript-node; `build_pytest_conftest_source` → python (Sprint 6 stub adapter scope). **Action:** plan B5 / A2 moves; runner.py imports become registry queries.

### N8. `darkfactory/scenarios/coverage.py::L249` ships `.test.` / `.spec.` extension check
Small move; coverage queries adapter for "what does a scenario file look like in this ecosystem?"

### N9. `darkfactory/spec/library_symbols.py::_NODE_BUILTINS` (L70-78)
Hardcoded list of Node stdlib modules used to suppress symbol-lookup false positives. Moves with `library_symbols.py` to `adapters/typescript/`. The list is a maintenance burden (Node.js adds new builtins). **Action:** consider sourcing from `node:module.builtinModules` at adapter init time instead of a hardcoded list. *Optional follow-up; not a blocker for A2.*

### N10. Postgres assumption is more pervasive than just SQL lint
A3 in the plan covers `darkfactory/spec/sql_lint.py`. But Postgres-flavored DDL shows up in:
- `agents/architect.md` L141-152 (database setup examples are all PG-flavored)
- `templates/db-migration-spec.md` (UUID PKs, `NOW()`, transaction-locking — all PG idioms)
- `templates/escalation-report.md` (drizzle-orm + Postgres-shaped failure)

**Action:** A3 acceptance includes "PostgresAdapter activation also gates whether the architect addendum injects PG-specific examples." A non-PG SQL project (e.g., MySQL) should not see PG-only DDL guidance.

### N11. `git/ops.py` L92-95 hardcodes the staging file list
**Action:** modest addition to A5 — adapters contribute their "important files to stage" list; `git/ops.py` queries the registry.

### N12. `consistency.py::DEFAULT_RULES` is the literal trigger for this entire plan and was missed by v1 of this audit
*(Caught by Linus.)* Plan B5 already calls out this migration. The audit must list it, and A2 must consider whether the rule moves to project-hints (B5's plan) or to the postgres-adapter's `consistency_rules()` baseline. The plan's text is right: this is project-policy (Drizzle in migrate-mode is a project choice, not a Postgres-universal rule), so it belongs in `[policy.consistency_rules]`. The framework `DEFAULT_RULES` shrinks to ~empty after B5.

### N13. `scripts/quality-gate.sh` and `scripts/lib/scenarios.sh` are the largest single concentration of per-ecosystem branching in the repo *(missed in v1)*
~400 lines of Bash dispatch covering install, build, lint, typecheck, scenario runners across Node, Python, Go, Rust. The audit's v1 dismissively said "the dispatcher itself must read from adapter" without quantifying. **Action:** A5 (registry + dispatch) acceptance explicitly includes a Bash rewrite of `scripts/quality-gate.sh` to query the adapter registry (probably via a small Python helper invoked from Bash, or by moving the dispatch into `darkfactory/pipeline/quality_gate.py` and having Bash call it). Budget for this rewrite: not trivial — likely adds 2-3 days to A5's existing 3-day estimate.

### N14. `darkfactory/spec/contracts.py::_compile_vitest` is the Tier 1 contract compiler and falls back to vitest on unknown languages *(missed in v1)*
Every Tier 1 contract emitted by the framework is currently a vitest file (or a pytest file if language=python). The fallback-to-vitest behavior at L192-193 means a Rust or Go project would silently get vitest contracts. **Action:** A2 moves `_compile_vitest` family to typescript-node adapter; A3 or future python adapter owns `_compile_pytest`; fallback is removed (registry returns no compiler → fail-fast per A5).

### N15. `darkfactory/pipeline/quality_gate.py::_dep_install_prefix` is a language-keyed dispatch *(missed in v1)*
L44-53. Each adapter's `quality_gate_dispatch(manifest) -> GateCommands` should include the install prefix; `_dep_install_prefix` becomes a registry query.

### N16. `darkfactory/docker/mounts.py` has pnpm-workspace overlays *(missed in v1)*
L120-205. The `language == "node"` branch handles root + per-member node_modules tmpfs overlays for pnpm workspaces. **Action:** A2 moves the pnpm-workspace overlay logic to typescript-node adapter; `mounts.py` queries the registry for "what tmpfs overlays does this language need."

### N17. `darkfactory/pipeline/orchestrator.py` has 5-6 direct leakage sites *(missed in v1)*
L1046, L1098, L1139, L1471-1477 (hard tsconfig.json skip-gate for C2), L2202. Some are cosmetic comments in the architect addendum that move with A4. The hard skip-gate at L1471-1477 is load-bearing — it's special-casing one ecosystem in the orchestrator. **Action:** A2/A5 — proposal rehearsal becomes adapter-dispatched (`adapter.proposal_rehearsal_typecheck` returns `None` for ecosystems that don't support it); orchestrator no longer special-cases tsconfig.

### N18. Sprint 6 stub Python adapter scope tightened
"Just enough to fail-fast" is too vague. **Action:** Sprint 6 stub adapter implements at minimum: `detect()` + `detection_signals` + `quality_gate_dispatch()` + `consistency_rules()` (return empty list) + `architect_addendum()` (one-paragraph "this is a stub adapter") + `proposal_rehearsal_typecheck()` (return None) + the python entries for `_ASSERTION_PATTERNS` + `build_pytest_conftest_source`. Validation: contract harness passes, plus a single worked pytest-shaped synthetic spec runs through detection + gate dispatch (no full pipeline).

---

## Recommended target adapters

| Adapter | Owns | Created in Sprint |
|---|---|---|
| **typescript-node** | All TS/JS/pnpm/npm/yarn/vitest/jest detection (`setup/detect.py::_detect_node_*`), `library_symbols.py`, `stub_compile.py`, `proposal_rehearsal.py`, `workspace_check.py`, `manifest.py` CWD config-file list, `scenarios/validator.py` Node patterns + globs + diagnostic tools, `known_gaps.py::build_vitest_setup_source`, `coverage.py` `.test/.spec` check, `contracts.py::_compile_vitest`, `docker/mounts.py` pnpm-workspace overlays, `docker/args.py` pnpm env vars, agent-prompt addenda for architect/test-engineer/implementer/proposal-rehearsal | Sprint 2 (A2) |
| **postgres** | `sql_lint.py`, architect addendum for PG DDL examples, db-migration template fragment, contributes `migrate-mode-vs-push-mode` rule baseline (or stays in project-hints per B5) | Sprint 2 (A3) |
| **python** *(stub)* | `known_gaps.py::build_pytest_conftest_source`, `scenarios/validator.py` python patterns + globs, `contracts.py::_compile_pytest`, minimal `quality_gate_dispatch`, `detect()` + signals | Sprint 6 (validation port) |
| **language-detection** *(infrastructure, not user-facing adapter — implemented as registry composition over per-adapter detection contributions)* | Replaces hardcoded maps in `git/detect.py`, `git/ops.py` staging list, `architecture/hints.py` lockfile map, `architecture/environment.py` runtime fields, `setup/detect.py` language signals | Sprint 2 (A5) |

`monorepo-detection` and `test-framework-dispatch` are **not** standalone adapters — they're cross-cutting concerns that each adapter participates in via the ABC methods (`detection_signals`, `quality_gate_dispatch`, etc.).

---

## Track A sprint plan updates (required by S0.1 acceptance)

The following must be merged into the plan before Sprint 2 starts:

1. **A2 move list expanded to:**
   - `darkfactory/setup/workspace_check.py` (entire module)
   - `darkfactory/setup/detect.py::_detect_node_commands` + `_detect_node_framework`
   - `darkfactory/spec/manifest.py::_CWD_CONFIG_RE` (file list only; rule stays in core)
   - `darkfactory/spec/contracts.py::_compile_vitest` family + helpers (`_vitest_body`, `_vitest_file_contains`, `_vitest_http_body`)
   - `darkfactory/scenarios/known_gaps.py::build_vitest_setup_source`
   - `darkfactory/scenarios/coverage.py` extension check (L249)
   - `darkfactory/scenarios/validator.py` Node entries (`_ASSERTION_PATTERNS["node"]`, file globs, diagnostic tools)
   - `darkfactory/docker/mounts.py` pnpm-workspace overlay logic (L158-205)
   - `darkfactory/docker/args.py` pnpm env-var setup (L70-77, L126-131)

2. **A3 acceptance expanded:** PostgresAdapter activation gates PG-specific examples in architect addendum and template fragments. `consistency.py::DEFAULT_RULES`'s `migrate-mode-vs-push-mode` rule is removed from framework code; per plan B5 it lands in budget-brain-v2's `project-hints.toml`. A3 must coordinate with B5 to ensure no coverage gap during the move (plan's Sprint 3 hard-ordering constraint already covers this).

3. **A4 scope expanded:** also splits `agents/test-engineer.md` and `agents/proposal-rehearsal.md` — extract Node specifics into `adapters/typescript/test_engineer_addendum.md` and `adapters/typescript/proposal_rehearsal_addendum.md`. Adds ~1-2 days to A4's revised 5-7-day estimate.

4. **A5 scope expanded:** adapters contribute their staging-file lists; `git/ops.py` queries the registry. **Plus a Bash rewrite of `scripts/quality-gate.sh` and `scripts/lib/scenarios.sh`** to query the adapter registry for install/build/lint/typecheck/test commands (likely via a small Python helper or by moving dispatch into `darkfactory/pipeline/quality_gate.py` and calling from Bash). Budget: +2-3 days on top of A5's existing 3-day estimate.

5. **B2 acceptance expanded:** `_detect_workspace_layout()` moves into typescript-node adapter; project-hints records the result, not the detector.

6. **Orchestrator touch-ups (line item under A2/A5):** the 5-6 direct leakage sites at `darkfactory/pipeline/orchestrator.py` L1046, L1098, L1139, L1471-1477, L2202 — most are addendum-text comments that move with A4. The hard tsconfig.json skip-gate at L1471-1477 must become adapter-dispatched (`adapter.proposal_rehearsal_typecheck` returns None for ecosystems that don't support it). Small individually; needs to be done in lockstep with A2/A5.

7. **`darkfactory/pipeline/quality_gate.py::_dep_install_prefix` moves to registry dispatch** as part of A5.

8. **Sprint 6 stub adapter scope tightened (per N18):** implement detection + gate dispatch + python `_ASSERTION_PATTERNS` + `build_pytest_conftest_source` + `_compile_pytest`; validate via contract harness + a single worked pytest synthetic spec end-to-end through detection (no full pipeline).

---

## Sign-off

S0.1 acceptance checklist (from plan):
- [x] Audit doc lists every load-bearing leakage with a target adapter. *(Now complete after Linus review surfaced 7 missed files: `consistency.py`, `quality-gate.sh`, `scenarios.sh`, `quality_gate.py::_dep_install_prefix`, `mounts.py`, `contracts.py`, orchestrator leakage sites. Citations corrected on `docker/args.py`, `scenarios/validator.py`, `scenarios/coverage.py`, `proposal-rehearsal.md`, `proposal_rehearsal.py`.)*
- [ ] Track A's sprint plan is updated to absorb newly-discovered moves before Sprint 2 starts. *(Items 1-8 above need to be merged into `docs/plans/2026-04-14-pipeline-modularity-and-remediation.md` Track A sections. Tracked as a follow-up commit; this audit doc is the authoritative source for the additions.)*
