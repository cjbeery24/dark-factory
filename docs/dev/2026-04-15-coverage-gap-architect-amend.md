# Plan: Route coverage gaps back to the right fixer

**Created:** 2026-04-15
**Revised:** 2026-04-15 (post-Neo review)
**Trigger:** `COVERAGE_FLOOR_VIOLATION` on AC-9 in `2026-03-28-database-foundation`
(run `e8949105d910413c`) — test-engineer could not cover AC-9 because the
architect manifest did not expose `apps/api/src/db/health-check.ts` in
`test_integration.entry_points`. The coverage regen loop re-invoked the
test-engineer three times, but only the architect can add the missing entry
point. The two agents share a contract; the loop only routed back to one of
them.

## Problem statement

When the test-engineer cannot cover an anchor, the cause falls into one of
four buckets:

1. **TE-fixable:** they missed a scenario or wrote a weak one. Re-invoking
   TE closes the gap. *Current behaviour handles this.*
2. **Architect-fixable:** the manifest's `test_integration.entry_points`
   doesn't expose the module the anchor tests. TE is structurally forbidden
   from inventing imports (test-engineer.md Hard Rule #8), so re-invoking
   TE is futile. *Current behaviour fails here — this plan fixes it.*
3. **Spec/vehicle-fixable:** the anchor requires a test vehicle unit-TE
   cannot provide (integration harness, real external service, subprocess
   for exit-code assertions). No amount of architect manifest tweaking
   helps; the spec needs a `vehicle: subprocess` annotation or the AC needs
   to be split/rescoped. *Current behaviour misreports this as TE-fixable
   and burns retries; this plan surfaces it distinctly so a human can fix
   the spec.*
4. **Runtime-untestable:** genuinely requires a capability neither agent
   can provide (interactive TTY, GPU). TE is expected to emit
   `@untestable` after retry-1. *Current behaviour handles this.*

The real COVERAGE-GAPS.md from the failing run contains all four buckets
(see `docs/pipeline-run-reports/2026-04-15-23-30-...md`):

- FR-4, AC-9, EC-2, EC-8 → **architect-fixable** ("No health check entry
  point declared — the architect manifest's test_integration section does
  not expose...")
- AC-2, AC-3, AC-4, EC-1, EC-9 → **spec/vehicle-fixable** ("Subprocess
  execution required")
- AC-5, AC-6, AC-7, AC-8, AC-10, AC-12, AC-13, EC-3, EC-4, EC-5, EC-6,
  EC-10 → **spec/vehicle-fixable** ("Requires Fastify server setup",
  "No concrete RLS-enabled table available")

The TE's own prompt (test-engineer.md:228) already tells it "the correct
fix is for the architect to expose the entry point" for the architect-
fixable case. The signal exists in the TE's head; the orchestrator just
doesn't receive it in a machine-readable form.

## Design decisions

### D1: How the TE signals bucket membership

**Structured marker, not regex on prose.** The TE already knows — it's
trained on the distinction in test-engineer.md:228. Require the TE to
emit a `- **Category:**` field on every gap, one of:

- `architect-entry-point` — a module the anchor needs isn't in
  `test_integration.entry_points`
- `spec-vehicle-mismatch` — the anchor requires a test vehicle unit-TE
  can't provide (Fastify harness, real DB, subprocess, concrete RLS
  tables that don't exist in this spec's migration)
- `runtime-untestable` — genuine capability gap (interactive TTY, GPU);
  TE should have tagged the scenario `@untestable` instead, but if it
  landed here, at least we know not to re-invoke

Gap format becomes:

```markdown
## AC-9: Database health check error response
- **Category:** architect-entry-point
- **Reason:** No health check entry point declared...
- **Needs:** apps/api/src/db/health-check.ts  (architect-entry-point only)
```

The `Needs` line is specific to architect-entry-point: the module path
the TE wants exposed. This removes the guesswork from the architect's
amend prompt — it's told exactly which modules to add.

Orchestrator does a trivial string match on the `Category` line. **No
regex on free text. No default-to-architect-fixable fallback.** If the
`Category` field is missing (TE drift or old output), emit a
`COVERAGE_GAP_CATEGORY_MISSING` warning event and treat as unknown —
don't guess.

**Required changes to test-engineer.md:**
- Update Hard Rule #9 (coverage gap reporting) to specify the three-field
  format (`Category`, `Reason`, optional `Needs`).
- Add a short table mapping failure descriptions to categories, so the TE
  has no ambiguity about bucket selection.

### D2: Ground classification on the floor report, not the md file

The floor report (`CoverageReport.uncovered_anchors`) lists anchor IDs
that actually block the floor. The md file is the TE's own record and
may include anchors the TE waived, duplicated, or mis-labeled.

Classification joins on anchor ID: for each anchor in
`report.uncovered_anchors`, find the matching `## (AC|FR|EC)-N:` heading
in the md file, read its `Category`. Only architect-entry-point gaps
that appear in both sets trigger an amend.

Parser must handle all three prefixes (`AC-`, `FR-`, `EC-`) — the real
md file has all three (see linked run report).

### D3: Architect amend — preserve-and-extend contract

The architect is invoked once with:

1. The existing manifest YAML (full text)
2. The anchor IDs, their anchor text from the spec, and the `Needs`
   module paths from the TE's COVERAGE-GAPS.md
3. Explicit instruction: "preserve every existing top-level section
   byte-identical; preserve every existing `entry_points[*]` entry
   byte-identical; add entries only to `test_integration.entry_points`
   for the modules listed below; the `path` of each new entry must
   resolve against an existing `file_map` key."

**Post-amend validation** (assert before proceeding):
- Every pre-existing `entry_points[*]` entry is preserved (entries
  compared as parsed dicts; set-equality on the pre-amend set ⊆
  post-amend set).
- Every other top-level section (`file_map`, `commands`, `runtime`,
  `dependencies`, `vitest_config`, and anything else present before)
  is preserved as a parsed dict (YAML key-order drift tolerated).
- Every new entry's `path` appears as a key in `file_map`.
- Every new entry's `specifier` is not already present in the
  pre-amend `entry_points[*].specifier` set (collision rejection —
  the architect may not silently rewrite which path an existing
  specifier points to, even if it's also adding the new requested
  module).
- `test_integration.entry_points` count did not shrink.

Outcome classification:
- **`success`** — all validations pass, count grew, at least one new
  `specifier` resolves the architect-fixable gaps in the amend request.
- **`no_progress`** — all validations pass, count unchanged OR
  count grew but none of the new specifiers correspond to modules
  in the amend request's `Needs:` list. The manifest is accepted
  (no disk rollback), but no gap closes from this amend; the permit-
  one-more logic will not retry on the same `(anchor, module)` pair.
- **`validation_failed`** — any validation bullet above fails.
  Manifest is rejected, in-memory restore, do NOT write to disk.

Emit `COVERAGE_ARCHITECT_AMEND_COMPLETED` with `outcome: success` or
`outcome: no_progress`; emit `COVERAGE_ARCHITECT_AMEND_FAILED` with
the specific failure reason for `validation_failed`. In all three
outcomes, proceed to the TE retry loop — graceful degradation.

### D4: Loop structure — permit-one-more, hard cap two

Per Neo's review: if the first amend exposes module A but a later TE
retry discovers anchor B needs module C (same bucket, different
module), a single-shot architect round shifts the failure by one but
doesn't fix it.

Structure:

```python
max_amend_calls = 2
seen_architect_gaps: set[tuple[anchor, module]] = set()
architect_amends_done = 0

# Initial classification
report = compute_coverage(...)
gaps_md = parse_coverage_gaps_md(...)
arch_gaps = classify_architect_fixable(report, gaps_md)

for te_attempt in range(1, max_regen_attempts + 1):
    # Architect amend, gated on new architect-fixable gaps
    new_arch = [g for g in arch_gaps if g not in seen_architect_gaps]
    if new_arch and architect_amends_done < max_amend_calls:
        emit(COVERAGE_ARCHITECT_AMEND, ...)
        ok = invoke_architect_amend(new_arch)
        seen_architect_gaps |= set(new_arch)
        architect_amends_done += 1
        emit(COVERAGE_ARCHITECT_AMEND_COMPLETED, outcome=...)
        # No coverage recompute — architect didn't change scenarios

    # TE retry
    invoke_te_retry(te_attempt, gaps=uncovered)
    report = compute_coverage(...)
    gaps_md = parse_coverage_gaps_md(...)
    arch_gaps = classify_architect_fixable(report, gaps_md)

    if not report.uncovered_anchors: return  # clean

# Exhausted retries
emit(COVERAGE_FLOOR_VIOLATION, ...)
raise DarkFactoryError(...)
```

Worst case: 1 initial TE + 2 architect amends + 2 TE retries = 5
agent calls for a coverage failure. Current code caps at 1 initial TE
+ 2 TE retries = 3 calls. Added cost: 2 architect calls ≈ 240s. Trade
vs. current behaviour (pipeline dies, human loops back): acceptable.

### D5: Spec-vehicle-mismatch gets its own signal, not the architect

For gaps categorized `spec-vehicle-mismatch`, the orchestrator does NOT
invoke the architect. It emits `COVERAGE_SPEC_VEHICLE_MISMATCH` with
the anchor ID and TE's reason, and includes these in the floor
violation message so the run report shows them prominently. The spec
author (human, via `/df-write-spec`) adds `vehicle: subprocess` or
splits the AC.

This is a signal, not an action: the current pipeline has no way to
auto-amend the spec, and spec amends belong to the human-in-the-loop
per spec_type semantics. The point of this event is so the next
session can see, in one glance, "these gaps are spec bugs, not
framework bugs."

### D6: Config flag placement and default

`PipelineConfig.coverage_architect_amend: bool = True`, sibling of
`coverage_max_regen_attempts` (confirmed at
`darkfactory/models/config.py:85`). Default true (this is the new
correct behaviour); flag exists for debugging and rollback-by-config.

### D7: Barrier hygiene for the architect amend prompt

The architect container masks `.dark-factory/scenarios/` and
`.dark-factory/audit/` (CLAUDE.md table). The amend prompt is
constructed in the orchestrator's process (unmasked), so it CAN pull
scenario filenames — we must not.

Amend prompt contains ONLY:
- Anchor IDs (e.g. `AC-9`, `FR-4`) — already in the architect's spec view
- Anchor text lifted from the spec (architect has read access)
- TE's `Needs:` module path (a file path inside the project, which the
  architect already sees in `file_map`)
- TE's `Reason:` text (this is author-controlled free text; risk is
  bounded — still, strip any substring matching
  `\.dark-factory/scenarios/` before inclusion, as defense-in-depth)
