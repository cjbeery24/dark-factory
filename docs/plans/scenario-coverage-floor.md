# Plan: Scenario Coverage Floor — Block Untestable Anchors

## Problem

In 1 of 4 recent pipeline runs, scenario-architect generated 20 narrow scenarios (contracts + happy path) instead of the 33 the spec implied. The implementer easily passed those, the spec shipped at 85%, and the gaps weren't caught — because the scenarios that would have failed simply didn't exist.

The "≥90% blind holdout pass rate" quality signal is **unfalsifiable when the architect chooses its own denominator**. "85% of 20" and "85% of 33" are treated identically by the gate. Ship-with-gaps becomes ship-with-blind-spots.

## Goal

Guarantee every FR / AC / EC declared in the spec maps to ≥1 generated scenario before any implementer run. Partial coverage (covered but weakly) remains a soft signal for the Oracle. Zero coverage on any anchor is a hard floor.

## Convergence note (Liotta + Neo)

Neo originally argued for a hard gate at scenario-gen time. Liotta argued for measurement-only, feeding coverage into the Oracle. Both converged on:

1. The hard floor is **only** zero-coverage anchors (cheap, deterministic, structural — not a heuristic).
2. The gate fires at **scenario-gen time, not at Oracle**, so we don't burn an Implementer run on a spec the holdout couldn't falsify.
3. Bounded regeneration of **only the uncovered anchors** before escalation — keeps the retry targeted, doesn't churn already-covered tests.
4. Partial coverage (anchor covered with fewer scenarios than declared intent) feeds Oracle as a structured prior, **not** as a gate.

"Uncovered" is strictly defined: zero scenarios mapping to the anchor ID. Weak coverage is the Oracle's job, not the floor's.

## Design (5 steps)

### Step 1 — Anchor manifest at precheck time

**File:** `darkfactory/spec/precheck.py`

Extend `check_scenario_intents` (or add a sibling `extract_anchor_manifest`) to enumerate every FR-N, AC-N, EC-N declared in the spec by heading and by ID-bearing rows in the criteria tables.

```python
@dataclass
class AnchorManifest:
    fr_ids: list[str]
    ac_ids: list[str]
    ec_ids: list[str]
    declared_intents: dict[str, int]  # anchor_id -> intent count from Scenario Intent section
```

Add to `PreCheckResult`. Persist as `.dark-factory/state/{spec}/anchor_manifest.json` so the post-scenario-gen check can read it without re-parsing the spec.

**Precheck rule (Neo cross-review fix — scoped):** every **FR and AC** ID must appear at least once in the Scenario Intent section. EC anchors are NOT enforced at precheck — ECs are sometimes structural invariants declared elsewhere, and double-blocking the writer is wasteful when the post-gen gate can still catch architect-side EC misses.

> Pre-edit: `gitnexus_impact({target: "check_scenario_intents", direction: "upstream"})` and `gitnexus_impact({target: "PreCheckResult", direction: "upstream"})`.

### Step 2 — Scenario tagging convention

Scenarios must declare which anchor IDs they cover. Two accepted forms:

```javascript
/**
 * @covers FR-1, FR-3, EC-2
 */
describe("...", () => { ... })
```

```javascript
it("validates JSON envelope on 400 [FR-3, AC-7]", ...)
```

Update **`agents/scenario-architect.md`** to require this tagging in all generated scenarios. The agent already reads the spec and intent section, so it has the IDs.

**Fallback inference (Neo cross-review fix):** if a scenario's filename, `describe` block text, `it` block text, or assertion message contains a literal anchor ID (`FR-3`, `AC-7`, `EC-2`), credit it even without an explicit `@covers` tag. This catches the most common LLM omission failure mode (good scenarios, forgotten tags) without burning a regen on duplicate work.

### Step 3 — Post-generation coverage check

**File:** `darkfactory/scenarios/coverage.py` (new)

