# Plan: AST-Backed Shape-Claim and AC Checks (two-check slice)

## Problem

The current `darkfactory/spec/precheck.py` is a regex linter pretending to be a parser. A real user-facing trace established the cost: **5 round-trips** to satisfy 2 shape claims, because every failure mode (depth-blind heading walk, inline-vs-fence rejection, bold pseudo-heading collision with `AC-N`, table breakage from interleaved fences) is the same root bug — token-level matching with no structural awareness. Each rule is locally reasonable; together they form an undocumented grammar the writer reverse-engineers by failing.

Both reviewers (Liotta + Neo) converged: the regex substrate will keep producing this class of failure forever until replaced. The error message is the only piece of the current implementation that's substrate-independent.

## Goal

Replace the regex-based shape-claim and structural checks with a single markdown-AST walk that builds an explicit, structural model of the spec. Failure diagnostics become "what I see" reports grounded in the parsed model, not regex hits.

**Success metric:** The exact 5-round trace from the budget-brain-v2 incident reaches 100/100 in **1 round** (one edit cycle from the writer). Round-count is instrumented so future regressions are measurable.

**FR linkage:** This slice replaces the implementation of FR-4 (acceptance criteria check) and the shape-claim post-check. FR-1/2/3/5, scenario intents, contracts, and anchor intents stay regex-based for this slice — see "Out of scope."

## Scope

**In scope (single slice):**
- New module `darkfactory/spec/spec_model.py` — markdown-AST walker that builds a typed `SpecModel` (heading tree, sections, tables with parsed rows, fenced blocks tagged by language, inline code spans, AC criteria with GWT keywords, wire contract definitions).
- Replace `_check_shape_claims` in `precheck.py` with a model-based check.
- Replace `check_acceptance_criteria` with a model-based check that distinguishes "row not parseable as a table row" from "row exists but lacks GWT keywords."
- Replace shape-claim error message with a structured "what I see" diagnostic that names the failure mode and lists candidate fix locations.
- Add round-count instrumentation: `precheck.run_count` written to `.dark-factory/state/{spec}/precheck_runs.json` (incremented per `run_precheck` call against the same spec hash).
- Salvage Neo's #1 (rich error message) — emitted from the new code path, grounded in the parsed model.

**Out of scope (deferred):**
- Migrating other check functions (`find_placeholders`, `find_ambiguities`, `check_fr_count`, `check_scenario_intents`, `check_contracts`, `check_anchor_intent_coverage`) to the AST. They remain regex-based for now. Only the two checks the trace tripped get rewritten — shape claims and acceptance criteria.
- Rewriting `validator_output.py` — separate concern.
- Surgical fixes #2-#5 from the prior plan are intentionally dropped. They would be thrown away.
- The `--fresh` ghost flag fix — orthogonal, will be cleaned up incidentally if encountered but not part of this plan.

## Design

### Step 0 — Dependency

Add `markdown-it-py` (>=3.0,<4.0) to `pyproject.toml` dependencies. Pinned, single wheel. Already used in similar projects; well-maintained; emits a token stream with heading levels, table rows, code-fence info-strings, and inline children resolved.

**Rejected alternatives:**
- `mistune` — GFM table inconsistencies (Neo and Liotta agreed on this; mistune's table rows don't always emit the same children depending on inline content).
- Hand-rolled parser — re-creates the same regex-substrate problem with extra steps.

### Step 1 — `darkfactory/spec/spec_model.py` (NEW)

