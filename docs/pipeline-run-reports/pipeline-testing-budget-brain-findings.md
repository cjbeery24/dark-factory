# Pipeline Testing Findings: budget-brain (2026-03-28)

## Overview

Ran the dark-factory pipeline 3 times on the budget-brain project (spec:
`2026-03-24-project-scaffolding`, a pnpm monorepo with React+Vite frontend, Fastify API,
and shared TypeScript types package). Each run included up to 3 internal iterations.

## Bug Fixes Applied

### Fix 1: SIGPIPE (exit 141) killing quality gate — RESOLVED

**Commit:** `fd699a2` — Fix SIGPIPE (exit 141) killing quality gate before blind scenarios

**Root cause:** `scripts/lib/scenarios.sh:102-103` used `find ... | head -1` in
`_df_resolve_node_runner()`. In pnpm workspaces with multiple vitest binaries, `head -1`
closes the pipe after the first line, and `find` receives SIGPIPE. Under `set -eo pipefail`
(quality-gate.sh line 2), this propagates as exit 141, killing the entire script before
Gate 5 (Blind Scenarios) can execute.

**Fix:** Replaced `| head -1` with `-print -quit` (POSIX-safe, no pipe needed).

**Secondary fix:** Added dependency installation in smoke test mode before Gate 5. When
`SMOKE_TEST=true`, Gates 1-4 are skipped (including the build step that runs `pnpm install`),
but tmpfs node_modules overlays start empty. Vitest can't resolve imports without deps.

**Verification:** Regression test added (`tests/shell/test_resolve_node_runner.sh` Test 5)
with 3 vitest binaries under `set -eo pipefail`. All 8 tests pass.

**Impact:** Before this fix, Gate 5 never executed on any pnpm workspace project. Quality
gate reports showed Gates 1-4 but no Gate 5 or Result line. Every pipeline run produced 0%
scenario pass rate regardless of implementation quality.

### Fix 2: Scenario Architect quality improvements — PARTIALLY RESOLVED

**Commit:** `d39524a` — Add scenario quality guardrails to Scenario Architect prompt

**Root cause:** The SA generated tests with systematic quality issues:
- Wrong platform config schemas (Railway flat vs nested)
- Config inheritance not recognized (tsconfig `extends`)
- Third-party imports in test context (`require('yaml')`, `import('@project/shared')`)
- Source code string matching instead of behavioral testing
- Brittle `indexOf` ordering assertions on YAML
- Assertion API misuse (`toMatch()` on objects, `toContain()` array/string confusion)

**Changes to `agents/scenario-architect.md`:**
- Strengthened Rule 2 (unconstrained values) with spec-quotation self-check and negative assertion clause
- Expanded Rule 4 (parse-first) with structure-before-values and YAML exception
- Added Rule 5 (stdlib-only imports — no third-party packages or project modules)
- Added "Common Pitfalls" subsection: config inheritance, platform schemas, string ordering, source code matching, comment/prose assertions
- Added "Assertion Method Reference" table (toContain array vs string, toMatch strings only)
- Kept critical rules at 6 (same count as original 5 + 1 new)

**Impact:** Mixed. Self-Tests gate went from FAIL to PASS. Some scenario categories improved.
But deployment config scenarios still hit the Railway schema issue — the SA still uses
`.toMatch()` on `railwayConfig.build` (which is an object in the actual Railway JSON schema).
The Common Pitfalls section wasn't sufficient to override the SA's assumptions about Railway's
format.

## Pipeline Run Results

### Run 1 (before SIGPIPE fix)

Gate 5 never executed. Quality gate exited 141 after ~7 seconds.

### Run 2 (after SIGPIPE fix, before SA improvements)

| Iteration | Passed | Failed | Crashed | Total | Rate | Self-Tests |
|-----------|--------|--------|---------|-------|------|------------|
| 1 | 80 | 19 | 0 | 99 | 80% | FAIL |
| 2 | 80 | 19 | 0 | 99 | 80% | FAIL |
| 3 | 81 | 18 | 0 | 99 | 81% | FAIL |

**Outcome:** Interrupted (max iterations). Self-Tests failed every iteration because
`apps/api` had a `test` script but no test files.

### Run 3 (after SA improvements)

| Iteration | Passed | Failed | Crashed | Total | Rate | Self-Tests |
|-----------|--------|--------|---------|-------|------|------------|
| 1 | 71 | 15 | 0 | 86 | 82% | PASS |
| 2 | 71 | 15 | 0 | 86 | 82% | PASS |
| 3 | (killed) | | | | | |

