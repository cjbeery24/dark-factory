# Plan: Coverage Scanner Recognizes Markdown Scenario Files

## Problem

`agents/scenario-architect.md` documents `.scenarios.md` (Format A, HTTP) and `.journey.md` (Format B, Browser) as **first-class scenario output formats** — and explicitly says Format A is "STRONGLY PREFERRED for all HTTP API specs" (line 244). The required structure for those files uses a markdown header convention:

```markdown
# Scenario: descriptive name here
# Requirement: FR-1

## Request
...
```

`darkfactory/scenarios/coverage.py` has never been taught about this format. Specifically:

1. **`_SCENARIO_EXTENSIONS`** (line 86) lists only `.js, .ts, .jsx, .tsx, .py, .mjs, .cjs`. `.scenarios.md` and `.journey.md` are absent.
2. **`_iter_scenario_files`** (line 136) filters by suffix-or-`.spec.`-or-`.test.`. None of those match a file named `01-health-endpoint.scenarios.md`.
3. **`_extract_anchors_from_text`** (line 93) only credits two patterns: `@covers` JSDoc tags and `[FR-N]` markers inside `describe(...)`/`it(...)`/`test(...)` string literals. The `# Requirement: FR-1` markdown header matches neither.

**Net effect:** for any HTTP API spec following the documented preferred path, the coverage gate is structurally unreachable. The pipeline run on `budget-brain-v2 / 2026-03-28-api-bootstrap` proved this concretely (report `2026-04-06-19-56-...md`):

- Scenario architect succeeded (12 well-formed `.scenarios.md` files generated, all carrying `# Requirement: ...` headers).
- Coverage scanner reported `23/23 uncovered`.
- Pipeline entered `coverage_regen` loop and burned attempts until interrupted at ~14 minutes wall, with no possible escape.

The two layers were each locally reasonable; nothing reconciled them.

## Goal

The coverage scanner correctly credits anchors declared via `# Requirement: FR-N[, AC-N, ...]` headers in `.scenarios.md` and `.journey.md` files. The pipeline run on the budget-brain-v2 spec (and any other HTTP API spec using Format A) reaches the gate instead of looping in coverage_regen.

## Out of Scope

- The fixed-default token accounting (`est_input_tokens=800000` etc. recorded identically across agents) — separate bug, lower priority, filed as a follow-up.
- Format C/D/E sentinel files (`SCENARIOS-NOT-APPLICABLE.md`, etc.) — those have explicit handling elsewhere; this slice does not touch them.
- Changing the scenario architect's output format. The architect's contract is documented and the user-visible artifacts (`# Scenario:` / `# Requirement:`) are useful in their own right. The fix is in the scanner, which is the layer that's wrong.
- Touching the runner / quality gate executors. Those already understand `.scenarios.md` (per the architect doc — "the runner will silently skip anything that doesn't match this exact structure" — implying the runner DOES match this structure). This plan only fixes the **anchor coverage check** that runs before the runner.
- Fixing the architect's filename conventions (e.g., `11-fr1-server-startup.scenarios.md` uses `fr1` instead of `FR-1`). The `# Requirement:` header is the canonical credit path; filename inference is a fallback.

## Design

### Step 1 — Extend `_SCENARIO_EXTENSIONS` and `_iter_scenario_files`

**File:** `darkfactory/scenarios/coverage.py`

Add the markdown scenario suffixes. Because `.scenarios.md` is a *compound* extension (`Path.suffix` returns only `.md`, not `.scenarios.md`), the iterator must check the full suffix chain, not just `path.suffix`.

```python
# File extensions to scan. Language-agnostic regex over file text — no AST.
_SCENARIO_EXTENSIONS = (".js", ".ts", ".jsx", ".tsx", ".py", ".mjs", ".cjs")

# Compound extensions for markdown scenario formats. Matched against the
# full suffix chain because Path.suffix only returns the last suffix.
# Documented in agents/scenario-architect.md as Format A (HTTP) and Format B
# (Browser); both use markdown header conventions for anchor declaration.
_MARKDOWN_SCENARIO_SUFFIXES = (".scenarios.md", ".journey.md")
```

