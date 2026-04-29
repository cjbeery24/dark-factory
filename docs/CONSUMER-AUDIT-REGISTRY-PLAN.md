# Consumer-Audit Registry — Implementation Plan

**Status**: DEFERRED. Plan attempt 2026-04-28 superseded by Neo review.
A full rewrite is required before this work starts; the original draft
shipped the wrong shape and at the wrong time.

## Context

The consumer-audit registry was identified in the 2026-04-28 sessions
as the systemic fix for the persisted-but-not-consumed anti-pattern.
See `~/.claude/projects/-Users-me-sites-dark-factory/memory/project_persisted_but_not_consumed_pattern.md`
for the seven documented instances of the pattern across 2026-04-20
→ 2026-04-28.

The high-level concept: a documented map of every persisted artifact
in the pipeline to its expected consumers, enforced by tests that
assert each consumer's code touches each artifact it should read.

## Why the 2026-04-28 plan attempt was rejected

Neo review (2026-04-28) flagged three blockers that kill the plan
as drafted:

### B1 — Chain failures invisible

The original plan registered "only direct readers." Gap 2's actual
failure shape was `producer → renderer → prompt-construction site`:
the renderer existed and worked; the prompt site never called it.
A registry that records only direct readers would have shown all
green and missed Gap 2 entirely.

**Required for rewrite**: every renderer/formatter must be modeled
as both a consumer of its inputs AND a producer of an in-memory
string artifact. Its consumers are the prompt-construction sites
that inline its output. Either model chains explicitly, or re-scope
the goal to acknowledge ~60% coverage.

### B2 — Opportunity cost (ship concrete wins first)

The registry is hypothetical-future-prevention. Three concrete
wins sit ahead of it in the queue:

1. Gap 3 follow-up commits (untracked-file capture +
   iteration_end_sha at gate time)
2. Two open deferred bugs (spec_validator wire-language FP,
   holdouts.md not consumed by iteration loop) — see
   `project_database_foundation_deferred_bugs.md`
3. Database-foundation discriminating run with prescriptive
   baseline guidance (or bootstrap_check rationale inlined into
   the implementer prompt) — disambiguates spec-vs-implementer
   in the non-shipping cause from the 2026-04-28 13:50 run

Spending 25-40 sessions (Neo's revised estimate, see B4) on a
registry while the spec that motivated all this still hasn't
shipped is the same anti-pattern: persisting a framework artifact
(the registry) without a consumer (a successfully-shipped spec
proving the framework works).

**Required for rewrite**: the plan starts AFTER the three items
above ship. By then there may be a 6th anti-pattern instance to
seed from, AND the framework has a shipping demonstration to point
at.

### B3 — P0 ordering inverted

The original plan delayed registry authoring until
`iteration_end_sha`'s contract was fixed, on the grounds that the
registry shouldn't document a known-broken contract. Neo: this
throws away the registry's first real demonstration of value
(forcing the contract renegotiation into the open). And it reveals
a general bug: if every entry must wait for its referent to be in
a "shippable" state, the registry never gets authored — there's
always something known-broken.

**Required for rewrite**: registering the broken contract IS the
value. P0.1 happens DURING P1.2, not before.

## Should-fix items also flagged

### B4 — Effort estimate 3x-4x low

Original estimate: 4-6 sessions for Phases 0-3. Neo's recalculation:
50-75 entries × (15-30 min producer grep+verify + 30-60 min
consumer mapping + 30-60 min hand-authored contract+tolerance+pin)
= 1.5-2.5 hours per entry minimum at quality. **Realistic floor:
25 sessions; ceiling: 60.** At that price point, B2's opportunity-
cost analysis is no longer debatable.

### B5 — Prose tolerance fields will rot

Original plan made `tolerance` a free-form prose field, with code
review as the discipline keeping it specific. Neo: code review of
registry-only changes will rubber-stamp; prose fields will rot
within 6 months.

**Required for rewrite**: replace prose-tolerance with structured
enums that Level 3 tests can mechanically check —
`under_report_safe: bool`, `missing_artifact_behavior:
enum{empty|fatal|fallback}`, `error_field_behavior: enum{...}`,
etc. Force consumer behavior into a small finite vocabulary.
Prose becomes explanation, not contract.

### B6 — Auto-seed misses the highest-value producers

Original plan auto-seeded from grep over `darkfactory/` source.
Neo: the highest-stakes producers are agent-prompt subprocess
writes (substrate-findings, holdouts, defects all originate from
agent containers). Source-grep misses them entirely.

**Required for rewrite**: auto-seed walks `agents/*.md` for
`/project/.dark-factory/...` write directives in addition to
source-grep.

## What ships next (the higher-priority queue)

In order:

1. **Gap 3 follow-up commit** — capture untracked files in
   `capture_iteration_diff` (per Neo's surgical scope from the
   live-pipeline review), AND capture `iteration_end_sha` at gate
   time so escape-hatch paths still produce patches. ~1-2 sessions.

2. **Database-foundation discriminating re-run** — with either a
   prescriptive baseline spec OR the bootstrap_check rationale
   inlined into the implementer prompt verbatim. Disambiguates the
   spec-ambiguity vs implementer-comprehension cause of the 13:50
   run's non-ship. ~1 session including report.

3. **Deferred bug #1 (spec_validator wire-language FP)** — adds
   prose-vs-prescription detection to the wire_language_rule_1
   regex, OR moves the rule to a mechanical validator. The
   spec-validator's non-determinism (95/87/92/75/98 across runs
   on the same spec) is also worth addressing in the same arc.
   ~1-2 sessions.

4. **Deferred bug #2 (holdouts.md not consumed)** — thread
   `holdouts.md` content into Oracle's prompt as a separate inline
   block, or surface in reviewer prompt only. Decide which based
   on whether the artifact's adversarial-coverage info changes
   ship/no-ship calculus or only review depth. ~1 session.

After those four ship, revisit this plan. By then we'll have:
- Concrete proof the framework can ship a spec (item 2)
- One more anti-pattern instance possibly surfaced (items 3-4)
- The Gap 3 contract renegotiation done (item 1) — the registry's
  first entry can document the OUTCOME, not just the negotiation

## Open questions for the rewrite

- Q1 (chain modeling): how do we represent producer→renderer→prompt
  chains without ballooning the registry into unreadability? B1's
  fix introduces this complexity; need a clean shape.
- Q2 (structured tolerance enums): what's the minimal vocabulary
  that covers the existing consumer behaviors without flattening
  important distinctions? Enumerate from the 7 documented
  instances first; expand only when forced.
- Q3 (R4/Phase 6 ordering): drift detector should ship in Phase
  1.5, not Phase 6. The registry is a museum from day 1 if drift
  detection is at the end of the arc.
- Q4 (acceptance criterion #2): "every write enumerated" is
  unfalsifiable without the drift detector. Either tie acceptance
  to detector existence, or pick a smaller falsifiable criterion.

## When this plan should be rewritten

After items 1-4 above have all landed. Until then, this stub stands.
Anyone tempted to ship a registry MVP in the meantime: re-read B2
and B5 first.