- Existing manifest YAML (architect already wrote it)

No scenario filenames, no TE output file paths, no holdout references.
Document this constraint in the function docstring so future edits
don't accidentally leak.

### D8: New events

- `COVERAGE_ARCHITECT_AMEND` — payload `{gaps: [{anchor, module}],
  amend_number: 1..2}`
- `COVERAGE_ARCHITECT_AMEND_COMPLETED` — payload `{outcome:
  "success"|"no_progress"|"validation_failed", new_entry_points:
  [str], validation_failure_reason: str?}`
- `COVERAGE_ARCHITECT_AMEND_FAILED` — payload `{reason: str}` (emitted
  instead of COMPLETED when the post-amend validation rejects)
- `COVERAGE_SPEC_VEHICLE_MISMATCH` — payload `{anchors: [{id, reason}]}`
  (emitted at most once per enforcement call)
- `COVERAGE_GAP_CATEGORY_MISSING` — payload `{anchors: [id]}` (emitted
  when the TE's md doesn't include the `Category:` field — signals
  TE drift or prompt mismatch)

## Implementation plan

### File: `darkfactory/scenarios/coverage.py`

Add pure functions (no I/O, no agent calls):

```python
_GAP_HEADING_RE = re.compile(r'^##\s+(AC|FR|EC)-(\d+)(?::|\s|$)', re.M)

@dataclass
class CoverageGap:
    anchor_id: str       # "AC-9", "FR-4", "EC-2"
    category: str | None  # None = missing from md (category-missing)
    reason: str
    needs: str | None    # module path for architect-entry-point; else None

def parse_coverage_gaps_markdown(
    gaps_md_path: Path,
) -> list[CoverageGap]:
    """Parse COVERAGE-GAPS.md into a list of CoverageGap.

    Handles AC-N, FR-N, EC-N headings. Reads `- **Category:**`,
    `- **Reason:**`, and `- **Needs:**` bullet lines (tolerant to
    `- Category:` without bold). Missing Category → gap.category = None.
    """

def classify_gaps(
    gaps: list[CoverageGap],
    uncovered_anchors: Iterable[str],
) -> dict[str, list[CoverageGap]]:
    """Partition gaps into buckets, restricted to anchors that block the floor.

    Returns:
        {
            "architect-entry-point": [...],
            "spec-vehicle-mismatch": [...],
            "runtime-untestable":    [...],
            "category-missing":      [...],  # drift / pre-revision TE
        }

    Gaps whose anchor_id is not in uncovered_anchors are dropped —
    classification is scoped to the set of gaps the floor report
    actually cares about.
    """
```

