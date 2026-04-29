# Phase 5 run fixtures (NEGATIVE — tooling MUST reject this primitive name)

These two files are **real production data** captured from the
2026-04-29 07:57 pipeline run against `budget-brain-v2` for the
`2026-03-28-database-foundation` spec (run_id `92062a08`). The run
shipped SHIP_WITH_GAPS at iter-2 but exposed the load-bearing bug
Phase 6 of the higher-priority queue arc fixes:

- `defects-iter1.json` — classifier output. ALL upstream-tagged
  defects emitted `upstream_cause.primitive == "bootstrap_check"`,
  the substrate-PRIMITIVE name from the spec's §4a
  `required_lifecycle_sequences` block — NOT the substrate-findings
  `code` field. The lookup at `_load_canonical_upstream_messages`
  keys on the finding `code`; the lookup quietly missed; Phase 4's
  mechanical canonical-sourcing path added zero value over the
  pre-existing `c68b3f8` `_format_substrate_findings_inline`
  block.

- `substrate-findings.json` — substrate-phase verifier output from
  the same iteration. Two findings whose `code` fields are
  `substrate-bootstrap-not-applied` and `substrate-guc-unregistered`.
  These are the values the classifier was supposed to emit as
  `primitive`, but did not.

## What the validator MUST do with these fixtures

`_validate_upstream_primitives` (Phase 6):

- Given these defects + this substrate-findings.json, MUST return
  `("upstream_primitive_unknown", <retry_feedback>)`.
- The retry_feedback string MUST name the offending value(s)
  (`'bootstrap_check'`) and the canonical valid set
  (`['substrate-bootstrap-not-applied', 'substrate-guc-unregistered']`).
- `_load_canonical_upstream_messages` keyed on
  `("substrate", "bootstrap_check")` MUST MISS (i.e. the dict
  the helper returns does not contain that key).

## Why these are committed as-is

Per Neo design pass (2026-04-29 Phase 6), the negative-fixture-only
test would be tautological — author the validator to reject this
exact shape, then assert it does. Coverage gap. The companion test
suite extends with:
1. A POSITIVE fixture (synthetic — built in-test) where `primitive`
   matches a real `code`, asserting the validator does NOT reject.
2. A paraphrase-family fuzz over near-miss strings
   (`"bootstrap"`, `"substrate_bootstrap"`, `"bootstrap-check"`,
   `"BootstrapCheck"`) — every non-exact match is rejected.
3. A regex assertion on `_CASCADE_DIRECTIVE_TEMPLATE` ensuring no
   single-token primitive example string lives inside the JSON
   shape — catches the next would-be `'pre_check'` example before
   it produces the same drift.

## Why no `classifier-prompt.txt`

Neo's Finding 5 recommended saving the classifier prompt for
contract-regression replay. The Phase 5 archive at
`~/sites/budget-brain-v2/.dark-factory/archive/20260429-064119-2026-03-28-database-foundation-92062a08/iter-1/`
does NOT preserve the classifier prompt as a separate artifact;
the prompt is built dynamically by `_step_classify` and only
appears in the events stream. Future framework work to persist
the rendered prompt under `archive/.../iter-N/prompts/` would
fill this gap; it is out of scope for Phase 6.

## Do NOT edit these files

They pin a specific historical failure mode. If you need to test
a new shape, add a new fixture directory (`phase_N_run/`); leave
this one frozen.
