# Pipeline run — budget-brain-v2 / 2026-03-28-database-foundation (d717e915)

**Date**: 2026-04-21 14:10:30Z → 14:43Z
**Run ID**: `d717e915ae034979`
**Target**: `~/sites/budget-brain-v2`
**Spec**: `2026-03-28-database-foundation`
**Outcome**: **ABORTED** — iter-1 preflight violations persisted after focused implementer retry.
**Terminal event emitted**: none (pipeline raised `DarkFactoryError` and exited)
**Wall time**: ~33 min
**Dark-factory HEAD at run**: `813c392` (three fixes landed immediately before the run: HardAbort routing, Import Contract bare-specifier, archive+counts.env)

---

## Outcome timeline

| t | event | detail |
|---|---|---|
| 0s   | `SPEC_START` | — |
| 260s | `spec-validator` complete | exit 0 |
| 391s | `architect` complete | exit 0; `setup = "cd apps/api && npx drizzle-kit migrate"` (correct per spec) |
| 864s | `qa-holdout` complete | 7 holdouts |
| 1120s | `test-engineer phase=generate` complete | 7 scenarios |
| 1121s | `COVERAGE_GAP` → `COVERAGE_REGEN` | — |
| 1339s | `test-engineer phase=coverage_regen` complete | 11 scenarios total, `COVERAGE_PASS` |
| 1340s | `implementer iter=1 attempt=1` start | — |
| 1814s | `implementer iter=1 attempt=1` complete | 474s, 10 files changed |
|  — | preflight FAILED (host-side) | FR-2 forbidden pattern match (see below) |
| 1814s | `implementer iter=1 attempt=2` start | focused retry |
| 1923s | `implementer iter=1 attempt=2` complete | 109s |
|  — | preflight FAILED again | same FR-2 match |
|  — | `DarkFactoryError` raised | `iteration.py:1789` — "Preflight violations persisted after the focused implementer retry" |
|  — | pipeline exit | **no** `SPEC_NOSHIP` / `SPEC_ABORTED` / `SPEC_ESCALATED` event; **no** `final/meta.json` in per-run archive |

---

## State file at exit

```
spec=2026-03-28-database-foundation
run_id=d717e915ae034979
status=interrupted
iteration=1
completed_step=2
phase=
smoke_result=
pass_rate=
gate_exit_code=
```

---

## Primary failure — preflight forbidden-pattern self-match

The architect's manifest declares FR-2 forbidden patterns at
`2026-03-28-database-foundation-manifest.yaml:139-142`:

```yaml
forbidden_patterns:
  FR-2:
  - sql`SET LOCAL app.tenant_id = ${tenantId}`     # line 141
  - re:sql`.*app\.tenant_id.*=.*\$\{.*\}`          # line 142
```

Preflight flagged the violation twice in a row:

```
  - FR-2 forbids `sql`SET LOCAL app.tenant_id = ${tenantId}``
      .dark-factory/architecture/2026-03-28-database-foundation-manifest.yaml:141
      matched: `- sql`SET LOCAL app.tenant_id = ${tenantId}``
  - FR-2 forbids `re:sql`.*app\.tenant_id.*=.*\$\{.*\}``
      .dark-factory/architecture/2026-03-28-database-foundation-manifest.yaml:141
      matched: `- sql`SET LOCAL app.tenant_id = ${tenantId}``
```

**The scanner matched the manifest's own declaration of the pattern.** Both
the literal match and the regex match cite the same source line (141) of
`.dark-factory/architecture/<spec>-manifest.yaml`, and the matched text is
the YAML list item that declares the pattern (leading `- sql\`…\``).

This is a **preflight self-match bug**: the forbidden-pattern scanner's file
corpus includes the architecture manifest YAML where the patterns are
declared, so every forbidden pattern matches itself with 100% reliability.
The focused implementer retry can't fix it — removing the pattern from the
manifest would violate the architect's authority, and there's no way to
"fix" the implementer's code to clear a match that lives in the manifest
file.

The implementer's actual code may or may not have violated FR-2 — we can't
tell from the preflight output because the self-match short-circuits
every scan.

---

## Pipeline fixes validated by this run

The run was launched immediately after committing four fixes (commits
`ee48a93`, `f386d02`, `813c392`). Their status:

| Fix | Status | Notes |
|---|---|---|
| HardAbort for classifier retry exhaustion (commit `ee48a93`) | Not exercised | Classifier never ran — iter aborted at preflight before scenarios executed. |
| Import Contract bare-specifier `./` prefix (commit `f386d02`) | Not exercised | Import Contract phase runs inside the quality gate; never reached. |
| `[pipeline]` phase-attribution prefix (commit `f386d02`) | Not exercised | Gate never emitted its full markdown table. |
| Archive `completed_iteration` invariant (commit `813c392`) | Partially validated | Callers in ship/escalate/abort handlers now pass `len(spec_run.iterations)` — but this run aborted **before** reaching any of those handlers. See "Archive routing gap (still open)" below. |
| Scope-filter counts.env writeback (commit `813c392`) | Not exercised | Scope filter never ran — gate didn't execute. |
| Phase D `blocking_acs` serialization | **VALIDATED** | `precheck/<spec>.json` carries `blocking_acs: {AC-6, AC-7, AC-10, AC-12, AC-13, AC-14}` and `blocking_ac_issues` as first-class keys. |

---

## Archive routing gap (still open — new finding)

Commit `ee48a93` converted the classifier-retry-exhaustion raise
(`iteration.py:3572`) from `DarkFactoryError` → `HardAbort` so that
`run_pipeline`'s abort handler (`orchestrator.py:_handle_abort`) would fire
and call `snapshot_final(..., status="aborted")`.