### File: `darkfactory/pipeline/orchestrator.py`

Add:

```python
def _architect_amend_entry_points(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    gaps: list[CoverageGap],  # architect-entry-point bucket
    spec_file: Path,
    manifest_path: Path,
    amend_number: int,
    events_file: Path | None,
    audit_path: Path | None,
    dry_run: bool,
) -> tuple[bool, str, list[str]]:
    """Invoke architect to amend test_integration.entry_points.

    Preserves the rest of the manifest. Returns:
        (success, failure_reason_or_empty, new_entry_point_specifiers)

    Post-invocation validation:
    - pre-existing entry_points preserved byte-identical
    - other top-level sections preserved byte-identical
    - new entries' path resolves against file_map
    - entry_points count grew

    On any validation failure, restores pre-amend manifest from
    memory (does NOT leave a broken manifest on disk) and returns
    (False, reason, []).
    """
```

Modify `_enforce_coverage_floor` (orchestrator.py:2499-2801):

- After initial `compute_coverage()` at line 2611, before entering the
  retry loop at line 2641, parse COVERAGE-GAPS.md and classify.
- Move the TE retry loop into a structure that interleaves the amend
  call per D4. Track `seen_architect_gaps` and `architect_amends_done`.
- Emit `COVERAGE_SPEC_VEHICLE_MISMATCH` the first time we see any gap
  in that bucket (at most once per enforcement invocation).
