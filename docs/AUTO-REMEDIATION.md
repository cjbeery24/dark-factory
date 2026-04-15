# Auto-Remediation Policy

This document defines when `dark-factory remediate` will apply a fix
automatically, when it requires human confirmation, and when it refuses
to act at all. The policy is enforced by the CLI dispatcher; this
document is the authoritative reference when the tooling behaves in a
way that surprises you.

## Levels

Three levels, applied per `failure_class` (emitted by C1 in the
`REMEDIATION_TARGET` event):

| Level | Failure class(es) | CLI behavior | Rationale |
|---|---|---|---|
| **Propose** | `consistency_violation` with `auto_applicable: true` | Show diff. Interactive: require `y/n`. CI: require explicit `--apply`. First occurrence of a novel `rule_or_check` additionally requires `--accept-novel` in CI. | A consistency rule derived from a clean contradiction is a mechanical fix — the diff is small, reviewable, and doesn't touch source code. Safe to automate once the operator has confirmed intent. |
| **Guided** | `spec_quality`, `precheck_finding` | Print rationale, next-step reference, and the `/df-write-hints from-failure <run-id>` and/or `/df-write-spec` skill commands. Never writes to disk. | Fixing a spec-validator finding requires the operator to read the spec, understand what the validator objected to, and choose an edit. A diff the tool could auto-generate would either be wrong or be so narrow it doesn't address the underlying finding. |
| **Manual** | `stub_compile_error`, `proposal_typecheck_failure`, any target with `auto_applicable: false` | Print error, rationale, and the offending report path. No action. | These classes mean the spec is asking for code shapes that don't compile. The fix is a spec re-scope, which is a human judgment call. Any tooling-generated "fix" would be papering over a spec defect. |

### What the "auto_applicable" bit really controls

`auto_applicable` is set by the *emitting* gate, not by the CLI. When
false, the CLI will not write even under `--apply`. The flag encodes
the emitter's declaration that the payload is mechanically safe — for
example, A1 sets it to `false` when the contradiction carries no
compiled regex (see `orchestrator.py:1654` comment block for the A1
case). Refusal under `auto_applicable: false` is not a policy decision,
it's a correctness gate: the tool doesn't have the inputs it needs.

## The novel-rule cross-cut

**First occurrence of any novel `rule_or_check` in this project always
requires human confirmation.** This applies regardless of failure class
or `auto_applicable` state.

Novelty is computed by scanning `.dark-factory/events.jsonl` for prior
`REMEDIATION_TARGET` events with the same `rule_or_check`. Project-
scoped, not spec-scoped — a rule that fired on one spec ceases to be
novel across the project. See `is_novel_rule` in
`darkfactory/events/remediation.py` for the exact query.

### Why

A novel rule is one the project has never seen before. Its first
occurrence may be:
- a real, new class of contradiction (legitimate — add the rule)
- an architect prompt regression that fabricated a plausible-looking
  rule (don't add — fix the prompt)
- a one-off flake from LLM non-determinism (don't add — the rule will
  never fire again and becomes noise)

The tool can't tell these apart. A human can. So the first occurrence
always gets a y/n, even under `--apply`.

### The `--accept-novel` override

`dark-factory remediate --apply --accept-novel` bypasses the novel-rule
guard in non-interactive contexts. Use this sparingly:

- **Audit.** Every use emits a `REMEDIATION_ACCEPTED_NOVEL` event to
  `.dark-factory/events.jsonl`. Reviewers can enumerate degraded-run
  bypasses with:
  ```
  grep '"event":"REMEDIATION_ACCEPTED_NOVEL"' .dark-factory/events.jsonl
  ```
- **Guardrails.** The override is only meaningful with `--apply`. The
  CLI rejects `--accept-novel` without `--apply` at arg-parse time
  (exit 2). Passing it on a non-novel target logs a warning and
  proceeds (CI configs can default it on without breaking).
- **Scope.** The override applies only to the first-occurrence guard.
  It does not override `auto_applicable: false` and does not change
  failure-class routing.

### Threat model — novelty tracking is not a security boundary

Novelty is computed from `.dark-factory/events.jsonl`, which is a repo-
writable file. A repo-writer could pre-seed a fabricated
`REMEDIATION_TARGET` record with any `rule_or_check` name, making future
real occurrences read as non-novel and silently bypass the confirmation
gate.

**This is not a regression in the security model.** Anyone who can
write `events.jsonl` can also write `project-hints.toml` directly; the
attack surface is unchanged.

The novel-rule rule is a guard against the *accidental* auto-install of
buggy one-off rules from honest pipeline runs — not a guard against an
adversarial repo-writer. If a stronger boundary becomes necessary
(supply-chain-aware deployments, multi-tenant infrastructure), move
novelty tracking to a pipeline-signed state file
(`.dark-factory/state/rule_seen.json` with a hash chain) written only
by C1 emission. Out of scope for v1.

Likewise, `REMEDIATION_ACCEPTED_NOVEL` audit events are written to the
same repo-writable `events.jsonl` and are not tamper-evident: a grep
recipe counts honest operator intent, not adversarial bypasses. The
audit trail's value is reconstructing what a trusted operator did, not
detecting an untrusted one.

## Not planned: a "Level 4" fully-autonomous loop

The plan's draft proposed graduating specific rule names to fully
autonomous apply after enough evidence (≥10 successful applies, ≥80%
`outcome=fixed`). This is deferred until:

1. The `REMEDIATION_OUTCOME` event has been accumulating long enough
   to produce meaningful per-rule statistics. Today (2026-04-14) this
   data is too sparse to calibrate against.
2. The policy file format supports per-rule autonomy overrides.
3. Someone has used `dark-factory hints stats` against real outcome
   data and has a principled graduation-threshold story.

In the meantime, `dark-factory hints stats` surfaces the per-rule
success rates so operators can make the call manually. When Level 4
lands, the graduation criteria will be data-driven and per-rule
rather than per-class — rules graduate, classes don't.

## How to check a rule's track record

```
dark-factory hints stats
```

Reads `.dark-factory/events.jsonl`, groups `REMEDIATION_OUTCOME` events
by `rule_or_check`, and prints a table. A rule with ≥10 total outcomes
and ≥80% `fixed` is a strong Level-4 candidate; anything below 5
outcomes is not enough evidence to reason about.

## Reference

- Plan: `docs/plans/2026-04-14-pipeline-modularity-and-remediation.md`
  §C5 (line 782).
- Event schemas: `darkfactory/events/remediation.py`
  (`REMEDIATION_TARGET`, `REMEDIATION_OUTCOME`,
  `REMEDIATION_ACCEPTED_NOVEL`).
- CLI dispatch: `darkfactory/cli.py:_cmd_remediate`.
- Novelty query: `is_novel_rule` in
  `darkfactory/events/remediation.py`.
