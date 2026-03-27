# Plan: Add Failure Classification to Oracle Feedback

**Status:** COMPLETE (2026-03-27)
**Priority:** 1 (Critical — every wasted iteration costs real API dollars)
**Scope:** Oracle agent definition update, iteration loop enhancement, classification parsing
**Estimated files changed:** 4-6
**Reviewed by:** Liotta, Neo

## Implementation Summary

Completed in 4 commits:
- `1ba26dd` — Step 0: Strengthen Implementer self-test requirements to be spec-derived
- `039ab80` — Step 1: Require failure classification on every Oracle NO-SHIP decision
- `3e46971` — Steps 2-4: Add failure classification parser, routing logic, and escalation short-circuit
- `4f4abbb` — Steps 5-6: Warn when Oracle feedback is missing CLASSIFICATIONS block

## Problem Statement

When the Oracle issues NO-SHIP feedback, all failures are treated identically — sent back to the Implementer with spec-section references. But failures have fundamentally different root causes:

- **Implementation bugs** — the Implementer wrote wrong code. Fixable by re-reading the spec.
- **Spec ambiguity** — the spec doesn't uniquely determine behavior. Another iteration will NOT fix this.
- **Scenario bugs** — the blind test itself is broken (crash, wrong assertion). Not the Implementer's fault.
- **Environment issues** — Docker timeout, OOM, flaky network. Retry may help; code changes won't.
- **Brittle tests** — test asserts on format/style the spec doesn't constrain. Not fixable.