- Emit `COVERAGE_GAP_CATEGORY_MISSING` if any gap has `category=None`
  (TE drift warning).
- Preserve existing floor-type check and `_apply_waivers` logic.

Guarded on `config.coverage_architect_amend`. When disabled, behaviour
is identical to today (no amend calls, no new events).

### File: `darkfactory/models/config.py`

Add on `PipelineConfig` (line 42, alongside `coverage_max_regen_attempts`):

```python
coverage_architect_amend: bool = True
coverage_architect_amend_max_calls: int = 2
```

### File: `darkfactory/events/types.py`

Add five event types + emit helpers (D8). Follow the existing pattern
from `emit_coverage_regen` for invocation signature.

### File: `agents/test-engineer.md`

Update Hard Rule #9 (line 186) to require the three-field COVERAGE-GAPS
format. Add a classification table:

| Failure description | Category |
|---|---|
| "module not in entry_points", "not exposed", "no … entry point declared" | `architect-entry-point` |
| "requires Fastify", "requires real database", "requires subprocess", "requires concrete RLS table" | `spec-vehicle-mismatch` |
| "requires TTY", "requires GPU", "requires human input" | `runtime-untestable` |

Example gap block:

```markdown
## AC-9: Database health check error response
- **Category:** architect-entry-point
- **Reason:** Health check registration module not exposed in test_integration.entry_points
- **Needs:** apps/api/src/db/health-check.ts
```

