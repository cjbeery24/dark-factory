# Pipeline Run Report — 2026-03-28-database-foundation (re-run after addendum fix)

**Target project:** `budget-brain-v2`
**Spec:** `2026-03-28-database-foundation`
**Run ID:** `2edc139ab7fa4732`
**Spec hash:** `1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662`
**Launched:** 2026-04-16 18:38:16 UTC
**Terminated:** 2026-04-16 19:53:25 UTC
**Wall time:** ~1 h 15 min 9 s
**Preceded by:** `2026-04-16-11-10-budget-brain-v2-2026-03-28-database-foundation.md` (INTERRUPTED at architecture-lock)
**Triggering fix:** `architect_addendum.md` swap of canonical worked example from push-mode to migrate-mode (this repo, un-committed SHA at run start)

## Outcome: ESCALATED after 3 iterations

The pipeline completed its full 3-iteration budget. Every pre-implementer phase (spec validation, architect, scenario generation, coverage gate, test-engineer) cleared. Implementation succeeded through iter 3 with every host-side gate green **except blind scenarios**, which stuck at 50% → 57% over iters 2 and 3. Oracle NO-SHIP'd each iteration; escalation agent ran and emitted `SPEC_ESCALATED iterations=3`.

This is the first run on this spec to reach the gate, the blind-scenario pass-rate surface, and the escalation path — exercises architecture-lock, Move 3b's success-path stamping, bounded architect-amend, `COVERAGE_SPEC_VEHICLE_MISMATCH` routing, smoke policy transition (advisory → fatal), and `SPEC_ESCALATED` all in one run.

State file (final):

```
spec=2026-03-28-database-foundation
run_id=2edc139ab7fa4732
spec_hash=1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662
completed_step=2
iteration=3
server_error=
smoke_result=passed
pass_rate=57
gate_exit_code=1
last_decision=NO-SHIP
phase=oracle_complete
status=escalated
```

## Iteration summary

| iter | implementer (s) | gate (s) | smoke | pass_rate | gate result | oracle |
|-----:|----------------:|---------:|:-----:|----------:|:-----------:|:------:|
| 1 | 435 + 343 (two attempts) | 778 | pass (advisory) | 0 | FAIL (setup) | NO-SHIP |
| 2 | 290 | 290 | pass (fatal) | 50 | FAIL (scenarios) | NO-SHIP |
| 3 | 568 | 568 | pass (fatal) | 57 | FAIL (scenarios) | NO-SHIP → ESCALATED |

Pre-implementer phases (once per run):

| phase | wall (s) | notes |
|-------|---------:|-------|
| spec-validator | 148 | in-pipeline re-validation (standalone 92/100, LOW divergence) |
| architect | 596 | fresh regenerate (cache invalidated, see Move 3b §) |
| qa-holdout | 323 | blind-holdout scenarios authored |
| test-engineer | 304 + 261 + 200 (three attempts) | one bounded amend + regen → `COVERAGE_PASS` |
| architect (amend) | 56 | `coverage_amend_1` — added `health-check.ts` entry point |

## Move 3b verification — success-path stamping landed

This run is the **first full-pipeline confirmation** that Move 3b's three-axis cache gate works on the happy path. Complements the prior run, which confirmed only the fail-closed ordering invariant (no stamp on broken manifest).

