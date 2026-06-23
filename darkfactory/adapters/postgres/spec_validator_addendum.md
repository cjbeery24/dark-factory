<!--
Postgres spec validator addendum — substrate-primitive completeness.

This file is the prose template loaded by
`PostgresAdapter.spec_validator_addendum()`. The substrate-rule taxonomy
table is rendered from `substrate-rules.toml` via `load_substrate_rules()`
and appended to the bottom of this file at composition time, so the
validator agent receives a self-contained prompt — it does not need (and
cannot get) on-disk access to the TOML.

Governance cap: 85 non-blank lines, applied to the rendered output
(prose + taxonomy table). The contract harness enforces it on the
composed string returned by `spec_validator_addendum()`
(`GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP` in tests/adapters/).
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

## Wire-accurate predicate language for library-mediated surfaces

An AC/EC asserting a predicate over a library call result MUST state
the predicate in the wire-level vocabulary the test runner observes,
not the SQL-semantic vocabulary true only at the database layer. Two
surface-form traps:

1. **`current_setting(name, true)` nullish.** postgres.js returns `''`
   (not SQL `NULL`) for an unset GUC. Fire ONLY when an AC/EC
   **prescribes** `NULL` / `is NULL` / `toBeNull` / `IS NULL` as the
   **expected pass-value** of a test over `current_setting(..., true)`.
   **Suppress** only when a wire-correct form
   (`nullish`, `empty string or null`, `NULLIF(...)`) is the predicate
   the clause PRESCRIBES for that same assertion (teaching/correcting
   the predicate — a rationale / Negative-Space note). Mere co-occurrence
   of `nullish` elsewhere does NOT suppress a distinct AC that still
   prescribes `NULL` / `toBeNull` / `IS NULL` as its pass-value.

2. **Ambiguous `sql` tagged-template origin.** `drizzle-orm`'s `sql` →
   a `SQL` consumed by `tx.execute(...)`; `postgres.js`'s → a
   `PendingQuery` that is NOT. Fire as CRITICAL only when the origin is
   **unresolved spec-wide**: both libs are deps AND no `import { sql }
   from 'drizzle-orm'`, Constraints/AC note, or example anywhere names
   it. If origin IS resolved spec-wide but a `tx.execute(sql\`…\`)`
   use-site lacks collocated attribution, emit a non-blocking **Warning**
   — the test-engineer reads the whole spec (`specs/` is not masked), so
   spec-wide resolution suffices to avoid blocking.

### Verdict (wire-language rules)

Rule 1 fires → mark **CRITICAL_ISSUE**, name the anchor ID, quote the
offending phrase.

Rule 2 (origin unresolved spec-wide) → mark **CRITICAL_ISSUE**, name the
anchor ID. Rule 2 (origin resolved spec-wide, use-site not collocated) →
non-blocking **Warning**, never a blocker.

### Substrate rule taxonomy