### File: `agents/architect.md` (amend-mode addition)

Add a short section: "Amend mode — invoked when an existing manifest
exists and the coverage floor reports architect-fixable gaps. Rules:
preserve every top-level section byte-identical except
`test_integration.entry_points`; add only the modules listed in the
delta request; new entries' `path` must resolve against a `file_map`
key; do not reformat, reorder, or rewrite existing entries."

This is defensive — the orchestrator's amend prompt is already
explicit — but aligns the agent's long-form guidance with the
constrained call.

## Test plan

### Unit tests — new `tests/unit/test_coverage_gap_classification.py`

1. `parse_coverage_gaps_markdown` handles:
   - Mix of `## AC-N:`, `## FR-N:`, `## EC-N:` in one file
   - `- **Category:** X` and `- Category: X` (tolerant formatting)
   - Missing `Category:` line → `gap.category is None`
   - Missing `Needs:` line → `gap.needs is None`
   - Multi-paragraph reason (joins until next `-` or `##`)

2. `classify_gaps`:
   - Restricts to `uncovered_anchors` (drops waived / duplicate-tagged
     anchors not in the floor report)
   - Puts each category into the right bucket
   - `category=None` → `category-missing` bucket (no guessing)
   - Empty gaps → empty buckets, no errors

### Unit tests — amend validation

3. Amend-validation helper:
   - Preserves existing entry_points → validates OK
   - Modifies an existing entry_point → rejects
   - Adds a new entry with `path` not in file_map → rejects
   - Reorders top-level sections (YAML key order drift) → validates OK
     (compare by dict, not by raw bytes)
   - Removes an existing top-level section → rejects

### Integration test — new `tests/integration/test_orchestrator_coverage_amend.py`

Scenario: simulated architect-fixable gap.

Setup fake `project_dir` with:
- Anchor manifest declaring `AC-9, FR-4` uncovered
- Manifest YAML missing `apps/api/src/db/health-check.ts` in entry_points
- COVERAGE-GAPS.md with three entries: one architect-entry-point
  (AC-9), one spec-vehicle-mismatch (AC-5), one category-missing (EC-1)

Mock `invoke_agent`:
- For ARCHITECT: write a manifest that adds the missing entry point
  (preserving everything else)
- For TEST_ENGINEER: write a scenario covering AC-9 and FR-4

Run `_enforce_coverage_floor`. Assert:
- `COVERAGE_ARCHITECT_AMEND` emitted with 1 gap (AC-9; FR-4 duped
  Needs-module collapses)
- `COVERAGE_SPEC_VEHICLE_MISMATCH` emitted once for AC-5
- `COVERAGE_GAP_CATEGORY_MISSING` emitted once for EC-1
- Architect invoked exactly once
- Final coverage is clean for AC-9/FR-4
- Final `COVERAGE_FLOOR_VIOLATION` includes AC-5 and EC-1 as unresolved

### Integration test — amend validation fails

Scenario: architect returns a manifest missing an existing entry_point.

Assert:
- `COVERAGE_ARCHITECT_AMEND_FAILED` emitted with the reason
- The on-disk manifest is unchanged from pre-amend
- The TE retry loop proceeds unchanged (graceful degradation)

### Integration test — permit-one-more

Scenario: first amend exposes module A; TE retry discovers anchor needs
module B. Second amend exposes module B; TE retry covers it.

Assert: architect invoked twice, second amend completed, floor clean.

### Integration test — hard cap

Scenario: each TE retry surfaces a new architect-entry-point gap.
Assert: architect invoked at most `coverage_architect_amend_max_calls`
times (2), then floor violation.

### Manual verification (regression anchor — not an AC)

Re-run the failing pipeline:
```
/df-pipeline-run 2026-03-28-database-foundation ../budget-brain-v2
```
Expected: `COVERAGE_ARCHITECT_AMEND` fires, architect adds
`health-check.ts`, TE retry covers AC-9/FR-4/EC-2/EC-8,
`COVERAGE_SPEC_VEHICLE_MISMATCH` fires for AC-5/6/7/8/10/12/13 and
EC-3/4/5/6/9/10 (pipeline still halts on spec-fixable gaps — this is
the correct behaviour and surfaces spec issues clearly for the human).

