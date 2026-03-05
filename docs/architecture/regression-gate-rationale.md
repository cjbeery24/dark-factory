# Regression Gate Rationale

Why post-merge + in-pipeline regression is sufficient for the Dark Factory pipeline.

## The Pipeline Is the Only Path to Main

In a properly configured Dark Factory deployment, code reaches `main` exclusively through the pipeline. The pipeline runs `registry_run_regression` before creating the PR (when `REGRESSION_ON_SHIP=true`). This is the de facto pre-merge gate: regression failures block PR creation entirely.

No code ships without passing all previously-shipped scenarios.

## What Post-Merge Catches

The `dark-factory-regression.yml` workflow catches regressions introduced by:

- **Manual merges** that bypass the pipeline (operational discipline failure)
- **Merge conflicts** resolved during GitHub merge queue processing
- **Non-dark-factory PRs** merged between pipeline run and PR merge

These are edge cases, not the normal path. The post-merge workflow is a safety net, not the primary gate.

## The TOCTOU Trade-Off

Between the pipeline's regression run and the PR merge, other PRs may land on `main`. The in-pipeline test ran against a snapshot that could be stale by merge time. Post-merge detection accepts that regressions may briefly exist on `main`, with rapid notification (`notify_regression_blocked` via Slack/webhook) as the mitigation.

This is the same trade-off that most CI systems make. The alternative (serialized merges with full regression on each) does not scale: it would serialize all PR merges and add minutes of latency per PR in the queue.

## Why Not a Pre-Merge Required Check?

- The full regression suite requires Docker infrastructure (agent containers, scenario execution). Running this on every PR `synchronize` event is expensive and slow.
- The pipeline already ran regression against the same commit. Running it again as a PR check is redundant for the normal path.
- Post-merge detection with notification enables rapid response without the latency cost on every PR update.

## Merge Queue Interaction

If GitHub merge queues are used, the queue rebases PRs before merging. The queue's own required status checks could run a lighter regression (only the shipping spec's scenarios, not the full registry). This provides a middle ground between full pre-merge regression and none.

This is an optional enhancement. Configure it by adding a merge queue workflow that invokes `registry_run_regression --exclude <current-spec>` as a required check.

## Recommended Production Configuration

```bash
REGRESSION_ON_SHIP="true"       # Run regression before PR creation
REGRESSION_POLICY="block"       # Regression failures prevent PR creation
```

Plus the post-merge `dark-factory-regression.yml` workflow with Slack/webhook alerting as a safety net.

## When to Upgrade to Pre-Merge

Convert the post-merge workflow to a pre-merge required check if:

- Non-pipeline PRs are allowed to carry the `dark-factory` label
- Merge queue conflict resolution becomes a frequent regression source
- The deployment model changes to allow multiple merge paths to `main`

For most teams, the combination of in-pipeline regression + post-merge detection provides the right balance of safety and throughput.
