# Sprint 1 (T1.1–T1.4) — STABLE_FAILURE design

> Design for Neo review BEFORE implementation. Implements 00-RESET-PLAN
> Sprint 1 part A: reclassify the `SHIP_WITH_GAPS` override into a terminal
> `STABLE_FAILURE` outcome. Blast radius is **CRITICAL** (gitnexus: 53 upstream,
> 20 flows) — the whole pipeline return contract. Minimal-surface is a goal.

## Problem (current behavior, verified)

When `should_ship_with_gaps` (`iteration.py:151`) is eligible, the pipeline:
1. Biases the Oracle prompt: *"You SHOULD output SHIP"* (`iteration.py:5353-5366`).
2. Overrides a NO-SHIP Oracle decision into SHIP (`iteration.py:5465-5477`),
   emits `SHIP_WITH_GAPS` event, sets `oracle_verdict="SHIP"`.
3. `_step_oracle` returns `True` (`:5511`) → `run_iteration_loop` returns `True`
   (`:3549`) → `_handle_ship` (`orchestrator.py:805`) → **exit 0, status
   SHIPPED, `shipped.jsonl` row** — i.e. an override-ship is indistinguishable
   from a real ship. This is the success-metric corruption Sprint 1 fixes.

## Target behavior

An eligible run whose Oracle says NO-SHIP terminates as **STABLE_FAILURE**:
honest non-ship, distinct from both ship (exit 0) and escalation (exit 2).
The Oracle is no longer biased or overridden — its NO-SHIP stands.

## Exit code

`STABLE_FAILURE = 5`. **4 is taken** (`orchestrator.py:828` HOLD). Update the
`run_pipeline` docstring (`:195`).

## Mechanism (minimal-surface)

`run_iteration_loop` returns a plain `bool` and `orchestrator.py:804` collapses
everything non-`True` into `_handle_escalation`. To express a third outcome
without changing the return type, carry the signal out-of-band on `SpecRun`:

1. **New field** `SpecRun.stable_failure: bool = False` (`models/pipeline.py`).
2. **`_step_oracle`**: replace the override block (`5465-5477`). When
   `decision == "NO-SHIP" and spec_run.ship_with_gaps` → set
   `spec_run.stable_failure = True`, leave `decision = "NO-SHIP"`,
   `oracle_verdict = "NO-SHIP"`, return `False` (no forced SHIP). Emit a
   terminal `SPEC_STABLE_FAILURE` event from the handler (not here).
3. **Remove the prompt-bias** (`5353-5366`) entirely. The Oracle still receives
   the stable-failure facts via the existing classification/defect context; it
   just isn't told what to conclude.
4. **`run_iteration_loop`**: right after `if shipped: return True` (`~3549`),
   add `if spec_run.stable_failure: return False` so the loop terminates this
   iteration (no further iterations, no classification-routing fall-through).
5. **`orchestrator.py:804`** route:
   ```python
   if shipped:
       return _handle_ship(...)
   elif spec_run.stable_failure:
       return _handle_stable_failure(...)   # exit 5
   else:
       return _handle_escalation(...)       # exit 2
   ```

## `_handle_stable_failure` (new, orchestrator.py)

Mirror `_handle_ship` but with the must-NOT-fire set removed.

**Fires:** `spec_run.status = PipelineStatus.STABLE_FAILURE`; `write_state
"stable_failure"`; `_write_defect_registry` (documents the gaps — diagnostic,
see Q2); `emit_spec_stable_failure` (new event); `emit_cost_summary`;
`record_end(final_status="stable_failure")`; `snapshot_final(status=
"stable_failure")`; `_progress` log. Returns **5**.

**Must NOT fire** (the corruption set, per change-surface map §3):
`PipelineStatus.SHIPPED`, `record_shipped_spec`/`append_shipped`
(`shipped.jsonl`), `emit_spec_shipped`, `notify_shipped`, `registry_register`
(shipped-scenarios), regression, PR/branch/merge, canary, rollback monitor.
**Does NOT invoke** `AgentRole.ESCALATION` (`orchestrator.py:5224`).

## Status enum + terminal set

`models/pipeline.py`: add `PipelineStatus.STABLE_FAILURE = "stable_failure"`.
`orchestrator.py:5403` `_TERMINAL_STATUSES`: **add STABLE_FAILURE** or the
atexit interrupted-state writer overwrites it.

## Events

`models/events.py`: add `EventType.SPEC_STABLE_FAILURE`. `events/types.py`: add
`emit_spec_stable_failure(...)` carrying `stable_failures`, `stable_failure_ratio`,
`pass_rate`, `oracle_decision="NO-SHIP"`. The old `SHIP_WITH_GAPS` event +
`emit_ship_with_gaps` lose their only call site — keep the definitions (old-log
back-compat) but stop emitting (mark deprecated in a comment).

## plan/executor.py (dispatch)

Exit 5 currently falls into the `else → FAILED` branch (`:285-292`). Add a
`SpecDispatchStatus.STABLE_FAILURE` and branch it. **Downstream policy:** treat
like FAILED for dependency blocking (do not build dependents on un-shipped
code) but with the distinct label (see Q4).

## cli.py

`_cmd_run` is pass-through (`sys.exit(exit_code)`); add a human-readable line
for code 5 ("STABLE_FAILURE — stable failing set, Oracle declined to ship").

## Test migration