`_iter_scenario_files`:

```python
def _iter_scenario_files(scenarios_dir: Path):
    """Yield every scenario file under scenarios_dir matching known patterns.

    Recognized:
    - Code-test extensions in _SCENARIO_EXTENSIONS (.js, .ts, .py, ...)
    - .spec.* / .test.* in the suffix chain (jest/mocha/vitest convention)
    - Markdown scenario formats in _MARKDOWN_SCENARIO_SUFFIXES
      (.scenarios.md, .journey.md) — agent-emitted Format A / Format B
    """
    if not scenarios_dir.exists():
        return
    for path in scenarios_dir.rglob("*"):
        if not path.is_file():
            continue
        name_lower = path.name.lower()
        suffix_chain = "".join(path.suffixes).lower()
        if path.suffix.lower() in _SCENARIO_EXTENSIONS:
            yield path
            continue
        if ".spec." in suffix_chain or ".test." in suffix_chain:
            yield path
            continue
        if any(name_lower.endswith(s) for s in _MARKDOWN_SCENARIO_SUFFIXES):
            yield path
            continue
```

**Why `name_lower.endswith()` and not the suffix-chain trick:** `Path("01-foo.scenarios.md").suffixes` is `['.scenarios', '.md']`, joined to `.scenarios.md` — that works. But a malicious or unusual filename like `foo.bar.scenarios.md` joins to `.bar.scenarios.md`, which would fail a suffix-chain equality check. `endswith()` handles both cleanly. (`Path.suffix` here is just `.md`, which is too broad for the existing tuple match.)

### Step 2 — Add `_extract_anchors_from_markdown_scenario`

**File:** `darkfactory/scenarios/coverage.py`

A separate extractor function for markdown scenarios. Parallel to `_extract_anchors_from_text`, not an extension of it. The two formats have different credit semantics — code files use `@covers` tags / `describe()` literals, markdown files use `# Requirement:` headers — and conflating them risks crediting random prose mentions in markdown body text.

```python
# Match `# Requirement: FR-1, AC-2` (or `# Requirements:` plural).
# - Anchored to start-of-line ((?m)^).
# - Allows leading whitespace before `#` (some editors auto-indent).
# - Captures everything after the colon for anchor extraction.
# - Single-line; the header is one line per the architect contract.
_MARKDOWN_REQUIREMENT_RE = re.compile(
    r"(?m)^\s*#\s*Requirements?\s*:\s*(.+)$",
    re.IGNORECASE,
)


def _extract_anchors_from_markdown_scenario(text: str) -> set[str]:
    """Return the set of anchor IDs credited by a markdown scenario file.

    Credit rule: only anchors appearing on a `# Requirement: FR-N[, AC-N]`
    header line, as documented in agents/scenario-architect.md (Format A
    and Format B). Body prose mentions of FR-N/AC-N/EC-N do NOT credit the
    anchor — same discipline as the code-file extractor's restriction to
    @covers tags and describe() literals.
    """
    anchors: set[str] = set()
    for header_match in _MARKDOWN_REQUIREMENT_RE.finditer(text):
        line = header_match.group(1)
        for m in _ANCHOR_RE.finditer(line):
            anchors.add(_normalise_anchor(m.group(1), m.group(2)))
    return anchors
```

### Step 3 — Dispatch in `compute_coverage`

**File:** `darkfactory/scenarios/coverage.py`, around line 164.

The current loop calls `_extract_anchors_from_text(text)` for every file. Branch on the file type:

```python
for path in _iter_scenario_files(scenarios_dir):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    name_lower = path.name.lower()
    if any(name_lower.endswith(s) for s in _MARKDOWN_SCENARIO_SUFFIXES):
        anchors = _extract_anchors_from_markdown_scenario(text)
    else:
        anchors = _extract_anchors_from_text(text)
    # Filename fallback inference — credit any anchor ID in the filename.
    for m in _FILENAME_ANCHOR_RE.finditer(path.name):
        anchors.add(_normalise_anchor(m.group(1), m.group(2)))
    for anchor in anchors:
        if anchor in per_anchor_files:
            per_anchor_files[anchor].add(str(path))
