# Plan: Validator `sketches_diverged_on` Quality Gate

## Problem

The unresolved-contracts gate (shipped via `spec-shape-ambiguity-detection.md`, Step 3) is bypassed by content-free divergence reasons. Two consecutive user traces have hit it:

```
ERROR: spec validation failed: validator flagged unresolved contracts:
  - Interface Contract (5. Interface Contract - envSchema): ?
Resolve each by adding a literal example to the spec's Wire Contracts section.
```

The user gets a hard block with **no actionable reason**. Worse: the same spec passed at 94/100 in a prior `dark-factory validate` run. Same code, same content. Non-deterministic LLM output.

### Root cause (concrete)

`darkfactory/pipeline/orchestrator.py:584`:

```python
diverged = entry.get("sketches_diverged_on", "?")
```

The orchestrator itself injects `"?"` as the printed default when the validator omits the `sketches_diverged_on` key entirely. The validator agent did not literally type `?` — it emitted an entry shaped like:

```yaml
unresolved_contracts:
  - surface: "Interface Contract"
    section: "5. Interface Contract - envSchema"
```

— no `sketches_diverged_on` field at all. The orchestrator's `?` placeholder turned a structurally-incomplete entry into a printed-but-meaningless block, and the writer was left guessing.

### Why this is two bugs stacked, not one

Both reviewers (Liotta + Neo) flagged the same secondary concern: even if the validator does emit a `sketches_diverged_on` string, an LLM can produce schema-compliant garbage:

- Vacuous sentinels: `"?"`, `"??"`, `"???"`, `"TODO"`, `"TBD"`, `"N/A"`, `"none"`, `"unknown"`, `"unclear"`, `"..."`, `"…"`, empty string, whitespace-only.
- Verbatim echo of the `surface` or `section` field (no actual divergence content).
- Single-token reasons under ~3 tokens (e.g., `"divergent"`, `"ambiguous"`, `"unclear"`).

All of these satisfy the schema. None of them tell the writer what to fix. The current parser accepts every one of them.

## Goal

When the validator agent emits an `unresolved_contracts` entry whose `sketches_diverged_on` is missing, vacuous, or echo-only, treat it the same way the orchestrator already treats a missing `unresolved_contracts:` block: **re-prompt the validator once** with a sharper instruction. If the second response is still vacuous, fail loud — but include the **raw validator output** in the error so the user can see what the validator actually said, not the orchestrator's `?` placeholder.

This is a hole in the shipped `spec-shape-ambiguity-detection.md` plan, not new functionality. Delivered as a new small plan file (cleaner git history; addendum was the alternative).

## Out of Scope