**Outcome:** Killed after iteration 2 showed no improvement. Self-Tests now pass consistently.

## Persistent Failure Analysis

### What improved (Run 2 → Run 3)
- Self-Tests: FAIL → PASS (SA no longer generates tests that conflict with the test runner)
- Fewer total scenarios: 99 → 86 (SA is more selective, per Rule 6 guidance)
- No crashes across any run (0 in all iterations)

### What didn't improve
- Pass rate: ~80-82% across all runs
- Deployment config scenarios: 6-7 failures persist every iteration
- Railway JSON schema assumption: SA still assumes `railwayConfig.build` and
  `railwayConfig.start` are strings (they're nested objects)

### Remaining failure categories (Run 3, 15 failures)

| Category | Count | Root Cause |
|----------|-------|------------|
| Railway schema assumptions | 6 | SA uses `.toMatch()` on objects; assumes flat schema |
| Environment/build validation | 3 | SA asserts on CI patterns the spec doesn't constrain |
| Web scaffolding details | 3 | SA checks for specific Vite/Tailwind config patterns |
| Security/type assertions | 2 | SA invents restrictions not in the spec |
| Root package.json | 1 | SA assumes `workspaces` field (pnpm uses `pnpm-workspace.yaml`) |

## Root Cause: Why SA Prompt Changes Are Insufficient

The Common Pitfalls section is positioned as **advisory guidance** in a subsection, while the
SA's core behavior is driven by the **Critical Rules** and **Layer definitions** (especially
Layer 8 — Adversarial). The SA generates adversarial tests that exercise deployment configs
by probing specific schema shapes, and the pitfalls section doesn't override that impulse
strongly enough.

The Railway schema issue is particularly stubborn because:
1. The SA has never seen the actual Railway schema (information barrier)
2. The spec doesn't define Railway's JSON format
3. The SA assumes a schema and generates tests against it
4. The implementation uses the correct schema (nested), but the SA doesn't know that
5. The pitfalls section says "don't assume flat vs nested" but the SA still has to pick one

## Recommended Next Steps

### Option A: Elevate platform schema to Critical Rule (low effort, moderate impact)

Move the platform schema pitfall back to a critical rule with stronger wording:

> **DO NOT assert on deployment platform config schemas the spec does not define.**
> If the spec says "create railway.json for API deployment" but does not specify the exact
> JSON structure, only verify the file exists and is valid JSON. Never assert on field names,
> nesting, or values — platform schemas are unstable and vary by version.

This is aggressive — it would eliminate ~6 failures but also remove some legitimate coverage
of deployment config correctness.

### Option B: Add platform schema context to execution context (moderate effort, high impact)

Inject a hint in the runtime prompt's EXECUTION CONTEXT block when deployment config files
are detected in the spec:

```
DEPLOYMENT_PLATFORMS=railway,vercel
NOTE: Railway uses nested JSON schema (build.buildCommand, deploy.startCommand).
      Vercel uses flat schema (buildCommand, outputDirectory).
      Assert on file existence and valid JSON, not specific field names,
      unless the spec defines the exact schema.
```

This gives the SA concrete knowledge without violating the information barrier (it's from the
spec, not the implementation).

### Option C: Make deployment configs Format E candidates (low effort, eliminates failures)

Scaffolding specs with deployment configs could be classified as Format E (sentinel) when the
spec doesn't define testable runtime behavior for the deployment files. Gates 1-3 already
verify that deployment configs parse correctly (build succeeds). Adding scenario tests for
config file structure adds coverage but also false failures.

### Option D: Accept 80-85% and tune Oracle threshold (no effort, pragmatic)

The implementation is actually good — Build, Lint, Type Check, and Self-Tests all pass. The
15 scenario failures are overwhelmingly scenario-quality issues, not implementation gaps.
Tuning the Oracle's SHIP threshold to accept 80%+ with 0 crashes and all gates passing might
be the pragmatic choice for scaffolding specs.

## Summary

| Fix | Status | Impact |
|-----|--------|--------|
| SIGPIPE (exit 141) | **Resolved** | Gate 5 now executes reliably |
| Smoke test dep install | **Resolved** | Scenarios can resolve imports |
| SA stdlib-only imports | **Resolved** | No more `require('yaml')` crashes |
| SA config inheritance | **Resolved** | tsconfig `extends` handled correctly |
| SA platform schemas | **Partially resolved** | Railway still assumes flat schema |
| SA unconstrained values | **Partially resolved** | Some over-assertions persist |
| Self-Tests gate | **Resolved** | FAIL → PASS |
| Overall pass rate | **Stalled at 80-82%** | Blocked by deployment config scenarios |
