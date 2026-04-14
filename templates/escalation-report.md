# Escalation Report: [Spec Name]

**Spec:** [spec-name]
**Date:** [YYYY-MM-DD]
**Iterations Attempted:** [N]
**Pipeline Run ID:** [from audit log]

> **Scope note (2026-04-14):** The narrative sections (per-iteration history,
> persistent-issues prose, root-cause evidence) were removed after the
> 2026-03-28-database-foundation escalation confabulated scenario counts,
> scores, and failure-rate math that did not exist in the run's artifacts.
> The recommended-action bullets in that report were correct; the surrounding
> prose was not. Until the agent has structured inputs (test-results JSON,
> iteration state, trajectory) wired directly into its context, it must not
> be asked to narrate what happened — it invents. See B4 in
> `docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md`.

---

## Recommended Action

Check all that apply:

- [ ] Revise specification and re-enter pipeline
- [ ] Decompose into smaller specs
- [ ] Regenerate scenarios — run `dark-factory run [spec-name] --regen-scenarios`
- [ ] Manual human implementation needed
- [ ] Architectural redesign required

**Details:**

Each bullet below must begin with the artifact path it cites, in backticks,
followed by an em-dash and a short recommendation. Bullets without a leading
artifact path are malformed and will be rejected. Do not summarize iteration
history; the human will read the artifacts directly.

Format: `` `path/to/artifact.md` — <recommendation> ``

Example:

- `` `.dark-factory/reports/[name]-quality-gate.md` — export-contract violation on iter-3 (undeclared `DrizzleTx`, `initializeDatabase`); spec §5 Interface Contract must be amended before re-entry ``
- `` `.dark-factory/scenarios/[name]/fr-2-schema-helpers.test.ts` — asserts library-internal shape `_` not present in drizzle-orm 0.44 public API; regenerate with `--regen-scenarios` after spec clarification ``

**Scenario-audit pointer:** If the same scenarios failed across all
iterations with zero diff in the failing set, list those scenarios by the
file path exactly as it appears in the test-results artifact or
`.dark-factory/scenarios/` directory — do not abbreviate, do not describe.
Do not speculate about *why* they failed — that is what the artifacts are for.

---

## Artifact Locations

- Spec: `.dark-factory/specs/[name].md`
- Scenarios: `.dark-factory/scenarios/[name]/`
- Quality gate reports: `.dark-factory/reports/[name]-quality-gate.md`
- Reviews: `.dark-factory/reviews/[name]-review.md`
- Feedback files: `.dark-factory/reviews/[name]-feedback-iter[N].md`
- Audit log: `.dark-factory/audit/[name]-[run-id].log`
- State file: `.dark-factory/state/[name].state`