- **Replacing LLM-emitted divergence reasons with orchestrator-computed structural sketch-diffing.** Both reviewers named this as the eventual right answer (the LLM's job becomes "produce two candidate sketches", the orchestrator computes divergence by structural diff, `sketches_diverged_on` becomes derived not authored). It is a real plan, not a one-line fix. Filed as a follow-up; revisit if vacuous-reason retry rate climbs past ~20% in observed runs.
- Hallucinated-but-plausible divergence reasons (the validator confidently asserts X diverges when it doesn't). The vacuous-reason fix won't catch this. Out of scope; requires the structural redesign above.
- Free Variables audit (`audit_free_variables`) — separate code path, working as intended.
- Changes to `dark-factory validate` itself — the bug is in the shared orchestrator gate.

## Design

### Step 1 — `is_vacuous_divergence_reason` predicate

**File:** `darkfactory/spec/validator_output.py`

New module-level constant and predicate:

```python
# Sentinel strings that satisfy the schema but carry no information.
# Lowercased for case-insensitive comparison; whitespace stripped before
# matching.
_VACUOUS_REASON_SENTINELS: frozenset[str] = frozenset({
    "", "?", "??", "???",
    "todo", "tbd", "n/a", "na", "none", "null",
    "unknown", "unclear", "ambiguous", "divergent",
    "...", "…",
})


def is_vacuous_divergence_reason(
    entry: dict,
    *,
    min_token_count: int = 4,
) -> bool:
    """Return True if the entry's ``sketches_diverged_on`` is content-free.

    A reason is vacuous if any of the following hold:

    1. The ``sketches_diverged_on`` key is missing entirely.
    2. The value is None or not a string.
    3. The whitespace-stripped, lowercased value is in the sentinel set.
    4. The value tokenizes to fewer than ``min_token_count`` whitespace-
       separated tokens (catches single-word non-reasons like
       ``"unclear shape"`` that aren't sentinels).
    5. The value is a verbatim, case-insensitive echo of the entry's
       ``surface`` or ``section`` field (after whitespace normalization)
       — i.e., the validator restated the field name instead of
       describing the divergence.

    The 4-token minimum is a heuristic, not a contract. It is calibrated
    to reject "?" / "unclear" / "shape diverges" while permitting short
    real reasons like "details field is array vs object".
    """
    raw = entry.get("sketches_diverged_on")
    if raw is None:
        return True
    if not isinstance(raw, str):
        return True
    stripped = raw.strip()
    if stripped.lower() in _VACUOUS_REASON_SENTINELS:
        return True
    tokens = stripped.split()
    if len(tokens) < min_token_count:
        return True
    norm = " ".join(stripped.lower().split())
    for field in ("surface", "section"):
        other = entry.get(field)
        if isinstance(other, str) and " ".join(other.lower().split()) == norm:
            return True
    return False
```

**Why a predicate, not a parser-side rejection:** the parser's job is to extract structure, not judge content. Putting the quality check in a separate function keeps `parse_unresolved_contracts` honest and lets the orchestrator decide what to do (currently: retry).

### Step 2 — Orchestrator: extend retry to vacuous-reason case

**File:** `darkfactory/pipeline/orchestrator.py`, around the existing block at lines 547–589.

The current logic is:
- `contracts is None` → re-prompt once, fail-closed if still None.
- `contracts` is a non-empty list → fail with printed entries.
- `contracts == []` → proceed.

New logic: insert a **vacuous-reason retry** between the parse and the fail-on-non-empty branch.

```python
contracts = parse_unresolved_contracts(report_text)

if contracts is None:
    # ... existing missing-block retry (unchanged) ...

# NEW: if any entry has a vacuous divergence reason, re-prompt once.
if contracts and any(is_vacuous_divergence_reason(e) for e in contracts):
    log.warning(
        "validator emitted unresolved_contracts with vacuous "
        "sketches_diverged_on; re-prompting once"
    )
    vacuous_entries_first = [
        e for e in contracts if is_vacuous_divergence_reason(e)
    ]
    retry_prompt = _build_vacuous_reason_retry_prompt(
        spec_run.spec_name, vacuous_entries_first,
    )
    retry_result = invoke_agent(
        AgentRole.SPEC_VALIDATOR, retry_prompt,
        config=config, barriers=barriers,
        run_id=spec_run.run_id, project_dir=project_dir,
        spec_name=spec_run.spec_name, iteration=0,
        events_file=events_file, audit_path=audit_path, dry_run=dry_run,
    )
    if retry_result.exit_code != 0:
        raise DarkFactoryError(
            f"spec validation re-prompt failed (exit {retry_result.exit_code})"
        )
    report_text_after_retry = (
        report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    )
    contracts_after_retry = parse_unresolved_contracts(report_text_after_retry)
    if contracts_after_retry is None:
        # Validator dropped the block entirely on retry. This is the
        # win condition Neo named: the validator couldn't articulate why
        # it flagged the surface, so it dropped the entry. Treat as
        # "block now missing" failure for symmetry with the existing
        # missing-block path.
        raise DarkFactoryError(
            "spec validation failed: validator dropped the "
            "`unresolved_contracts:` block on the vacuous-reason retry"
        )
    if any(is_vacuous_divergence_reason(e) for e in contracts_after_retry):
        # Second strike: fail loud, include the raw validator output
        # for both attempts so the user can see what the validator
        # actually said (not the orchestrator's `?` placeholder).
        vacuous_entries_second = [
            e for e in contracts_after_retry
            if is_vacuous_divergence_reason(e)
        ]
        raise DarkFactoryError(_format_vacuous_reason_failure(
            first_entries=vacuous_entries_first,
            second_entries=vacuous_entries_second,
            first_raw=report_text,
            second_raw=report_text_after_retry,
        ))
    contracts = contracts_after_retry
    report_text = report_text_after_retry  # for downstream FV audit

if contracts:
    # ... existing print + raise (with the orchestrator's `?` default
    #     replaced — see Step 3) ...
```

`_build_vacuous_reason_retry_prompt` is a small private helper that lists the offending surfaces and tells the validator: *"Your previous validation report flagged these contracts as unresolved but did not provide a meaningful `sketches_diverged_on` reason. For each, either (a) describe in one concrete sentence what differs between your Precision Analysis sketches — naming the field, type, or shape that disagrees — OR (b) drop the entry from `unresolved_contracts` if your sketches actually agree."*

`_format_vacuous_reason_failure` includes:
1. The vacuous entries from both attempts (so the user sees what the validator emitted, not the `?` default).
2. A trailing `Raw validator output (last attempt):` block with the relevant fenced YAML extracted from `report_text_after_retry` — this is what Neo asked for. Bounded to ~40 lines to avoid spam.

### Step 3 — Stop using `?` as a print-time default

**File:** `darkfactory/pipeline/orchestrator.py`, line 584 area.

The current code:
```python
diverged = entry.get("sketches_diverged_on", "?")
```

After Step 2, an entry that reaches this line has been retry-checked. But the print-time `?` default is still actively misleading — it makes "field missing" indistinguishable from "value was `?`". Fix:

```python
raw_diverged = entry.get("sketches_diverged_on")
if raw_diverged is None:
    diverged = "<no sketches_diverged_on field emitted by validator>"
elif not isinstance(raw_diverged, str) or not raw_diverged.strip():
    diverged = f"<empty sketches_diverged_on: {raw_diverged!r}>"
else:
    diverged = raw_diverged
```

This branch should now be unreachable in normal flow (Step 2 would have retried + failed), but defense-in-depth: if any code path skips the retry (test harnesses, future refactors), the printed message no longer lies.

Apply the same fix to the `surface` and `section` defaults in the same loop — same reason.

### Step 4 — Tests

**File:** `tests/unit/test_validator_output.py`

Add tests for `is_vacuous_divergence_reason`:

| Case | Expected |
|---|---|
| `{"sketches_diverged_on": "details field is array vs object of {field, message}"}` | False (real reason) |
| `{}` (no key) | True |
| `{"sketches_diverged_on": None}` | True |
| `{"sketches_diverged_on": ""}` | True |
| `{"sketches_diverged_on": "   "}` | True |
| `{"sketches_diverged_on": "?"}` | True |
| `{"sketches_diverged_on": "TODO"}` | True |
| `{"sketches_diverged_on": "n/a"}` | True |
| `{"sketches_diverged_on": "..."}` | True |
| `{"sketches_diverged_on": "unclear shape"}` | True (under 4 tokens) |
| `{"sketches_diverged_on": "shape of details field"}` | False (4 tokens) — sanity check the threshold |
| `{"surface": "error envelope", "sketches_diverged_on": "Error Envelope"}` | True (echo of surface, case-insensitive) |
| `{"section": "FR-3", "sketches_diverged_on": "FR-3"}` | True (echo of section) |
| `{"sketches_diverged_on": 42}` (non-string) | True |

**File:** `tests/unit/test_orchestrator.py` (extend, do not duplicate)

Add three integration tests for the new retry path. Use the same harness pattern the existing missing-block retry tests use:

1. **Vacuous reason → retry → real reason → proceed.** Mock validator first call returns `unresolved_contracts: [{surface: X, section: Y}]` (no `sketches_diverged_on`). Mock second call returns `unresolved_contracts: []` (validator dropped the entry — Neo's win condition). Assert pipeline proceeds, no DarkFactoryError. Assert `invoke_agent` called twice.

2. **Vacuous reason → retry → still vacuous → fail loud with raw output.** Mock both calls return the same vacuous entry. Assert raised `DarkFactoryError` message contains: (a) the surface name, (b) the literal phrase "Raw validator output", (c) does NOT contain a bare `: ?` (defending against the regression we're fixing).

3. **Vacuous reason → retry → block dropped entirely (None).** Mock first call vacuous, second call has no `unresolved_contracts:` block at all. Assert raised error mentions "dropped the `unresolved_contracts:` block on the vacuous-reason retry". This is intentionally distinct from the missing-block-on-first-call path so logs disambiguate.

**File:** `tests/unit/test_orchestrator.py` (extend the existing print-format test if there is one, otherwise add)

4. **Print-format regression.** When `unresolved_contracts` contains an entry missing `sketches_diverged_on`, the printed error must contain the literal `<no sketches_diverged_on field emitted by validator>` and NOT contain `: ?` as a divergence value. This locks Step 3.

## File Change Summary

| File | Change |
|---|---|
| `darkfactory/spec/validator_output.py` | Add `_VACUOUS_REASON_SENTINELS` constant + `is_vacuous_divergence_reason` predicate |
| `darkfactory/pipeline/orchestrator.py` | Add vacuous-reason retry branch between parse and non-empty fail; add `_build_vacuous_reason_retry_prompt` and `_format_vacuous_reason_failure` private helpers; replace `"?"` default in print loop |
| `tests/unit/test_validator_output.py` | ~14 cases for `is_vacuous_divergence_reason` |
| `tests/unit/test_orchestrator.py` | 4 new tests (3 retry cases + print-format regression) |

## Ordering

1. Step 1 — predicate + tests for it. Land green standalone.
2. Step 4.4 — print-format regression test asserts the current `: ?` behavior is gone. Will fail.
3. Step 3 — fix the print defaults. Step 4.4 now passes.
4. Step 2 — orchestrator retry branch + helpers.
5. Steps 4.1–4.3 — orchestrator retry tests. Land green.
6. Run targeted suite + full unit suite. Confirm 59 baseline failures unchanged.

## Verification

```bash
python3 -m pytest tests/unit/test_validator_output.py tests/unit/test_orchestrator.py -v --timeout=30
python3 -m pytest tests/unit --timeout=30   # baseline 59 failures, no new
```

Manual smoke (post-fix): synthetic spec that the validator is likely to flag with a vacuous reason. Run `dark-factory validate <spec>`. Expected behavior: either the retry produces a real reason and the spec proceeds (or fails with a real divergence sentence), or the second-strike error message includes the raw validator output.

## Rollout Risk

- **Retry doubles validator latency on affected runs.** One additional `invoke_agent` call. Same pattern as the existing missing-block retry. Acceptable.
- **4-token threshold could reject a terse-but-real reason.** The plan picks 4 because "details field is array vs object" passes at 7 tokens and "shape diverges" fails at 2. If real reasons start hitting the threshold, lower it to 3 — the failure mode is "we re-prompt the validator", not "we fail the build". Soft failure.
- **Verbatim-echo check is case-and-whitespace insensitive only.** A reason like `"the surface field is divergent"` won't be caught as an echo of `surface: "the surface field"`. Acceptable. The token-count check catches the shortest cases; the redesign (out of scope) catches the rest.
- **`is_vacuous_divergence_reason` is a heuristic that will be gamed by future model revs.** Both reviewers said this. Mitigation: log every retry to `events.jsonl` with both the first and second `sketches_diverged_on` strings. If retry rate climbs past ~20%, that's the trigger to file the structural-sketch-diff redesign.
- **Behavior change to a shipped gate.** A run that previously printed `: ?` and exited will now retry once. Total wall-clock cost: one extra validator invocation per affected run. No silent slip-through — the gate fails harder on second strike, not softer.

## Why a New Plan File, Not an Addendum

Liotta and Neo split: Liotta said "addendum to spec-shape-ambiguity-detection.md", Neo said "new plan file is overhead for ~50 lines but you do you." Going with new file because:

1. The cleanest commit history is one plan → one slice → one commit. spec-shape-ambiguity-detection.md spans many commits already; threading a Step 3.5 into it muddles the audit trail.
2. The failure-mode telemetry (retry rate as the trigger to revisit the structural-diff redesign) is easier to attach to a discrete plan file than to a sub-step of a larger plan.
3. The structural-sketch-diff redesign, when it lands, will reference this plan as "the heuristic stopgap that the redesign retires." Easier to reference a file than a sub-section.
