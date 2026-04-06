# Plan: Catch Spec Shape-Ambiguity Before Pipeline Burns Iterations

## Problem

A spec shipped successfully (85% pass) and then three reruns of the same spec produced 33–72% pass rates and escalations. Root cause: the spec mentions cross-boundary surfaces (JSON error envelope, log fields, server stdout, redaction targets) without pinning their literal shape. Scenario-architect and implementer LLMs each invent a shape, the shapes don't agree, and 9–18 scenarios fail every iteration. Pipeline burns ~$252 producing nothing.

This is not a code regression. It's a spec gap that the validator doesn't catch because:
- The deterministic precheck only flags weasel words (`appropriate`, `etc.`) and missing sections, not "you mentioned a JSON envelope but didn't show it".
- The validator agent's Precision Analysis can rate divergence HIGH, but the only structured output is "Free Variables", which is **explicitly informational and non-blocking**.
- `templates/api-spec.md` already has literal JSON examples for every status code, but `templates/feature-spec.md` (used by api-bootstrap) has only an "Interface Contract" header with no shape requirement, and the writer skill never asks for examples.

## Goal

Make it impossible for a spec to reach the pipeline if it mentions a serialized cross-boundary structure without pinning the structure's literal shape.

## Design Overview (4 changes, layered defense)

1. **Precheck shape-claim detector** (deterministic, blocking) — scan FR / AC / Interface Contract / Error Handling sections for shape tokens; require a fenced code block or wire-contract reference within the same section.
2. **Wire Contracts requirement** in writer skill + templates — `feature-spec.md` gains a Wire Contracts section; `df-write-spec` interview asks for literal examples whenever a serialized boundary is mentioned.
3. **Validator agent emits structured `unresolved_contracts:` YAML** — when Precision Analysis sketches diverge on serialized surfaces, the validator must list them in a machine-readable block; orchestrator blocks if non-empty.
4. **Downgrade Free Variables scope** — explicitly limit Free Variables to internal-only choices; cross-boundary structures are never "free", they're contract gaps.

Writer surfaces them → template provides the slot → precheck enforces the slot → validator catches anything precheck missed.

---

## Step 0 — Extract shared constants

**File:** `darkfactory/spec/shape_tokens.py` (new)

Holds `SHAPE_TOKENS` so both `precheck.py` (Step 1) and `validator_output.py` (Steps 3–4) can import without a cycle. This must land before Steps 1 and 4.

```python
# High-precision: each token strongly implies a literal serialized structure.
# Loose words like "field", "format", "header", "schema" are EXCLUDED on purpose
# — they appear constantly in legitimate prose and would flood false positives.
SHAPE_TOKENS: list[str] = [
    "json", "envelope", "response body", "request body", "payload",
    "log line", "log entry", "log format", "stdout", "stderr",
    "status code mapping", "wire format", "redacted",
]
```

`gitnexus_impact` is not needed (new module, no callers yet).

---

## Step 1 — Precheck shape-claim detector

**File:** `darkfactory/spec/precheck.py`

> **Pre-edit:** run `gitnexus_impact({target: "run_precheck", direction: "upstream"})` and `gitnexus_impact({target: "PreCheckResult", direction: "upstream"})`. Report blast radius before editing. Both have many callers (CLI, orchestrator, tests) — adding a new field to `PreCheckResult` is additive but the new `passed=False` failure mode is observable to all callers.

### 1a. Import constants

```python
from darkfactory.spec.shape_tokens import SHAPE_TOKENS

_SHAPE_BEARING_SECTIONS: set[str] = {  # local to precheck
    "Functional Requirements",
    "Acceptance Criteria",
    "Interface Contract",
    "Error Handling",
    "Wire Contracts",
}
```

### 1b. New dataclass

```python
@dataclass
class ShapeClaimFinding:
    section: str
    line_number: int
    matched_token: str
    context: str
```

Add `shape_claims: list[ShapeClaimFinding]` field to `PreCheckResult`.

