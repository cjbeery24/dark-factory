# Pipeline Run Report — budget-brain-v2 / 2026-03-28-api-bootstrap

**Date (UTC):** 2026-04-06 19:17
**Repo:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap

## Pre-Run Setup

- Repo budget-brain-v2 was clean (only `.claude/settings.local.json` untracked, left in place).
- Removed `.dark-factory/{audit,events.jsonl,metrics.jsonl,reports,reviews,shipped-scenarios,state,scenarios,failures}`. Kept `agents/`, `templates/`, `plans/`, `specs/`, `config.toml`.
- Cleared all `__pycache__` under dark-factory.
- Synced `agents/` and `templates/` from dark-factory → budget-brain-v2/.dark-factory/ (rsync --delete).
- `pip install -e ~/sites/dark-factory` → dark-factory-1.0.0 reinstalled.
- `dark-factory build node` → all images cached/built successfully.
- No orphan df-* containers/networks to clean.

## Run 1

**Command:** `dark-factory run 2026-03-28-api-bootstrap`
**Outcome:** Failed immediately at spec validation (step 0, never entered iteration loop).

### Error
```
ERROR: error: spec validation failed: validator flagged unresolved contracts:
  - Interface Contract (5. Interface Contract - envSchema): ?
Resolve each by adding a literal example to the spec's Wire Contracts section.
```

### State file
```
spec=2026-03-28-api-bootstrap
run_id=dc97d803de3141ee
spec_hash=173013a21fcd806f997bb17beb5cb93b65981dc253e96d4264fd5aae98f80b2a
status=interrupted
iteration=1
completed_step=0
```

### Diagnostic notes
- The unresolved-contracts validator (added in commit 1c20010 "Add shape-claim precheck and unresolved-contracts validator gating") flagged the `envSchema` Zod definition in `## 5. Interface Contract` of the spec (lines 133-142):
  ```ts
  const envSchema = z.object({
    PORT: z.coerce.number().default(3001),
    NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
    ...
  });
  export type EnvConfig = z.infer<typeof envSchema>;
  export const config: EnvConfig; // validated at import time
  ```
- The validator labels this contract as "?" — apparently because the spec declares the *type* but not a literal example value in a Wire Contracts section. The validator wants a concrete example in a "Wire Contracts" subsection.
- The spec was previously usable; this is a **regression introduced by the new unresolved-contracts gating**, applied to a spec that pre-dates the requirement.
- No `.dark-factory/events.jsonl`, no `reports/`, no `reviews/` were generated — pipeline aborted before any agent ran. No container logs to inspect.

### Blocking issue
The pipeline cannot proceed without spec edits to add literal Wire Contract examples for `envSchema`. Per task instructions step 10, no fixes were attempted.

**Possible pipeline issue worth investigating:**
1. The validator's error message says "Wire Contracts section" but the spec template / older specs use `## 5. Interface Contract` with no `Wire Contracts` subheading. Either the template needs updating or the validator should accept Interface Contract sections.
2. Type-only TypeScript declarations (`z.object({...})`, `export type ...`) are arguably *complete* contracts — flagging them as unresolved "?" may be a false positive in the precheck heuristic.

## Findings & Metrics

| Metric | Value |
|---|---|
| Pipeline reached agent execution | No |
| Iterations completed | 0 |
| Phase reached | spec validation (step 0) |
| Wall time to failure | <5s |
| Container logs produced | None |
| Quality gate runs | 0 |
| Spec validator regression | **Yes** — unresolved-contracts validator rejects pre-existing valid spec |

**Next action:** Either (a) adjust the validator's contract-resolution heuristic to accept type-declaration contracts in `Interface Contract` sections, or (b) update the spec template + this spec to include an explicit `Wire Contracts` subsection with literal example values.