```

The filename fallback (`_FILENAME_ANCHOR_RE`) is preserved for both branches. It's a *fallback*, not the primary path; the markdown header is canonical for `.scenarios.md`/`.journey.md`.

### Step 4 — `@untestable` markers in markdown scenarios

**File:** `darkfactory/scenarios/coverage.py`

`_extract_untestable_markers` (line 123) is currently called from `compute_coverage` (need to verify — read the next 30 lines below the dispatch loop). If it's called per file, it must work on markdown scenario files too. The `@untestable FR-N: reason` marker syntax is comment-style and could theoretically appear in any file format, but in practice the architect would emit it inside the markdown body — not in a header.

**Decision:** Run `_extract_untestable_markers` against the file text regardless of format. The existing regex (`@untestable FR-N: reason`) is format-agnostic and will work in markdown comments, code comments, or anywhere else. No code change needed; just verify (Step 0) and document.

**Step 0 (verification, no code change):** Read `compute_coverage` lines 164–199 to confirm where `_extract_untestable_markers` is called. If it's in the same loop as `_extract_anchors_from_text`, no change needed. If it's elsewhere, this plan needs an addendum.

### Step 5 — Tests

**File:** `tests/unit/test_scenario_coverage.py` (verify name with grep first; otherwise add to whatever existing coverage test module uses `compute_coverage`)

New tests:

1. **`test_markdown_scenario_file_is_iterated`** — write a `.scenarios.md` file under a temp scenarios dir, assert `_iter_scenario_files` yields it.
2. **`test_journey_md_file_is_iterated`** — same with `.journey.md`.
3. **`test_extract_anchors_from_markdown_requirement_header`** — input string with `# Requirement: FR-1, AC-3`, assert `{FR-1, AC-3}`.
4. **`test_extract_anchors_handles_plural_requirements`** — input with `# Requirements: FR-1` (plural), assert `{FR-1}`.
5. **`test_extract_anchors_ignores_body_prose_mentions`** — markdown file with `# Scenario: foo` / `# Requirement: FR-1` followed by body text mentioning `FR-7` and `AC-2` in prose, assert ONLY `{FR-1}` is credited (not the prose mentions). This is the discipline test — locks the rule that markdown body prose does NOT credit anchors.
6. **`test_extract_anchors_handles_multiple_scenario_blocks`** — markdown file with two `# Scenario:` blocks, each with its own `# Requirement:` header, assert both anchor sets are credited.
7. **`test_compute_coverage_credits_markdown_scenarios_end_to_end`** — the budget-brain-v2 regression. Build an `AnchorManifest` with `FR-1, FR-2, AC-3`. Write a fake `01-health.scenarios.md` with `# Requirement: FR-1, AC-3`. Assert `compute_coverage` reports `FR-1` and `AC-3` covered, `FR-2` uncovered. The previous behavior was 3/3 uncovered.
8. **`test_compute_coverage_does_not_credit_md_files_without_scenario_suffix`** — write a plain `notes.md` file with `# Requirement: FR-1` text. Assert it is NOT iterated and FR-1 is NOT credited. Locks the suffix discrimination — plain `.md` is intentionally excluded (per the architect contract: "Plain `.md` files are NOT recognized and will be silently skipped.").
9. **`test_extract_anchors_handles_leading_whitespace_on_requirement_header`** — `   # Requirement: FR-1`, assert credited. Editors that auto-indent shouldn't break this.
10. **`test_code_files_still_use_describe_extractor`** — sanity test that `.test.ts` files with `describe("[FR-1]", ...)` still work via the existing path. Make sure the dispatch branch didn't break the original code-file path.

**Hard requirement:** existing tests in the coverage test module continue to pass. The dispatch in `compute_coverage` is the only behavior change for code files; it should be a no-op.

## File Change Summary

| File | Change |
|---|---|
| `darkfactory/scenarios/coverage.py` | Add `_MARKDOWN_SCENARIO_SUFFIXES`, `_MARKDOWN_REQUIREMENT_RE`, `_extract_anchors_from_markdown_scenario`; extend `_iter_scenario_files` to recognize markdown suffixes; branch dispatch in `compute_coverage` |
| `tests/unit/test_scenario_coverage.py` (or wherever `compute_coverage` is tested) | 10 new tests per Step 5 |

