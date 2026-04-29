<!-- BEGIN-CONTRADICTION-TYPOLOGY -->
## Contradiction Typology (SSOT)

A contradiction between two spec clauses is reported only when both
clauses can be reduced to a structured tuple AND the pair fits one of
five named types AND the **refinement filter** below does not apply.
Open-vocabulary "this seems contradictory" is banned. If you cannot
name the type and cite the discriminating slot, you may not flag it.

### Claim tuple shape

Each load-bearing prose clause is reduced to:

```
(subject, predicate, polarity, object, anchor)
```

- **subject** — noun phrase referring to a system entity (table, file,
  function, env-var, command, role, request, event).
- **predicate** — one verb from the closed vocabulary below.
- **polarity** — `positive` or `negative`. Negation cues:
  `no`, `none`, `zero`, `never`, `not`, `MUST NOT`, `cannot`,
  `excludes`, `forbidden`, `without`.
- **object** — noun phrase the predicate acts on.
- **anchor** — byte-addressable pointer back to the spec:
  `FR-N.processing@line<L>`, `FR-N.side_effects@line<L>`,
  `FR-N.output@line<L>`, `AC-N`, `EC-N`, or `NFR-N`.

### Closed predicate vocabulary

Map free-text verbs to one of these by semantic class. Add a synonym
note when the prose verb differs from the canonical:

| Canonical predicate | Synonyms in prose |
|---------------------|-------------------|
| `creates` | creates, generates, emits, instantiates, spawns, makes |
| `produces_record_in` | records, tracks, writes-row-to, registers, logs (when persistent) |
| `requires` | requires, needs, depends_on, must-have, expects |
| `forbids` | forbids, MUST NOT, prohibits, excludes, never |
| `reads` | reads, fetches, loads, retrieves, queries |
| `writes` | writes, persists, saves, stores, mutates |
| `returns` | returns, yields, resolves-with, responds-with |
| `rejects` | rejects, errors, throws, fails-with, refuses, denies |
| `runs_before` | runs-before, precedes, prerequisite-for, must-occur-before |
| `runs_after` | runs-after, follows, succeeds, must-occur-after |

Verbs outside this list either map by closest semantic class (note the
synonym in the tuple's `raw` field) or reduce to `requires` /
`forbids` for declarative state.

**Compound clauses joined by AND/OR/COMMA split into separate claims.**
Example — the prose `"creates no tables and applies no policies"`
yields TWO tuples:

1. `(baseline migration, creates, negative, tables, FR-3.processing@line62)`
2. `(baseline migration, applies, negative, policies, FR-3.processing@line62)`

### Five contradiction types

| # | Name | Trigger rule | Discriminator slot |
|---|------|--------------|---------------------|
| **T1** | **entity_creation_polarity_mismatch** | Both claims share the same `subject`. Both predicates come from the `{creates, produces_record_in, generates, emits, writes}` family. Polarities differ (one positive, one negative). The negative claim's `object` is a superset of, or names an entity that includes, the positive claim's `object`. | `object` (the entity whose existence the two claims disagree on) |
| **T2** | **quantitative_bound_mismatch** | Both claims bound the same metric on the same `subject`. The bounds (counts, ranges, thresholds) are disjoint sets. | the metric / bound |
| **T3** | **lifecycle_ordering_inversion** | Both claims assert a temporal/causal ordering between the same two events using `runs_before` / `runs_after` predicates. The asserted order is opposite. | the (event_a, event_b) ordered pair |
| **T4** | **behavioral_exclusivity_violation** | One claim has `predicate=forbids` or carries an "ONLY through X" / "MUST NOT" exclusivity. A second claim asserts an action that fits the forbidden shape on a NEW external entry point, not on the internals of the asserted entry point. | the action surface |
| **T5** | **wire_contract_shape_mismatch** | Both claims describe the same observable surface (return value, error envelope, exit code, log line, JSON shape, header set, status code mapping). The asserted shapes differ. | the surface name |

### Refinement filter (mandatory before any flag)

Before flagging a pair as contradiction, ask: **"Is one clause a strict
subset / specialization / enumeration-of-cases of the other?"**

Discriminating signal: **the more specific clause's claim is true under
the more general clause's claim.** If yes, refinement, not
contradiction. Drop the pair without flagging.

Refinement examples (must NOT trigger):

| General | Specific | Why it is a refinement |
|---------|----------|-------------------------|
| `"function is idempotent"` | `"first call creates row, second call returns existing row"` | Specific behavior is one valid implementation of the general property. |
| `"ENABLE ROW LEVEL SECURITY on every tenant table"` | `"FORCE ROW LEVEL SECURITY on every tenant table"` | Specific is a strictly stronger restatement; both must hold. |
| `"all queries go through the ORM"` | `"withTenantDb internally issues SET LOCAL via the ORM"` | Specific describes the asserted entry point's internals, not a new external entry point. (See T4 anti-pattern below.) |

**T4 anti-pattern (must NOT fire):** "ONLY through X" + a clause
describing X's internal mechanism does NOT violate exclusivity. T4
fires only when the second clause describes a NEW external entry
point that bypasses X.

### Worked example — FR-3 baseline migration case

Prose at `FR-3.processing@line62`:

> "The baseline migration itself creates no tables and applies no policies."

Prose at `FR-3.side_effects@line70`:

> "The `__drizzle_migrations` tracking table is created in the database when the baseline migration is applied."

AC-24:

> "GIVEN baseline applies WHEN db:migrate runs THEN `__drizzle_migrations` records the baseline's hash."

Reduced claims:

1. `(baseline migration, creates, negative, tables, FR-3.processing@line62)`
2. `(baseline migration, applies, negative, policies, FR-3.processing@line62)`
3. `(baseline migration, creates, positive, __drizzle_migrations table, FR-3.side_effects@line70)`
4. `(baseline migration, produces_record_in, positive, __drizzle_migrations, AC-24)`

Pairwise walk:

- (1, 3): same subject, predicate family `{creates}`, polarities differ
  (negative vs positive), object overlap (claim 1's "tables" is the
  superset; claim 3's `__drizzle_migrations table` is a member of that
  set). **Refinement filter**: claim 3 is NOT a refinement of claim 1
  — claim 3 asserts presence; claim 1 asserts absence. Specific
  contradicts general. **T1 fires.**
- (1, 4): subject same, predicates differ (`creates` vs
  `produces_record_in`); but `produces_record_in` requires the table
  to exist as a side effect of the same subject. Closure of the
  predicate family forces (1) to also deny (4)'s precondition.
  **T1 fires** (same discriminator: `__drizzle_migrations` table
  existence).

The pair (1, 3) is the canonical T1 fixture. The
discriminator slot is the `object` — the `__drizzle_migrations` table.
<!-- END-CONTRADICTION-TYPOLOGY -->