### 1c. New function `_check_shape_claims(text, section_lines)`

Algorithm:
1. For each section in `_SHAPE_BEARING_SECTIONS` present in `section_lines`, slice the section body.
2. Find every fenced ```` ``` ```` block in the section; record presence (boolean — section either has at least one fenced block or it doesn't).
3. Build a set of all wire-contract names from headings above fenced blocks in the Wire Contracts section.
4. For each non-fenced line, search for any `SHAPE_TOKENS` token using a precompiled word-boundary regex: `re.compile(r"\b(" + "|".join(map(re.escape, SHAPE_TOKENS)) + r")\b", re.IGNORECASE)`.
5. A match is satisfied if:
   - the section contains at least one fenced code block, OR
   - the line contains `wire contracts:` and the named contract exists.
6. Otherwise emit a `ShapeClaimFinding`.

**Section-membership rule, not a line window** — a long FR section with a fenced block at the top correctly satisfies tokens 50 lines down. The earlier ±20-line window was rejected as arbitrary.

**Token list is intentionally narrow** (Step 0 comment). Loose words `field`/`format`/`header`/`schema` are excluded; they appear in legitimate prose ("validate each field", "schema migration") and would flood false positives. Only high-precision tokens trigger the check. Word boundaries additionally prevent `jsonp`/`payloads`/`headerless` matches.

### 1d. Wire into `run_precheck`

- Call after structure/ambiguity checks.
- Append findings to `PreCheckResult.shape_claims`.
- Set `passed = False` if any unresolved.
- Summary line: `"N unresolved shape claim(s) (mention serialized structure without literal example)"`.

**Exact failure message** (one per finding, printed by `dark-factory validate`):

```
{spec_path}:{line}: shape token "{token}" used in {section} without a literal example.
  Fix: add a fenced code block in the same section showing the literal shape,
  or add `Wire Contracts: <name>` and define <name> in the Wire Contracts section.
```

### 1e. CLI surface

Extend `dark-factory validate` printer to include shape claims as `file:line  token  — context` so users can jump to the line.

### 1f. Tests — `tests/unit/test_precheck.py`

- `test_shape_claim_blocked_when_envelope_mentioned_without_example`
- `test_shape_claim_satisfied_by_inline_fenced_block`
- `test_shape_claim_satisfied_by_wire_contract_reference`
- `test_shape_claim_token_word_boundary` (`jsonp` does not match `json`)
- `test_shape_claim_log_format_requires_example`
- `test_shape_claim_outside_shape_bearing_section_ignored` (Examples section can mention "json" freely)
- `test_shape_claim_prose_field_mention_does_not_trigger` — FR with "the response includes a `requestId` field" and no envelope ambiguity must pass (regression for the high-risk false positive Neo flagged)
- `test_shape_claim_long_section_satisfied_by_top_fenced_block` — fenced block at top of FR section, shape token 60 lines later, must pass (proves section-membership rule, not line window)
- Regression: synthetic spec mentioning "JSON envelope" with no example must fail.
- Regression: synthetic spec mentioning "JSON envelope" with adjacent fenced block must pass.
- **Real-world fixture:** copy `2026-04-06-03-49-budget-brain-v2-api-bootstrap.md`'s spec content (the $252 incident spec) into `tests/fixtures/specs/incident_2026_04_06.md` and assert `run_precheck` flags it. Then add a fixed version with Wire Contracts and assert it passes. This is the most valuable single test in the plan.

---

## Step 2 — Wire Contracts in templates and writer skill

### 2a. `templates/feature-spec.md`

Add new section between `Interface Contract` and `Edge Cases`:

````markdown
## Wire Contracts

> Required if any FR / AC / Interface Contract / Error Handling section mentions
> a serialized cross-boundary structure (JSON envelope, log line, stdout format,
> response/request body, header, status code mapping, redaction target).
>
> Each contract is a named, literal, copy-pasteable example. Implementer and
> scenario-architect agents both treat these as ground truth.

### Error Envelope (failure)
```json
{ "ok": false, "error": { "code": "STRING", "message": "STRING", "details": [] } }
```

### Request Log Line
```json
{ "level": "info", "ts": "ISO8601", "requestId": "uuid", "method": "GET",
  "url": "/path", "statusCode": 200, "responseTimeMs": 12 }
