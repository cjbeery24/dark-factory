# Plan: `dark-factory validate` Should Never Block on Stale Pipeline State

## Problem

The post-AST trace surfaced a hard block:

```
$ dark-factory validate 2026-03-28-api-bootstrap
precheck run #1 for this spec content
ERROR: error: spec has changed since last run — use --fresh to start over
$ dark-factory precheck 2026-03-28-api-bootstrap --fresh
ERROR: unrecognized arguments: --fresh
$ rm -rf .dark-factory/state/2026-03-28-api-bootstrap/   # manual workaround
```

Two real bugs, one root cause:

1. `dark-factory validate` invokes `run_pipeline(..., stop_after_step=1)`, which routes through `_try_auto_resume` → `_handle_resume`. When the spec content has changed since the last validate invocation, `_handle_resume` raises `DarkFactoryError("spec has changed since last run — use --fresh to start over")` (`darkfactory/pipeline/orchestrator.py:2125`).
2. The `validate` subcommand has **no `--fresh` argparse argument** (`darkfactory/cli.py:451`). Only `dark-factory run` exposes `--fresh`. So the error tells users to pass a flag they cannot pass.

The correct behavior for `validate` is **never resume**: validation is read-only, idempotent, and step-1-only. There is no half-finished pipeline state worth preserving across a content edit. The hard block exists for `dark-factory run` (where it protects in-progress iteration state), but `validate` has no analogous state to protect.

The recorder (`record_precheck_run`) is correct — it resets the round counter on hash change as the plan specified. The bug is upstream of it, in the orchestrator resume path that `validate` shares with `run` but shouldn't.

## Goal

`dark-factory validate` always proceeds, regardless of any pre-existing pipeline state file. No stale-state errors. No phantom flags. No manual `rm -rf`.

`dark-factory run` behavior is **unchanged** — its hash-mismatch hard block + `--fresh` instruction is a legitimate safety property for real pipeline runs (you don't want to silently throw away a half-finished iteration loop because the spec changed).

## Out of Scope

- Validator agent emitting `?` as the divergence reason for unresolved contracts. Separate, lower-severity bug; tracked as a follow-up slice.
- Changing `dark-factory run`'s resume semantics in any way.
- Changing `record_precheck_run` (it's already correct).
- Adding a `--fresh` flag to `validate` (unnecessary if validate never resumes).

## Design

### Step 1 — `_cmd_validate` passes `fresh=True` to `run_pipeline`

**File:** `darkfactory/cli.py`, function `_cmd_validate` (~line 500).

Change:

```python
exit_code = run_pipeline(
    spec, config, project, dry_run=args.dry_run, stop_after_step=1,
    progress=lambda msg: print(msg),
)
```

To:

```python
exit_code = run_pipeline(
    spec, config, project, dry_run=args.dry_run, stop_after_step=1,
    fresh=True,  # validate is read-only, never resumes — see plan: validate-fresh-state
    progress=lambda msg: print(msg),
)
```

That's the entire fix. `fresh=True` causes `run_pipeline` to skip `_try_auto_resume` (`orchestrator.py:181`), which is the only path that touches the resume state file and triggers the hash-mismatch check.

### Why this is safe

- `run_pipeline(stop_after_step=1)` only executes step 1 (spec validation). Steps 2+ (architect, scenarios, implementer, gate, review, oracle) are skipped. There is nothing for a "resume" to pick up.
- The state file `_handle_resume` reads is the iteration-loop state (`completed_step`, `iteration`, `deployment_step`). None of those fields are populated by a step-1-only run.
- `fresh=True` does NOT delete anything irreversibly. Per `orchestrator.py:676`, `fresh` only deletes the architecture manifest if it exists AND the run is going to regenerate it. Validate's step-1-only run never reaches the architect step, so the manifest is untouched.
- Precheck round counter is recorded BEFORE `run_pipeline` is called and is unaffected.

### Step 2 — Test

**File:** `tests/unit/test_cli_validate.py` (existing or new — verify with grep first).

Add a test that constructs a fake project with:
- A spec file at `.dark-factory/specs/{name}.md`
- A pre-existing pipeline state file at `.dark-factory/state/{name}/state.json` containing `spec_hash: "old_hash_xyz"` and `completed_step: 5, iteration: 2, status: "running"`
- Spec content whose sha256 differs from `old_hash_xyz`

Invoke `_cmd_validate` (mocking `run_pipeline` to capture its kwargs and return 0). Assert:
- No `DarkFactoryError` raised
- `run_pipeline` was called with `fresh=True`
- The pipeline state file was NOT modified (validate doesn't touch it)
- The precheck_runs.json was written with `count=1, spec_hash=<new>` (recorder did its job)

If `tests/unit/test_cli_validate.py` doesn't exist, the test goes in a new file. If a CLI test harness already exists for other subcommands, follow that convention.

**Two additional cases in the same test module** (cheap, guard against someone later gating `fresh=True` on a condition):
- No prior pipeline state file at all → validate proceeds, no error.
- Prior pipeline state file with **matching** hash → validate still proceeds, doesn't accidentally clobber unrelated state.

### Step 3 — Document the invariant

Add a one-line comment above the new `fresh=True` kwarg explaining WHY it's there, so a future reader doesn't "clean up" the apparently-redundant flag and re-introduce the bug.

```python
# fresh=True: validate is read-only and never resumes. Without this,
# a stale pipeline state file (from a prior `dark-factory run`) blocks
# `validate` with "spec has changed since last run — use --fresh" when
# the user has since edited the spec. validate has no --fresh flag of
# its own, so the error is unactionable. See docs/plans/validate-fresh-state.md.
```

## File Change Summary

| File | Change |
|---|---|
| `darkfactory/cli.py` | Add `fresh=True` to the `run_pipeline` call inside `_cmd_validate`, plus the explanatory comment |
| `tests/unit/test_cli_validate.py` (new or extend) | One regression test asserting validate proceeds across a hash mismatch |

## Ordering

1. Step 2 — write the failing test first; assert the pre-fix behavior (`DarkFactoryError` raised) to prove the test bites
2. Step 1 — apply the one-line fix
3. Step 2 again — flip the assertion; confirm the test now passes
4. Step 3 — add the comment
5. Run the targeted suite + the full unit suite to confirm 59 baseline failures unchanged

## Verification

```bash
python3 -m pytest tests/unit/test_cli_validate.py -v --timeout=30
python3 -m pytest tests/unit --timeout=30  # baseline 59 failures, no new
```

Plus a manual smoke that mirrors the user's trace:

```bash
cd /tmp/fake-project
mkdir -p .dark-factory/specs .dark-factory/state/myspec
echo "..." > .dark-factory/specs/myspec.md
echo '{"spec_hash": "deadbeef", "completed_step": 3, "iteration": 1, "status": "running"}' > .dark-factory/state/myspec/state.json
dark-factory validate myspec   # should NOT error with "spec has changed"
```

## Rollout Risk

- **Could `fresh=True` discard wanted state on a real validate?** No. Validate doesn't write to the iteration state file (it stops after step 1, before any state-mutating step). The only file it does write is `precheck_runs.json`, which is unaffected by `fresh`.
- **Could a user be relying on the current error to detect drift?** Theoretically yes — someone might use `validate` as a "did the spec change since I last ran the pipeline?" check. This is not a documented use case and there are better tools for it (`git diff`, `dark-factory run --fresh` itself). Acceptable break.
- **Does this change interact with the round counter?** No. The recorder is called before `run_pipeline` in `_cmd_validate` and operates on its own state file (`precheck_runs.json`), independent of the iteration state file.
