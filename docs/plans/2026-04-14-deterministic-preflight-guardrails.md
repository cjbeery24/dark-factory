# Deterministic Preflight Guardrails — Forbidden Patterns + Test-Presence

**Date**: 2026-04-14
**Campaign**: Principled-reset mid-campaign (between F-U1 and next spec)
**Status**: Reviewed by Neo — updated for findings
**Target**: `~/sites/dark-factory`

---

## Motivation

The `2026-04-08-shared-parse-amount` pipeline run (SHIP on iter 2) surfaced two
iteration-burning defect classes that a deterministic post-implementer check
would have caught before the pre-gate container even launched:

1. **Spec-anchored negative constraints violated in code.** FR-3 said "no
   rounding" four times; the implementer shipped `Math.round(numValue * 100)`
   at `packages/shared/src/parse-amount.ts:70`. LLM reviewer caught it; cost
   was one full iteration.
2. **Source-without-tests pattern.** Implementer created
   `packages/shared/src/parse-amount.ts` with no companion `parse-amount.test.ts`
   despite the spec listing both paths and the test-engineer having produced 3
   visible TDD scenario files. Self-tests gate caught it; cost was the same
   iteration (amortized with defect 1).

Both defect classes have repeated across prior runs. Neo's retrospective called
forbidden-pattern enforcement "the highest leverage change" and test-presence
preflight "the same deterministic class — fires on every spec that adds source."

Data point from Neo's spec-corpus check: ~18% of specs in the budget-brain-v2
campaign use explicit "MUST NOT" language. Forbidden-patterns is additive, not
load-bearing: zero cost on specs that don't opt in; deterministic catch on
those that do. Test-presence preflight fires on every spec that adds source.

## Architectural note (from Neo review)

Both checks run in Python on the host, in the iteration loop, between the
implementer's exit and the pre-gate Docker launch. This is the same layer as
`workspace_check` and `preflight.py` — no container, no barrier crossing, no
shell-script coupling. Failures reuse the existing pre-gate retry feedback
path (see `darkfactory/pipeline/iteration.py:952-1000`) so the implementer
gets the structured defect report without a new code path.

## Non-goals

- Not expanding holdout coverage (behaviorally-invisible defects like
  `Math.round` on small integers aren't detectable by holdouts; the signal
  comes from the spec, not from behavior).
- Not rewriting oracle feedback-leakage retry prompts (aggregate-effect
  improvement — collect data over the campaign first).
- Not updating report/rubric language around "100% holdout pass + NO-SHIP"
  (communication-only; no ship-rate impact).

## Phase 1 — Forbidden-pattern anchors

### 1.1 — Extend `AnchorManifest` dataclass

**File**: `darkfactory/spec/anchors.py`

Add optional field to the `AnchorManifest` dataclass (it is a dataclass, not
a JSON schema file):

```python
# anchor_id -> list of forbidden patterns (literal substrings or regex).
# Empty dict = no anchor declares forbidden patterns (the common case).
forbidden_patterns: dict[str, list[str]] = field(default_factory=dict)
```

Update `to_json_path` (already uses `asdict`, no change) and `from_json_path`
to parse the new field with `dict(data.get("forbidden_patterns", {}))`.

### 1.2 — Architect surfaces negative constraints

**File**: `agents/architect.md` (prompt update)

Add an explicit instruction: when a spec contains "MUST NOT <token>" or
"no <X>" constraints tied to a specific FR/EC, include that anchor in
`forbidden_patterns` with the literal tokens to block. Examples to seed the
prompt:
- "no rounding" on FR-3 → `{"FR-3": ["Math.round(", "Math.floor(", "Math.ceil(", ".toFixed("]}`
- "no console logging" on FR-5 → `{"FR-5": ["console.log(", "console.error("]}`

The architect decides what patterns are appropriate per spec — this is domain
judgment, not boilerplate. The field is optional.

### 1.3 — Python check module

**File**: `darkfactory/pipeline/forbidden_patterns.py` (new)

```python
def check_forbidden_patterns(
    manifest: AnchorManifest,
    project_dir: Path,
    diff_base: str = "HEAD",
) -> list[ForbiddenPatternViolation]:
    """Scan added lines in the implementer diff for manifest-declared forbidden patterns.

    Returns a list of (anchor_id, pattern, file, line_number, matched_text)
    for every violation. Empty list = pass.
    """
```

Semantics:
- Diff source: `git diff --unified=0 <diff_base> -- <project_dir>` parsed for
  added lines (lines starting with `+`, excluding the `+++` file header).
- Each pattern is tried as a **literal substring** first; if it contains
  regex metachars (`.*?+[](){}|^$\\`), fall back to `re.search`. Escape is
  user responsibility — we don't try to auto-detect.
- Comment stripping: **not** applied in v1. If the implementer writes
  `Math.round(` inside a comment, they are thinking about it and the block
  is warranted. Revisit if false positives appear.

### 1.4 — Wire into iteration loop

**File**: `darkfactory/pipeline/iteration.py`

Insert check between implementer exit and `run_pre_gate` call (around line
952). On violation, synthesize a `PreGateResult`-shaped failure so the
existing feedback/retry path consumes it identically. The implementer sees
a structured feedback message listing anchor-id, pattern, file, line.