```python
@dataclass
class FencedBlock:
    line_number: int          # 1-based, of the opening fence
    end_line: int             # 1-based, of the closing fence (inclusive)
    language: str             # "" if no info-string, else "json", "yaml", etc.
    content: str              # body without the fence delimiters
    section_path: tuple[str, ...]  # heading path to the block

@dataclass
class TableRow:
    line_number: int
    cells: list[str]          # raw cell text (markdown not stripped)
    section_path: tuple[str, ...]

@dataclass
class Table:
    """A grouping of TableRows that markdown-it parsed as one GFM table.

    Critical for TABLE_BROKEN_AT_AC: a table that the user wrote as one
    contiguous block but which got split by an interleaved fence/paragraph
    becomes TWO Table objects in the model. That structural fact is the
    detector. Without this grouping object, table breakage can only be
    re-derived by line-adjacency reasoning — i.e., the regex bug we're
    leaving behind.
    """
    line_number: int          # opening row
    end_line: int             # closing row (inclusive)
    rows: list[TableRow]
    section_path: tuple[str, ...]

@dataclass
class Paragraph:
    line_number: int
    text: str                 # plain-text, inline markup stripped

@dataclass
class Heading:
    line_number: int
    level: int                # 1-6
    text: str                 # plain text, inline markup stripped
    raw: str                  # original line for error reporting

@dataclass
class Section:
    heading: Heading
    end_line: int             # exclusive
    fenced_blocks: list[FencedBlock]
    tables: list[Table]       # GROUPED — see Table docstring
    paragraphs: list[Paragraph]

@dataclass
class SpecModel:
    sections: list[Section]   # flat list, in document order
    all_fenced_blocks: list[FencedBlock]
    all_tables: list[Table]

    def find_section(self, name: str) -> Section | None: ...
    def wire_contracts(self) -> dict[str, FencedBlock]:
        """Return {contract_name (lowercased): defining FencedBlock}.

        A wire contract is any sub-heading at any depth that lives under a
        heading whose text contains 'wire contracts' (case-insensitive). The
        defining FencedBlock is the first fenced block under that sub-heading.
        Recognizes `## Wire Contracts`, `### Wire Contracts` nested under
        another section, and any depth — heading level no longer matters.
        """
```

**Cut from earlier draft:** `SpecModel.by_path`, `Section.children` (recursive tree), `SpecModel.line_count`. Sections are stored as a flat document-order list — every check this slice cares about is "find sections by name and look at their contents", not "walk a tree". The recursive child tree is dead weight and an extra invariant to maintain.

The walker builds the model in one pass over the markdown-it token stream:
- `heading_open` / `heading_close` → push/pop heading stack, build Section tree
- `fence` → emit FencedBlock with the current heading path; markdown-it gives us the info-string (`json`, `yaml`, etc.) directly
- `table_open` / `tr_open` / `td_open` → emit TableRow with cells; the parser already understands GFM tables
- `paragraph_open` → collect plain-text paragraphs
- inline tokens → resolved by markdown-it; no need to re-tokenize

This is the structural model. Every check below operates on it, not on raw lines.

### Step 2 — Replace `_check_shape_claims`

New function `check_shape_claims(model: SpecModel) -> list[ShapeClaimFinding]`.

Algorithm:
1. For each section whose heading text matches a shape-bearing canonical name (FR, AC, IC, EH, Wire Contracts), apply existing alias resolution.
2. Check satisfaction in this order, producing structured candidates for the diagnostic:
   - **(a) Section has a fenced block (recursive)** — language is `json`/`yaml`/no-info-string-with-`{`/`[` content. (Tightens Linus's earlier complaint about empty/bash fences from the previous slice — done structurally now: we have the language tag.)
   - **(b) Section's table rows contain a wire-contract reference** — `Wire Contracts: <name>` in any cell, where `<name>` resolves via `model.wire_contracts()`.
   - **(c) Section's paragraphs contain a wire-contract reference.**
3. If none satisfied, scan the section for shape tokens (use existing `SHAPE_TOKENS`). For each match, build a `ShapeClaimFinding` carrying:
   - `section_path`
   - `line_number`
   - `matched_token`
   - `available_wire_contracts: list[str]` — what the user could reference today (from `model.wire_contracts()`); empty list is meaningful and drives diagnostic branch (B) in Step 4
   - `would_break_table: bool` — derived as `any(t.line_number <= line_number <= t.end_line for t in section.tables)`. True iff the matched token sits inside one of the section's `Table` spans

`nearby_fenced_blocks` is intentionally cut. The diagnostic uses `Section.tables[-1].end_line` (a structural fact) to recommend an insertion line, not a vague "nearby block" list.

### Step 3 — Replace `check_acceptance_criteria`

New function `check_acceptance_criteria(model: SpecModel) -> list[CriteriaFinding]`.

**Scope:** This check operates ONLY on the section whose heading resolves (via existing alias resolution) to "Acceptance Criteria". Other sections may legitimately contain `**AC-2**`-shaped text or AC-N references.

Distinguishes three failure modes:
- `MALFORMED_GWT` — a `TableRow` inside the AC section's `Table(s)` whose first cell matches `AC-N` but whose remaining cells lack GIVEN/WHEN/THEN keywords.
- `TABLE_BROKEN_AT_AC` — the AC section contains **two or more `Table` objects** AND any earlier `Table` ends with an `AC-N` row. Structural fact: the user wrote one table but markdown-it parsed two because something (fence, paragraph, blank-line-then-prose) split it. No line-adjacency reasoning needed — the existence of `len(ac_section.tables) > 1` with AC-N IDs straddling the split IS the detection.
- `BOLD_PSEUDO_HEADING_COLLISION` — a `Paragraph` inside the AC section (not inside any `Table`) whose text matches `**AC-N ...**`.

**Precedence (resolves overlap when the same `AC-N` ID would fire multiple modes):**
1. `BOLD_PSEUDO_HEADING_COLLISION` wins — it explains why the AC-N "row" looks malformed (it isn't a row at all).
2. Else `TABLE_BROKEN_AT_AC` if the ID straddles a split.
3. Else `MALFORMED_GWT`.

Each maps to a distinct error message that names the structural cause.

### Step 4 — Rich diagnostic emitter

New function `format_shape_claim_diagnostic(finding: ShapeClaimFinding, section: Section) -> str`. The `section` is passed so the emitter can compute the recommended insertion line as `section.tables[-1].end_line + 1` (or fall back to `section.end_line - 1` if the section has no tables).

The emitter has **two branches**:

- **Contracts available** (`finding.available_wire_contracts` non-empty) — recommend (A) reference an existing contract; show (B) as a secondary option.
- **No contracts available** — skip (A) entirely; lead with (B) and add a one-line explanation that no Wire Contracts are defined in this spec yet, plus a pointer to add a `## Wire Contracts` section.

Example output (contracts-available branch):

```
Unresolved shape claim at line 271: token "stderr" in section "Acceptance Criteria"

  This section mentions a serialized cross-boundary structure but no
  fenced ``` block in this section pins the literal shape, and no
  wire-contract reference in this section resolves to a defined contract.

  Wire contracts currently defined in this spec:
    - stderr-env-validation  (defined at line 244)
    - log-line-request       (defined at line 256)

  Pick ONE fix:

  (A) RECOMMENDED — reference an existing wire contract.
      Add literal text to the AC-2 row:  "Wire Contracts: stderr-env-validation"
      The check resolves names case-insensitively against any heading
      under a "Wire Contracts" section at any depth.

  (B) Add a NEW fenced block in this section.
      Place it AFTER line 280 (the closing row of the AC table).
      Do NOT place it between table rows — that will break GIVEN/WHEN/THEN
      parsing for AC-2 and AC-7 (this is detected separately).
      Do NOT use **Bold pseudo-headings** like "**AC-2 stderr shape:**" —
      the AC-N parser will match them and report "AC-2 missing GIVEN/WHEN/THEN".

      Example fenced block format:
      ```json
      {"level": "info", "msg": "..."}
      ```

  Why the previous attempts failed (if you've been here before):
   - Inline `single-backtick` code does not count; only ``` fences count.
   - "### Wire Contracts" nested under another section IS recognized now —
     the depth restriction was removed in the AST rewrite.
```

The diagnostic is grounded entirely in the parsed model:
- The "wire contracts currently defined" list comes from `model.wire_contracts()`.
- "Place it AFTER line 280" comes from `section.tables[-1].end_line` (the closing row of the AC section's last `Table`).
- "Do not place it between rows" is a structural fact derived from `would_break_table` (i.e., the matched-token line falls inside a `Table` span).
- "Do not use bold pseudo-headings" is the AC-N collision check from Step 3.

This is the "what I see" report Liotta asked for. It is impossible to write without the AST — every line is grounded in a model lookup.

### Step 5 — Round-count instrumentation

New file: `darkfactory/spec/precheck_runs.py`.

```python
def spec_hash(raw_bytes: bytes) -> str:
    """sha256 of the raw spec file bytes. Stable, content-addressed."""
    return hashlib.sha256(raw_bytes).hexdigest()

def record_precheck_run(project_dir: Path, spec_name: str, hash_: str) -> int:
    """Increment precheck run count for a given spec hash. Returns new count."""
    path = project_dir / ".dark-factory" / "state" / spec_name / "precheck_runs.json"
    ...
```

**Schema** — single object, NOT a dict keyed by hash. The previous run's hash is overwritten when the spec content changes; we don't keep history per-hash:

```json
{"spec_hash": "abc123...", "count": 3, "first_run": "...", "last_run": "..."}
```

When `record_precheck_run` is called and the existing `spec_hash` differs from the new one, the file is rewritten with `count: 1` (counter resets on each passing edit cycle).

**Boundary statement:** `run_precheck` does NO filesystem I/O for this counter. The CLI (`dark-factory validate` and any orchestrator caller) computes `spec_hash(spec_path.read_bytes())` and calls `record_precheck_run` itself, then calls `run_precheck`. This keeps `run_precheck` pure and testable.

CLI prints: `precheck run #N for this spec content` on every invocation. When `count >= 3`, prints a flag: `(round count >= 3 — please open an issue if the diagnostics aren't telling you what to fix)`.

This is the trigger Liotta wanted as the AST-rewrite-failure detector. After this slice ships, if any single spec produces round-count > 2 we know the AST diagnostic isn't doing its job and the fix is in this code, not in piling on more rules.

### Step 1.5 — Observer pilot (de-risk the parser before it's load-bearing)

After Step 1 lands, before any check is rewritten on top of it, add a one-shot observer in `run_precheck`:

```python
try:
    model = parse_spec_model(text)
    _log_model_vs_legacy_disagreement(text, model)  # writes to .dark-factory/audit/
except Exception as exc:
    _log_parser_error(spec_path, exc)
```

The observer parses the model alongside the still-active regex checks, compares the sections / fenced-block locations / table boundaries the model sees against what the regex pipeline currently finds, and writes any disagreements to `.dark-factory/audit/precheck_model_disagreement.jsonl`. **No behavior change.** This runs for one slice of real spec traffic so we know the parser handles the corner cases in actual specs (not just synthetic tests) before Step 2 starts depending on it.

Once Step 6 lands, the observer is removed — the model becomes load-bearing and disagreements would surface as test failures, not log lines.

### Step 6 — Wire into `run_precheck`

Replace the old `_check_shape_claims(text)` and `check_acceptance_criteria(text)` callsites with:

```python
def run_precheck(spec_path: Path) -> PreCheckResult:
    text = spec_path.read_text(encoding="utf-8")
    model = parse_spec_model(text)  # NEW
    ...
    criteria_issues = check_acceptance_criteria(model)  # was: check_acceptance_criteria(text)
    shape_claims = check_shape_claims(model)            # was: _check_shape_claims(text)
    ...
```

Old `_check_shape_claims` and the regex-based `check_acceptance_criteria` are deleted. The other regex checks (placeholders, ambiguities, FR count, scenario intents, contracts, anchor intents) stay regex-based for this slice — see "Out of scope."

### Step 7 — Tests

- `tests/unit/test_spec_model.py` (NEW) — parser-level tests:
  - Single section with one fenced block → 1 FencedBlock at the right path
  - Nested sub-headings → sections list in document order, heading levels correct
  - GFM table → one `Table` containing TableRows with correct cell counts; `Table.line_number`/`end_line` span the rows
  - **Two `Table` objects** when a paragraph or fenced block is interleaved between rows the user wrote contiguously (this is the test that proves `TABLE_BROKEN_AT_AC` detection)
  - Code spans inside table cells do NOT become FencedBlocks (markdown-it handles this)
  - Fenced block inside a list item still recognized; `end_line` correct
  - `model.wire_contracts()` returns names from `## Wire Contracts` AND `### Wire Contracts` nested inside another section (the R1 fix)

- `tests/unit/test_precheck_shape_claims.py` — UPDATE existing tests + add:
  - **Existing 14 tests stay green via a thin wrapper.** They currently call `_check_shape_claims(text)`. Add `check_shape_claims_from_text(text: str) -> list[ShapeClaimFinding]` that does `parse_spec_model(text)` then calls the new model-based `check_shape_claims(model)`. Tests are updated to call the wrapper. No test rewrites; no assertion changes — this is the regression contract.
  - **Regression for the budget-brain-v2 trace.** Synthetic spec with the exact failure pattern: AC table with `stderr` and `log line` tokens, no fenced block in AC section, Wire Contracts defined as `### Wire Contracts` nested under Error Handling, AC rows referencing the contract by name. With the AST rewrite this MUST pass.
  - **`### Wire Contracts` precheck-level recognition test** — beyond the parser-level test in `test_spec_model.py`, this asserts that `run_precheck` itself accepts a spec where Wire Contracts is `###`-nested. The parser test proves the model sees it; this test proves the check actually consults `model.wire_contracts()` (catches the wiring bug, not the parser bug).
  - **Diagnostic emitter test — contracts available.** Given a `ShapeClaimFinding` with two available wire contracts and a `Section` containing a `Table`, assert the contract names appear, the recommended placement line is `section.tables[-1].end_line + 1`, and the "do not break the table" warning is present.
  - **Diagnostic emitter test — no contracts.** Given a finding with `available_wire_contracts=[]`, assert branch (A) is absent, branch (B) is present, and the output contains the "no Wire Contracts defined yet" pointer.
  - **Falsifier integration test — `test_diagnostic_recommended_fix_produces_passing_spec`.** Take a synthetic spec that fails shape-claim with a known finding. Programmatically apply the diagnostic's recommended fix (insert a fenced block at the recommended line, OR insert a `Wire Contracts: <name>` reference into the recommended row). Re-run `run_precheck`. Assert `passed=True`. **This is the test that would have prevented the 5-round trace from existing** — it asserts that following the diagnostic's instructions actually works, not just that the diagnostic prints plausible-looking text.

- `tests/unit/test_precheck_acceptance_criteria.py` (NEW or extend existing):
  - `MALFORMED_GWT` — table row exists, no GIVEN/WHEN/THEN
  - `TABLE_BROKEN_AT_AC` — fenced block between AC-2 and AC-7 rows; assert distinct error message naming the structural cause (round 2 of the trace)
  - `BOLD_PSEUDO_HEADING_COLLISION` — paragraph `**AC-2 shape:**` outside the table; assert distinct error (round 4 of the trace)

- `tests/unit/test_precheck_runs.py` (NEW, small):
  - Initial run records count=1
  - Same hash → count increments
  - Hash change → counter resets to 1
  - File is JSON, parseable, idempotent

**Hard requirement:** the existing 27 tests in `test_precheck_shape_claims.py` and `test_validator_output.py` continue to pass. The 14 + 13 = 27 represent the contract from the previous slice and must not regress.

## File Change Summary

| File | Change |
|---|---|
| `pyproject.toml` | Add `markdown-it-py>=3.0,<4.0` dependency |
| `darkfactory/spec/spec_model.py` (new) | `SpecModel`, `Section`, `FencedBlock`, `TableRow`, `Heading`, `parse_spec_model` |
| `darkfactory/spec/precheck.py` | Replace `_check_shape_claims` and `check_acceptance_criteria` with model-based versions; add `format_shape_claim_diagnostic`; delete the regex code paths these replace |
| `darkfactory/spec/precheck_runs.py` (new) | `record_precheck_run` |
| `darkfactory/cli/validate.py` (or wherever `dark-factory validate` lives) | Call `record_precheck_run` before/after `run_precheck`; print diagnostics from `format_shape_claim_diagnostic` |
| `tests/unit/test_spec_model.py` (new) | Parser tests, ~10 cases |
| `tests/unit/test_precheck_shape_claims.py` | Update existing tests to use the new entrypoint; add the budget-brain-v2 regression and the diagnostic emitter test |
| `tests/unit/test_precheck_acceptance_criteria.py` (new or extend) | Three failure-mode tests |
| `tests/unit/test_precheck_runs.py` (new) | Round-count instrumentation tests |

## Ordering

1. **Step 0** — add markdown-it-py to pyproject.toml; verify import in a throwaway test.
2. **Step 1** — `spec_model.py` + parser tests. Land green before any check rewrites.
3. **Step 2** — replace `_check_shape_claims`. Tests first, then implementation.
4. **Step 3** — replace `check_acceptance_criteria` with three-mode classifier.
5. **Step 4** — `format_shape_claim_diagnostic` + tests.
6. **Step 5** — round-count instrumentation.
7. **Step 6** — wire into `run_precheck`, delete the old regex code.
8. **Step 7** — final test pass; full unit suite must show 1340+ → 1340+ + new tests with zero regressions outside the known 59 baseline failures.

## Rollout Risk

- **markdown-it-py edge cases on tables.** Mitigation: Step 1.5 observer pilot logs disagreements against real spec traffic before any check is rewritten. Parser tests in Step 1 cover the GFM table cases the existing codebase needs. **Escape-hatch policy (pick one, not both):** `parse_spec_model` raises `SpecModelParseError` on unparseable input. `run_precheck` catches it ONCE at the top level and emits a single hard-fail finding (`"spec parser could not understand this file: <message>"`). There is no per-check fallback to the regex path. If the parser breaks, every check fails together — surfacing the issue immediately rather than silently dropping coverage. Choosing hard-fail over silent fallback because partial coverage is the failure mode this whole rewrite exists to eliminate.
- **Behavior change visible to existing users.** The shape-claim error message format changes. Anyone scraping the error text breaks. Mitigation: error messages are human-facing only; no scraping is documented. The structured `PreCheckResult.shape_claims` field is unchanged in shape — only the print formatter changes. Existing tests asserting on the dataclass continue to pass.
- **`### Wire Contracts` recognition is a behavior loosening.** A spec that previously failed because Wire Contracts was nested at `###` will now pass. This is intentional and matches the user's mental model. No false-positive risk identified.
- **Round-count file growth.** Bounded by spec count × hash distribution. Gitignored under `.dark-factory/state/`. Not a problem.
- **Dependency surface.** Adds one wheel. markdown-it-py is pure-Python with no native deps. Acceptable.

## Verification

```bash
# Unit tests
python3 -m pytest tests/unit/test_spec_model.py \
                  tests/unit/test_precheck_shape_claims.py \
                  tests/unit/test_precheck_acceptance_criteria.py \
                  tests/unit/test_precheck_runs.py \
                  tests/unit/test_validator_output.py \
                  tests/unit/test_orchestrator.py \
                  tests/unit/test_orchestrator_wiring.py \
                  -v --timeout=30

# Full suite — must show same baseline-failure count as before (59) + new passes
python3 -m pytest tests/unit --timeout=30

# Real-world fixture: copy the budget-brain-v2 spec post-fix to a test fixture
# and verify it precheck-passes in 1 round (count=1 in precheck_runs.json after
# a single run from a clean state).
```

## Out of Scope (explicit)

- Migrating placeholders/ambiguities/FR-count/scenario-intents/contracts/anchor-intents to the AST. Separate slice when needed.
- `--fresh` flag fix. Independent.
- Templates/skill copy updates. The new diagnostic is self-explanatory and contains the recommended fix inline; template updates would be redundant.
- Surgical fixes #2-#5 from the prior plan (recognize `### Wire Contracts`, table-row breakage detection, etc.). The AST rewrite implements all of these structurally — they don't need to ship separately.
- AST rewrites of `validator_output.py` and `coverage.py`. These don't have the same failure history; not load-bearing for this trace.
