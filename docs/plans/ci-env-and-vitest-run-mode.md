# Plan: Set CI=true in All Containers

## Problem

Quality gate self-tests time out (300s) when workspace packages use `"test": "vitest"`.
Root cause: vitest defaults to watch mode unless `CI=true` is set in the environment or
`--run` is passed explicitly. Without `CI=true`, vitest starts in watch mode and hangs
indefinitely inside a non-interactive container.

Note: the hang is not caused by pnpm PTY allocation. Docker without `-t` does not attach
a TTY, so `process.stdout.isTTY` is already false in Dark Factory containers. The cause
is simply that vitest v1+ defaults to watch mode when `CI` is absent from the environment,
regardless of TTY state.

## Fix

Add `"-e", "CI=true"` to `build_base_args` in `darkfactory/docker/args.py`.

This is the architecturally correct fix:

- Dark Factory containers **are** a CI environment. Every hosted CI system (GitHub Actions,
  CircleCI, Railway, Vercel) sets `CI=true`. Dark Factory should too.
- vitest v1+ (all actively-used versions — v0.x reached EOL in 2022) checks `CI` and
  disables watch mode.
- Jest v27+ also respects `CI=true` (`--watchAll=false`). Most build tools suppress
  interactive prompts. No negative side effects in a pipeline context.
- One-line change. No vitest-specific detection logic. No workspace scanning. No changes
  to quality-gate.sh, project configs, or package scripts.

## What This Does NOT Do

- Does not change `test_cmd` detection in `detect.py`. `pnpm -r run test` remains
  correct once `CI=true` is in the environment.
- Does not require any project's `package.json` test scripts to be modified.
- Does not affect the `refresh_gate_commands` feature — that fills absent config fields
  and is orthogonal.

## Scope

Single file, single line:

| File | Change |
|------|--------|
| `darkfactory/docker/args.py` | Add `"-e", "CI=true"` to the env block in `build_base_args` |

## Edge Cases

| Case | Behaviour |
|------|-----------|
| vitest v0.x (EOL 2022) | Does not check `CI`; would still hang. Accepted: no active projects should be on v0.x. Explicit `--run` in test scripts is the fix if encountered. |
| Jest watch mode | Jest respects `CI=true` → `--watchAll=false`. Fixed. |
| App code that branches on `CI=true` | Implementer container also gets `CI=true`. App code that skips seeds/migrations in CI may behave differently. Accepted tradeoff — the container is a non-production pipeline environment, not a dev machine. |
| Non-node containers (python, go, rust) | `CI=true` is set. Most tooling ignores it; pytest, go test, cargo test are unaffected. |

## Tests

Add to `tests/unit/test_docker_args.py`:

1. **`CI=true` present in base args** — call `build_base_args` with a minimal config;
   assert `["-e", "CI=true"]` appears as consecutive items in the returned list (verify
   list structure, not just string membership).
2. **`CI=true` present for all language configs** — repeat for language="node",
   "python", "go", "rust"; assert `CI=true` appears in each.