```python
def compute_coverage(
    scenarios_dir: Path,
    manifest: AnchorManifest,
) -> CoverageReport:
    """Parse @covers tags, [ID] markers, and inferred anchor IDs from
    scenario files. Return per-anchor scenario counts and uncovered set."""

@dataclass
class CoverageReport:
    per_anchor_counts: dict[str, int]
    uncovered_anchors: list[str]      # zero scenarios mapping to ID — gate trigger
    weakly_covered: list[str]         # 1 scenario but >2 intents declared — Oracle prior only
    duplicate_tags_by_anchor: dict[str, int]  # anchor_id -> count of scenarios tagged the same anchor (sanity warning)
```

Persist as `.dark-factory/state/{spec}/coverage_gaps.yaml`.

**Strict definition of "uncovered":** zero scenarios mapping to the anchor ID via tag OR fallback inference. Weak coverage is NOT a gate trigger.

**Idempotency / duplicate-anchor warning (Neo cross-review fix):** if regen-attempt-2 produces a scenario tagged with the same anchor as an existing scenario, log it in `duplicate_tags_by_anchor`. Doesn't fail the run, but signals architect drift for post-hoc investigation.

### Step 4 — Hard gate + bounded regeneration

**File:** `darkfactory/pipeline/orchestrator.py`

After scenario-architect completes, before implementer:

1. Load anchor manifest, run `compute_coverage`.
2. If `uncovered_anchors` is empty → emit `COVERAGE_PASS` event, proceed to implementer.
3. If non-empty → emit `COVERAGE_GAP` event with uncovered IDs, re-invoke scenario-architect with a targeted prompt (see retry strategy below).
4. Re-run `compute_coverage`. Repeat up to **2 regeneration attempts** (3 total scenario-architect invocations).
5. If still uncovered after retries → fail with `COVERAGE_FLOOR_VIOLATION`, list permanently uncovered anchors. Escalate.