Without classification, the pipeline wastes iterations sending spec ambiguity problems back to the Implementer, who can't fix what isn't broken. The original Dark Factory (Yassine's pipeline) explicitly classifies failures to route them differently.

**Direct cost impact:** Each wasted iteration costs a full Implementer + Quality Gate + Review + Oracle cycle. Classification stops the pipeline from burning 3 iterations on unfixable problems.

## Current State

The Oracle agent definition (`agents/oracle.md`) already has a **partial** classification system for stable failures (lines 196-219): `FIXABLE`, `AMBIGUOUS`, `BRITTLE`. But this only activates when the pipeline provides a `## Stable Failure Analysis` section — meaning it works only on iteration 2+, and only for failures that persisted.

First-iteration failures get no classification at all.

## Architecture Decision

### Extend classification to ALL iterations

Every NO-SHIP feedback must classify each failing spec section. Use **structured JSON output** instead of markdown table parsing (review feedback: markdown parsing is fragile and will break when the Oracle produces variations).

### Classification taxonomy

Consolidate the existing `FIXABLE`/`AMBIGUOUS`/`BRITTLE` names with the new taxonomy. The parser must accept both old and new names for backward compatibility with existing feedback files.

| Classification | Old name | Description | Routing |
|---|---|---|---|
| `IMPL_BUG` | `FIXABLE` | Code diverges from clear spec requirement | → Implementer (normal feedback) |
| `SPEC_AMBIGUOUS` | `AMBIGUOUS` | Spec doesn't uniquely determine expected behavior | → Escalation (short-circuit iteration) |
| `SCENARIO_CRASH` | (new) | Test crashes (TypeError, import error, etc.) | → Log for now (repair mode is future work) |
| `ENV_ISSUE` | (new) | Timeout, OOM, network failure | → Retry gate once without re-implementing |
| `BRITTLE` | `BRITTLE` | Test asserts on format/style spec doesn't constrain | → Count toward escalation threshold |

### Routing logic

1. **IMPL_BUG** — standard feedback loop (current behavior)
2. **SPEC_AMBIGUOUS + BRITTLE** — if >50% of failures are these two types, set `escalation_recommended=True` and skip further iteration
3. **SCENARIO_CRASH** — log it. Do NOT trigger repair mode in v1 (separate future work)
4. **ENV_ISSUE** — if ALL failures are ENV_ISSUE, retry quality gate once without re-implementation. Max 1 env retry per iteration.
5. **Fallback** — if classification parsing fails entirely, treat all failures as IMPL_BUG (preserves current behavior)

## Implementation Steps

### Step 1: Update Oracle agent definition

**File:** `agents/oracle.md`

Replace the two-table approach (one without Classification, one with) with a single format that always includes classification. Add structured JSON output instruction.

Add to the NO-SHIP feedback template:

```markdown
### Failure Classification (required on every NO-SHIP)

Classify every failing area into exactly one category:

- **IMPL_BUG**: Code clearly diverges from a specific, unambiguous spec requirement.
  You can cite the exact spec line. Tell the Implementer what to re-read.

- **SPEC_AMBIGUOUS**: The spec does not uniquely determine the expected behavior.
  Two reasonable implementations could differ. Do NOT send to Implementer.
  Write: "ESCALATION RECOMMENDED: [spec section] needs clarification on [what]."

- **SCENARIO_CRASH**: Test crashes (TypeError, SyntaxError, import error) rather
  than failing an assertion. Not an implementation problem.

- **ENV_ISSUE**: Timeout, OOM, network failure, Docker infrastructure problem.
  Not a code problem.

- **BRITTLE**: Test asserts on serialization format, quoting style, import syntax,
  or other choices the spec does not constrain. Do NOT send to Implementer.

**Reclassification rule**: Failures persisting identically for 2+ iterations after
being classified as IMPL_BUG should be reclassified as SPEC_AMBIGUOUS unless you
can cite the exact spec line that uniquely determines the expected behavior.

**REQUIRED: After your markdown feedback, include the following structured block:**

```json
<!-- CLASSIFICATIONS: {"FR-2": "IMPL_BUG", "EC-3": "IMPL_BUG", "FR-7": "SPEC_AMBIGUOUS"} -->
```

This machine-readable block maps spec sections to classifications. Include every
failing spec section. Use the exact classification names above.
```

Also update the Spec Section Failures table to always include the Classification column:

```markdown
## Spec Section Failures
| # | Spec Section(s) | Behavioral Category | Classification | Severity |
|---|---|---|---|---|
| 1 | FR-2, EC-3 | boundary condition handling | IMPL_BUG | Critical |
| 2 | FR-7 | config key naming | SPEC_AMBIGUOUS | Medium |
```

### Step 2: Add classification parsing to iteration loop

**File:** `darkfactory/pipeline/iteration.py`

Add a parsing function that extracts classifications from the structured JSON block, with fallback to the old names:

```python
import re
import json

# Map old names → new names for backward compatibility
_CLASSIFICATION_ALIASES = {
    "FIXABLE": "IMPL_BUG",
    "AMBIGUOUS": "SPEC_AMBIGUOUS",
}

_VALID_CLASSIFICATIONS = {"IMPL_BUG", "SPEC_AMBIGUOUS", "SCENARIO_CRASH", "ENV_ISSUE", "BRITTLE"}


def _parse_feedback_classifications(
    feedback_text: str,
) -> dict[str, str]:
    """Parse Oracle feedback and extract failure classifications.

    Returns a dict mapping spec section → classification.
    Example: {"FR-2": "IMPL_BUG", "FR-7": "SPEC_AMBIGUOUS"}
    Returns empty dict if parsing fails (soft failure — preserves current behavior).
    """
    # Try structured JSON block first
    match = re.search(r'<!-- CLASSIFICATIONS:\s*(\{.*?\})\s*-->', feedback_text, re.DOTALL)
    if match:
        try:
            raw = json.loads(match.group(1))
            result = {}
            for section, cls in raw.items():
                cls = _CLASSIFICATION_ALIASES.get(cls, cls)
                if cls in _VALID_CLASSIFICATIONS:
                    result[section] = cls
                else:
                    log.warning("unknown classification %r for %s, defaulting to IMPL_BUG", cls, section)
                    result[section] = "IMPL_BUG"
            return result
        except (json.JSONDecodeError, TypeError) as exc:
            log.warning("failed to parse CLASSIFICATIONS JSON: %s", exc)

    # Fallback: try to parse from markdown table (backward compat)
    # Look for rows with Classification column
    # ... simple regex fallback ...

    # Complete fallback: return empty (all treated as IMPL_BUG by routing logic)
    return {}
```

### Step 3: Add routing logic and consume escalation flag

**File:** `darkfactory/pipeline/iteration.py`

After `_step_oracle` receives NO-SHIP, parse classifications and route. **Critical: add the code that actually reads `escalation_recommended` and acts on it** (review feedback: the flag was set but never consumed in the original draft).

In `run_iteration_loop()`, after the oracle NO-SHIP path (around line 334):

```python
# Parse failure classifications from Oracle feedback
feedback_path = (
    project_dir / ".dark-factory" / "reviews"
    / f"{spec_run.spec_name}-feedback-iter{spec_run.iteration}.md"
)
if feedback_path.exists():
    classifications = _parse_feedback_classifications(
        feedback_path.read_text(encoding="utf-8", errors="replace")
    )
    iter_state.failure_classifications = _count_classifications(classifications)
    spec_run.classification_history.append(classifications)

    # Route based on classification distribution
    total = len(classifications)
    if total > 0:
        ambiguous = sum(1 for c in classifications.values() if c == "SPEC_AMBIGUOUS")
        brittle = sum(1 for c in classifications.values() if c == "BRITTLE")
        env = sum(1 for c in classifications.values() if c == "ENV_ISSUE")
        impl_bug = sum(1 for c in classifications.values() if c == "IMPL_BUG")

        # Majority non-implementation failures → recommend escalation
        if (ambiguous + brittle) / total > 0.5:
            spec_run.escalation_recommended = True
            spec_run.escalation_reason = (
                f"{ambiguous} SPEC_AMBIGUOUS + {brittle} BRITTLE out of {total} failures"
            )
            log.warning("escalation recommended: %s", spec_run.escalation_reason)

        # All ENV_ISSUE → retry gate once without re-implementing
        if env > 0 and impl_bug == 0 and ambiguous == 0 and brittle == 0:
            if not iter_state.env_retry_attempted:
                log.info("all failures are ENV_ISSUE — retrying gate")
                iter_state.env_retry_attempted = True
                # Re-run quality gate without incrementing iteration
                gate_result = _step_quality_gate(...)
                if gate_result.pass_rate == 100:
                    # Re-run review and oracle with new gate result
                    continue  # Skip iteration increment
                # If retry also fails, fall through to normal iteration

# ACT on escalation_recommended (the consumer that was missing in v1 draft)
if spec_run.escalation_recommended and spec_run.iteration >= 2:
    _progress("  Escalation recommended: majority of failures are SPEC_AMBIGUOUS or BRITTLE")
    spec_run.iteration += 1
    return False  # Triggers escalation path
```

### Step 4: Add fields to SpecRun model

**File:** `darkfactory/models/pipeline.py`

Add to `SpecRun`:
```python
escalation_recommended: bool = False
escalation_reason: str = ""
classification_history: list[dict[str, str]] = field(default_factory=list)
```

Add to `IterationState`:
```python
failure_classifications: dict[str, int] = field(default_factory=dict)
env_retry_attempted: bool = False
```

### Step 5: Update feedback validation

**File:** `darkfactory/audit/checker.py`

Update `validate_oracle_feedback` to check for the structured JSON block. Log a warning (not hard failure) if missing — backward compatible.

```python
def _check_classification_present(feedback_text: str) -> bool:
    """Check if feedback contains the structured CLASSIFICATIONS block."""
    return "<!-- CLASSIFICATIONS:" in feedback_text
```

Add to the validation flow: if classification block is absent, log warning but do not reject the feedback.

### Step 6: Update existing tests

**Files:** `tests/unit/test_iteration.py` (and new test file if needed)

Test cases:
- Parse JSON classifications from feedback text
- Parse with old names (FIXABLE → IMPL_BUG, AMBIGUOUS → SPEC_AMBIGUOUS)
- Parse failure: returns empty dict (soft failure)
- Routing: majority SPEC_AMBIGUOUS + BRITTLE sets escalation_recommended
- Routing: escalation_recommended=True at iteration >= 2 returns False (escalation)
- Routing: all ENV_ISSUE triggers gate retry (max 1)
- Routing: ENV_ISSUE retry failure falls through to normal iteration
- Backward compatible: feedback without CLASSIFICATIONS block treated as all-IMPL_BUG

## Verification Criteria

1. Oracle feedback includes `<!-- CLASSIFICATIONS: {...} -->` block on NO-SHIP
2. Classification is parsed correctly from structured JSON block
3. Old names (FIXABLE, AMBIGUOUS) are mapped to new names
4. Parsing failure returns empty dict — current behavior preserved
5. Majority SPEC_AMBIGUOUS/BRITTLE at iteration >= 2 triggers escalation
6. All ENV_ISSUE triggers single gate retry without re-implementation
7. `escalation_recommended` flag is set AND consumed (not just set)
8. `validate_oracle_feedback` accepts both old format (no block) and new format
9. All existing tests pass

## Risks

- **Oracle may misclassify** — mitigated by the reclassification rule (persistent = AMBIGUOUS)
- **JSON parsing fails** — mitigated by soft fallback to empty dict (all-IMPL_BUG behavior)
- **SCENARIO_CRASH not acted on in v1** — accepted; logged only, repair mode is future work
- **ENV_ISSUE retry could loop** — mitigated by `env_retry_attempted` flag (max 1 retry per iteration)