```
````

### 2b. `templates/api-spec.md`

Add a one-line note at the top of section 4: "These response shapes are wire contracts: implementer and scenario-architect both treat them as ground truth. Add additional contracts (log lines, stdout, redaction targets) under a Wire Contracts subsection if applicable."

### 2c. `.claude/skills/df-write-spec/SKILL.md`

Add to interview phase, after FR elicitation:

> **Wire Contract elicitation (mandatory).** After listing FRs, scan them for
> JSON, envelope, response, request body, log, stdout, format, field, header,
> status code, redacted, schema, payload. (This interview list is intentionally
> broader than the precheck `SHAPE_TOKENS` — humans filter false positives during
> the interview, the precheck cannot. Do not sync the two lists.) For each occurrence ask:
> *"You mentioned <token> in <FR-N>. Paste a literal example of the exact shape —
> this becomes a wire contract that both the implementer and scenario architect
> will treat as ground truth."* Capture into the Wire Contracts section under
> a descriptive heading. Do not let the user wave this off — if they say
> "obvious" or "standard", ask anyway, because two LLMs will produce two
> different "obvious" shapes.

Add checklist item: `[ ] Every shape token in FR/AC/IC/EH has a corresponding Wire Contract.`

### 2d. Already-shipped spec — out of scope

We don't edit shipped specs. Going forward only.

---

## Step 3 — Validator agent: structured `unresolved_contracts:` block

### 3a. `agents/spec-validator.md`

After the Precision Analysis section, add:

````markdown
## Unresolved Contracts (machine-readable)

When your Precision Analysis sketches diverge on a serialized cross-boundary
structure (the three implementations would produce different JSON, log lines,
stdout text, response shapes, header sets, or status code mappings), you MUST
emit a fenced YAML block at the END of your output:

```yaml
unresolved_contracts:
  - surface: "error envelope"
    section: "FR-3 Error Handling"
    sketches_diverged_on: "shape of details field — array of strings vs array of {field, message}"
```

If none, emit `unresolved_contracts: []`. The block is REQUIRED on every run.
Precheck/orchestrator will reject the spec if the block is missing or non-empty.
````

### 3b. `darkfactory/spec/validator_output.py` (new)

```python
def parse_unresolved_contracts(validator_output: str) -> list[dict] | None:
    """Return list (possibly empty) of unresolved contracts, or None if missing."""
```

Tolerates markdown noise around the block; finds the last ```` ```yaml ```` block containing key `unresolved_contracts`.

### 3c. Orchestrator wiring

`darkfactory/pipeline/orchestrator.py`, after the validator agent invocation:
1. Parse `unresolved_contracts` from the validator's stdout.
2. If `None` (block missing) → **re-prompt validator once** with: *"Your previous response did not include the required `unresolved_contracts:` YAML block. Append it now: empty list if no contracts diverge across your sketches, otherwise list each surface, section, and divergence."* If the second response also lacks the block, fail validation.
3. If non-empty list → fail validation, print each entry (`surface`, `section`, `sketches_diverged_on`).

The single retry handles LLM omission noise; fail-closed on the second miss prevents silent slip-through.

Use `gitnexus_impact` on the validator-invocation symbol before editing.

### 3d. Tests

- `tests/unit/test_validator_output.py` — non-empty, empty, missing, markdown noise around block.
- `tests/unit/test_orchestrator.py` — mock validator output, assert pipeline aborts when non-empty.

---

## Step 4 — Tighten Free Variables scope

### 4a. `agents/spec-validator.md`

Reword the Free Variables section:

> **Free Variables are INTERNAL choices only.** A free variable is a decision
> the implementer can make freely without affecting any cross-boundary contract
> (internal helper names, in-memory data structure for a cache, sort algorithm).
> If a candidate "free variable" describes a serialized surface — JSON shape,
> log line format, stdout text, header set, status code mapping — it is NOT a
> free variable. It is an unresolved contract and MUST go in the
> `unresolved_contracts:` YAML block. Free Variables remain informational and
> non-blocking; unresolved contracts are blocking.

### 4b. Misclassification guard

In `validator_output.py`, add `audit_free_variables(text) -> list[str]` that flags any Free Variable entry containing a `SHAPE_TOKENS` token (imported from `darkfactory.spec.shape_tokens` — Step 0). Orchestrator treats hits as unresolved-contract violations.

### 4c. Tests

- `test_free_variable_with_shape_token_is_contract_violation`

---

## File Change Summary

| File | Change |
|---|---|
| `darkfactory/spec/shape_tokens.py` (new) | `SHAPE_TOKENS` constant, shared by precheck and validator_output |
| `darkfactory/spec/precheck.py` | Add tokens, dataclass, `_check_shape_claims`, wire into `run_precheck`, extend `PreCheckResult` |
| `darkfactory/spec/validator_output.py` (new) | `parse_unresolved_contracts`, `audit_free_variables` |
| `darkfactory/pipeline/orchestrator.py` | Call parser after validator, abort on non-empty/missing |
| `templates/feature-spec.md` | Add Wire Contracts section |
| `templates/api-spec.md` | Add ground-truth note |
| `.claude/skills/df-write-spec/SKILL.md` | Mandatory Wire Contract interview + checklist item |
| `agents/spec-validator.md` | Add Unresolved Contracts YAML; tighten Free Variables |
| `tests/unit/test_precheck.py` | 8 new tests |
| `tests/unit/test_validator_output.py` (new) | 6 new tests |
| `tests/unit/test_orchestrator.py` | 1 new test |

## Ordering

1. **Step 0** — extract `shape_tokens.py` (no behavior change; unblocks Steps 1 and 4b without an import cycle).
2. **Step 4a** — agent prompt rewording (no code).
3. **Step 2** — templates + writer skill (no code behavior change yet).
4. **Step 3** — validator block + parser + orchestrator wiring + retry path.
5. **Step 4b** — misclassification guard (depends on Step 0 + Step 3's `validator_output.py`).
6. **Step 1** — precheck shape-claim detector (most aggressive blocker; lands last so users have Wire Contracts to put shapes in).

## Rollout Risk

- **False positives in precheck.** Narrow token list (Step 0) + word-boundary regex + section-scoping (any fenced block in same section satisfies all tokens in that section) keeps this tight. Tests cover known false-positive shapes (`jsonp`, Examples section mentions, prose `field` references).
- **Existing un-shipped specs.** Users see new precheck failures on next run. Error includes file:line and one-line fix instruction. Acceptable — these are real bugs.
- **Validator prompt drift.** Parser fails closed (missing block = violation), so worst case is a noisy retry, not a silent miss.

## Verification

```bash
python3 -m pytest tests/unit/test_precheck.py tests/unit/test_validator_output.py tests/unit/test_orchestrator.py -v --timeout=30
dark-factory validate <synthetic-spec-with-shape-gap>   # must fail with file:line
dark-factory validate <synthetic-spec-with-wire-contracts>  # must pass
```

## Out of Scope

- Editing shipped specs.
- Modifying implementer/scenario-architect agents — they'll consume Wire Contracts naturally because they're in the spec they already read.
- Iteration loop or oracle changes. The whole point is to fail earlier so the loop never starts.

## Review History

- Approved by Neo on second review after revisions to: token list narrowing, section-membership (not line window), retry path, Step 0 extraction for circular-import resolution, exact failure message, gitnexus impact analysis, real-world incident regression fixture.
