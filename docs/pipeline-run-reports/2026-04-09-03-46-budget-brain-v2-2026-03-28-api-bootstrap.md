# Pipeline Run Report — 2026-04-09 03:46 UTC

**Repo:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Framework commit:** 70612e0 (Phase 4.6b.1d)

Trigger: validate Phase 4.6b.1d gate parser fix + fail-loud invariant.

## Run 10 — CRASHED in `_step_scenario_generation` (run_id `456d0a493e9744db`)

**Outcome:** framework crash after qa-holdout completes, before TE
generation. No scenarios executed, no quality gate, no 4.6b.1d signal.

### Timeline
| Phase | Wall (s) | Status |
|---|---|---|
| spec-validator | 362 | PASS (twice Run 9's 161s — non-determinism) |
| architect | 184 | PASS |
| qa-holdout | 438 | PASS |
| test-engineer | — | **never started** |

### Crash
```
File ".../darkfactory/pipeline/orchestrator.py", line 1445, in _step_scenario_generation
    manifest_context, _manifest_has_api = _load_manifest_context_for_sa(...)
File ".../darkfactory/spec/manifest.py", line 835, in format_manifest_for_sa
    status = c.response.get("status", "???") if c.response else "???"
AttributeError: 'str' object has no attribute 'get'
```

### Root cause — three-layer failure

The architect wrote the manifest's `response` field as a YAML string
containing inline JSON-like text, not as a proper YAML dict:

```yaml
# .dark-factory/architecture/2026-03-28-api-bootstrap-manifest.yaml:70
response: "{ status: 200, body: { type: object, properties: { data: { ... } } } }"
satisfies: [FR-4]
```

1. **Architect prompt gap.** The architect agent produced a `response`
   value as a quoted string literal — probably because the inline
   example in its prompt or the manifest schema doc showed an embedded
   JSON-ish shape without explicit YAML-dict structure.
2. **Parser has no schema guard.** `parse_manifest` accepts the string
   value silently. The `ApiContract.response` field is typed loosely
   (probably `Any` or `dict | str | None`) so this drifts past parse
   time.
3. **Formatter has no type check.** `format_manifest_for_sa` at
   `darkfactory/spec/manifest.py:835` assumes `c.response` is a dict
   and calls `.get("status")`, crashing on the string.

Any ONE of these three could have caught it. None did.

### Why this matters

- Fully blocks any 4.6b.1d validation signal — the gate never ran.
- The crash is in a code path that runs between qa-holdout and
  test-engineer. No partial artifacts are produced; resume would
  re-crash deterministically.
- The manifest file on disk is corrupt from Dark Factory's POV but
  syntactically valid YAML. An architect re-run without prompt
  changes will likely reproduce the same string shape.

### Bug report (documented, not fixed per protocol #10)

**Phase 4.6b.1f candidate:** three-layer fix, any single layer is
sufficient to unblock but all three are cheap:

1. **Parser guard (authoritative, do this first).** In
   `parse_manifest`, validate `ApiContract.response` is a dict — if
   it's a string, fail the parse with a clear error naming the
   contract path and the expected shape. Stops the corrupt manifest
   from ever leaving the architect's container.

2. **Formatter type guard.** In
   `format_manifest_for_sa::manifest.py:835`, guard
   `isinstance(c.response, dict)` before calling `.get`. Fallback to
   `str(c.response)` if it's a string. Defense-in-depth — catches
   any future field-shape drift without crashing the pipeline.

3. **Architect prompt correction.** Update `agents/architect.md` (or
   the manifest template it references) with an explicit YAML-dict
   example for `response`:
   ```yaml
   response:
     status: 200
     body:
       type: object
       properties: {...}
   ```
   Prevents the architect from reproducing the string shape.

Order: 1 > 2 > 3. Layer 1 is the real fix; layers 2 and 3 are the
guards that would have caught it earlier.

### Phase 4.6b.1d status

Still unvalidated end-to-end. The shell fix + fail-loud invariant
are in the commit (70612e0), the shell fixture test passes, and
the coupling check is documented — but no live pipeline run has
exercised them yet. Run 11 (after 4.6b.1f lands) will be the real
validation.

### Non-determinism note
spec-validator wall time doubled (161s → 362s) between Run 9 and
Run 10 on identical spec content. Same pattern as Run 2's
FAIL→PASS flip. Still tracked as a separate bug, not new.