No agent files, no orchestrator files, no scenario-architect changes.

## Ordering

1. **Step 0** — verify `_extract_untestable_markers` call site. Read coverage.py:160-200. No code change unless the call site is in an unexpected place.
2. **Step 5 test #7 (the budget-brain-v2 regression)** — write it FIRST against the unmodified scanner. It must fail (current behavior reports 3/3 uncovered). Confirms the test bites.
3. **Step 1** — `_iter_scenario_files` extension. Test #1 and #2 should now pass; test #7 still fails (extractor isn't there yet).
4. **Step 2** — `_extract_anchors_from_markdown_scenario`. Tests #3, #4, #5, #6, #9 pass.
5. **Step 3** — dispatch in `compute_coverage`. Test #7 passes (the regression). Test #10 still passes (no regression in code-file path).
6. **Step 5 test #8** — exclusion test. Confirms plain `.md` is not over-credited.
7. Run the full unit suite. Confirm 59 baseline failures unchanged.

## Verification

```bash
python3 -m pytest tests/unit/test_scenario_coverage.py -v --timeout=30
python3 -m pytest tests/unit --timeout=30   # baseline 59 failures, no new
```

Manual smoke (post-fix), mirroring the budget-brain-v2 trace:

```bash
cd /tmp/fake-project
mkdir -p .dark-factory/scenarios
cat > .dark-factory/scenarios/01-health.scenarios.md <<'EOF'
# Scenario: health endpoint returns ok
# Requirement: FR-1, AC-3

## Request
GET /health

## Expect
- Status: 200
EOF
# Then run compute_coverage from a python -c against a manifest with FR-1/AC-3
# and assert uncovered_anchors == [].
```

## Rollout Risk

- **Could the new dispatch over-credit code files that happen to contain `# Requirement:` in a docstring?** No. The dispatch branches by filename suffix (`.scenarios.md` / `.journey.md`), not by content. A `.test.ts` file with `# Requirement:` in a comment goes through the original code-file extractor, which doesn't recognize markdown headers.
- **Could the `(?m)^\s*#` pattern false-match on a code file's preprocessor directive (`#include`, `#define`)?** Only `.scenarios.md` and `.journey.md` files reach this regex, per the dispatch branch. Code files never see it.
- **Could the markdown extractor under-credit a real file?** If the architect emits `# requirement:` lowercase, yes — but the regex is `re.IGNORECASE`. If the architect emits `**Requirement:**` bold, yes — but that violates the documented format (line 216 of `scenario-architect.md`: *"Each scenario block MUST start with `# Scenario: <name>` (not `##`, not `**`, not any other heading)"*). We follow the contract.
- **Filename fallback (`_FILENAME_ANCHOR_RE`) still misses `fr1`/`ac10`-style names.** Acknowledged. Out of scope; the `# Requirement:` header is the canonical path. Filename fallback is a defense-in-depth helper, not load-bearing.
- **`.scenarios.md` and `.journey.md` are agent-emitted formats — what if someone hand-writes one badly?** Same answer as the runner: silently skipped at the extractor level (no `# Requirement:` headers → no credited anchors → uncovered → gate failure). That's the correct behavior; it surfaces the malformed file as a coverage gap rather than silently passing it.

## Why This is the Third Pipeline Bug This Session

This is the third "two layers each locally reasonable, never reconciled" bug we've shipped fixes for in this session (after the AST precheck rewrite and the validator divergence-reason gate). The pattern is:

1. Layer A (agent contract) is updated.
2. Layer B (validator/scanner/orchestrator) is not updated to match.
3. The disconnect is invisible until an end-to-end pipeline run on a real spec.

This is not a bug in any one fix — it's a systemic gap in how we test the seam between agent contracts and the deterministic Python code that consumes them. **Out of scope for this slice**, but worth filing: a smoke-test harness that runs a synthetic spec through the entire pipeline (validator → architect → scanner → gate) on every PR would have caught all three. Probably the highest-leverage thing to build next, after this fix lands.