## Risks / failure modes

| Risk | Mitigation |
|---|---|
| TE prompt change fails to roll out (old TE still emits free-form) | `category-missing` bucket + `COVERAGE_GAP_CATEGORY_MISSING` event make this visible. Loop falls through to TE retry — behaviour is strictly no-worse than today. |
| Architect breaks the manifest on amend | Post-amend validation rejects; in-memory restore; never write broken state to disk. |
| Amend leaks scenario filenames via TE reason text | Defense-in-depth: strip `.dark-factory/scenarios/` substrings from reason text before building amend prompt. |
| Agent call budget explosion | Hard cap 2 amend calls. Worst case adds ~240s to a failing run. |
| Classifier routes a spec-vehicle-mismatch to architect | Classifier only routes `architect-entry-point` — wrong category is either spec-vehicle-mismatch (emits its own event) or category-missing (emits its own event). No default to architect. |
| YAML round-trip drift on architect rewrite | Compare by parsed dict, not raw bytes, for top-level section preservation. Key order drift is tolerated. |

## Non-goals

- **Spec-amend automation.** `spec-vehicle-mismatch` surfaces the problem
  but does not fix it; the human fixes the spec via `/df-write-spec`.
- **Fix `dark-factory init --update` adapter regression.** Separate
  issue; tracked in run reports.
- **Substrate verifier exercise.** Separate phase; unblocked once this
  lands because the pipeline will reach implementer.
- **New agent role.** Architect exists; amend mode is a constrained call.
- **Structured COVERAGE-GAPS.md (YAML/JSON).** A future plan can
  convert md → structured format; this plan works with the existing
  markdown + added fields.

## Rollout / commit

Single commit. Commit message:

```
feat(coverage): route architect-fixable gaps back to the architect

The test-engineer now categorises each coverage gap as
architect-entry-point, spec-vehicle-mismatch, runtime-untestable, or
unknown. The orchestrator invokes the architect (up to twice) to amend
test_integration.entry_points when gaps are architect-fixable, preserves
the rest of the manifest via byte-identical validation, and surfaces
spec-vehicle-mismatch gaps via a distinct event so humans can fix the
spec. Behaviour is unchanged when coverage_architect_amend=false.
```

## Acceptance criteria

AC-A. Unit tests for gap parsing and classification pass. All four
buckets are assignable; `category-missing` is distinct from the other
three.

AC-B. Unit tests for manifest amend validation pass. A manifest that
modifies an existing entry_point, removes a top-level section, adds
a path not in `file_map`, or adds an entry whose `specifier` collides
with an existing `specifier` (rewriting which path it resolves to) is
rejected. A manifest that grows `entry_points` but where none of the
new specifiers match the amend request's `Needs:` list is classified
`no_progress` (accepted but signalled as non-productive).

AC-C. Integration test for the happy-path amend flow passes: one
architect call, architect-fixable gaps closed, spec-vehicle-mismatch
emitted for unfixable gaps, floor violation message references them.

AC-D. Integration test for amend-validation-failure passes: broken
architect output is rejected, on-disk manifest is unchanged,
`COVERAGE_ARCHITECT_AMEND_FAILED` is emitted, TE retry loop runs.

AC-E. Integration test for permit-one-more: two architect amends in
sequence both succeed, floor clean.

AC-F. Integration test for hard cap: architect invoked no more than
`coverage_architect_amend_max_calls` times.

AC-G. Existing coverage tests
(`tests/unit/test_coverage.py`, `tests/integration/test_orchestrator_coverage.py`)
still pass. Behaviour with `coverage_architect_amend=False` is
byte-identical to pre-change.

AC-H. When the TE's output lacks the `Category:` field,
`COVERAGE_GAP_CATEGORY_MISSING` fires and the gap is NOT routed to the
architect (no guessing).
