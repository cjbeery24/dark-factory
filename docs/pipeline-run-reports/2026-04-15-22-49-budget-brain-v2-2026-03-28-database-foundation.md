# Pipeline run — 2026-04-15 22:49 — budget-brain-v2 / 2026-03-28-database-foundation

## Outcome

**INTERRUPTED** at spec-validator phase. Iteration 1, `completed_step=0`. Parser halted the run on `blockers=1`; pipeline never reached architect, so the substrate-verifier fix (commit `33fcbe2`) remains unexercised end-to-end.

## Wall time

~256s (spec-validator only).

## Run ID

`3fbd1659aa204cc9`

## What the pipeline did

| t | event | detail |
|---|-------|--------|
| 0s | `SPEC_START` | spec-validator queued |
| 0s | `AGENT_START` | role=spec-validator, attempt=1 |
| 256s | `AGENT_COMPLETED` | role=spec-validator, exit_code=0, wall=255.96s, ~800k in / ~400k out tokens, sonnet-4 |
| 256s | `REMEDIATION_TARGET` | failure_class=spec_quality, auto_applicable=false, next_command=`dark-factory remediate 3fbd1659aa204cc9` |

Then the pipeline process exited with:
```
ERROR: error: spec validation failed: blockers=1 (critical issues must be resolved)
To fix: dark-factory remediate 3fbd1659aa204cc9
   or:  /df-write-hints from-failure 3fbd1659aa204cc9   (in Claude Code)
```

## State file

```
spec=2026-03-28-database-foundation
run_id=3fbd1659aa204cc9
spec_hash=4c2fda34c917de8b48309a2ba05eb0837c77dfd42e1dac0126af79be4e5e4a25
status=interrupted
iteration=1
completed_step=0
```

## Validation status

```
verdict=PASS
score=87
divergence=MEDIUM
blockers=1
```

Under the new parser (commit `29a46a8`), `blockers>0` is authoritative regardless of `verdict`. Run halted correctly.

## The one blocker — CI-1, and it's real

From `.dark-factory/reviews/2026-03-28-database-foundation-validation.md`:

> ### CI-1: SQL Syntax Error in GUC Creation
> **Location:** Line 63, Section 5 (Interface Contract), Line 228 (Baseline Migration)
>
> ```sql
> -- INCORRECT (current spec):
> ALTER DATABASE current_database() SET app.tenant_id = '';
> ```
>
> **Root Cause:** `current_database()` is a function that returns the database name, but cannot be used as a literal in `ALTER DATABASE` statements.
>
> **Required Fix:**
> ```sql
> ALTER DATABASE budget_brain SET app.tenant_id = '';
> ```

Cross-confirmed by the structural `sql_lint` pre-check, which emitted a warning on the same line:

```
WARNING: sql_lint: line 224 (Feature Specification: Database Foundation/5. Interface Contract/Baseline Migration (pattern documentation)) — syntax error at or near "(", at index 110
```

The prior-session Neo review had flagged exactly this: **both** `ALTER DATABASE CURRENT` **and** `ALTER DATABASE current_database()` are invalid PostgreSQL — the identifier slot requires a literal database name. The user's 2026-04-15 spec edit (`3e043fd`) fixed the first form but landed on the other invalid form. A correct rewrite is one of:

1. **Literal db name:** `ALTER DATABASE budget_brain SET app.tenant_id = '';`
2. **Omit `ALTER DATABASE` entirely:** rely on `current_setting('app.tenant_id', true)` in RLS policies — returns NULL when unset, producing zero rows (fail-closed).

## Validator behaviour under the new prompt/parser

- The `sql.raw` "needs verification" noise from the 2026-04-15 22:03 run is **gone**. The refined prompt (commit `29a46a8`, library-API-inline bullet) worked — the validator no longer flags demonstrated use-site API as a Critical Issue.
- Score drifted: 89 → 87, divergence LOW → MEDIUM. Same validator (sonnet-4), same input file, different run. LLM non-determinism.
- The validator correctly isolated the one remaining genuine defect and emitted coherent status: `verdict=PASS, blockers=1`. The parser correctly rejected it. No "re-run validation" loop.

## Structural fix validated

The point of the parser change (commit `29a46a8`) was: **don't let the LLM's `verdict` prose override the `blockers` count**. This run is the confirmation — the validator wrote `verdict=PASS` + `blockers=1`, the parser said "nope, blockers is truth, stop here," and the pipeline halted before wasting architect/implementer cycles on a spec with a known SQL error. Previous behaviour would have been to either retry the validator (burning tokens) or pass through based on the verdict string.

## Advisory warnings (not blockers, just noise to note)

```
WARNING: spec '2026-03-28-database-foundation' declares ID-form depends-on entries
         that cannot be resolved outside a plan context: F-1b — ignoring for
         standalone run
WARNING: test_server_cmd is set but test_base_url is empty; server will default
         to port 8000
WARNING: library_symbols: 3 undeclared import(s) in spec §5
         — drizzle-orm/postgres-js (line 141)
         — postgres (line 141)
         — drizzle-kit (line 196)
```

Library-symbols warnings are expected: this spec's purpose is to *add* those deps. They're advisory, didn't contribute to the fail.

## Collateral: `dark-factory init --update` regression

Step 2 of the skill ran `dark-factory init --update` on the target, which rewrote `.dark-factory/project-hints.toml`:

- `test_runner = "vitest"` → `test_runner = ""` (detection lost — `vitest` is still in `package.json`)
- `active_adapters = ["typescript-node", "postgres"]` → `["typescript-node"]` (postgres adapter dropped — even though drizzle-orm / postgres are about to be added)
- Hand-written comment block explaining the policy precedence + Sprint 3 B5 motivation replaced with a terse default stub
- TOML regex strings re-serialized single-quoted literals → basic strings with escaped backslashes (functionally equivalent, visually churned)

The `migrate-mode-vs-push-mode` consistency rule body is preserved intact. Regression reproduced both in the prior session's Step 2 and this session's Step 2. Worth a follow-up fix in the hints serializer — this file degrades every time anyone runs `init --update`.

## Next move for the user

The spec's `ALTER DATABASE current_database() SET ...` is genuinely broken SQL that would fail in an architect→implementer→gate cycle anyway. The fix is straightforward (use a literal db name or drop `ALTER DATABASE` in favour of `current_setting(..., true)`) but the framework's `/df-write-spec` remediation path now exists:

```
/df-write-hints from-failure 3fbd1659aa204cc9
```

or the direct form:

```
dark-factory remediate 3fbd1659aa204cc9
```

Once the spec is fixed and validation passes cleanly, the substrate-verifier exercise is still waiting downstream.

## Substrate verifier status

**Not exercised.** Pipeline halted at spec-validator. No architect output, no substrate-findings.json. The substrate-verifier fix (`33fcbe2`) is still un-tested end-to-end.