This run shows the **same routing gap applies to the preflight raise** at
`iteration.py:1789`:

```python
raise DarkFactoryError(
    "Preflight violations persisted after the focused implementer "
    "retry. Iteration aborted …"
)
```

The archive for this run (`archive/20260421-071030-…-d717e915/`) contains
**only `run.json`** — no `iter-1/`, no `final/meta.json`. Same symptom the
earlier fix addressed for the classifier case, but on a different raise
site.

There is also a third raise at `iteration.py:2291` (`Scenario
infrastructure failure`) with the same pattern. That one did not fire on
this run, but carries the same risk.

**Recommendation**: convert all three "abort iteration to save budget"
raises in `iteration.py` to `HardAbort` (the preflight-failure comment at
line 1783 explicitly cites the classifier case as the intended pattern, so
this is strictly a completeness fix).

---

## Run-hygiene observations

### Abandoned per-run archive from a pre-session launch
When the skill ran step 1a it discovered `run_id=be1bae1cb2884226` in the
state file and created `archived-runs/be1bae1cb2884226/` with full
architecture + audit + 2 scenario files. But the matching per-run archive
at `archive/20260421-070700-…-be1bae1c/` contained only `run.json` — no
iteration or final dirs. That suggests `be1bae1c` crashed between
`init_archive_root` and the first iteration, leaving a skeleton directory.
The user identified this during the run and we deleted the orphan.

Impact: `snapshot_iteration`/`snapshot_final` only write when their
respective call sites are reached; a crash in the prep phase leaves the
archive empty. Not a regression; documented here as expected behavior.

### `init --update` silently rewrote `project-hints.toml`
Running `dark-factory init --update` regenerated the hints file and
dropped:

- `test_runner = "vitest"` (now empty string)
- `"postgres"` from `active_adapters`
- Custom comments explaining the migrate-mode-vs-push-mode consistency
  rule

Skill doc claims `init --update` preserves `project-hints.toml` — it does
not. I restored the committed version via `git checkout` before the run
continued. **Follow-up**: investigate why `init --update` regenerates
the `[detected]` block without merging existing hand-edits; or clarify
the skill doc if regeneration is intentional.

### Preflight logged but not emitted as an event
Preflight failures log to stdout and raise `DarkFactoryError`, but emit
no `PREFLIGHT_FAILED` event in `events.jsonl`. The Monitor task had no
visibility into the failure — I only learned of it by tailing
`/tmp/df-run-<spec>.log` after the process exited. Consider adding a
`PREFLIGHT_VIOLATION` event so live monitors can flag this without
polling stdout.

---

## Full preflight output (from /tmp/df-run-...log)

```
ERROR: error: Preflight violations persisted after the focused implementer retry.
Iteration aborted — continuing would spend full-gate and Oracle budget on
code that already fails a deterministic contract check (forbidden patterns
or test-presence drift). Most common root causes: (1) architect's
forbidden_patterns rule is wrong and blocks valid code; (2) implementer
added a source file without its companion test; (3) spec ↔ architecture
manifest drift. Last preflight output:

The following forbidden patterns were found in your implementation:

  - FR-2 forbids `sql`SET LOCAL app.tenant_id = ${tenantId}``
      .dark-factory/architecture/2026-03-28-database-foundation-manifest.yaml:141
      matched: `- sql`SET LOCAL app.tenant_id = ${tenantId}``
  - FR-2 forbids `re:sql`.*app\.tenant_id.*=.*\$\{.*\}``
      .dark-factory/architecture/2026-03-28-database-foundation-manifest.yaml:141
      matched: `- sql`SET LOCAL app.tenant_id = ${tenantId}``

These patterns are declared in the architecture manifest as spec-level
MUST-NOT constraints tied to the cited anchor(s). Remove them before the
pre-gate will accept this iteration.
```

---

## Recommended next steps (ranked by blast radius)

1. **Fix preflight forbidden-pattern scanner** — exclude
   `.dark-factory/architecture/*-manifest.yaml` from the scan corpus. This
   is a **blocker**: every spec that declares any `forbidden_patterns` in
   its manifest will self-match every iteration. The three recent runs
   that did ship (d8268a4a, dc53cf0d) may have had fewer/no
   forbidden_patterns declared, which masked this.
2. **Convert remaining `DarkFactoryError` aborts to `HardAbort`** at
   `iteration.py:1789` (preflight) and `:2291` (scenario infrastructure
   failure). Same pattern as the classifier-retry fix.
3. **Emit `PREFLIGHT_VIOLATION` events** before raising, so Monitor-based
   observers can report the failure in real time.
4. **Investigate `init --update` regenerating `project-hints.toml`** — at
   minimum update the skill doc; at best fix the merge logic to preserve
   `[detected]` hand-edits.

---

## Files and paths

- **Run archive (incomplete)**: `~/sites/budget-brain-v2/.dark-factory/archive/20260421-071030-2026-03-28-database-foundation-d717e915/`
  (only `run.json`, confirming archive routing gap)
- **Pipeline stdout log**: `/tmp/df-run-2026-03-28-database-foundation.log`
- **State file**: `~/sites/budget-brain-v2/.dark-factory/state/2026-03-28-database-foundation.state`
- **Architect manifest (source of self-match)**: `~/sites/budget-brain-v2/.dark-factory/architecture/2026-03-28-database-foundation-manifest.yaml:141`
- **Events**: `~/sites/budget-brain-v2/.dark-factory/events.jsonl` (terminates at `FILES_CHANGED` for iter-1 attempt-2)
