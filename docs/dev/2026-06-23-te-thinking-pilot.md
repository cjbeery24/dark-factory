# Design: TE-thinking pilot — checkpoint-resume the Test Engineer for cheap repeated study

**Status:** DESIGN — not implemented. Neo-reviewed (code-grounded). The one framework
change (`stop_after_step==2`) is Linus-gated; the experiment wrapper is throwaway.
**Resumes:** the paused TE-speedup investigation (this session) + the stream-summarizer's
designed "backfill on TE streams" payoff (`docs/dev/2026-06-23-stream-summarizer.md`).
**Template:** the validator-confirm pilot (serial, preserve everything, decompose the mechanism).

## Goal
Characterize TE non-determinism by running TE-generate MANY times (N≥10) on one fixed spec,
preserving full thinking streams, to locate fixable causes: mega-turns, the ~906s stall class,
the contract-helper-scaffolding confusion, and the `validate` spin-vs-progress split. Output is
discarded — this is a STUDY, not a ship.

## Why resume-TE-only is sound (the earlier "TE+QA atomic" rule does NOT forbid this)
- The "TE+QA scenario-gen is one atomic phase" rule is a **ship-coherence** constraint (the TE
  scenarios + QA holdouts are a matched set feeding the gate/impl). A discard-the-output study
  triggers no gate and no ship, so it doesn't apply.
- The TE **masks `.dark-factory/holdouts/`** (`barriers.py:163`) — it cannot read anything QA
  produces. Its inputs are spec + anchor manifest + architect manifest + substrate (+env/contracts/
  free-vars/Tier-1). So "skip QA" doesn't change the TE at all.
- TE runs at **non-zero temperature** (not in `_TEMPERATURE_ZERO_ROLES`, `agents.py:198-217`), so
  the study samples the *real* non-determinism distribution.

## Mechanism (Neo-corrected — NOT a bespoke harness, NOT a first-class --from-phase)
1. **Framework seam (Linus-gated, ~3 lines):** add `stop_after_step == 2` halt after scenario-gen
   sets `completed_step = 2` (`orchestrator.py:835`). `stop_after_step` is already wired for `==1`
   (`run_pipeline` sig :227, honored :425/:794; CLI uses `==1` at `cli.py:2229`). This is the clean
   "stop after TE" exit. (Do NOT invert `--from-phase gate` — it SKIPS the TE; it's ~40% of the
   plumbing and in the wrong direction.)
2. **Throwaway experiment wrapper (no ship path):** snapshot the clean pre-TE state ONCE, then per
   sample: `rm -rf .dark-factory/scenarios/<spec> .dark-factory/holdouts/<spec>` → restore snapshot
   → `run_pipeline(resume=True, stop_after_step=2, completed_step=1, proposal_rehearsal_enabled=False)`
   → preserve that sample's streams/report (rename by sample index — `run_id` is reused from state,
   `orchestrator.py:6851`). Runs the REAL `_step_scenario_generation → _invoke_test_engineer_generation`
   path, not a reimplementation.
3. **Skip QA** in the study (`scenario_parallel_generation=False` + short-circuit the QA call) —
   else QA burns ~20 min/sample for discarded output.

## Checkpoint contents — get these EXACTLY right or you study a TE that doesn't exist
The dominant TE input is the **anchor manifest**, and it is a **Step-1 (validation/precheck)
artifact, NOT an architect artifact** (`orchestrator.py:703-731`). Snapshot must capture:
1. `.dark-factory/state/<spec>/anchor_manifest.json` (Step-1; the AC/FR/EC list, `:3775`)
2. `.dark-factory/architecture/<spec>-manifest.yaml` (architect, `:3821`)
3. `.dark-factory/architecture/environment.toml` (`:3787`)
4. `.dark-factory/architecture/substrate-manifests/<spec>.toml` — **gates `_contracts/` staging
   AND the SA addenda** (`:3715,:3861`); miss it and the TE prompt silently changes
5. Tier-1 contract source (`_compile_tier1_contracts`, `:5698`)
6. Free-variables source (`_extract_free_variables`, `:3806`)
7. the state file (`completed_step`, `run_id`)
8. a CLEAN (empty/absent) `.dark-factory/scenarios/<spec>/`
**Validation:** diff sample-1's assembled TE prompt against a known-good full-run TE prompt before
trusting any sample (extend the env-gated prompt-dump — added for the validator — to the TE).

## Traps that invalidate the study (must handle)
- **`te_files_existed` short-circuit** (`orchestrator.py:3541`): if `scenarios/<spec>/` has test
  files, the TE is **skipped** (only QA runs). Restore a clean scenarios dir EVERY sample.
- **Throwaway tree only:** the TE writes real scenarios; a later real `dark-factory run` on the
  same spec would pick them up (`te_files_existed` → contamination + the impl reads them as TDD
  targets). Never run the study in a tree you'll later ship from.
- **Step-1.75 proposal-rehearsal** (`:813`) fires between architect and TE on resume — disable it.

## The confound that would manufacture a false finding (the headline discipline)
Headline metrics (mega-turns, stalls, validate-spin) are **latency** metrics. Serial same-checkpoint
runs MAXIMIZE two confounds simultaneously: (a) server-side **prompt-cache warmth** (biases later
samples' latency DOWN), (b) **5h rate-budget drift** (biases later samples' gaps UP — our own
post-mortem: "biggest gaps at full budget"). Read naively → a fake non-determinism trend.
**Controls:**
- **Separate BEHAVIORAL metrics (intrinsic — trust these): turn count, file-map churn, validate
  retries, files-written, which-anchors-covered.** From LATENCY metrics (confounded — covariate-adjust):
  wall, per-turn gaps, stalls.
- Block-randomize sample order; spread across multiple FRESH 5h windows; cap N per window.
- Log per sample: cache-read tokens (`run-telemetry.jsonl`, `agents.py:730`), wall-clock,
  elapsed-since-window-start.

## What we expect to glean (ties to the Neo-reviewed TE-speedup levers)
- `validate` spin-vs-progress (tsc error-count trajectory) → turn-bound vs convergence-bound →
  whether intra-TE parallelism helps or is useless.
- Frequency of the contract-helper-scaffolding confusion → validates the **L0 auto-stage-starters**
  fix (the contract-provisioning gap found in the run4 hand-analysis).
- Mega-turn / stall frequency + whether they're intrinsic-thinking vs budget/cache artifacts.
- cache-read distribution → whether the cold-start-prefix folding lever is worth building.

## Gates
`stop_after_step==2` seam: Linus before merge. Wrapper: throwaway, never enters a ship path.
Schedule AFTER the validator pilot completes (it's a heavier full-runs-through-TE study, even with
the checkpoint).