**1. Cache invalidation decision (t=0 s → t=153 s).** Pre-run manifest carried no stamp (left over from run #1's INTERRUPTED state). Gate correctly classified the prior manifest as stale and forced a regenerate:

```json
{"event":"ARCHITECT_CACHE_INVALIDATED","data":{
  "reason":"adapter_set_drift",
  "prior_adapters":[],
  "current_adapters":["postgres","typescript-node"]
}}
```

`prior_adapters=[]` is the signature of the ordering invariant holding: the broken run #1 apply raised before `_stamp_active_adapters` could run, so no stamp exists to match against. The cache gate treated absence as drift and regenerated — exactly the design.

**2. Success-path stamp applied.** Fresh iter 1 manifest carries the stamp:

```yaml
active_adapters:
  - postgres
  - typescript-node
stamp_integrity: bdda62432c9c1d28a9d6f8a038124b87247a3528780e844f18c4a7d103b25598
```

Confirms `_stamp_active_adapters` runs **after** `_apply_manifest_to_environment` succeeds. The invariant pinned by `test_manifest_not_stamped_when_apply_fails` (fail path) now has a matching real-run confirmation on the success path.

**3. G1 backstop fired.** `SPEC_ADAPTER_ACTIVATED adapter=postgres reason=spec_triggers` at t=0 s — project-level `active_adapters` did not include postgres; the adapter's `spec_triggers(spec_text)` matched on the spec's RLS density. Move 3b's design audit-ability target.

## Addendum fix confirmed on real run

Architect manifest now carries the intended setup command:

```yaml
commands:
  setup: cd apps/api && npx drizzle-kit migrate
```

No `REMEDIATION_TARGET` event fired. No consistency-rule violation surfaced. The three-edit addendum rewrite (example swap from push→migrate; migrate-mode variants promoted; spec-primacy paragraph) shifted architect output on the first exposure to the spec — a calibration win, not a model failure.

## Coverage gate exercised end-to-end

First run to ever reach scenario generation. The coverage gate fired three distinct signals on one pass:

**1. `COVERAGE_SPEC_VEHICLE_MISMATCH` — 5 anchors.** Test-engineer flagged AC-3, AC-4, EC-6, EC-9, FR-1 as requiring CLI execution / pool exhaustion / package-management semantics outside the unit-test framework. These emit a **distinct event class** (not `COVERAGE_ARCHITECT_AMEND`) — the design intent is "spec author amend, not architect amend." The distinction was implemented pre-run; this is its first real exercise.

**2. `COVERAGE_ARCHITECT_AMEND` — 3 anchors.** AC-9, EC-8, FR-4 — all three cited `apps/api/src/db/health-check.ts` as an unregistered entry point. The bounded architect-amend round ran in 56 s (fast — targeted edit), added the module to `test_integration.entry_points`, emitted `COVERAGE_ARCHITECT_AMEND_COMPLETED outcome=success new_entry_points=["../../../apps/api/src/db/health-check.js"]`, and handed off to the test-engineer regen. First real-run exercise of the feature introduced by commit 0424950 (`feat(coverage): route architect-fixable gaps to bounded amend round`).

**3. `COVERAGE_PASS anchor_count=28`** after one TE regen (attempt 2). The flow closed the architect-fixable gaps (entry points registered) and left spec-vehicle-mismatch anchors on the waiver path — the exact partitioning the design wanted.

## Blind-scenario failure surface (the actual signal)

Gate at iter 3: `pass_rate=57 (23/40)` — everything green except the blind-scenario bank:

```
| Install          | PASS |
| Setup            | PASS |   ← implementer produced migrations (see §)
| Build            | PASS |
| Lint             | PASS |
| Type Check       | PASS |
| Self-Tests       | PASS |
| Blind Scenarios  | FAIL |   ← pass_rate=57, 17 failures
| Mutation Testing | SKIP |
```

This is the **information-barrier working as designed**: the implementer's own tests pass, but half the blind scenarios (which it cannot read) catch behavior gaps it did not close from anchor-level oracle feedback alone.

Iter 3 defect-classifier output:

| category | count |
|----------|------:|
| IMPL_BUG | 16 |
| BRITTLE | 1 |

Failing anchors (iter 3) concentrate on RLS semantics: AC-10 (×3 — tenant isolation positive/negative), AC-12 (×2 — fail-closed with unset GUC), AC-5 (×2), AC-7, AC-8 (RLS policy patterns), AC-4 (migration application via CLI), AC-9, EC-8 (health check), AC-11, EC-9.

The rationales from the classifier repeat the phrase *"implementation likely has setup or database connectivity issues"* across the RLS group — a hint that the implementer's fix for one failure mode may not have addressed the underlying substrate-interaction issue. Worth inspecting during remediation rather than accepting the classifier's verbatim categorization; the *"likely"* hedge means the defect-classifier itself is unsure.

## Implementer recovery arc — setup resolved, RLS semantics persistent

**Iter 1 setup FAIL:** architect emitted `setup: drizzle-kit migrate`, but the implementer wrote `schema.ts` without running `drizzle-kit generate` to produce SQL migration files. `drizzle-kit migrate` stalled ("applying migrations..." then hang), failed the setup phase, aborted the gate. `pass_rate=0`.

**Iter 2 setup PASS:** implementer **recovered from anchor-level feedback alone**. Produced `apps/api/src/db/migrations/0000_baseline_rls_infrastructure.sql` — matches `drizzle.config.ts`'s `out:` path. Setup completed, tests ran. `pass_rate=50`. This is a good datapoint on what the barrier can and cannot resolve via feedback: the implementer inferred "migrate needs generate output" from spec §11 + NO-SHIP signal alone. Recovery without gate-log access worked here.

**Iter 3 marginal (+7 pp):** 50 → 57. Six additional scenarios cleared, but the RLS/tenant-isolation cluster stuck. The barrier's anchor-level feedback wasn't enough to surface whatever specific substrate interaction is wrong — classic case where a human spec/impl inspection is needed. That's exactly what ESCALATED is for.

## Events emitted (histogram)

```
 20 AGENT_START
 20 AGENT_COMPLETED
  4 FILES_CHANGED
  3 SMOKE_COMPLETED
  3 GATE_COMPLETED
  3 ITER_COST
  2 COVERAGE_GAP
  2 COVERAGE_REGEN
  2 BARRIER_VIOLATION
  2 TRAJECTORY_CLASSIFIED
  1 SPEC_START
  1 SPEC_ADAPTER_ACTIVATED
  1 ARCHITECT_CACHE_INVALIDATED
  1 CONTRACTS_STAGED
  1 COVERAGE_SPEC_VEHICLE_MISMATCH
  1 COVERAGE_ARCHITECT_AMEND
  1 COVERAGE_ARCHITECT_AMEND_COMPLETED
  1 COVERAGE_PASS
  1 SPEC_ESCALATED
```

No `REMEDIATION_TARGET` (architecture-lock cleared). No `BARRIER_PATH_ECHO_DETECTED`.

## Two `BARRIER_VIOLATION` events — noted, not fatal

Both from `role=test-engineer, path=apps/api/src`. The TE output referenced the masked source directory verbatim (almost certainly synthesized from the spec's Interface Contract, not read — the tmpfs mask is complete). Matches the known limitation documented in `CLAUDE.md`:

> the audit log detects naive/accidental barrier violations via path-string matching; it does not detect natural-language or semantic leakage

Defense-in-depth fired; primary barrier held. Not a regression — a false-positive from the path-string matcher when a legitimate source-tree reference appears in spec-derived output.

## Substrate manifest

Architect emitted the expected `.dark-factory/architecture/substrate-manifests/2026-03-28-database-foundation.toml`:

```toml
spec_id = "2026-03-28-database-foundation"
postgres_version = "16"

[[guc]]
name = "app.tenant_id"
default = ""

[[rls_table]]
name = "baseline_pattern"
expected_policies = ["tenant_isolation_policy", "tenant_isolation_nullable_policy"]

[[tenant_isolation]]
tenant_table = "tenants"
verifiable = false
```

Gate substrate-verification phase: `{"status": "skip", "findings": []}` — the Postgres substrate verifier ran but produced no findings. The `rls_table.name = "baseline_pattern"` (vs a concrete table like `users`) reflects F-1c's bootstrap nature: infrastructure patterns only, no concrete tenant tables yet. The verifier's `skip` status is consistent with this — no rows-level data to probe.

This is the substrate hook Move 4 (pending task #17) is designed to assert on pre-scenario-gen. Move 5 (pending task #18) assertion on `CONTRACTS_STAGED` is what gated the Postgres contract helpers into `.dark-factory/scenarios/2026-03-28-database-foundation/_contracts/postgres/` at t=740s.

## Next steps (in priority order)

1. **Don't remediate — escalate to spec-author inspection.** `SPEC_ESCALATED` is the right terminal. The failures concentrate in RLS semantics (AC-7, AC-8, AC-10, AC-12); the classifier's repeated "setup or connectivity issues" hedge is weak ground for auto-remediation. A human should read `apps/api/src/db/*.ts` + the failing scenarios together and decide whether the bug is impl, spec, or test-brittleness.
2. **Task #19 (`_write_project_hints` fix) still standalone.** Same story as run #1 — this run did not hit the framework bug behind task #19. G1 backstop handled the postgres activation path. Task #19 remains true-but-not-blocking.
3. **Task #17, #18 (Moves 4, 5) still relevant.** This run touched `CONTRACTS_STAGED` and the substrate-manifest emission — the two surfaces those moves will gate on. Neither failed, but neither was asserted.
4. **Consider task #21 (governance).** Re-running this spec without the run-#1 addendum fix would push-mode again. The "spec-anchored migrate mandate" reasoning is now encoded in a single adapter's `architect_addendum.md`. Whether to promote to a baseline consistency rule (prescriptive) or keep as policy-layer-only (current) is still pending.
5. **Optional task: revisit implementer prompt for RLS.** The RLS-test cluster cleared the build/typecheck/self-test but failed blind scenarios — a failure mode that suggests the implementer's mental model of `SET LOCAL` + `current_setting(name, true)` interaction may need a stronger pointer. Not urgent; happens to be visible here because F-1c is a substrate-heavy spec.

## Compared to run #1

| axis | run #1 | run #2 (this one) |
|------|--------|-------------------|
| terminated at | architecture-lock (apply contradiction) | SPEC_ESCALATED (iter 3 oracle) |
| manifest stamp | absent (ordering invariant) | present (success path) |
| architect setup command | `drizzle-kit push` (rejected) | `drizzle-kit migrate` (accepted) |
| REMEDIATION_TARGET | 1 | 0 |
| iterations | 0 | 3 |
| wall time | 5 min | 75 min |
| phases exercised | spec-validator, architect | all core + escalation |
| Move 3b axis confirmed | fail-path invariant | success-path stamp |

The addendum fix worked: first exposure produced the desired behavior with zero adapter-governance disruption. The consistency rule in `project-hints.toml` remained the policy-layer safety net but did not need to fire — the addendum's spec-primacy paragraph ("mirror it verbatim in `commands.setup`") calibrated the architect's first-pass output.

## Next operator action

The escalation agent has written its summary to `.dark-factory/reports/2026-03-28-database-foundation-*.md`. The operator should read the iter-3 gate details (especially the RLS failure cluster) and either:

- amend the spec to split AC-10/AC-12 into smaller behavioral anchors with clearer predicates, OR
- manually inspect the implementation and issue a directed spec-authored fix, OR
- determine the classifier's "connectivity issues" hedge is accurate and extend `commands.setup` to include a post-migrate RLS-policy verification step.

Choice depends on whether the underlying RLS policies *exist but don't enforce* (impl bug), or *aren't created at all* (migration gap). Worth reading `0000_baseline_rls_infrastructure.sql` directly.
