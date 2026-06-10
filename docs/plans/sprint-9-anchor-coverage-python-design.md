# Sprint 9 — anchor-coverage extractor is JS-coupled (first breadth blocker)

> Surfaced by Sprint 2 T2.0. python-fastapi completes the pipeline through the TE,
> then ABORTS on a coverage-floor violation: ALL anchors (FR-1..4/AC-1..7/EC-1..10)
> reported zero-covered DESPITE the TE generating 8 valid pytest scenario files.

## Root cause (trace-confirmed, file:line)
`scenarios/coverage.py:131 _extract_anchors_from_text` credits an anchor only via
two JS idioms:
- `_COVERS_TAG_RE = re.compile(r"@covers\s+([A-Za-z0-9,\s\-]+)")` (coverage.py:66)
  — matches JSDoc `@covers FR-1, AC-7`.
- `[FR-N]` / `FR-N` ONLY inside `describe()/it()/test()` call string literals
  (`_DESCRIBE_CALL_RE`).

The python TE tagged anchors correctly in PYTEST idiom (verified in the generated
scenarios):
- `@pytest.mark.covers("FR-1")` — `.covers(` + quoted args; the `@covers\s+`
  regex needs `@covers` + WHITESPACE, so `.covers("FR-1")` does NOT match.
- `[FR-1]` in the docstring of a `def test_*():` function — not a
  `describe/it/test(...)` call literal, so `_DESCRIBE_CALL_RE` does NOT match.

→ zero anchor credit for every python scenario → false coverage-floor abort.
The TE did its job; the extractor is JS-only.

## Fix options
- **A — broaden the explicit-tag regex to recognize the pytest marker.** Match
  `covers` followed by EITHER whitespace (JS `@covers FR-1`) OR `(` (python
  `@pytest.mark.covers("FR-1")`), then extract anchor IDs from the captured run
  (quotes/commas/parens are non-anchor chars, harmless). One-regex change; mirrors
  the existing explicit-tag credit path; LOW false-credit risk (an explicit
  `covers(...)` marker is an intentional tag, same as `@covers`).
- **B — also credit `[FR-N]` in pytest test docstrings** (`def test_*():` +
  docstring). Higher coverage of TE styles, but higher false-credit risk (a
  docstring `[FR-3]` mention isn't as intentional as a marker) and needs a
  python-test-boundary detector (def test_* + its docstring) analogous to
  `_DESCRIBE_CALL_RE`. More surface.
- **C — both A and B.**

## Proposal: A (broaden the explicit-tag regex).
The python TE emitted the explicit `@pytest.mark.covers("FR-1")` marker on every
test — recognizing it credits ALL anchors with the minimal, lowest-false-credit
change. B's docstring path is a nice-to-have but adds false-credit surface; defer
unless a TE variant relies on docstrings without the marker.

## Questions for Neo
- **Q1:** A vs C? Is broadening the `@covers` regex to also match the
  `.covers(...)` pytest-marker form the right minimal fix, or is the docstring
  path (B) needed for robustness across TE output variance?
- **Q2:** False-credit risk: the coverage floor is a ship-gate; crediting an
  anchor that isn't really tested is worse than a false abort. Does recognizing
  `.covers("FR-1")` (an explicit marker) keep the "explicit-tag only, no prose"
  discipline the current extractor deliberately enforces? Any way a non-test
  `something.covers(...)` string false-credits?
- **Q3:** Language-agnostic framing: should this be a generic "explicit covers
  marker in any of `@covers FR-1` / `.covers("FR-1")` forms" rather than
  python-specific? (The extractor already scans `.py` files — coverage.py:99
  includes `.py` in `_SCENARIO_EXTENSIONS` — so it's meant to be multi-language;
  the tag-grammar just lagged.)
- **Q4:** Is this authorization-sensitive? It changes anchor-coverage crediting
  (a gate input) — but it credits MORE (fewer false aborts), doesn't promote a
  barrier-restricted signal. Confirm.
- **Q5:** Scope: just the extractor, or does the TE's python addendum / the
  coverage-floor retry feedback also need to teach the pytest tag convention so
  the TE reliably emits it? (The TE already emitted it correctly here, so the
  extractor is the gap.)