**Unchanged** (eligibility + registry + parsing): `TestMechanicalShipOverride
Conditions`, `TestBlockingAcVeto`, `TestEffortGate`, `TestHardGateParsing`,
`TestConfigValidation`, `TestDefectRegistry`, `TestShipWithGapsFlagPersistence`,
`TestClassificationRoutingGuard`.

**Must flip** (`TestShipWithGapsOracleOverride`):
- `test_oracle_noship_overridden_when_ship_with_gaps` → assert `shipped is
  False`, `oracle_verdict == "NO-SHIP"`, `spec_run.stable_failure is True`.
- `test_no_override_when_ship_with_gaps_false` → unchanged (False / NO-SHIP /
  stable_failure False).
- `test_override_suppressed_by_satisfaction_floor` → reframe: eligibility no
  longer force-ships, so satisfaction floor is moot for the terminal choice;
  assert stable_failure True regardless (see Q3).
- `test_ship_with_gaps_event_emitted` → move to an orchestrator-level test of
  `_handle_stable_failure` emitting `SPEC_STABLE_FAILURE`.

**New:**
- `test_orchestrator.py::test_stable_failure_path` — mock loop returns False +
  `spec_run.stable_failure=True` → exit 5; `notify_shipped`/`record_shipped_spec`
  NOT called; `AgentRole.ESCALATION` NOT invoked.
- **T1.4 integrity test** (`test_stable_failure_terminal.py`) — a STABLE_FAILURE
  run writes **zero** `shipped.jsonl` rows and exits ≠ 0. This is the linchpin's
  integrity guarantee.

## Open decisions for Neo

- **Q1 (rename):** `spec_run.ship_with_gaps` is now an *eligibility* flag, not a
  ship signal. Rename to `stable_failure_eligible`? It touches ~8 files + many
  tests on a CRITICAL path. **Proposal:** KEEP the name this sprint (minimal
  surface), rename in a dedicated follow-up. Accept the temporary misnomer?
- **Q2 (defect registry):** keep `_write_defect_registry` in the STABLE_FAILURE
  handler so gaps are still documented? **Proposal: yes** (diagnostic, not a
  ship signal).
- **Q3 (suppressions):** the old override had satisfaction-floor / composite-
  blocked suppressions (`5466-5470`). Those gated ship-vs-not; now the outcome
  is stable_failure-vs-escalation and stable_failure is the right terminal for
  any eligible NO-SHIP. **Proposal:** drop them — eligible + NO-SHIP → STABLE_
  FAILURE unconditionally. Correct, or do composite-blocked runs deserve
  escalation instead?
- **Q4 (dispatch blocking):** should a STABLE_FAILURE upstream block dependent
  specs like FAILED? **Proposal: yes**, block (don't build on un-shipped code).
- **Q5 (event/notify):** distinct `SPEC_STABLE_FAILURE` event (proposed) +
  no Slack notify in v1 (vs reusing escalation notify). OK?
- **Q6 (config name):** `stable_failure_ship_override` is now a misnomer
  ("...ship_override"). Rename to `stable_failure_terminal_enabled`? Touches
  config + tests. **Proposal:** defer with Q1.

## Neo review — APPROVE-WITH-CHANGES (applied)

Mechanism approved (out-of-band flag is correct; do NOT change the bool return
type). Required fixes folded in:

1. **Hole A (resume re-ship leak):** add `"stable_failure"` to the hardcoded
   `_TERMINAL` tuple in `_try_auto_resume` (`orchestrator.py:5344`) — separate
   from `_TERMINAL_STATUSES` (`:5403`). Without it, `--resume` re-enters the
   loop and a second Oracle pass could SHIP.
2. **Hole B (remediation telemetry):** `_handle_stable_failure` MUST call
   `_emit_remediation_outcome_if_pending(..., terminal_status="stable_failure")`
   (siblings do at 4983/5244/5297).
3. **Hole C (dependent wedge):** add `SpecDispatchStatus.STABLE_FAILURE` to the
   blocking set at `executor.py:308-311` (also resolves Q4). Not blocking →
   dependents hang PENDING → dispatch never reaches all_done.
4. **Q3 correction (SPLIT):** drop ONLY the satisfaction-floor suppression.
   **Composite-blocked must ESCALATE, not stable-fail** — anomaly/barrier
   violations need human eyes and must not launder into a "clean gap" terminal.
   Guard: set `stable_failure` only when `ship_with_gaps and not
   composite_blocked`.
5. **`:804` guard:** assert `not (shipped and spec_run.stable_failure)` — cheap
   insurance against a future edit that sets the flag AND returns True.
6. **Test fixes:** `TestClassificationRoutingGuard` is a FAKE test (asserts an
   inline-reconstructed expression, calls no production code) — delete/rewrite,
   do NOT count as coverage. Add: `test_composite_blocked_does_not_stable_fail`
   (locks Q3), `test_stable_failure_keeps_noship_verdict` (HOLD-safety
   invariant), executor dependency-blocking test. T1.4 integrity test also
   asserts resume treats `stable_failure` as terminal + remediation-outcome
   emitted.

Verdicts: Q1 defer (+comment), Q2 yes (call unconditionally in handler),
Q3 split (above), Q4 yes/block, Q5 ok (+iteration+run_id on event), Q6 defer
(+comment). Autonomy-1 HOLD cannot misroute (HOLD is inside `if decision==
"SHIP"`; stable_failure keeps NO-SHIP) — add the NO-SHIP invariant test to lock it.
