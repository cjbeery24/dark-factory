# Pipeline Run Report — budget-brain-v2 / 2026-03-28-api-bootstrap

**Date (UTC):** 2026-04-06 19:56
**Repo:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Run ID:** eb1ad3c9bf8745a6
**Outcome:** ❌ INTERRUPTED — stuck in coverage_regen loop (pipeline bug)

## Setup
- Repo reset (restore + clean), `.dark-factory/{scenarios,reports,audit,state,failures,events.jsonl}` removed
- Python `__pycache__` cleared
- `agents/` and `templates/` synced to budget-brain-v2/.dark-factory/
- `pip install -e dark-factory` ✓
- `dark-factory build node` ✓ (all layers cached)
- Orphan docker containers/networks cleaned

## Run 1 — Phase progression

| Phase | Wall (s) | Result |
|---|---|---|
| spec-validator (attempt 1) | 286.8 | exit 0 |
| architect (attempt 1) | 150.6 | exit 0 |
| scenario-architect generate (attempt 1) | 168.2 | exit 0 → COVERAGE_GAP (23/23 uncovered) |
| scenario-architect coverage_regen (attempt 1) | 253.1 | exit 0 → COVERAGE_GAP (23/23 still uncovered) |
| scenario-architect coverage_regen (attempt 2) | running when killed | — |

State at interrupt:
```
spec=2026-03-28-api-bootstrap
run_id=eb1ad3c9bf8745a6
status=running
completed_step=1
```

### Coverage gap (identical across both attempts)
```json
{"uncovered":["FR-1","FR-2","FR-3","FR-4","AC-1","AC-2","AC-3","AC-4","AC-5","AC-6","AC-7","AC-8","AC-9","AC-10","EC-1","EC-2","EC-3","EC-4","EC-5","EC-6","EC-7","EC-8","EC-9"],"attempt":2}
```

Anchor manifest declared 4 FRs + 10 ACs + 9 ECs (23 total). Scenario architect actually produced 12 scenario files:
```
01-health-endpoint.scenarios.md      07-app-error-handling.scenarios.md
02-error-handling.scenarios.md       08-health-checks.scenarios.md
03-cors-security.scenarios.md        09-rate-limiting.scenarios.md
04-edge-cases.scenarios.md           10-environment-edge-cases.scenarios.md
05-server-configuration.scenarios.md 11-fr1-server-startup.scenarios.md
06-structured-logging.scenarios.md   12-fr2-structured-logging.scenarios.md
```
…and these files DO reference anchors via the documented format:
```
# Scenario: health endpoint returns ok status with server uptime
# Requirement: FR-4, AC-3
```
Yet the coverage detector reports zero coverage on every anchor on both passes — indicating the detector cannot read these files at all.

## Root cause — pipeline bug in `darkfactory/scenarios/coverage.py`

`_iter_scenario_files` (line 136) only yields files whose suffix is in
```python
_SCENARIO_EXTENSIONS = (".js", ".ts", ".jsx", ".tsx", ".py", ".mjs", ".cjs")
```
or whose name contains `.spec.` / `.test.`. The scenario architect emits `*.scenarios.md` (markdown) — **none of these are matched**, so the iterator yields zero files and every anchor is reported uncovered.

Even if `.md` were added to the extension list, `_extract_anchors_from_text` would still fail: it only credits anchors found inside `@covers` JSDoc tags or inside `describe(...)`/`it(...)`/`test(...)` string-literal arguments. The markdown format used here (`# Requirement: FR-4, AC-3`) matches neither pattern. Filename fallback (`_FILENAME_ANCHOR_RE`) also misses for names like `01-health-endpoint.scenarios.md`. Files like `11-fr1-server-startup.scenarios.md` would *almost* trigger filename fallback but the regex requires uppercase or `re.IGNORECASE` matching on `FR-1` as a token — `fr1` (no hyphen) does not match.

**Net effect:** scenario architect succeeds, but the gate is unreachable. Pipeline will burn through `coverage_regen` attempts without progress until the retry budget runs out, regardless of what the scenario agent produces. This is a hard blocker.

### Code reference
- darkfactory/scenarios/coverage.py:86 — `_SCENARIO_EXTENSIONS` excludes `.md`
- darkfactory/scenarios/coverage.py:93 — `_extract_anchors_from_text` doesn't recognize `# Requirement:` markdown headers
- darkfactory/scenarios/coverage.py:136 — `_iter_scenario_files` filter excludes `.scenarios.md`

The disconnect is between the scenario architect's output format (markdown headers per agents/scenario-architect.md) and the coverage scanner's expected format (executable test files with @covers / describe()).

## Cost / time observations
- spec-validator wall = 286s, all three sonnet agents recorded `est_input_tokens=800000, est_output_tokens=400000, turns=200` — these look like fixed default placeholders, not actual measurements. Worth verifying that token accounting is wired up.
- Total wall time before interrupt: ~14 min, all in scenario-architect cycles that cannot succeed.

## Cleanup
- Killed pid 43863
- Removed 3 orphan df containers + df networks

## Recommendation
This pipeline cannot complete a run on a markdown-scenario spec until coverage.py is taught about `.scenarios.md` files and the `# Requirement:` header format (or the scenario architect is changed to embed `@covers` tags). Per instructions, no fix attempted.