Feedback template:
```
The following forbidden patterns were found in your implementation:
  - FR-3 forbids `Math.round(`
      packages/shared/src/parse-amount.ts:70
      matched: `const cents = Math.round(numValue * 100);`
These patterns are declared in the architecture manifest as spec-level
MUST-NOT constraints. Remove them before the pre-gate will accept this
iteration.
```

### 1.5 — Unit tests

**File**: `tests/unit/test_forbidden_patterns.py` (new)

- Manifest with `forbidden_patterns: {"FR-3": ["Math.round("]}` + synthetic
  diff adding a line with `Math.round(x * 100)` → returns one violation
  with correct file/line/anchor citation.
- Same manifest + diff that *removes* a line with `Math.round` → empty
  result (we only check added lines).
- Pattern with regex metachars (`r"\.toFixed\(\d+\)"`) matches regex-style.
- Manifest with empty `forbidden_patterns` dict → no-op, empty result.
- Manifest where `forbidden_patterns` references an anchor not in
  `fr_ids`/`ac_ids`/`ec_ids` → check still runs but logs a warning (belt
  and suspenders: we don't trust anchor-id cross-referencing for
  enforcement).

## Phase 2 — Test-presence preflight

### 2.1 — Python check module

**File**: `darkfactory/pipeline/test_presence.py` (new)

```python
def check_test_presence(
    config: PipelineConfig,
    project_dir: Path,
    diff_base: str = "HEAD",
) -> list[TestPresenceViolation]:
    """Fail when added source files have no accompanying test file.

    Returns a list of unaccompanied source paths. Empty list = pass.
    """
```

Semantics:
1. Diff scope: added files (`git diff --name-only --diff-filter=A
   <diff_base> -- <project_dir>`) **plus** modified files (`--diff-filter=M`)
   to support "added function to existing file" cases.
2. Partition: a file is a **test** if its name matches `*.test.*`,
   `*.spec.*`, or its path contains `/__tests__/` or `/tests/` or
   `/test/` as a directory segment. Otherwise it's **source** if it lies
   under any `config.source_dirs` entry; otherwise it's neither (ignored).
3. Fail condition: at least one source file was added, and **zero** test
   files were added OR modified in the diff.
4. **No greenfield bypass.** Per Neo: the first spec that adds source
   without tests is exactly where the convention gets set; bypassing
   encodes the wrong precedent. Mirrors `source_dirs_preflight.py`
   pattern (bypass only when there's nothing to check, not when the
   project is new).
5. Empty-test-file guard: if test files were added but none contain any
   `it(`, `test(`, `describe(`, `def test_`, `#[test]` token (language-
   appropriate; derived from `config.language`), emit a separate warning.
   Cheap, deterministic, same call site.
6. Source files outside configured `source_dirs`: emit a
   `SOURCE_DIRS_COVERAGE` warning alongside the main check, mirroring
   `source_dirs_preflight.py`. Don't fail — that's a config-drift signal
   for the architect, not an implementer defect.

### 2.2 — Wire into iteration loop

**File**: `darkfactory/pipeline/iteration.py`

Same insertion point as forbidden_patterns (around line 952). Run both
checks; collect all violations from both; if either has violations,
synthesize the combined `PreGateResult` failure. Feedback lists both
defect classes separately.

### 2.3 — Unit tests

**File**: `tests/unit/test_test_presence.py` (new)

- Diff adds `src/foo.ts` only → fails with citation.
- Diff adds `src/foo.ts` + `src/foo.test.ts` → passes.
- Diff adds `src/foo.ts` + modifies existing `src/bar.test.ts` → passes.
- Diff adds `src/foo.ts` + adds empty `src/foo.test.ts` (no `it(` etc.)
  → warning emitted (not a hard fail if we treat the presence condition
  as satisfied; decide during implementation — leaning toward hard fail
  since an empty test file is the same defect class as no test file).
- Diff only deletes source → passes (nothing to test).
- Diff only adds a test file → passes.
- Diff adds source outside any `source_dirs` entry → warning, not failure.
- Python-language config: `*.test.*` heuristic replaced with `test_*.py`
  / `*_test.py` / `/tests/` directory; `it(`/`test(` replaced with
  `def test_`.

**Decision needed during implementation**: does empty-test-file warrant a
hard fail or a warning? Recommend hard fail, since the failure mode it
guards against (implementer creates stub test file to satisfy presence
check) is exactly what a warning fails to prevent. Escalate to user if
unsure.

## Ordering / dependencies

- Phase 1 and Phase 2 can be implemented in either order; the iteration-
  loop wiring is shared so pick one integration point and wire both
  through it.
- Neither touches barrier enforcement, container masking, iteration
  feedback shape, or orchestrator state machine — lowest-risk intervention
  surface.
- Both fail into the existing pre-gate retry feedback path — no new
  classifier categories, no new audit-log entries, no new orchestrator
  states.

## Success criteria

- Both phases have passing unit tests.
- Synthetic end-to-end test #1: manifest with `forbidden_patterns` on an
  anchor + synthetic implementer diff that violates it → combined check
  returns a violation before `run_pre_gate` is invoked; feedback surfaces
  the pattern + anchor to the (mocked) implementer.
- Synthetic end-to-end test #2: diff adds source without test → combined
  check returns a violation before `run_pre_gate` is invoked.
- Full existing test suite remains green (delta-zero regressions).

## Risk / rollback

- **Risk**: false positives block legitimate implementations. Mitigations:
  forbidden_patterns is opt-in per-anchor; test-presence's design choices
  are conservative (added+modified diff scope; empty-test-file guard
  explicit). Both checks emit structured feedback the implementer can act
  on in the next iteration. No barrier risk.
- **Rollback**: both checks are callable modules wired through a single
  `iteration.py` hook. Removing the hook call disables both; the modules
  remain inert.

## Out of scope for this plan (deferred to post-campaign)

- Oracle leakage retry prompt improvements (tell Oracle *which* phrase
  leaked) — aggregate-effect fix; collect data first.
- Report / Oracle rubric language around holdout coverage vs. reviewer
  coverage — communication-only; no ship-rate impact.
- Declarative `gate_members` config to replace the workspace_check regex
  parser (already tracked in the `8fb122c` commit message and the
  workspace_check module docstring).
