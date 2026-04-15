# Sprint 6 #3 — Replay validation of 2026-04-14 15:21 budget-brain-v2 failure

**Run-IDs referenced:** `9e844d34426d4717` (original failing run), `034c318ff2eb4ef3` (pre-hardening predecessor)
**Validation type:** Structural / dry-replay (no wet pipeline invocation)
**Plan reference:** `docs/plans/2026-04-14-pipeline-modularity-and-remediation.md` § Success criteria
**Dark Factory HEAD:** `560f2f0` (Sprint 6 #2 shipped; python-fastapi adapter registered)

## Why dry-replay (not a wet end-to-end run)

The original failing run was emitted against Dark Factory HEAD `13b783b`,
**before** C2's `REMEDIATION_TARGET` event existed. The budget-brain-v2
`events.jsonl` therefore carries no target record for run_id
`9e844d34426d4717` — `dark-factory remediate 9e844d34426d4717` cannot
find a target without a fresh pipeline run that emits one.

This report scores the success criteria via code inspection, CLI
surface probes, and comparison against the existing 2026-04-14 15:21
run. **What this report does not cover:** the LLM-driven composition
test. The new architect prompt (A4 refactor) + adapter addenda + the
project-hints rule, composed together under claude-sonnet-4
nondeterminism, will produce a *different-shaped* architect manifest
than the 2026-04-14 15:21 run. Whether A1's
`migrate-mode-vs-push-mode` regex still matches that new shape — and
whether it even *fires* (i.e., whether the architect, armed with the
explicit prohibition now in the prompt via project-hints, avoids
emitting `drizzle-kit push` at all) — is not verifiable without a fresh
pipeline invocation. Unit tests pin the pieces; the pieces' composition
under LLM nondeterminism is unpinned by definition. That composition
test is the single most load-bearing assertion in the plan and is
explicitly deferred to the bounded follow-up wet run (scoped in the
"Recommended follow-up" section).

The structural evidence collected here is the *prior* on the wet-run
outcome, not the verdict.

## Scoring against the 8 plan success criteria

### ✅ #1 — Same A1 catch (migrate-mode vs push-mode)

**Structural evidence:** The consistency-rule machinery used by A1
(`darkfactory/architecture/consistency.py`) is unchanged across Sprint
1–5; the `migrate-mode-vs-push-mode` rule now lives in
`.dark-factory/project-hints.toml` on budget-brain-v2 (moved there by A5
and confirmed present in the current file). The 2026-04-14 15:21 run
proved A1 fires on this exact rule against the exact same spec and the
exact same architect output. Nothing in Sprint 4–5 changed the A1
invocation path — C2 added *emission after* the raise, not a new
raise-site.

**Verdict:** PASS by construction.

### ✅ #2 — Cleaner error message with `To fix:` next-command hint

**Structural evidence:**
- `darkfactory/cli.py:374` — `_print_next_command_hint(args, run_id)`
- `darkfactory/cli.py:415` — `print(f"To fix: {next_cmd}", file=sys.stderr)`
- `darkfactory/cli.py:368` — hint printed in the `DarkFactoryError`
  handler with `getattr(e, "run_id", "")` so any error carrying a
  `run_id` gets the hint
- `darkfactory/pipeline/orchestrator.py:1363` — `next_command` is
  threaded into `REMEDIATION_TARGET` emission at A1 and A5 failure
  points.

**Verdict:** PASS. Output shape pinned by C3 unit tests.

### ⏳ #3 — `dark-factory remediate --apply --rerun <run_id>` works

**Structural evidence (prior, not verdict):**
- CLI surface (`dark-factory remediate --help`) correctly lists
  `--apply`, `--rerun`, `--accept-novel`, `--target-index`.
- `--rerun` docstring confirms partial re-trigger (auto-resumes from the
  failed step) and loop-guard (aborts if the same `rule_or_check` fires
  again) — C4 design.
- `find_targets_by_run_id` (`darkfactory/events/remediation.py:421`)
  reads `REMEDIATION_TARGET` events by run_id with tolerance for
  malformed JSONL lines.

**What remains unverified:**
- That the end-to-end composition (failing pipeline → target event →
  remediate → apply → rerun → pass) actually works under
  claude-sonnet-4 nondeterminism. Each piece has unit-test coverage;
  their composition under a real LLM-driven architect has not been
  observed since the Sprint 4–5 code landed.
- That the rule already in `project-hints.toml` on budget-brain-v2 is
  sufficient to prevent the architect from re-emitting
  `drizzle-kit push`. This is the plan's critical assertion and is
  LLM-dependent.

**Verdict:** **UNVERIFIED.** Structural evidence supports a strong prior
on success, but the composition has not been exercised end-to-end.
Bounded wet run required to tick this criterion off.

### ✅ #4 — `/df-write-hints from-failure` produces equivalent result

**Structural evidence:** `skills/df-write-hints.md` ships with `init`,
`add`, and `from-failure` flows (B4, commit `407f978`). The skill and
the `dark-factory remediate` CLI share the same `hints_writer` module
for the final write. The skill reads the same `REMEDIATION_TARGET`
envelope shape. Equivalent *input* arguments to the shared writer is
the standing design claim; this report confirms the shared writer but
has not directly compared outputs from both paths on the same target.

**Verdict:** PASS — shared writer module confirmed; identical-inputs
claim rests on the single rule-synthesis code path, not a side-by-side
output comparison.

### ⚠️ #5 — Architect prompt ≤250 lines

**Measured:** `agents/architect.md` = 403 lines (314 non-blank) after
the 2026-04-15 extraction pass. Pre-A4 was 465. Plan numeric target
was ≤250. The numeric direction **regressed** 388 → 394 → 403 during
the extraction work — a real signal that extraction has hit
diminishing returns and the 250-line target requires a different
approach, not more of the same.

**Extraction arithmetic:** A4 took the core from 465 → 388
(−77 lines). The 2026-04-15 follow-up pass swapped the pnpm-flavored
bootstrap YAML for placeholder values, generalized the CWD-rule and
YAML-glob examples, moved concrete TS/Node bootstrap shape into the
TypeScript addendum, rewrote the `test_integration` schema's inline
annotations to placeholder+handoff style, and added a Python
forbidden-patterns example to break JS-only bias. The TypeScript
addendum is at 79 non-blank content lines by the contract-harness
count (governance cap is ≤80); the `dual` module-system description
was compressed 3→2 lines to stay under cap after the bootstrap bullet
was added.

**Complete marker inventory (Linus caught my narrow grep missing
categories — the full honest count):**

*Multi-ecosystem illustrative lists (retained; not biasing):*
- Line 23 — `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`
  as dependency-manifest examples, pointing at addenda.
- Line 61 — `pnpm test`, `pytest`, `go test ./...` as command-shape
  examples, pointing at addenda.
- Line 142 — `pnpm -r`, `cargo --workspace`, `go ./...`,
  `turbo run --filter='*'` as recursive-flag examples to avoid.

*Structural schema fields (can't be removed without refactoring
`ArchitectureManifest`):*
- Lines 254-255 — `vitest_config` and `tsconfig` as actual dataclass
  field names on the manifest model.

*Illustrative examples using ecosystem syntax (cosmetic bias,
non-behavioral — my narrow grep missed these):*
- Lines 302-305, 312-313 — `forbidden_patterns` examples now include
  **both** a Node example (`Math.round(`, `console.log(`) **and** a
  Python example (`eval(`, `os.system(`) per Linus's recommendation.
  Balanced.
- Lines 320-321 — regex-form example uses `.toFixed(\d+)` (TypeScript).
  Still cosmetic; the rule being illustrated is format syntax, not
  ecosystem choice.
- Lines 355-356 — YAML-quoting "common traps" all use TypeScript
  syntax (`Promise<{status: ok | error}>`, `Record<string, number>`,
  `message?: string`). Cosmetic illustration of YAML-indicator-char
  cases, not behavioral bias.

*Previously-behavioral content now fixed:*
- Lines 252-253 (was `<esm | cjs | dual — Node only>` and
  `<".js" for esm, "" for cjs — see TS adapter addendum>`) — rewritten
  to placeholder+handoff: `<module system value — see active adapter's
  addendum>` and `<import extension — see active adapter's addendum>`.
  This was the last piece of Node-specific *behavioral* guidance in
  the core prompt schema.

**Verdict:** **PARTIAL.** The qualitative target ("Node specifics
live in adapter addenda") is now met for behavioral content — no
remaining marker biases the architect's *behavior* on a non-Node
spec. Cosmetic bias remains in illustrative examples (YAML-quoting
traps all use TS syntax) but doesn't affect generated manifests.

The numeric target (≤250 lines) **regressed** 388 → 403 during this
work. The extraction approach is out of headroom: each extracted
ecosystem-specific line is being replaced by a longer
placeholder+handoff phrase. Hitting ≤250 requires a structural
reorganization (e.g., moving the manifest schema YAML ~100 lines to a
separately-loaded reference the architect reads on demand rather than
receives inline), not another extraction pass. That structural project
is deferred to a separate plan.

### ✅ #6 — `project-hints.toml` exists and drives consistency rules

**Measured:**
`~/sites/budget-brain-v2/.dark-factory/project-hints.toml`
exists and carries `[detected]` (written by `dark-factory init`) plus
`[[policy.consistency_rules]]` for the migrate-mode-vs-push-mode fingerprint.
The precedence order `project-hints > adapter > default` is enforced by
`darkfactory/architecture/consistency.py` per A5. `DEFAULT_RULES` is
empty in framework code — the rule moved out successfully.

**Verdict:** PASS.

### ✅ #7 — Synthetic Python spec fails-fast cleanly

**Structural evidence:** Sprint 6 #2 (commit `560f2f0`) shipped the
`python-fastapi` stub adapter. `dark-factory init` on a Python project
(pyproject.toml with FastAPI dep) would detect and register
`active_adapters = ["python-fastapi"]`. The A5 fail-fast gate
(`load_active_adapters`) raises `DarkFactoryError` on unregistered
adapter names with the plan-mandated error text:
> `No adapter for ecosystem 'X'. Available: postgres, python-fastapi,
> typescript-node. Install or implement an adapter at
> darkfactory/adapters/X/.`

Pinned by `tests/unit/adapters/test_registry.py` and the contract
harness parametrization over `python-fastapi`. No TS precheck findings
can reach a Python spec because `active_adapters` routes through the
registry before any precheck dispatch.

**Verdict:** PASS.

### ⏳ #8 — `remediation_outcome` events accumulate

**Current state:** `budget-brain-v2/.dark-factory/events.jsonl` carries
**zero** `REMEDIATION_OUTCOME` events (`dark-factory hints stats`
reports "no remediation outcomes recorded"). No outcome event has ever
been written against this project — accumulation has not *begun*, not
merely "been deferred."

**Structural evidence (prior):** The C5 emission code path is pinned
by unit tests. The rule already sits in `project-hints.toml`, so a
remediate-apply against a fresh failing run would immediately match the
existing rule (dedup path) rather than proposing a new one — `outcome`
would be `already_present` or similar, still recording the event.

**Verdict:** **UNVERIFIED.** Requires the wet run to record the first
real `REMEDIATION_OUTCOME` event.

## UX walkthrough scoring

The plan's "as simple as possible" walkthrough compares pre-plan
(3 steps, ~3 min, high judgment) to post-plan (1 command, 1 keystroke
with C2+C3+C4+C5). With all four remediation-loop items shipped, the
tooling supports the 1-command path:

```
# Failing pipeline run prints:
#   ERROR: Architecture manifest contradicts spec constraints: ...
#   To fix: dark-factory remediate --apply --rerun <run_id>

dark-factory remediate --apply --rerun <run_id>
# → shows TOML diff of proposed rule
# → novel-rule guard prompts y/n on first occurrence (C5)
# → writes project-hints.toml, emits REMEDIATION_OUTCOME
# → --rerun invokes `dark-factory run <spec>` in-process
# → partial re-trigger auto-resumes from architect step (skips spec-validator)
# → loop-guard: if the same rule_or_check fires again, abort exit 1
```

**Verdict on the walkthrough bar:** Structural evidence shows the
tooling supports the post-plan path. Wall-clock measurement is the one
piece dry-replay cannot deliver — a bounded wet run would give the final
keystroke/second comparison.

## Summary

| Criterion | Verdict | Notes |
|-----------|---------|-------|
| #1 A1 catch | ✅ PASS | Proven by 2026-04-14 15:21; consistency-rule path unchanged |
| #2 `To fix:` hint | ✅ PASS | C3 wired; unit-test pinned |
| #3 remediate --apply --rerun | ❓ UNVERIFIED | Composition under LLM not exercised; wet run required |
| #4 /df-write-hints from-failure | ✅ PASS | Shared `hints_writer` confirmed; identical-inputs not side-by-side tested |
| #5 architect ≤250 lines | ⚠️ PARTIAL | 394 lines; 5 remaining markers are all multi-ecosystem illustrative or structural schema fields (qualitative target met; numeric target deferred) |
| #6 project-hints drives rules | ✅ PASS | `DEFAULT_RULES` empty; rule in .toml |
| #7 Python fail-fast | ✅ PASS | Sprint 6 #2 adapter registered |
| #8 REMEDIATION_OUTCOME events | ❓ UNVERIFIED | Zero events ever written; wet run required |

**Net:** 4 PASS, 2 UNVERIFIED (wet run required), 1 MISS, 1 PASS with
a narrowed claim.

Sprint 6 #3 is **not shippable without the wet run.** Criteria #3 and
#8 are the load-bearing end-to-end claims of the remediation loop;
they remain unverified until a bounded wet run exercises the
composition under claude-sonnet-4 nondeterminism. Criterion #5 is a
separate miss requiring additional extraction work on `architect.md`
(not wet-run-dependent).

The structural evidence collected here is a strong prior, not a
verdict.

## Required follow-up

1. **Bounded wet-run** against budget-brain-v2 (criteria #3, #8):
   - Re-invoke the failing spec (`2026-03-28-database-foundation`).
     One of three wet-run outcomes is expected:
     (a) **Architect avoids the contradiction entirely** because the
         project-hints rule is in the prompt now — A1 does not fire;
         pipeline proceeds past architect. This would be criterion #3's
         strongest confirmation.
     (b) **A1 fires again** against a new-shape manifest — dump the
         manifest, verify the regex still matches, remediate, confirm
         outcome event.
     (c) **A1 fires on a different rule** — signal, investigate.
   - Whatever the outcome, a `REMEDIATION_TARGET` / `REMEDIATION_OUTCOME`
     pair should land in `events.jsonl`.
   - Scope: one spec, one run, ~$4–5, ~4 minutes.

2. **Architect prompt further extraction** (criterion #5): move the
   bootstrap example, pnpm-flavored `commands.test` examples, and the
   `vitest_config`/`tsconfig` schema fields out of `agents/architect.md`
   into the TypeScript addendum or a generalized adapter-provided
   schema block. Not wet-run-dependent; separate commit.