**Retry strategy (Neo cross-review fix — escalate prompt specificity, don't repeat):**

- **Retry 1 prompt:** *"Your previous run did not produce scenarios covering anchors: [FR-3, EC-2]. Generate scenarios for ONLY these anchors. Do not modify or duplicate existing scenarios. The following files already exist and you must not overwrite them: [list]. Tag each new scenario with `@covers <anchor-id>`."*

- **Retry 2 prompt (different from retry 1):** Includes the **anchor text itself** pulled from the spec for each remaining uncovered anchor, plus an explicit escape hatch: *"If you believe an anchor is structurally untestable from the spec alone, emit a `@untestable FR-N: <reason>` marker in a stub file instead of fabricating a scenario."*

The `@untestable` marker becomes a structured escalation signal, distinguishing **"architect is being lazy"** from **"spec is broken"** — your current escalation conflates them.

**Config flag:** `coverage_floor_enabled` (default `True`). When `False`, gate is logged but not enforced. The flag value MUST be written to the run report so disabled runs are auditable.

### Step 5 — Oracle prior + telemetry

**File:** `darkfactory/pipeline/iteration.py` (oracle invocation)

Pass a structured field into the oracle prompt — **policy lives in the prompt, not in telemetry**:

```yaml
low_confidence_anchors:
  - FR-3
  - EC-2
```

Updated oracle prompt includes the weighting policy: *"Anchors in `low_confidence_anchors` had only one scenario despite multiple intents declared. A SHIP decision should require all scenarios for these anchors to pass; treat any failure on a low-confidence anchor as proportionally more severe."* (Neo: don't smuggle policy into telemetry.)

**Telemetry events** (`events.jsonl`):
- `COVERAGE_PASS` — all anchors covered, proceeding to implementer
- `COVERAGE_GAP` — anchor IDs uncovered after each scenario-architect attempt
- `COVERAGE_REGEN` — regeneration triggered, with attempt number
- `COVERAGE_FLOOR_VIOLATION` — escalation
- `COVERAGE_DUPLICATE_TAGS` — duplicate-tag warning surfaced

**Tracked metrics (v1):**
- `zero_coverage_block_rate` — % of runs where the gate fired (target: drops over time as scenario-architect learns)
- `regen_success_rate` — % of regen attempts that closed the gap

**Deferred to v2 (Neo cross-review fix):** `false_ship_rate` (shipped specs where a post-hoc-discovered defect maps to a covered anchor) requires post-hoc defect attribution infrastructure that doesn't exist yet. Cut from v1; revisit when defect registry is in place.

## File Change Summary

| File | Change |
|---|---|
| `darkfactory/spec/precheck.py` | `extract_anchor_manifest`, FR+AC enforcement in Scenario Intent (EC stays at post-gen gate), persist manifest JSON |
| `darkfactory/scenarios/coverage.py` (new) | `compute_coverage`, `CoverageReport`, tag parsers, fallback inference, duplicate-tag detector |
| `darkfactory/pipeline/orchestrator.py` | Post-gen coverage gate, bounded regen loop with escalating prompt specificity, escalation path, `coverage_floor_enabled` flag |
| `darkfactory/pipeline/iteration.py` | Pass `low_confidence_anchors` into oracle prompt; add weighting policy to oracle prompt template |
| `agents/scenario-architect.md` | Mandate `@covers` tagging; document `@untestable` marker for retry-2 |
| `tests/unit/test_precheck.py` | Anchor manifest extraction, FR+AC enforcement, EC NOT enforced |
| `tests/unit/test_coverage.py` (new) | Tag parsing (header + inline), fallback inference (filename, describe, it, assertion), uncovered detection, weak vs zero distinction, duplicate-tag warning |
| `tests/unit/test_orchestrator.py` | Bounded regen, escalation on persistent gap, gate skipped when fully covered, retry-2 prompt differs from retry-1, `coverage_floor_enabled=False` bypass |

## Ordering

1. **Step 1** — manifest extraction + precheck FR+AC floor (additive, no downstream behavior change yet).
2. **Step 2** — scenario-architect prompt update for tagging convention (agent only, no code).
3. **Step 3** — `coverage.py` module + parser + fallback inference (pure new module, fully tested in isolation).
4. **Step 4** — orchestrator gate + bounded regen loop (observable behavior change, behind `coverage_floor_enabled` flag default-True).
5. **Step 5** — oracle prior wiring + telemetry events (depends on `coverage_gaps.yaml` from Step 3 and Step 4).

## Rollout Risk

- **False positives from missing `@covers` tags.** Mitigated by fallback inference: filename/describe/it/assertion text containing a literal anchor ID counts. Worst case is a regen, not an escalation.
- **Regen loops.** Bounded at 2 retries, then escalates. No infinite loop possible.
- **Spec churn from structurally untestable anchors.** The `@untestable` marker (retry 2) gives the architect a structured way to flag this; escalation surfaces the writer mistake instead of looping forever.
- **Architect drift across regens.** Duplicate-tag detection catches it as a warning without failing the run.
- **`coverage_floor_enabled=False` runs become invisible.** Mitigated by writing the flag value into the run report.

## Verification

```bash
python3 -m pytest tests/unit/test_precheck.py tests/unit/test_coverage.py tests/unit/test_orchestrator.py -v --timeout=30
# End-to-end: run pipeline against a spec with deliberately under-covered anchor;
# verify COVERAGE_GAP event, regen fires, and either closes or escalates with COVERAGE_FLOOR_VIOLATION.
```

## Out of Scope

- Quality of generated scenarios beyond count + anchor mapping.
- Mutation testing.
- Implementer-side coverage.
- `false_ship_rate` metric (deferred to v2 — needs defect-attribution infrastructure).
- Anchors in non-feature templates.

## Review History

- Liotta + Neo cross-review converged on: zero-coverage hard floor, gate at scenario-gen time, bounded targeted regeneration.
- Neo plan review approved with changes (all incorporated): scope precheck floor to FR+AC only, add fallback inference for missing tags, escalate retry-2 prompt specificity with `@untestable` escape hatch, define duplicate-anchor warning, replace vague "weight failures" with concrete `low_confidence_anchors` Oracle field, log `coverage_floor_enabled` flag in run report, defer `false_ship_rate` metric to v2.
