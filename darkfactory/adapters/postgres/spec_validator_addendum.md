<!--
Postgres spec validator addendum — substrate-primitive completeness.

This file is the prose template loaded by
`PostgresAdapter.spec_validator_addendum()`. The substrate-rule taxonomy
table is rendered from `substrate-rules.toml` via `load_substrate_rules()`
and appended to the bottom of this file at composition time, so the
validator agent receives a self-contained prompt — it does not need (and
cannot get) on-disk access to the TOML.

Governance cap: 80 non-blank lines, applied to the rendered output
(prose + taxonomy table). The contract harness enforces it on the
composed string returned by `spec_validator_addendum()`.
-->

## Postgres substrate-primitive completeness

When the spec asserts behavior that depends on stateful Postgres
substrate (custom GUCs, RLS policies, tenant isolation, etc.), the
spec MUST also declare the corresponding **substrate primitives** in
structured form. Without that declaration, the architect cannot emit
a verifiable substrate manifest — substrate-thin implementations
shipped on 2026-04-15 because spec assertions were prose-only.

### How to evaluate

1. Read the substrate-rule taxonomy table appended at the end of this
   addendum. Each row declares: a `trigger` name, a list of
   `match_patterns` (regex, case-insensitive), and the
   `required_primitives` the spec must declare when the trigger fires.

2. Scan the spec text against every rule's `match_patterns`. For each
   rule that fires, check whether the spec contains a top-level
   section titled exactly `## Substrate Primitives` containing a
   fenced TOML code block whose entries name every required primitive
   for that rule.

3. The fenced TOML block format is the contract between the spec
   author and the architect. Treat it as a near-mechanical declaration:
   the architect performs no semantic inference on its contents.

### Verdict

If a rule fires AND the `## Substrate Primitives` block is missing
OR omits a required primitive for that rule, mark a **CRITICAL_ISSUE**
naming:

- The triggering match (which trigger row, which spec phrase fired it),
- The specific required primitives that are missing.

Example phrasing:

> CRITICAL_ISSUE: spec asserts behavior matching the `custom_guc`
> trigger but does not declare the required primitives in a
> `## Substrate Primitives` fenced TOML block. Add the section listing
> the missing primitives shown in the taxonomy below before
> re-validating.

If no rule fires, this addendum contributes nothing to the verdict —
substrate verification is conditional, not universal.

### Substrate rule taxonomy
